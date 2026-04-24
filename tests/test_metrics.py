"""Wave 4 / w4-1：进程内 Prometheus 指标。

覆盖：
1. 单次 recap 生成后 ``recap_runs_total`` / ``recap_phase_duration_ms`` 累计；
2. ``call_llm`` 成功/失败分别计入 ``llm_calls_total`` 与 ``llm_tokens_total``；
3. ``RecapToolRunner`` 拒绝/成功调用都会进 ``tool_invocations_total``；
4. ``outbox.process_due`` 成功 / 失败重试 / no_handler 都会进 ``outbox_actions_total``；
5. ``/metrics/prom`` 端点输出 Prometheus 文本格式（含 HELP/TYPE/+Inf 桶）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import pytest
from fastapi.testclient import TestClient

from stock_recap.application.side_effects import outbox
from stock_recap.config.settings import Settings
from stock_recap.domain.models import (
    GenerateRequest,
    LlmBudgetExceeded,
    LlmTokens,
    LlmTransportError,
    Recap,
    RecapDaily,
    RecapDailySection,
)
from stock_recap.infrastructure.llm.backends import call_llm
from stock_recap.infrastructure.llm.providers import register_provider
from stock_recap.infrastructure.persistence.db import init_db
from stock_recap.infrastructure.tools.runner import RecapToolRunner
from stock_recap.observability.metrics import (
    get_metrics,
    record_outbox_action,
    record_phase_duration,
    record_recap_run,
    reset_default_metrics,
)
from stock_recap.policy.tools import (
    ToolDisabled,
    ToolPolicy,
    ToolPolicyRegistry,
)


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_default_metrics()
    yield
    reset_default_metrics()


def _settings_via_env(tmp_path, monkeypatch, name: str = "metrics.db") -> Settings:
    """Pydantic-Settings + AliasChoices 不接受构造器 kwargs，必须走环境变量。"""
    db = tmp_path / name
    monkeypatch.setenv("RECAP_DB_PATH", str(db))
    monkeypatch.setenv("RECAP_WXWORK_WEBHOOK_URL", "http://example.invalid/hook")
    monkeypatch.setenv("RECAP_PUSH_ENABLED", "false")
    return Settings()


def _build_valid_recap() -> Recap:
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
        disclaimer="本内容仅供参考，不构成投资建议。投资有风险，入市需谨慎。",
    )


# ─── 1. record_* 直接调用 ────────────────────────────────────────────────────


def test_record_helpers_accumulate():
    record_recap_run("daily", "live", "ok")
    record_recap_run("daily", "live", "ok")
    record_recap_run("daily", "mock", "failed")
    record_phase_duration("perceive", 12.0)
    record_phase_duration("perceive", 8.0)
    record_outbox_action("evolution", "done")

    m = get_metrics()
    assert m.counter_value(
        "recap_runs_total", labels={"mode": "daily", "provider": "live", "status": "ok"}
    ) == 2.0
    assert m.counter_value(
        "recap_runs_total", labels={"mode": "daily", "provider": "mock", "status": "failed"}
    ) == 1.0
    assert m.histogram_count("recap_phase_duration_ms", labels={"phase": "perceive"}) == 2
    assert m.histogram_sum("recap_phase_duration_ms", labels={"phase": "perceive"}) == 20.0
    assert m.counter_value(
        "outbox_actions_total", labels={"action_type": "evolution", "status": "done"}
    ) == 1.0


# ─── 2. call_llm 成功/失败分别记账 ───────────────────────────────────────────


class _FakeProvider:
    backend_name = "metrics_fake"

    def __init__(self, recap: Recap | None = None, exc: Exception | None = None,
                 tokens: Tuple[int, int] = (10, 20)):
        self._recap = recap
        self._exc = exc
        self._tokens = tokens

    def call(self, settings, mode, messages, *, model, db_path, date):
        if self._exc is not None:
            raise self._exc
        return self._recap, LlmTokens(input_tokens=self._tokens[0], output_tokens=self._tokens[1])


@pytest.fixture
def _patched_backend(monkeypatch):
    """绕过 resolve.py 的 backend 字符串严格匹配；让 call_llm 直接路由到 fake provider。

    这里我们关心的是 ``call_llm`` 在 ok/transport/budget/business 各分支是否记账正确，
    backend 字符串只是 metric 标签，不影响测试断言。
    """
    holder: Dict[str, Any] = {"provider": None}

    def _fake_resolve_provider(_backend):
        return holder["provider"]

    def _fake_backend_effective(_model_spec, _settings=None):
        return "metrics_fake"

    monkeypatch.setattr(
        "stock_recap.infrastructure.llm.backends.resolve_provider",
        _fake_resolve_provider,
    )
    monkeypatch.setattr(
        "stock_recap.infrastructure.llm.backends.llm_backend_effective",
        _fake_backend_effective,
    )
    return holder


def _call_llm_with(
    settings: Settings, provider: _FakeProvider, holder: Dict[str, Any], mode: str = "daily"
):
    holder["provider"] = provider
    return call_llm(
        settings=settings,
        mode=mode,
        messages=[{"role": "user", "content": "hi"}],
        model_spec="metrics_fake:echo",
        db_path=settings.db_path,
        date="2025-01-02",
    )


def test_call_llm_records_ok_and_tokens(tmp_path, monkeypatch, _patched_backend):
    settings = _settings_via_env(tmp_path, monkeypatch)
    init_db(settings.db_path)
    recap = _build_valid_recap()

    _call_llm_with(settings, _FakeProvider(recap=recap, tokens=(7, 13)), _patched_backend)

    m = get_metrics()
    assert m.counter_value(
        "llm_calls_total", labels={"backend": "metrics_fake", "status": "ok"}
    ) == 1.0
    assert m.counter_value(
        "llm_tokens_total", labels={"backend": "metrics_fake", "kind": "input"}
    ) == 7.0
    assert m.counter_value(
        "llm_tokens_total", labels={"backend": "metrics_fake", "kind": "output"}
    ) == 13.0


def test_call_llm_records_transport_error_each_retry(tmp_path, monkeypatch, _patched_backend):
    """``LlmTransportError`` 被 tenacity 重试，每次都进 transport_error 计数。"""
    settings = _settings_via_env(tmp_path, monkeypatch)
    init_db(settings.db_path)

    err = LlmTransportError("net down")
    with pytest.raises(LlmTransportError):
        _call_llm_with(settings, _FakeProvider(exc=err), _patched_backend)

    transport = get_metrics().counter_value(
        "llm_calls_total", labels={"backend": "metrics_fake", "status": "transport_error"}
    )
    # tenacity 默认 stop_after_attempt(3) → 计 3 次
    assert transport == 3.0


def test_call_llm_records_budget_exceeded_once(tmp_path, monkeypatch, _patched_backend):
    settings = _settings_via_env(tmp_path, monkeypatch)
    init_db(settings.db_path)

    err = LlmBudgetExceeded(kind="tool_calls", used=5, limit=5)
    with pytest.raises(LlmBudgetExceeded):
        _call_llm_with(settings, _FakeProvider(exc=err), _patched_backend)

    assert get_metrics().counter_value(
        "llm_calls_total", labels={"backend": "metrics_fake", "status": "budget_exceeded"}
    ) == 1.0


# ─── 3. RecapToolRunner 工具调用计数 ────────────────────────────────────────


def test_tool_invocation_metric_records_ok_and_denied(tmp_path, monkeypatch):
    settings = _settings_via_env(tmp_path, monkeypatch)
    init_db(settings.db_path)
    settings.tools_enabled = True
    settings.tools_web_search = True
    settings.tool_audit_enabled = False  # 测试不依赖审计落库

    # runner 内部通过 ``execute_tool(name, arguments, db_path)`` 真正执行；
    # 把它桩成成功返回，确保关注点只在 metric。
    def _fake_execute(name, args, db_path):
        return json.dumps({"ok": True, "name": name, "args": args}, ensure_ascii=False)

    monkeypatch.setattr(
        "stock_recap.infrastructure.tools.runner.execute_tool", _fake_execute
    )

    policy_reg = ToolPolicyRegistry()
    policy_reg.register(
        ToolPolicy(
            name="web_search",
            enabled=True,
            read_only=True,
            description="ok tool",
        )
    )
    policy_reg.register(
        ToolPolicy(
            name="query_market_data",
            enabled=False,
            read_only=True,
            description="disabled",
        )
    )
    runner = RecapToolRunner(settings=settings, policy_registry=policy_reg)

    out = runner.execute("web_search", {"q": "x"}, settings.db_path)
    assert "ok" in out

    with pytest.raises(ToolDisabled):
        runner.execute("query_market_data", {}, settings.db_path)

    m = get_metrics()
    assert m.counter_value(
        "tool_invocations_total", labels={"tool": "web_search", "status": "ok"}
    ) == 1.0
    assert m.counter_value(
        "tool_invocations_total",
        labels={"tool": "query_market_data", "status": "denied"},
    ) == 1.0


# ─── 4. outbox 失败/成功 metric ──────────────────────────────────────────────


def test_outbox_metric_done_and_no_handler(tmp_path, monkeypatch):
    settings = _settings_via_env(tmp_path, monkeypatch)
    init_db(settings.db_path)

    calls: List[Dict[str, Any]] = []

    def _ok_handler(payload: Dict[str, Any]) -> None:
        calls.append(payload)

    outbox.register_handler("metrics_action_ok", _ok_handler)
    outbox.enqueue(
        settings.db_path, request_id="r1", action_type="metrics_action_ok", payload={"k": 1}
    )
    outbox.enqueue(
        settings.db_path,
        request_id="r2",
        action_type="metrics_action_unknown",
        payload={},
    )
    summary = outbox.process_due(settings.db_path, batch=10)
    assert summary.done == 1
    assert summary.failed_final == 1

    m = get_metrics()
    assert m.counter_value(
        "outbox_actions_total", labels={"action_type": "metrics_action_ok", "status": "done"}
    ) == 1.0
    assert m.counter_value(
        "outbox_actions_total",
        labels={"action_type": "metrics_action_unknown", "status": "failed"},
    ) == 1.0


# ─── 5. /metrics/prom 端点 ────────────────────────────────────────────────────


def test_metrics_prom_endpoint_returns_exposition_format(tmp_path, monkeypatch):
    monkeypatch.setenv("RECAP_DB_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("RECAP_WXWORK_WEBHOOK_URL", "http://example.invalid/hook")
    monkeypatch.setenv("RECAP_PUSH_ENABLED", "false")

    record_recap_run("daily", "live", "ok")
    record_phase_duration("plan", 42.0)

    from stock_recap.interfaces.api.app import create_app

    app = create_app()
    client = TestClient(app)
    resp = client.get("/metrics/prom")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "# TYPE recap_runs_total counter" in body
    assert "# TYPE recap_phase_duration_ms histogram" in body
    assert (
        'recap_runs_total{mode="daily",provider="live",status="ok"} 1' in body
    )
    assert 'recap_phase_duration_ms_bucket' in body
    assert 'le="+Inf"' in body
    assert 'recap_phase_duration_ms_count{phase="plan"} 1' in body


def test_metrics_prom_renders_for_unrecorded_metrics(tmp_path, monkeypatch):
    """初始状态（没有任何指标写入）也应给出可被抓取的输出（避免 Prometheus 报错）。"""
    monkeypatch.setenv("RECAP_DB_PATH", str(tmp_path / "ep2.db"))
    monkeypatch.setenv("RECAP_WXWORK_WEBHOOK_URL", "http://example.invalid/hook")
    monkeypatch.setenv("RECAP_PUSH_ENABLED", "false")
    from stock_recap.interfaces.api.app import create_app

    client = TestClient(create_app())
    resp = client.get("/metrics/prom")
    assert resp.status_code == 200
    assert "recap_runs_total" in resp.text
    assert "recap_phase_duration_ms_count 0" in resp.text
