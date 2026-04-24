"""市场数据采集层 — 统一入口，经 ``DataProviderRegistry`` 路由到具体实现。

内置 id（``build_default_data_provider_registry``）：
- ``mock``    : 确定性随机数据（seed=日期），用于无网络/自测
- ``live``    : 关键指数走东方财富 push2，其余 AkShare
- ``akshare`` : 全量 AkShare（``collect_akshare``）

扩展：见 ``domain.data_providers`` 模块文档；运行时 ``register_data_provider`` 登记新 id。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from stock_recap.domain.data_providers import DataProviderRegistry, DataProviderSpec
from stock_recap.domain.models import MarketSnapshot

logger = logging.getLogger("stock_recap.collector")

_DEFAULT_REGISTRY: Optional[DataProviderRegistry] = None


def default_data_provider_registry() -> DataProviderRegistry:
    """进程内单例；测试可 ``reset_default_data_provider_registry()`` 后重建。"""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        from stock_recap.infrastructure.data.builtin_data_providers import (
            build_default_data_provider_registry,
        )

        _DEFAULT_REGISTRY = build_default_data_provider_registry()
    return _DEFAULT_REGISTRY


def reset_default_data_provider_registry() -> None:
    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None


def register_data_provider(spec: DataProviderSpec) -> None:
    """向默认注册表追加一个行情源（幂等覆盖同名）。"""
    default_data_provider_registry().register(spec)


def list_data_provider_ids() -> list[str]:
    return default_data_provider_registry().names()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _stable_json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def collect_snapshot(
    provider: str,
    date: Optional[str],
    skip_trading_check: bool = False,
    *,
    registry: Optional[DataProviderRegistry] = None,
) -> MarketSnapshot:
    """采集市场快照：按注册表解析 ``provider`` id，再调用对应 ``collect``。"""
    from stock_recap.infrastructure.data.calendar import is_trading_day

    d = date or _today_str()
    asof = _utc_now_iso()

    if not skip_trading_check and not is_trading_day(d):
        logger.warning(
            _stable_json(
                {
                    "event": "non_trading_day",
                    "date": d,
                    "note": "使用 --skip-trading-check 强制生成",
                }
            )
        )

    reg = registry or default_data_provider_registry()
    try:
        spec = reg.require(provider)
    except KeyError as e:
        raise RuntimeError(str(e)) from e
    return spec.collect(d, asof, skip_trading_check)
