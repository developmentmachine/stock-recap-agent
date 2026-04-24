"""``AgentBudget`` 与端到端预算执行的回归测试。

覆盖：
- ``AgentBudget.from_settings`` 读取 ``Settings`` 三个维度；
- ``record_tool_call`` / ``record_tokens`` 超额抛 ``LlmBudgetExceeded``；
- ``max_*=0`` 的「不限制」语义；
- ``RecapToolRunner.execute`` 在 ContextVar 中的预算下扣减；
- pipeline 的 ``_check_budget_between_phases`` 触发后 ``act`` 被跳过；
- ``call_llm`` 抛 ``LlmBudgetExceeded`` 时 ``act`` 节点优雅落库。
"""
from __future__ import annotations

import time
from typing import Dict, List, Tuple

import pytest

from stock_recap.application.orchestration.budget import AgentBudget
from stock_recap.application.orchestration.context import RecapAgentRunState
from stock_recap.application.orchestration.pipeline import _check_budget_between_phases
from stock_recap.config.settings import Settings
from stock_recap.domain.models import (
    GenerateRequest,
    LlmBudgetExceeded,
    LlmTokens,
    Recap,
)
from stock_recap.domain.run_context import RunContext
from stock_recap.observability.runtime_context import current_budget


_ENV_KEYS = {
    "agent_max_tool_calls": "RECAP_AGENT_MAX_TOOL_CALLS",
    "agent_max_tokens": "RECAP_AGENT_MAX_TOKENS",
    "agent_max_wall_ms": "RECAP_AGENT_MAX_WALL_MS",
    "tools_enabled": "RECAP_TOOLS_ENABLED",
}


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides) -> Settings:
    base = dict(
        agent_max_tool_calls=4,
        agent_max_tokens=1_000,
        agent_max_wall_ms=60_000,
        tools_enabled=True,
    )
    base.update(overrides)
    for k, v in base.items():
        monkeypatch.setenv(_ENV_KEYS[k], str(v))
    return Settings(_env_file=None)


def test_from_settings_reads_three_dims(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch)
    b = AgentBudget.from_settings(s)
    assert b.max_tool_calls == 4
    assert b.max_tokens == 1_000
    assert b.max_wall_ms == 60_000
    assert b.tool_calls_used == 0
    assert b.tokens_used == 0


def test_record_tool_call_exceed() -> None:
    b = AgentBudget(max_tool_calls=2, max_tokens=0, max_wall_ms=0)
    b.record_tool_call()
    b.record_tool_call()
    with pytest.raises(LlmBudgetExceeded) as ei:
        b.record_tool_call()
    assert ei.value.kind == "tool_calls"
    assert ei.value.limit == 2
    assert ei.value.used == 3


def test_record_tokens_exceed() -> None:
    b = AgentBudget(max_tool_calls=0, max_tokens=100, max_wall_ms=0)
    b.record_tokens(60)
    with pytest.raises(LlmBudgetExceeded) as ei:
        b.record_tokens(50)
    assert ei.value.kind == "tokens"
    assert ei.value.used == 110


def test_zero_means_unlimited() -> None:
    b = AgentBudget(max_tool_calls=0, max_tokens=0, max_wall_ms=0)
    for _ in range(100):
        b.record_tool_call()
    b.record_tokens(10**6)
    b.check()  # 不抛


def test_wall_ms_exceed_after_sleep_simulated() -> None:
    b = AgentBudget(max_tool_calls=0, max_tokens=0, max_wall_ms=10)
    b.started_at_monotonic = time.monotonic() - 5  # 已过 5s ≫ 10ms
    with pytest.raises(LlmBudgetExceeded) as ei:
        b.check()
    assert ei.value.kind == "wall_ms"


def test_tool_runner_increments_via_contextvar(monkeypatch: pytest.MonkeyPatch) -> None:
    from stock_recap.infrastructure.tools import runner as runner_mod
    from stock_recap.infrastructure.tools.runner import RecapToolRunner

    monkeypatch.setattr(
        runner_mod, "execute_tool", lambda name, arguments, db_path=":memory:": "ok"
    )

    s = _settings(monkeypatch, agent_max_tool_calls=2)
    runner = RecapToolRunner(s)
    budget = AgentBudget.from_settings(s)
    prev = current_budget.get()
    current_budget.set(budget)
    try:
        runner.execute("query_market_data", {"date": "2024-01-02"}, db_path=":memory:")
        runner.execute("query_market_data", {"date": "2024-01-02"}, db_path=":memory:")
        with pytest.raises(LlmBudgetExceeded):
            runner.execute("query_market_data", {"date": "2024-01-02"}, db_path=":memory:")
    finally:
        current_budget.set(prev)
    assert budget.tool_calls_used == 3


def test_check_budget_between_phases_skips_act_and_critique(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _settings(monkeypatch, agent_max_wall_ms=10)
    state = RecapAgentRunState(
        request=GenerateRequest(mode="daily", provider="mock", force_llm=False),
        settings=s,
        run_ctx=RunContext.new(),
        t0=time.time(),
    )
    assert state.budget is not None
    state.budget.started_at_monotonic = time.monotonic() - 5  # 已超 wall_ms

    assert _check_budget_between_phases(state, "act") is False
    assert state.llm_error and "budget_exceeded" in state.llm_error
    assert state.budget_error and state.budget_error.startswith("wall_ms:")


def test_act_phase_handles_budget_exceeded_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider 抛 ``LlmBudgetExceeded`` 时 act 节点不应让整个 pipeline 崩。"""
    from stock_recap.application.orchestration import pipeline as pipeline_mod
    from stock_recap.infrastructure.llm import backends as backends_mod

    s = _settings(monkeypatch)
    state = RecapAgentRunState(
        request=GenerateRequest(mode="daily", provider="mock", force_llm=True),
        settings=s,
        run_ctx=RunContext.new(),
        t0=time.time(),
    )

    # 给 act 喂上必要的前置状态
    from stock_recap.domain.models import Features, MarketSnapshot

    state.snapshot = MarketSnapshot(
        asof="2024-01-02T00:00:00+00:00", provider="mock", date="2024-01-02"
    )
    state.features = Features()
    state.messages = [{"role": "user", "content": "hi"}]

    class _BudgetExceededProvider:
        name = "budget-exceeded-fake"

        def call(self, *args, **kwargs) -> Tuple[Recap, LlmTokens]:
            raise LlmBudgetExceeded("tool_calls", limit=4, used=5)

    monkeypatch.setattr(
        backends_mod, "resolve_provider", lambda _name: _BudgetExceededProvider()
    )

    class _NoopTracer:
        def start_as_current_span(self, *_a, **_kw):
            from contextlib import nullcontext

            return nullcontext()

    pipeline_mod._phase_act(state, _NoopTracer())

    assert state.recap is None
    assert state.llm_error and "budget_exceeded(tool_calls" in state.llm_error
    assert state.budget_error == "tool_calls:5/4"
