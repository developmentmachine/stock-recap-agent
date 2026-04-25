"""Mode 与 LlmBackend 的运行时注册表。

为什么要把 ``Literal["daily","strategy"]`` 这样的字面量改成注册表：
- **可扩展性**：新增模式（如 ``intraday`` 中场速记 / ``weekly`` 周报）时，旧版要
  改 ``parse_and_validate`` / ``renderers`` / ``manifest.json`` / Pydantic Literal …
  N 处 if-else；改成注册表后只需 ``register_mode(...)`` 一行；
- **解耦**：注册表只描述「这个 mode 用哪个 Recap schema、哪段 skill_id」，不再
  让基础设施层硬编码具体业务名；
- **运行时可发现**：``available_modes()`` 直接返回当前生效的列表，CLI / 文档生成 /
  健康检查都能用。

兼容性：
- 仍然保留 ``Mode``/``LlmBackend`` 类型别名（来自 ``domain.models``）；
- 老调用 ``provider.call(... mode="daily" ...)`` 不需要改，注册表只是把
  ``daily`` → schema 的映射集中化。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Type

from pydantic import BaseModel

from agent_platform.domain.models import (
    LlmBackend,
    Mode,
    RecapDaily,
    RecapStrategy,
)


# ─── ModeSpec / ModeRegistry ────────────────────────────────────────────────


@dataclass(frozen=True)
class ModeSpec:
    """单个 Mode 的元描述，集中所有「以 mode 为枢纽」的派生选择。

    字段：
    - ``name``                  Mode 字面量值，例如 ``"daily"``
    - ``recap_class``           Pydantic schema 类，用于 ``parse_and_validate``
    - ``display_name``          人类可读名（CLI / 文档 / metric 标签）
    - ``default_skill_id``      对应的内置 skill_id；可被 ``manifest.json`` /
                                ``Settings.skill_id_override`` 覆盖
    - ``triggers_backtest``     该 mode 完成后是否需要触发次日回测
    """

    name: str
    recap_class: Type[BaseModel]
    display_name: str = ""
    default_skill_id: Optional[str] = None
    triggers_backtest: bool = False


@dataclass
class ModeRegistry:
    _specs: Dict[str, ModeSpec] = field(default_factory=dict)

    def register(self, spec: ModeSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> Optional[ModeSpec]:
        return self._specs.get(name)

    def require(self, name: str) -> ModeSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise KeyError(f"mode '{name}' is not registered")
        return spec

    def names(self) -> List[str]:
        return sorted(self._specs.keys())


def build_default_mode_registry() -> ModeRegistry:
    """与 ``Mode = Literal["daily","strategy"]`` 对齐的内置注册表。"""
    reg = ModeRegistry()
    reg.register(
        ModeSpec(
            name="daily",
            recap_class=RecapDaily,
            display_name="日终复盘",
            default_skill_id="recap_daily",
            triggers_backtest=True,
        )
    )
    reg.register(
        ModeSpec(
            name="strategy",
            recap_class=RecapStrategy,
            display_name="次日策略",
            default_skill_id="recap_strategy",
            triggers_backtest=False,
        )
    )
    return reg


_DEFAULT_MODE_REGISTRY: Optional[ModeRegistry] = None


def default_mode_registry() -> ModeRegistry:
    """进程内单例；测试 / 多租户场景可以构造独立的注册表传入业务函数。"""
    global _DEFAULT_MODE_REGISTRY
    if _DEFAULT_MODE_REGISTRY is None:
        _DEFAULT_MODE_REGISTRY = build_default_mode_registry()
    return _DEFAULT_MODE_REGISTRY


def reset_default_mode_registry() -> None:
    """供测试 / 热更新使用。"""
    global _DEFAULT_MODE_REGISTRY
    _DEFAULT_MODE_REGISTRY = None


# ─── LlmBackendSpec / LlmBackendRegistry ────────────────────────────────────


@dataclass(frozen=True)
class LlmBackendSpec:
    """LLM 后端的元描述，注册表条目。

    字段：
    - ``name``                          Canonical backend 名（与 ``LlmBackend`` 对齐）
    - ``display_name``                  人类可读名
    - ``requires_api_key_env``          需要的环境变量名（如 ``OPENAI_API_KEY``）；为
                                        ``None`` 时表示无需 API key（CLI / 本地）
    - ``supports_function_calling``     是否原生支持 OpenAI-style tool calls
    - ``aliases``                       接受的别名（``cursor-agent`` → ``cursor-cli`` 等）
    """

    name: str
    display_name: str = ""
    requires_api_key_env: Optional[str] = None
    supports_function_calling: bool = False
    aliases: Tuple[str, ...] = ()


@dataclass
class LlmBackendRegistry:
    _specs: Dict[str, LlmBackendSpec] = field(default_factory=dict)
    _alias_to_name: Dict[str, str] = field(default_factory=dict)

    def register(self, spec: LlmBackendSpec) -> None:
        self._specs[spec.name] = spec
        # canonical 名也算一个别名，便于 resolve_alias 一次解决
        self._alias_to_name[spec.name.lower()] = spec.name
        for alias in spec.aliases:
            self._alias_to_name[alias.strip().lower()] = spec.name

    def get(self, name: str) -> Optional[LlmBackendSpec]:
        return self._specs.get(name)

    def require(self, name: str) -> LlmBackendSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise KeyError(f"llm backend '{name}' is not registered")
        return spec

    def resolve_alias(self, alias_or_name: str) -> Optional[str]:
        """``cursor-agent`` / ``cursor`` / ``Cursor-CLI`` 都映射到 ``cursor-cli``。

        未知别名返回 None；调用方决定是用默认还是抛错。
        """
        if not alias_or_name:
            return None
        return self._alias_to_name.get(alias_or_name.strip().lower())

    def names(self) -> List[str]:
        return sorted(self._specs.keys())

    def alias_map(self) -> Dict[str, str]:
        """供调试 / 文档生成；返回完整 alias → canonical 映射。"""
        return dict(self._alias_to_name)


def build_default_backend_registry() -> LlmBackendRegistry:
    """与 ``LlmBackend = Literal["openai","ollama","cursor-cli","gemini-cli"]`` 对齐。"""
    reg = LlmBackendRegistry()
    reg.register(
        LlmBackendSpec(
            name="openai",
            display_name="OpenAI 兼容",
            requires_api_key_env="OPENAI_API_KEY",
            supports_function_calling=True,
            aliases=(),
        )
    )
    reg.register(
        LlmBackendSpec(
            name="ollama",
            display_name="Ollama 本地",
            requires_api_key_env=None,
            supports_function_calling=True,
            aliases=(),
        )
    )
    reg.register(
        LlmBackendSpec(
            name="cursor-cli",
            display_name="Cursor CLI（agent 命令）",
            requires_api_key_env=None,
            supports_function_calling=False,
            # 兼容老配置：cursor-agent / cursor / agent 全部归一
            aliases=("cursor", "cursor-agent", "agent"),
        )
    )
    reg.register(
        LlmBackendSpec(
            name="gemini-cli",
            display_name="Gemini CLI",
            requires_api_key_env="GEMINI_API_KEY",
            supports_function_calling=False,
            aliases=("gemini",),
        )
    )
    return reg


_DEFAULT_BACKEND_REGISTRY: Optional[LlmBackendRegistry] = None


def default_backend_registry() -> LlmBackendRegistry:
    global _DEFAULT_BACKEND_REGISTRY
    if _DEFAULT_BACKEND_REGISTRY is None:
        _DEFAULT_BACKEND_REGISTRY = build_default_backend_registry()
    return _DEFAULT_BACKEND_REGISTRY


def reset_default_backend_registry() -> None:
    global _DEFAULT_BACKEND_REGISTRY
    _DEFAULT_BACKEND_REGISTRY = None


__all__ = [
    "LlmBackendRegistry",
    "LlmBackendSpec",
    "ModeRegistry",
    "ModeSpec",
    "build_default_backend_registry",
    "build_default_mode_registry",
    "default_backend_registry",
    "default_mode_registry",
    "reset_default_backend_registry",
    "reset_default_mode_registry",
]
