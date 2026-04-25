"""W5-2 多租户：tenants 表 + PrincipalContext + tenant_id 贯穿读写路径。

覆盖：
1. tenants CRUD 与 API key 哈希查询；
2. ``require_api_key`` 三种模式（多租户 / 单租户 / 匿名）的身份解析；
3. 写路径 (insert_run / insert_feedback / insert_recap_audit / enqueue_pending_action)
   能正确把 tenant_id 落库；
4. 读路径 (load_history / load_recent_runs / load_feedback_summary / load_recap_audit)
   按 tenant_id 严格隔离；
5. ``RunContext.with_overrides`` 能正确覆盖 tenant_id；
6. 端到端：HTTP /v1/history / /v1/audit 不会跨租户泄漏数据。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterator

import pytest
from fastapi.testclient import TestClient

import agent_platform.config.settings as settings_module
import agent_platform.infrastructure.persistence.db as db_module
from agent_platform.config.settings import Settings
from agent_platform.domain.models import (
    Features,
    LlmTokens,
    MarketSnapshot,
    RecapDaily,
    RecapDailySection,
)
from agent_platform.domain.principal import PrincipalContext, current_principal
from agent_platform.domain.run_context import RunContext
from agent_platform.infrastructure.persistence.db import (
    count_tenants,
    enqueue_pending_action,
    init_db,
    insert_feedback,
    insert_recap_audit,
    insert_run,
    list_tenants,
    load_feedback_summary,
    load_history,
    load_recap_audit,
    load_recent_runs,
    load_tenant_by_api_key_hash,
    upsert_tenant,
)


# ─── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def file_db(tmp_path):
    path = str(tmp_path / "tenants.db")
    init_db(path)
    return path


@pytest.fixture(autouse=True)
def _reset_principal() -> Iterator[None]:
    """每个 case 跑完都把 ``current_principal`` 还原，避免污染下一个测试。"""
    token = current_principal.set(PrincipalContext.anonymous())
    try:
        yield
    finally:
        try:
            current_principal.reset(token)
        except Exception:
            pass


def _hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _make_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        asof="2024-04-02T08:00:00+00:00",
        provider="mock",
        date="2024-04-02",
        is_trading_day=True,
    )


def _make_features() -> Features:
    return Features(
        index_view="平", sector_view="混", sentiment_view="中性", macro_view="稳"
    )


def _insert_run(db_path: str, *, request_id: str, tenant_id: str | None) -> None:
    insert_run(
        db_path,
        request_id=request_id,
        created_at="2024-04-02T08:00:00+00:00",
        mode="daily",
        provider="mock",
        date="2024-04-02",
        prompt_version="v1",
        model=None,
        snapshot=_make_snapshot(),
        features=_make_features(),
        recap=None,
        rendered_markdown=None,
        rendered_wechat_text=None,
        eval_obj={},
        error=None,
        latency_ms=10,
        tokens=LlmTokens(),
        tenant_id=tenant_id,
    )


# ─── 1. tenants CRUD + 查询 ──────────────────────────────────────────────────


def test_upsert_and_load_tenant_by_api_key_hash(file_db):
    digest_a = _hash("key-a")
    upsert_tenant(
        file_db,
        tenant_id="t-a",
        name="Alice",
        api_key_hash=digest_a,
        role="user",
        status="active",
    )
    digest_b = _hash("key-b")
    upsert_tenant(
        file_db,
        tenant_id="t-b",
        name="Bob",
        api_key_hash=digest_b,
        role="admin",
        status="active",
    )

    found = load_tenant_by_api_key_hash(file_db, api_key_hash=digest_a)
    assert found is not None
    assert found["tenant_id"] == "t-a"
    assert found["role"] == "user"

    # disabled 的租户不应被查到
    upsert_tenant(
        file_db,
        tenant_id="t-c",
        name="Carol",
        api_key_hash=_hash("key-c"),
        role="user",
        status="disabled",
    )
    assert load_tenant_by_api_key_hash(file_db, api_key_hash=_hash("key-c")) is None

    assert count_tenants(file_db, status="active") == 2
    items = list_tenants(file_db, status="active")
    assert {it["tenant_id"] for it in items} == {"t-a", "t-b"}


# ─── 2. require_api_key 三种模式 ─────────────────────────────────────────────


def _build_app_with_settings(settings: Settings):
    """每个 case 重新装一遍 app + 注入 settings，避免 Settings 单例污染。"""
    settings_module._settings_instance = settings
    from agent_platform.config.settings import get_settings as _get_settings
    from agent_platform.interfaces.api.app import create_app

    app = create_app()
    app.dependency_overrides[_get_settings] = lambda: settings
    return app


def _settings(tmp_path, monkeypatch, **overrides) -> Settings:
    """构造一份只到本 case 用的临时 settings；不污染其它测试。

    必须通过环境变量构造（Pydantic-Settings + AliasChoices 不读 ctor kwargs）。
    """
    db = str(tmp_path / "deps.db")
    init_db(db)
    settings_module._settings_instance = None

    monkeypatch.setenv("RECAP_DB_PATH", db)
    monkeypatch.setenv("RECAP_RATE_LIMIT_RPM", "1000")
    monkeypatch.setenv("RECAP_TOOLS_ENABLED", "false")
    monkeypatch.setenv("RECAP_AGENT_MAX_WALL_MS", "60000")
    if "recap_api_key" in overrides:
        monkeypatch.setenv("RECAP_API_KEY", overrides.pop("recap_api_key"))
    else:
        monkeypatch.delenv("RECAP_API_KEY", raising=False)
    s = Settings()
    return s


def test_require_api_key_multi_tenant_path(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    upsert_tenant(
        s.db_path,
        tenant_id="t-1",
        name="t1",
        api_key_hash=_hash("secret-1"),
        role="user",
        status="active",
    )
    app = _build_app_with_settings(s)
    client = TestClient(app)

    # 没带 key → 401
    assert client.get("/v1/history").status_code == 401
    # 带错误 key → 401
    assert client.get(
        "/v1/history", headers={"X-API-Key": "wrong"}
    ).status_code == 401
    # 带正确 key → 200
    assert client.get(
        "/v1/history", headers={"X-API-Key": "secret-1"}
    ).status_code == 200


def test_require_api_key_single_tenant_path(tmp_path, monkeypatch):
    """tenants 表为空 + 设置了 RECAP_API_KEY → 走单租户固定 key。"""
    s = _settings(tmp_path, monkeypatch, recap_api_key="single-key")
    app = _build_app_with_settings(s)
    client = TestClient(app)

    assert client.get("/v1/history").status_code == 401
    assert client.get(
        "/v1/history", headers={"X-API-Key": "wrong"}
    ).status_code == 401
    assert client.get(
        "/v1/history", headers={"X-API-Key": "single-key"}
    ).status_code == 200


def test_require_api_key_anonymous_path(tmp_path, monkeypatch):
    """tenants 表为空 + 没设 RECAP_API_KEY → 本地开发匿名放行。"""
    s = _settings(tmp_path, monkeypatch)
    app = _build_app_with_settings(s)
    client = TestClient(app)

    r = client.get("/v1/history")
    assert r.status_code == 200


# ─── 3+4. 写路径落库 + 读路径隔离 ────────────────────────────────────────────


def test_insert_and_load_run_isolated_by_tenant(file_db):
    _insert_run(file_db, request_id="r-a-1", tenant_id="t-a")
    _insert_run(file_db, request_id="r-a-2", tenant_id="t-a")
    _insert_run(file_db, request_id="r-b-1", tenant_id="t-b")
    _insert_run(file_db, request_id="r-anon", tenant_id=None)

    # 全局视图（tenant_id=None）能看到所有
    assert len(load_history(file_db, limit=10)) == 4
    # 按租户严格隔离
    a = load_history(file_db, limit=10, tenant_id="t-a")
    assert {it["request_id"] for it in a} == {"r-a-1", "r-a-2"}
    b = load_history(file_db, limit=10, tenant_id="t-b")
    assert {it["request_id"] for it in b} == {"r-b-1"}
    # 不存在的租户 → 空
    assert load_history(file_db, limit=10, tenant_id="t-nope") == []


def test_load_recent_runs_isolated_by_tenant(file_db):
    """``load_recent_runs`` 是 memory 召回入口，必须按租户隔离。"""
    def _section(idx: int) -> RecapDailySection:
        return RecapDailySection(
            title=f"题材-{idx}",
            core_conclusion=f"结论-{idx}",
            key_observations=[f"o{idx}-1", f"o{idx}-2"],
            bullets=[f"b{idx}-1", f"b{idx}-2"],
            evidence=[f"e{idx}-1", f"e{idx}-2"],
        )

    recap = RecapDaily(
        mode="daily",
        date="2024-04-01",
        summary="ok",
        sections=[_section(1), _section(2), _section(3)],
        action_items=[],
        risks=[],
        confidence=0.7,
    )
    # 历史 run 在更早的日期，便于 date<? 过滤命中
    insert_run(
        file_db,
        request_id="hist-a",
        created_at="2024-04-01T08:00:00+00:00",
        mode="daily",
        provider="mock",
        date="2024-04-01",
        prompt_version="v1",
        model=None,
        snapshot=_make_snapshot(),
        features=_make_features(),
        recap=recap,
        rendered_markdown=None,
        rendered_wechat_text=None,
        eval_obj={"ok": True},
        error=None,
        latency_ms=10,
        tokens=LlmTokens(),
        tenant_id="t-a",
    )
    insert_run(
        file_db,
        request_id="hist-b",
        created_at="2024-04-01T08:00:00+00:00",
        mode="daily",
        provider="mock",
        date="2024-04-01",
        prompt_version="v1",
        model=None,
        snapshot=_make_snapshot(),
        features=_make_features(),
        recap=recap,
        rendered_markdown=None,
        rendered_wechat_text=None,
        eval_obj={"ok": True},
        error=None,
        latency_ms=10,
        tokens=LlmTokens(),
        tenant_id="t-b",
    )

    a = load_recent_runs(file_db, "2024-04-02", "daily", limit=10, tenant_id="t-a")
    assert {r["date"] for r in a} == {"2024-04-01"}
    assert len(a) == 1
    b = load_recent_runs(file_db, "2024-04-02", "daily", limit=10, tenant_id="t-b")
    assert len(b) == 1
    assert load_recent_runs(file_db, "2024-04-02", "daily", limit=10, tenant_id="t-x") == []


def test_feedback_summary_isolated_by_tenant(file_db):
    insert_feedback(
        file_db,
        request_id="r-a",
        created_at="2024-04-02T09:00:00+00:00",
        rating=5,
        tags=["清晰"],
        comment="A 很好",
        tenant_id="t-a",
    )
    insert_feedback(
        file_db,
        request_id="r-b",
        created_at="2024-04-02T09:00:00+00:00",
        rating=1,
        tags=["错误"],
        comment="B 很差",
        tenant_id="t-b",
    )
    summary_a = load_feedback_summary(file_db, limit=10, tenant_id="t-a")
    assert summary_a["avg_rating"] == 5.0
    assert "清晰" in summary_a["praise_tags"]
    summary_b = load_feedback_summary(file_db, limit=10, tenant_id="t-b")
    assert summary_b["avg_rating"] == 1.0
    assert "错误" in summary_b["low_rated_tags"]


def test_recap_audit_isolated_by_tenant(file_db):
    insert_recap_audit(
        file_db,
        request_id="r-a",
        created_at="2024-04-02T10:00:00+00:00",
        mode="daily",
        provider="mock",
        prompt_version="v1",
        model=None,
        trace_id=None,
        session_id=None,
        messages=[{"role": "user", "content": "hi"}],
        recap=None,
        eval_obj=None,
        tokens=None,
        llm_error=None,
        budget_error=None,
        critic_retries_used=0,
        tenant_id="t-a",
    )
    insert_recap_audit(
        file_db,
        request_id="r-b",
        created_at="2024-04-02T10:00:00+00:00",
        mode="daily",
        provider="mock",
        prompt_version="v1",
        model=None,
        trace_id=None,
        session_id=None,
        messages=[{"role": "user", "content": "hi"}],
        recap=None,
        eval_obj=None,
        tokens=None,
        llm_error=None,
        budget_error=None,
        critic_retries_used=0,
        tenant_id="t-b",
    )

    only_a = load_recap_audit(file_db, limit=10, tenant_id="t-a")
    assert {a["request_id"] for a in only_a} == {"r-a"}
    only_b = load_recap_audit(file_db, limit=10, tenant_id="t-b")
    assert {a["request_id"] for a in only_b} == {"r-b"}

    # 跨租户访问按 request_id 应当查不到（避免泄漏存在性）
    cross = load_recap_audit(file_db, request_id="r-a", limit=1, tenant_id="t-b")
    assert cross == []


def test_pending_action_persists_tenant_id(file_db):
    inserted = enqueue_pending_action(
        file_db,
        request_id="req-1",
        action_type="push",
        payload_json="{}",
        now_iso="2024-04-02T10:00:00+00:00",
        tenant_id="t-a",
    )
    assert inserted is True
    with db_module.get_conn(file_db) as conn:
        row = conn.execute(
            "SELECT tenant_id FROM pending_actions WHERE request_id = ?",
            ("req-1",),
        ).fetchone()
    assert row is not None
    assert row["tenant_id"] == "t-a"


# ─── 5. RunContext.with_overrides ───────────────────────────────────────────


def test_run_context_with_overrides_tenant_id():
    base = RunContext.new()
    assert base.tenant_id is None
    overridden = base.with_overrides(mode="daily", provider="mock", tenant_id="t-x")
    assert overridden.tenant_id == "t-x"
    # 原对象不变（dataclass(frozen=True) + replace 语义）
    assert base.tenant_id is None
    # tenant_id=None 不会清空已有值
    kept = overridden.with_overrides(tenant_id=None)
    assert kept.tenant_id == "t-x"


# ─── 6. 端到端：HTTP 不跨租户 ────────────────────────────────────────────────


def test_http_history_does_not_leak_across_tenants(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    upsert_tenant(
        s.db_path,
        tenant_id="t-a",
        name="A",
        api_key_hash=_hash("key-a"),
        role="user",
        status="active",
    )
    upsert_tenant(
        s.db_path,
        tenant_id="t-b",
        name="B",
        api_key_hash=_hash("key-b"),
        role="user",
        status="active",
    )
    _insert_run(s.db_path, request_id="r-a-1", tenant_id="t-a")
    _insert_run(s.db_path, request_id="r-b-1", tenant_id="t-b")

    app = _build_app_with_settings(s)
    client = TestClient(app)

    a_resp = client.get("/v1/history", headers={"X-API-Key": "key-a"})
    assert a_resp.status_code == 200
    a_ids = {it["request_id"] for it in a_resp.json()["items"]}
    assert a_ids == {"r-a-1"}

    b_resp = client.get("/v1/history", headers={"X-API-Key": "key-b"})
    assert b_resp.status_code == 200
    b_ids = {it["request_id"] for it in b_resp.json()["items"]}
    assert b_ids == {"r-b-1"}


def test_http_audit_lookup_isolated(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    upsert_tenant(
        s.db_path,
        tenant_id="t-a",
        name="A",
        api_key_hash=_hash("key-a"),
        role="user",
        status="active",
    )
    upsert_tenant(
        s.db_path,
        tenant_id="t-b",
        name="B",
        api_key_hash=_hash("key-b"),
        role="user",
        status="active",
    )
    insert_recap_audit(
        s.db_path,
        request_id="r-a",
        created_at="2024-04-02T10:00:00+00:00",
        mode="daily",
        provider="mock",
        prompt_version="v1",
        model=None,
        trace_id=None,
        session_id=None,
        messages=[{"role": "user", "content": "hi"}],
        recap=None,
        eval_obj=None,
        tokens=None,
        llm_error=None,
        budget_error=None,
        critic_retries_used=0,
        tenant_id="t-a",
    )

    app = _build_app_with_settings(s)
    client = TestClient(app)

    # tenant A 自己查得到
    ok = client.get("/v1/audit/r-a", headers={"X-API-Key": "key-a"})
    assert ok.status_code == 200
    assert ok.json()["request_id"] == "r-a"
    # tenant B 看不见，应 404（避免泄漏存在性）
    not_found = client.get("/v1/audit/r-a", headers={"X-API-Key": "key-b"})
    assert not_found.status_code == 404
