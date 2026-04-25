"""Wave 4 / w4-3：``recap_audit`` 表与 recap_runs 分离。

覆盖：
1. ``insert_recap_audit`` + ``load_recap_audit`` 的字段映射；
2. 同 request_id 重复 insert 不会爆 IntegrityError（幂等）；
3. ``recap_audit_enabled=False`` 时 pipeline 不写表；
4. ``/v1/audit/{request_id}`` 与 ``/v1/audit?mode=...`` HTTP 端点。
"""
from __future__ import annotations

import json
from typing import List

import pytest
from fastapi.testclient import TestClient

from agent_platform.config.settings import Settings
from agent_platform.domain.models import LlmTokens, RecapDaily, RecapDailySection
from agent_platform.infrastructure.persistence.db import (
    init_db,
    insert_recap_audit,
    load_recap_audit,
)


def _settings_via_env(tmp_path, monkeypatch, *, audit_enabled: bool = True) -> Settings:
    db = tmp_path / "audit.db"
    monkeypatch.setenv("RECAP_DB_PATH", str(db))
    monkeypatch.setenv("RECAP_WXWORK_WEBHOOK_URL", "http://example.invalid/hook")
    monkeypatch.setenv("RECAP_PUSH_ENABLED", "false")
    monkeypatch.setenv("RECAP_API_KEY", "test-key")
    monkeypatch.setenv("RECAP_AUDIT_ENABLED", "true" if audit_enabled else "false")
    # 关键：FastAPI DI 内部走 ``get_settings()``，它是模块级单例（不是 lru_cache），
    # 必须显式重置 ``_settings_instance`` 才能让新 env 生效。
    import agent_platform.config.settings as _settings_mod
    _settings_mod._settings_instance = None  # noqa: SLF001
    return Settings()


def _build_recap() -> RecapDaily:
    section = RecapDailySection(
        title="示例标题",
        core_conclusion="示例结论。",
        bullets=["【复盘基准日：2025年01月02日 星期四】", "分析点 A", "分析点 B"],
    )
    return RecapDaily(
        mode="daily",
        date="2025-01-02",
        sections=[section, section, section],
        risks=["不构成投资建议"],
    )


def test_insert_and_load_round_trip(tmp_path, monkeypatch):
    settings = _settings_via_env(tmp_path, monkeypatch)
    init_db(settings.db_path)
    recap = _build_recap()
    messages = [
        {"role": "system", "content": "you are a recap analyst"},
        {"role": "user", "content": "give me daily recap"},
    ]
    insert_recap_audit(
        settings.db_path,
        request_id="req-1",
        created_at="2025-01-02T15:00:00+00:00",
        mode="daily",
        provider="live",
        prompt_version="v123",
        model="openai:gpt-4",
        trace_id="trace-x",
        session_id="sess-1",
        messages=messages,
        recap=recap,
        eval_obj={"ok": True, "score": 0.91},
        tokens=LlmTokens(input_tokens=120, output_tokens=480),
        llm_error=None,
        budget_error=None,
        critic_retries_used=1,
    )

    rows = load_recap_audit(settings.db_path, request_id="req-1")
    assert len(rows) == 1
    row = rows[0]
    assert row["mode"] == "daily"
    assert row["provider"] == "live"
    assert row["prompt_version"] == "v123"
    assert row["model"] == "openai:gpt-4"
    assert row["trace_id"] == "trace-x"
    assert row["session_id"] == "sess-1"
    assert row["critic_retries_used"] == 1
    # 解构后字段
    assert row["messages"] == messages
    assert row["recap"]["date"] == "2025-01-02"
    assert row["eval"]["ok"] is True
    assert row["tokens"]["input_tokens"] == 120


def test_idempotent_on_same_request_id(tmp_path, monkeypatch):
    settings = _settings_via_env(tmp_path, monkeypatch)
    init_db(settings.db_path)
    recap = _build_recap()

    common = dict(
        request_id="req-dup",
        mode="daily",
        provider="live",
        prompt_version="v1",
        model=None,
        trace_id=None,
        session_id=None,
        recap=recap,
        eval_obj=None,
        tokens=None,
        llm_error=None,
        budget_error=None,
        critic_retries_used=0,
    )
    insert_recap_audit(
        settings.db_path,
        created_at="2025-01-02T10:00:00+00:00",
        messages=[{"role": "user", "content": "first"}],
        **common,
    )
    insert_recap_audit(
        settings.db_path,
        created_at="2025-01-02T11:00:00+00:00",  # 后到的时间戳
        messages=[{"role": "user", "content": "second"}],
        **common,
    )
    rows = load_recap_audit(settings.db_path, request_id="req-dup")
    assert len(rows) == 1, "UNIQUE(request_id) 必须只保留一条"
    # 保留首条（更接近真实 LLM 输入）
    assert rows[0]["messages"] == [{"role": "user", "content": "first"}]


def test_pipeline_writes_audit_when_enabled(tmp_path, monkeypatch):
    """全链路：generate_once → recap_audit 表能查到对应 request_id。"""
    monkeypatch.setenv("RECAP_DB_PATH", str(tmp_path / "audit-e2e.db"))
    monkeypatch.setenv("RECAP_WXWORK_WEBHOOK_URL", "http://example.invalid/hook")
    monkeypatch.setenv("RECAP_PUSH_ENABLED", "false")
    monkeypatch.setenv("RECAP_AUDIT_ENABLED", "true")

    from agent_platform.application.recap import generate_once
    import agent_platform.config.settings as _settings_mod
    _settings_mod._settings_instance = None  # noqa: SLF001
    settings = _settings_mod.Settings()
    init_db(settings.db_path)

    from agent_platform.domain.models import GenerateRequest

    req = GenerateRequest(
        mode="daily",
        provider="mock",
        force_llm=False,
        skip_trading_check=True,
    )
    resp = generate_once(req, settings)
    rows = load_recap_audit(settings.db_path, request_id=resp.request_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["mode"] == "daily"
    assert row["provider"] == "mock"
    # force_llm=False 时不会有 LLM 调用，因此 messages 字段可能为 None / 空
    assert row["llm_error"] is None
    assert row["critic_retries_used"] == 0


def test_pipeline_skips_audit_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("RECAP_DB_PATH", str(tmp_path / "audit-off.db"))
    monkeypatch.setenv("RECAP_WXWORK_WEBHOOK_URL", "http://example.invalid/hook")
    monkeypatch.setenv("RECAP_PUSH_ENABLED", "false")
    monkeypatch.setenv("RECAP_AUDIT_ENABLED", "false")

    from agent_platform.application.recap import generate_once
    import agent_platform.config.settings as _settings_mod
    from agent_platform.domain.models import GenerateRequest

    _settings_mod._settings_instance = None  # noqa: SLF001
    settings = _settings_mod.Settings()
    init_db(settings.db_path)

    req = GenerateRequest(
        mode="daily", provider="mock", force_llm=False, skip_trading_check=True
    )
    resp = generate_once(req, settings)
    rows = load_recap_audit(settings.db_path, request_id=resp.request_id)
    assert rows == []


def test_audit_endpoint_get_by_id(tmp_path, monkeypatch):
    settings = _settings_via_env(tmp_path, monkeypatch)
    init_db(settings.db_path)
    insert_recap_audit(
        settings.db_path,
        request_id="api-req",
        created_at="2025-01-02T12:00:00+00:00",
        mode="daily",
        provider="mock",
        prompt_version="v1",
        model=None,
        trace_id=None,
        session_id=None,
        messages=[{"role": "user", "content": "ping"}],
        recap=None,
        eval_obj=None,
        tokens=None,
        llm_error=None,
        budget_error=None,
        critic_retries_used=0,
    )

    from agent_platform.interfaces.api.app import create_app

    client = TestClient(create_app())

    r = client.get("/v1/audit/api-req", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["request_id"] == "api-req"
    assert body["messages"] == [{"role": "user", "content": "ping"}]

    r404 = client.get("/v1/audit/non-exist", headers={"X-API-Key": "test-key"})
    assert r404.status_code == 404


def test_audit_endpoint_list_filter_by_mode(tmp_path, monkeypatch):
    settings = _settings_via_env(tmp_path, monkeypatch)
    init_db(settings.db_path)
    for rid, mode in [("a", "daily"), ("b", "strategy"), ("c", "daily")]:
        insert_recap_audit(
            settings.db_path,
            request_id=rid,
            created_at="2025-01-02T12:00:00+00:00",
            mode=mode,
            provider="mock",
            prompt_version="v1",
            model=None,
            trace_id=None,
            session_id=None,
            messages=None,
            recap=None,
            eval_obj=None,
            tokens=None,
            llm_error=None,
            budget_error=None,
            critic_retries_used=0,
        )

    from agent_platform.interfaces.api.app import create_app

    client = TestClient(create_app())
    r = client.get("/v1/audit?mode=daily&limit=10", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert {row["request_id"] for row in items} == {"a", "c"}
