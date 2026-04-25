"""Critic 重入回归：业务异常 → 注入结构化反馈 → 再调一次 LLM。

只覆盖 ``_phase_act`` 行为；端到端流式/同步路径已被既有 stream 测试覆盖。
"""
from __future__ import annotations

import time
from contextlib import nullcontext
from typing import Tuple

import pytest

from agent_platform.application.orchestration import pipeline as pipeline_mod
from agent_platform.application.orchestration.context import RecapAgentRunState
from agent_platform.config.settings import Settings
from agent_platform.domain.models import (
    Features,
    GenerateRequest,
    LlmSchemaError,
    LlmTokens,
    LlmTransportError,
    MarketSnapshot,
    Recap,
    RecapDaily,
    RecapDailySection,
)
from agent_platform.domain.run_context import RunContext
from agent_platform.infrastructure.llm import backends as backends_mod


def _settings(monkeypatch: pytest.MonkeyPatch, *, critic_max_retries: int = 1) -> Settings:
    monkeypatch.setenv("RECAP_AGENT_CRITIC_MAX_RETRIES", str(critic_max_retries))
    monkeypatch.setenv("RECAP_AGENT_MAX_TOOL_CALLS", "16")
    monkeypatch.setenv("RECAP_AGENT_MAX_TOKENS", "0")
    monkeypatch.setenv("RECAP_AGENT_MAX_WALL_MS", "0")
    return Settings(_env_file=None)


def _state_for_act(monkeypatch: pytest.MonkeyPatch, **settings_overrides) -> RecapAgentRunState:
    s = _settings(monkeypatch, **settings_overrides)
    state = RecapAgentRunState(
        request=GenerateRequest(mode="daily", provider="mock", force_llm=True),
        settings=s,
        run_ctx=RunContext.new(),
        t0=time.time(),
    )
    state.snapshot = MarketSnapshot(
        asof="2024-01-02T00:00:00+00:00", provider="mock", date="2024-01-02"
    )
    state.features = Features()
    state.messages = [{"role": "user", "content": "请输出 daily JSON"}]
    return state


class _NoopTracer:
    def start_as_current_span(self, *_a, **_kw):
        return nullcontext()


def _ok_recap() -> RecapDaily:
    section = RecapDailySection(
        title="核心观点",
        core_conclusion="指数小幅收涨",
        bullets=["【复盘基准日：2024年01月02日 星期二】", "成交量维持均量线"],
    )
    return RecapDaily(
        mode="daily",
        date="2024-01-02",
        sections=[section, section, section],
        risks=["关注外围扰动"],
        closing_summary="震荡格局延续",
    )


class _SequencedProvider:
    """按调用次序返回不同结果：先抛 schema 错，再返回合法 recap。"""

    name = "seq-fake"

    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls = 0

    def call(self, *args, **kwargs) -> Tuple[Recap, LlmTokens]:
        self.calls += 1
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, LlmTokens(input_tokens=10, output_tokens=20, total_tokens=30)


def test_critic_retry_recovers_after_schema_error(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state_for_act(monkeypatch, critic_max_retries=1)
    initial_msgs = len(state.messages)

    provider = _SequencedProvider([LlmSchemaError("missing risks"), _ok_recap()])
    monkeypatch.setattr(backends_mod, "resolve_provider", lambda _name: provider)

    pipeline_mod._phase_act(state, _NoopTracer())

    assert provider.calls == 2, "应触发一次 critic 重入"
    assert state.recap is not None
    assert state.llm_error is None, "成功恢复后应清空错误"
    assert state.critic_retries_used == 1
    # 反馈消息已注入
    assert len(state.messages) == initial_msgs + 1
    last = state.messages[-1]
    assert last["role"] == "user"
    assert "missing risks" in last["content"]


def test_critic_retry_disabled_when_setting_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state_for_act(monkeypatch, critic_max_retries=0)
    initial_msgs = len(state.messages)

    provider = _SequencedProvider([LlmSchemaError("bad shape"), _ok_recap()])
    monkeypatch.setattr(backends_mod, "resolve_provider", lambda _name: provider)

    pipeline_mod._phase_act(state, _NoopTracer())

    assert provider.calls == 1, "重入次数=0 时只调一次"
    assert state.recap is None
    assert state.llm_error and "business_error" in state.llm_error
    assert state.critic_retries_used == 0
    assert len(state.messages) == initial_msgs, "未注入反馈"


def test_critic_retry_does_not_repeat_for_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """传输错已被 tenacity 重试 3 次；act 不再 critic 重入。"""
    state = _state_for_act(monkeypatch, critic_max_retries=2)

    provider = _SequencedProvider(
        [
            LlmTransportError("network down"),
            LlmTransportError("network down"),
            LlmTransportError("network down"),
        ]
    )
    monkeypatch.setattr(backends_mod, "resolve_provider", lambda _name: provider)
    # 关掉 tenacity sleep 以加速
    from agent_platform.infrastructure.llm.backends import call_llm
    call_llm.retry.sleep = lambda _s: None  # type: ignore[attr-defined]

    pipeline_mod._phase_act(state, _NoopTracer())

    assert provider.calls == 3, "tenacity 重试 3 次后由 act 接住"
    assert state.recap is None
    assert state.llm_error and "network down" in state.llm_error
    assert state.critic_retries_used == 0, "传输错不计入 critic 重入"


def test_critic_retry_exhausts_when_business_keeps_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state_for_act(monkeypatch, critic_max_retries=2)
    initial_msgs = len(state.messages)

    provider = _SequencedProvider(
        [
            LlmSchemaError("v1 bad"),
            LlmSchemaError("v2 bad"),
            LlmSchemaError("v3 bad"),
        ]
    )
    monkeypatch.setattr(backends_mod, "resolve_provider", lambda _name: provider)

    pipeline_mod._phase_act(state, _NoopTracer())

    assert provider.calls == 3, "1 次原始 + 2 次 critic 重入 = 3"
    assert state.recap is None
    assert state.llm_error and "v3 bad" in state.llm_error
    assert state.critic_retries_used == 2
    # 共注入 2 条反馈 user 消息
    assert len(state.messages) == initial_msgs + 2
    assert all(m["role"] == "user" for m in state.messages[initial_msgs:])
