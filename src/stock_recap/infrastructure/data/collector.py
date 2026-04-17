"""市场数据采集层 — 统一入口，委托给各 provider 实现。

Provider 说明：
- mock   : 确定性随机数据（seed=日期），用于无网络/自测
- akshare: 全量使用 AkShare
- live   : 关键指数走东方财富 push2（无 key），其余补充数据走 AkShare
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from stock_recap.infrastructure.data.providers.mock import collect_mock
from stock_recap.infrastructure.data.providers.live import collect_live
from stock_recap.domain.models import MarketSnapshot, Provider

logger = logging.getLogger("stock_recap.collector")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _stable_json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def collect_snapshot(
    provider: Provider,
    date: Optional[str],
    skip_trading_check: bool = False,
) -> MarketSnapshot:
    """采集市场快照，委托给对应 provider。"""
    from stock_recap.infrastructure.data.calendar import is_trading_day

    d = date or _today_str()
    asof = _utc_now_iso()

    if not skip_trading_check and not is_trading_day(d):
        logger.warning(
            _stable_json({"event": "non_trading_day", "date": d, "note": "使用 --skip-trading-check 强制生成"})
        )

    if provider == "mock":
        return collect_mock(d, asof)

    try:
        import akshare as ak  # type: ignore
    except Exception:
        raise RuntimeError(
            "AkShare 未能导入。请确认已安装（uv sync），或切换 --provider mock"
        )

    date_short = d.replace("-", "")
    return collect_live(ak, d, date_short, asof)
