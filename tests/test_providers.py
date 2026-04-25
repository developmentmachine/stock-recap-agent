"""LlmProvider 注册表 + call_llm 路由 behavior。"""
from typing import Dict, List, Tuple

import pytest

from agent_platform.config.settings import Settings
from agent_platform.domain.models import LlmError, LlmTokens, RecapDaily, RecapDailySection
from agent_platform.domain.registries import (
    LlmBackendSpec,
    default_backend_registry,
    reset_default_backend_registry,
)
from agent_platform.infrastructure.llm.backends import call_llm
from agent_platform.infrastructure.llm.providers import (
    available_backends,
    default_provider_registry,
    register_provider,
    resolve_provider,
)
from agent_platform.infrastructure.llm.providers.base import LlmProvider


def _make_stub_recap() -> RecapDaily:
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


class _FakeProvider:
    name = "fake"

    def __init__(self):
        self.calls: List[Tuple[str, str, str]] = []

    def call(
        self,
        settings: Settings,
        mode,
        messages: List[Dict[str, str]],
        *,
        model: str,
        db_path: str,
        date: str,
    ) -> Tuple[RecapDaily, LlmTokens]:
        self.calls.append((mode, model, date))
        return (
            _make_stub_recap(),
            LlmTokens(input_tokens=1, output_tokens=1, total_tokens=2),
        )


def test_builtin_backends_available():
    assert set(available_backends()) >= {"openai", "ollama", "cursor-cli", "gemini-cli"}


def test_resolve_provider_protocol_compatible():
    provider = resolve_provider("openai")
    assert isinstance(provider, LlmProvider)


def test_resolve_provider_unknown_raises():
    with pytest.raises(LlmError):
        resolve_provider("does-not-exist")


def test_register_custom_provider_routes_through_call_llm(monkeypatch):
    """新增 backend 的两步：先在 LlmBackendRegistry 登记 spec，再注册 provider。"""
    # Step 1: 登记 backend 元描述（否则 ProviderRegistry.register 会拒绝）。
    default_backend_registry().register(
        LlmBackendSpec(
            name="fake",
            display_name="测试用 Fake",
            requires_api_key_env=None,
            supports_function_calling=False,
            aliases=(),
        )
    )
    fake = _FakeProvider()
    register_provider("fake", fake)
    try:
        import agent_platform.infrastructure.llm.backends as be

        monkeypatch.setattr(be, "llm_backend_effective", lambda *a, **kw: "fake")
        monkeypatch.setattr(be, "model_effective", lambda s, spec: "test-model")

        settings = Settings(model="test-model", tools_enabled=False)
        recap, tokens = call_llm(settings, "daily", [{"role": "user", "content": "hi"}])
        assert recap.mode == "daily"
        assert tokens.total_tokens == 2
        assert len(fake.calls) == 1
        assert fake.calls[0][1] == "test-model"
    finally:
        # 重置两侧默认注册表 → 下次取用都会重建为内置。
        reset_default_backend_registry()
        from agent_platform.infrastructure.llm import providers as providers_mod

        providers_mod._DEFAULT_REGISTRY = None
