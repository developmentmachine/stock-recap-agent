"""装配内置行情 Provider：mock / live / akshare。"""
from __future__ import annotations

from agent_platform.domain.data_providers import DataProviderRegistry, DataProviderSpec
from agent_platform.infrastructure.data.providers.akshare import collect_akshare
from agent_platform.infrastructure.data.providers.live import collect_live
from agent_platform.infrastructure.data.providers.mock import collect_mock


def build_default_data_provider_registry() -> DataProviderRegistry:
    reg = DataProviderRegistry()

    reg.register(
        DataProviderSpec(
            name="mock",
            collect=lambda d, asof, _skip: collect_mock(d, asof),
            display_name="确定性 Mock（按日期 seed）",
        )
    )

    def _collect_live(d: str, asof: str, _skip: bool):
        try:
            import akshare as ak  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "AkShare 未能导入。请确认已安装（uv sync），或切换 --provider mock"
            ) from e
        date_short = d.replace("-", "")
        return collect_live(ak, d, date_short, asof)

    reg.register(
        DataProviderSpec(
            name="live",
            collect=_collect_live,
            display_name="Live（东财 push2 + AkShare 补充）",
        )
    )

    def _collect_akshare(d: str, asof: str, _skip: bool):
        try:
            import akshare as ak  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "AkShare 未能导入。请确认已安装（uv sync），或切换 --provider mock"
            ) from e
        date_short = d.replace("-", "")
        return collect_akshare(ak, d, date_short, asof)

    reg.register(
        DataProviderSpec(
            name="akshare",
            collect=_collect_akshare,
            display_name="全量 AkShare",
        )
    )
    return reg


__all__ = ["build_default_data_provider_registry"]
