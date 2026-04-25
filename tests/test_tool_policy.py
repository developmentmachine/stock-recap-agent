"""Tool 治理：ToolPolicy 拒绝路径 + tool_invocations 审计。"""
from __future__ import annotations

import time
from typing import Any, Dict

import pytest

from agent_platform.application.orchestration.budget import AgentBudget
from agent_platform.config.settings import Settings
from agent_platform.domain.models import LlmBudgetExceeded
from agent_platform.infrastructure.persistence.db import (
    init_db,
    load_recent_tool_invocations,
)
from agent_platform.infrastructure.tools import runner as runner_mod
from agent_platform.infrastructure.tools.runner import RecapToolRunner
from agent_platform.observability.runtime_context import (
    current_budget,
    current_run_context,
)
from agent_platform.policy.tools import (
    ToolBudgetExceeded,
    ToolDisabled,
    ToolForbidden,
    ToolNotRegistered,
    ToolPolicy,
    ToolPolicyRegistry,
    ToolTimeout,
    build_default_registry,
)
from agent_platform.domain.run_context import RunContext


# ─── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def file_db(tmp_path):
    path = str(tmp_path / "tool.db")
    init_db(path)
    yield path


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides) -> Settings:
    """统一从环境变量构造 Settings（pydantic alias 不接 kwargs）。"""
    base: Dict[str, str] = {
        "RECAP_TOOLS_ENABLED": "true",
        "RECAP_TOOLS_WEB_SEARCH": "true",
        "RECAP_TOOLS_MARKET_DATA": "true",
        "RECAP_TOOLS_HISTORY": "true",
        "RECAP_TOOL_AUDIT_ENABLED": "true",
        "RECAP_PRINCIPAL_ROLE": "user",
        "RECAP_AGENT_MAX_TOOL_CALLS": "999",
        "RECAP_AGENT_MAX_TOKENS": "0",
        "RECAP_AGENT_MAX_WALL_MS": "0",
    }
    for k, v in overrides.items():
        base[k] = str(v)
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    return Settings()


def _patch_execute_tool(monkeypatch: pytest.MonkeyPatch, fn=None) -> None:
    if fn is None:
        fn = lambda name, arguments, db_path=":memory:": "ok"
    monkeypatch.setattr(runner_mod, "execute_tool", fn)


# ─── enabled_tool_names ──────────────────────────────────────────────────────


def test_enabled_tool_names_intersects_settings_and_policy(
    monkeypatch: pytest.MonkeyPatch, file_db: str
) -> None:
    s = _settings(monkeypatch, RECAP_DB_PATH=file_db, RECAP_TOOLS_HISTORY="false")
    runner = RecapToolRunner(s)
    names = runner.enabled_tool_names()
    assert "query_history" not in names, "Settings.tools_history=false 应该剔除"
    assert "web_search" in names and "query_market_data" in names


def test_enabled_tool_names_excludes_role_forbidden(
    monkeypatch: pytest.MonkeyPatch, file_db: str
) -> None:
    s = _settings(monkeypatch, RECAP_DB_PATH=file_db, RECAP_PRINCIPAL_ROLE="guest")
    reg = ToolPolicyRegistry()
    reg.register(ToolPolicy(name="web_search", required_role="admin"))
    reg.register(ToolPolicy(name="query_market_data"))
    runner = RecapToolRunner(s, policy_registry=reg)
    names = runner.enabled_tool_names()
    assert "web_search" not in names, "guest < admin 应被剔除"
    assert "query_market_data" in names


def test_total_switch_off_returns_empty(monkeypatch: pytest.MonkeyPatch, file_db: str) -> None:
    s = _settings(monkeypatch, RECAP_DB_PATH=file_db, RECAP_TOOLS_ENABLED="false")
    assert RecapToolRunner(s).enabled_tool_names() == set()


# ─── execute：拒绝路径 ───────────────────────────────────────────────────────


def test_execute_unknown_tool_raises_and_audits(
    monkeypatch: pytest.MonkeyPatch, file_db: str
) -> None:
    _patch_execute_tool(monkeypatch)
    s = _settings(monkeypatch, RECAP_DB_PATH=file_db)
    reg = ToolPolicyRegistry()  # 空注册表
    runner = RecapToolRunner(s, policy_registry=reg)

    with pytest.raises(ToolNotRegistered):
        runner.execute("ghost", {}, db_path=file_db)
    rows = load_recent_tool_invocations(file_db, tool_name="ghost")
    assert len(rows) == 1 and rows[0]["status"] == "denied"


def test_execute_disabled_policy_raises_and_audits(
    monkeypatch: pytest.MonkeyPatch, file_db: str
) -> None:
    _patch_execute_tool(monkeypatch)
    s = _settings(monkeypatch, RECAP_DB_PATH=file_db)
    reg = ToolPolicyRegistry()
    reg.register(ToolPolicy(name="web_search", enabled=False))
    runner = RecapToolRunner(s, policy_registry=reg)

    with pytest.raises(ToolDisabled):
        runner.execute("web_search", {"query": "x"}, db_path=file_db)
    rows = load_recent_tool_invocations(file_db, tool_name="web_search")
    assert len(rows) == 1 and rows[0]["status"] == "denied"
    assert "disabled" in (rows[0]["error"] or "")


def test_execute_role_forbidden_raises_and_audits(
    monkeypatch: pytest.MonkeyPatch, file_db: str
) -> None:
    _patch_execute_tool(monkeypatch)
    s = _settings(monkeypatch, RECAP_DB_PATH=file_db, RECAP_PRINCIPAL_ROLE="user")
    reg = ToolPolicyRegistry()
    reg.register(ToolPolicy(name="query_market_data", required_role="admin"))
    runner = RecapToolRunner(s, policy_registry=reg)

    with pytest.raises(ToolForbidden):
        runner.execute("query_market_data", {}, db_path=file_db)
    rows = load_recent_tool_invocations(file_db, tool_name="query_market_data")
    assert len(rows) == 1 and rows[0]["status"] == "denied"
    assert "principal_role" in (rows[0]["error"] or "")


def test_execute_per_tool_budget_exceeded(
    monkeypatch: pytest.MonkeyPatch, file_db: str
) -> None:
    _patch_execute_tool(monkeypatch)
    s = _settings(monkeypatch, RECAP_DB_PATH=file_db)
    reg = ToolPolicyRegistry()
    reg.register(ToolPolicy(name="web_search", max_calls_per_run=2))
    runner = RecapToolRunner(s, policy_registry=reg)

    runner.execute("web_search", {"query": "1"}, db_path=file_db)
    runner.execute("web_search", {"query": "2"}, db_path=file_db)
    with pytest.raises(ToolBudgetExceeded) as ei:
        runner.execute("web_search", {"query": "3"}, db_path=file_db)
    assert ei.value.tool == "web_search" and ei.value.limit == 2

    rows = load_recent_tool_invocations(file_db, tool_name="web_search")
    statuses = sorted(r["status"] for r in rows)
    assert statuses == ["denied", "ok", "ok"]


def test_execute_timeout_raises_and_audits(
    monkeypatch: pytest.MonkeyPatch, file_db: str
) -> None:
    def slow(name, arguments, db_path=":memory:"):
        time.sleep(0.5)
        return "late"

    _patch_execute_tool(monkeypatch, slow)
    s = _settings(monkeypatch, RECAP_DB_PATH=file_db)
    reg = ToolPolicyRegistry()
    reg.register(ToolPolicy(name="query_history", timeout_s=0.05))
    runner = RecapToolRunner(s, policy_registry=reg)

    with pytest.raises(ToolTimeout):
        runner.execute("query_history", {"mode": "daily"}, db_path=file_db)
    rows = load_recent_tool_invocations(file_db, tool_name="query_history")
    assert len(rows) == 1 and rows[0]["status"] == "timeout"


# ─── execute：成功路径 + 审计 ───────────────────────────────────────────────


def test_execute_ok_writes_audit_with_request_id(
    monkeypatch: pytest.MonkeyPatch, file_db: str
) -> None:
    _patch_execute_tool(monkeypatch)
    s = _settings(monkeypatch, RECAP_DB_PATH=file_db)
    runner = RecapToolRunner(s)

    ctx = RunContext.new()
    token = current_run_context.set(ctx)
    try:
        runner.execute("query_market_data", {"data_type": "index"}, db_path=file_db)
    finally:
        current_run_context.reset(token)

    rows = load_recent_tool_invocations(file_db, request_id=ctx.request_id)
    assert len(rows) == 1 and rows[0]["status"] == "ok"
    assert rows[0]["read_only"] == 1
    assert rows[0]["latency_ms"] is not None and rows[0]["latency_ms"] >= 0


def test_audit_disabled_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, file_db: str
) -> None:
    _patch_execute_tool(monkeypatch)
    s = _settings(monkeypatch, RECAP_DB_PATH=file_db, RECAP_TOOL_AUDIT_ENABLED="false")
    runner = RecapToolRunner(s)
    runner.execute("query_market_data", {}, db_path=file_db)
    assert load_recent_tool_invocations(file_db) == []


# ─── 与全局 AgentBudget 协同 ────────────────────────────────────────────────


def test_global_agent_budget_still_enforced(
    monkeypatch: pytest.MonkeyPatch, file_db: str
) -> None:
    """per-tool 还没满，但全局 AgentBudget 已经满 → 抛 LlmBudgetExceeded。"""
    _patch_execute_tool(monkeypatch)
    s = _settings(
        monkeypatch,
        RECAP_DB_PATH=file_db,
        RECAP_AGENT_MAX_TOOL_CALLS="2",
    )
    reg = ToolPolicyRegistry()
    reg.register(ToolPolicy(name="query_market_data", max_calls_per_run=10))
    runner = RecapToolRunner(s, policy_registry=reg)

    budget = AgentBudget.from_settings(s)
    prev = current_budget.get()
    current_budget.set(budget)
    try:
        runner.execute("query_market_data", {}, db_path=file_db)
        runner.execute("query_market_data", {}, db_path=file_db)
        with pytest.raises(LlmBudgetExceeded) as ei:
            runner.execute("query_market_data", {}, db_path=file_db)
        assert ei.value.kind == "tool_calls"
    finally:
        current_budget.set(prev)

    # 第三次「全局拒绝」也要落审计（denied）。
    rows = load_recent_tool_invocations(file_db, tool_name="query_market_data")
    statuses = sorted(r["status"] for r in rows)
    assert statuses == ["denied", "ok", "ok"]


# ─── 默认注册表 ─────────────────────────────────────────────────────────────


def test_default_registry_covers_all_built_in_tools() -> None:
    reg = build_default_registry()
    assert set(reg.names()) == {"web_search", "query_market_data", "query_history"}
    assert reg.require("web_search").read_only is True
