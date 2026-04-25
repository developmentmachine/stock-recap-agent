"""ModeRegistry / LlmBackendRegistry：注册、解析、别名与默认值。"""
from __future__ import annotations

from typing import Type

import pytest

from agent_platform.domain.models import (
    Recap,
    RecapDaily,
    RecapStrategy,
)
from agent_platform.domain.registries import (
    LlmBackendRegistry,
    LlmBackendSpec,
    ModeRegistry,
    ModeSpec,
    build_default_backend_registry,
    build_default_mode_registry,
    default_backend_registry,
    default_mode_registry,
    reset_default_backend_registry,
    reset_default_mode_registry,
)
from agent_platform.infrastructure.llm.parse import parse_and_validate
from agent_platform.infrastructure.llm.resolve import _model_prefix_to_backend


# ─── ModeRegistry ───────────────────────────────────────────────────────────


def test_default_mode_registry_has_daily_and_strategy() -> None:
    reg = build_default_mode_registry()
    assert reg.names() == ["daily", "strategy"]
    assert reg.require("daily").recap_class is RecapDaily
    assert reg.require("strategy").recap_class is RecapStrategy
    assert reg.require("daily").triggers_backtest is True


def test_mode_registry_register_unknown_then_use() -> None:
    reg = ModeRegistry()
    reg.register(
        ModeSpec(
            name="weekly",
            recap_class=RecapDaily,  # 复用 schema 仅作演示
            display_name="周报",
            triggers_backtest=False,
        )
    )
    assert reg.require("weekly").display_name == "周报"
    assert reg.get("not-there") is None
    with pytest.raises(KeyError):
        reg.require("not-there")


def test_parse_and_validate_uses_mode_registry_for_unknown_mode() -> None:
    """未知 mode 应落入 LlmSchemaError，由上层 critic / pipeline 决定如何处理。"""
    from agent_platform.domain.models import LlmSchemaError

    payload = '{"mode": "ghost", "date": "2024-01-02"}'
    with pytest.raises(LlmSchemaError):
        parse_and_validate(payload, "ghost")  # type: ignore[arg-type]


def test_parse_and_validate_with_custom_mode_registry() -> None:
    """传入注入的 ModeRegistry 让 parse_and_validate 不走全局默认。"""
    custom = ModeRegistry()
    custom.register(ModeSpec(name="daily", recap_class=RecapDaily))

    payload = (
        '{"mode": "daily", "date": "2024-01-02", '
        '"sections": ['
        '{"title": "A", "core_conclusion": "c", "bullets": ["b1", "b2"]},'
        '{"title": "B", "core_conclusion": "c", "bullets": ["b1", "b2"]},'
        '{"title": "C", "core_conclusion": "c", "bullets": ["b1", "b2"]}'
        '], "risks": []}'
    )
    out = parse_and_validate(payload, "daily", mode_registry=custom)
    assert isinstance(out, RecapDaily)


# ─── LlmBackendRegistry ─────────────────────────────────────────────────────


def test_default_backend_registry_has_all_canonical_names() -> None:
    reg = build_default_backend_registry()
    assert set(reg.names()) == {"openai", "ollama", "cursor-cli", "gemini-cli"}


def test_alias_resolution_for_cursor_family() -> None:
    reg = build_default_backend_registry()
    for alias in ("cursor", "cursor-cli", "cursor-agent", "agent", "CURSOR-CLI"):
        assert reg.resolve_alias(alias) == "cursor-cli", alias


def test_alias_resolution_for_unknown_returns_none() -> None:
    reg = build_default_backend_registry()
    assert reg.resolve_alias("rocket-llm") is None
    assert reg.resolve_alias("") is None


def test_resolve_module_function_uses_default_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_model_prefix_to_backend`` 必须复用注册表，而不是私有 hardcoded dict。"""
    # 注入一个新别名到默认注册表 → 模块级函数立刻能识别。
    reset_default_backend_registry()
    default_backend_registry().register(
        LlmBackendSpec(name="openai", aliases=("oai", "azure-openai"))
    )
    try:
        assert _model_prefix_to_backend("oai") == "openai"
        assert _model_prefix_to_backend("AZURE-OPENAI") == "openai"
    finally:
        reset_default_backend_registry()


def test_register_custom_backend_with_aliases() -> None:
    reg = LlmBackendRegistry()
    reg.register(
        LlmBackendSpec(
            name="my-backend",
            display_name="自定义后端",
            requires_api_key_env="MY_KEY",
            supports_function_calling=True,
            aliases=("mine", "MyBackend"),
        )
    )
    assert reg.resolve_alias("mine") == "my-backend"
    spec = reg.require("my-backend")
    assert spec.requires_api_key_env == "MY_KEY"
    assert spec.supports_function_calling is True


def test_default_backend_registry_singleton() -> None:
    """``default_backend_registry()`` 多次调用返回同一实例（修改可见）。"""
    reset_default_backend_registry()
    a = default_backend_registry()
    b = default_backend_registry()
    assert a is b
    a.register(LlmBackendSpec(name="probe-one", aliases=()))
    assert b.get("probe-one") is not None
    reset_default_backend_registry()
    assert default_backend_registry().get("probe-one") is None
