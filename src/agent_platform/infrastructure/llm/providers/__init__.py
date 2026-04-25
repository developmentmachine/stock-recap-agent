"""LlmProvider 抽象 + 注册表。

新增后端的两层注册：
1. ``domain.registries.LlmBackendRegistry``：登记元描述（display name / 别名 /
   是否需要 API key / 是否支持 function calling）。元描述与「具体执行体」解耦
   是为了让 CLI / 健康检查 / 自动文档不需要 import infra 层就能列出后端。
2. 本模块 ``ProviderRegistry``：登记 ``LlmProvider`` 实例（真正可调用的执行体）。

调用方一律通过 ``resolve_provider(name)`` 拿到 provider；它会：
- 用 backend 注册表把别名归一（``cursor-agent`` → ``cursor-cli``）；
- 找到对应 ``LlmProvider`` 实例；
- 找不到时抛 ``LlmError``，错误信息列出当前可用 backend 帮助排错。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agent_platform.domain.models import LlmError
from agent_platform.domain.registries import LlmBackendRegistry, default_backend_registry

from agent_platform.infrastructure.llm.providers.base import LlmProvider
from agent_platform.infrastructure.llm.providers.openai_provider import OpenAiProvider
from agent_platform.infrastructure.llm.providers.ollama_provider import OllamaProvider
from agent_platform.infrastructure.llm.providers.cursor_cli_provider import CursorCliProvider
from agent_platform.infrastructure.llm.providers.gemini_cli_provider import GeminiCliProvider


# ─── ProviderRegistry：执行体层 ────────────────────────────────────────────


@dataclass
class ProviderRegistry:
    """``LlmProvider`` 实例的注册表，与 ``LlmBackendRegistry`` 元描述解耦。

    不直接复用 BackendRegistry 是因为：
    - BackendRegistry 是「描述」，可以在 domain 层无依赖地列出；
    - ProviderRegistry 是「实现」，必然依赖 infra 层（subprocess / openai SDK / httpx）；
    - 解耦后单测 backend 注册表不需要 mock provider 实例。
    """

    backend_registry: LlmBackendRegistry = field(default_factory=default_backend_registry)
    _providers: Dict[str, LlmProvider] = field(default_factory=dict)

    def register(self, name: str, provider: LlmProvider) -> None:
        """``name`` 必须先在 ``backend_registry`` 中登记，否则视为未知 backend。"""
        canonical = self.backend_registry.resolve_alias(name) or name
        if self.backend_registry.get(canonical) is None:
            raise LlmError(
                f"无法注册 provider：backend '{name}' 未在 LlmBackendRegistry 登记。"
                f" 请先 register(LlmBackendSpec(name='{name}', ...))。"
            )
        self._providers[canonical] = provider

    def resolve(self, name: str) -> LlmProvider:
        canonical = self.backend_registry.resolve_alias(name) or name
        provider = self._providers.get(canonical)
        if provider is None:
            available = ", ".join(self._providers.keys()) or "(empty)"
            raise LlmError(f"未知 backend: {name} (可用: {available})")
        return provider

    def names(self) -> List[str]:
        """返回当前已注册「执行体」的 canonical 名列表。"""
        return sorted(self._providers.keys())


def _build_default_provider_registry() -> ProviderRegistry:
    reg = ProviderRegistry(backend_registry=default_backend_registry())
    reg.register("openai", OpenAiProvider())
    reg.register("ollama", OllamaProvider())
    reg.register("cursor-cli", CursorCliProvider())
    reg.register("gemini-cli", GeminiCliProvider())
    return reg


_DEFAULT_REGISTRY: Optional[ProviderRegistry] = None


def default_provider_registry() -> ProviderRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = _build_default_provider_registry()
    return _DEFAULT_REGISTRY


# ─── 模块级兼容 API（保留给现存调用方） ─────────────────────────────────────


def register_provider(name: str, provider: LlmProvider) -> None:
    """注册/覆盖 provider（便于测试与第三方扩展）。

    若 ``name`` 是新 backend，调用方应该先在 ``default_backend_registry()`` 上
    ``register(LlmBackendSpec(...))``。
    """
    default_provider_registry().register(name, provider)


def resolve_provider(name: str) -> LlmProvider:
    return default_provider_registry().resolve(name)


def available_backends() -> List[str]:
    return default_provider_registry().names()


__all__ = [
    "CursorCliProvider",
    "GeminiCliProvider",
    "LlmProvider",
    "OllamaProvider",
    "OpenAiProvider",
    "ProviderRegistry",
    "available_backends",
    "default_provider_registry",
    "register_provider",
    "resolve_provider",
]
