"""异常分层 + ``call_llm`` tenacity 作用域回归。

验证关键边界：
- ``LlmTransportError`` 继承 ``LlmError``，会被 tenacity 重试 N 次后抛出；
- ``LlmBusinessError`` / ``LlmSchemaError`` / ``LlmParseError`` 继承 ``LlmError``，
  但 **不** 在 ``call_llm`` 层重试（交由 Critic 处理）；
- ``LlmBudgetExceeded`` 不会被重试。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from stock_recap.config.settings import Settings
from stock_recap.domain.models import (
    LlmBudgetExceeded,
    LlmBusinessError,
    LlmError,
    LlmParseError,
    LlmSchemaError,
    LlmTokens,
    LlmTransportError,
    Recap,
)
from stock_recap.infrastructure.llm import backends as backends_mod
from stock_recap.infrastructure.llm.backends import call_llm
from stock_recap.infrastructure.llm.parse import parse_and_validate


class _CountingProvider:
    name = "counting-fake"

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    def call(
        self,
        settings: Settings,
        mode: str,
        messages: List[Dict[str, str]],
        *,
        model: str,
        db_path: str,
        date: str,
    ) -> Tuple[Recap, LlmTokens]:
        self.calls += 1
        raise self.exc


@pytest.fixture
def no_retry_sleep():
    """禁用 tenacity 在测试里的 sleep，避免拖时长。"""
    original = call_llm.retry.sleep  # type: ignore[attr-defined]
    call_llm.retry.sleep = lambda _s: None  # type: ignore[attr-defined]
    try:
        yield
    finally:
        call_llm.retry.sleep = original  # type: ignore[attr-defined]


def _patch_resolve_to(monkeypatch: pytest.MonkeyPatch, provider: _CountingProvider) -> None:
    """让 ``call_llm`` 内部 ``resolve_provider`` 始终返回我们的假 provider。"""
    monkeypatch.setattr(backends_mod, "resolve_provider", lambda _name: provider)


def test_hierarchy_relationships() -> None:
    assert issubclass(LlmTransportError, LlmError)
    assert issubclass(LlmBusinessError, LlmError)
    assert issubclass(LlmSchemaError, LlmBusinessError)
    assert issubclass(LlmParseError, LlmBusinessError)
    assert issubclass(LlmBudgetExceeded, LlmError)
    assert not issubclass(LlmBusinessError, LlmTransportError)


def test_parse_failure_raises_parse_error() -> None:
    with pytest.raises(LlmParseError):
        parse_and_validate("not a json at all", mode="daily")


def test_schema_failure_raises_schema_error() -> None:
    with pytest.raises(LlmSchemaError):
        parse_and_validate('{"mode": "daily"}', mode="daily")


def test_call_llm_retries_transport_then_raises(
    monkeypatch: pytest.MonkeyPatch, no_retry_sleep
) -> None:
    provider = _CountingProvider(LlmTransportError("network down"))
    _patch_resolve_to(monkeypatch, provider)

    with pytest.raises(LlmTransportError):
        call_llm(Settings(), "daily", [{"role": "user", "content": "x"}])

    assert provider.calls == 3, "tenacity should retry transport errors 3 times"


def test_call_llm_does_not_retry_business_error(
    monkeypatch: pytest.MonkeyPatch, no_retry_sleep
) -> None:
    provider = _CountingProvider(LlmSchemaError("bad shape"))
    _patch_resolve_to(monkeypatch, provider)

    with pytest.raises(LlmSchemaError):
        call_llm(Settings(), "daily", [{"role": "user", "content": "x"}])

    assert provider.calls == 1, "business errors must propagate immediately for Critic"


def test_call_llm_does_not_retry_budget_exceeded(
    monkeypatch: pytest.MonkeyPatch, no_retry_sleep
) -> None:
    provider = _CountingProvider(LlmBudgetExceeded("tool_calls", limit=8, used=9))
    _patch_resolve_to(monkeypatch, provider)

    with pytest.raises(LlmBudgetExceeded) as ei:
        call_llm(Settings(), "daily", [{"role": "user", "content": "x"}])

    assert provider.calls == 1
    assert ei.value.kind == "tool_calls"
    assert ei.value.limit == 8
    assert ei.value.used == 9
