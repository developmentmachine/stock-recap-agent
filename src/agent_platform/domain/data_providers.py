"""市场数据采集 Provider 注册表（W5-4）。

与 ``LlmBackendRegistry`` 对称：domain 层只放 **类型与容器**，具体 ``collect_*``
 实现留在 ``infrastructure/data/providers/``，由 ``builtin_data_providers`` 装配。

扩展方式（运行时）：
    from agent_platform.domain.data_providers import DataProviderSpec
    from agent_platform.infrastructure.data.collector import (
        default_data_provider_registry,
    )

    default_data_provider_registry().register(
        DataProviderSpec(
            name="my-feed",
            collect=my_collect_fn,
            display_name="内部行情源",
        )
    )

``collect`` 签名统一为 ``(date: str, asof: str, skip_trading_check: bool) -> MarketSnapshot``，
其中 ``date`` 已是 ``YYYY-MM-DD``（由 ``collect_snapshot`` 归一化），``asof`` 为 UTC ISO。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List

from agent_platform.domain.models import MarketSnapshot


CollectFn = Callable[[str, str, bool], MarketSnapshot]


@dataclass(frozen=True)
class DataProviderSpec:
    """单个行情源的元数据 + 采集入口。"""

    name: str
    collect: CollectFn = field(repr=False)
    display_name: str = ""


@dataclass
class DataProviderRegistry:
    _specs: Dict[str, DataProviderSpec] = field(default_factory=dict)

    def register(self, spec: DataProviderSpec) -> None:
        key = spec.name.strip().lower()
        self._specs[key] = DataProviderSpec(
            name=key,
            collect=spec.collect,
            display_name=spec.display_name or key,
        )

    def get(self, name: str) -> DataProviderSpec | None:
        if not name:
            return None
        return self._specs.get(name.strip().lower())

    def require(self, name: str) -> DataProviderSpec:
        spec = self.get(name)
        if spec is None:
            known = ", ".join(sorted(self.names())) or "(empty)"
            raise KeyError(f"market data provider '{name}' is not registered; known: {known}")
        return spec

    def names(self) -> List[str]:
        return sorted(self._specs.keys())


__all__ = [
    "CollectFn",
    "DataProviderRegistry",
    "DataProviderSpec",
]
