"""LlmProvider 抽象 + 注册表。

新增后端只需：
1. 实现 ``LlmProvider`` 的 ``call()``；
2. 在 ``_BUILTIN`` 中登记，或运行时调用 ``register_provider()``。

``resolve_provider(backend_name)`` 会返回对应 provider；未知名字抛 ``LlmError``。
"""
from __future__ import annotations

from typing import Dict

from stock_recap.domain.models import LlmError

from stock_recap.infrastructure.llm.providers.base import LlmProvider
from stock_recap.infrastructure.llm.providers.openai_provider import OpenAiProvider
from stock_recap.infrastructure.llm.providers.ollama_provider import OllamaProvider
from stock_recap.infrastructure.llm.providers.cursor_cli_provider import CursorCliProvider
from stock_recap.infrastructure.llm.providers.gemini_cli_provider import GeminiCliProvider


_BUILTIN: Dict[str, LlmProvider] = {
    "openai": OpenAiProvider(),
    "ollama": OllamaProvider(),
    "cursor-cli": CursorCliProvider(),
    "gemini-cli": GeminiCliProvider(),
}

_REGISTRY: Dict[str, LlmProvider] = dict(_BUILTIN)


def register_provider(name: str, provider: LlmProvider) -> None:
    """注册/覆盖 provider（便于测试与第三方扩展）。"""
    _REGISTRY[name] = provider


def resolve_provider(name: str) -> LlmProvider:
    try:
        return _REGISTRY[name]
    except KeyError as e:
        raise LlmError(f"未知 backend: {name}") from e


def available_backends() -> list[str]:
    return sorted(_REGISTRY.keys())


__all__ = [
    "CursorCliProvider",
    "GeminiCliProvider",
    "LlmProvider",
    "OllamaProvider",
    "OpenAiProvider",
    "available_backends",
    "register_provider",
    "resolve_provider",
]
