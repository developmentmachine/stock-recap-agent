"""交易日历工具。

- is_trading_day(date): 判断给定日期是否为交易日（缓存到内存）
- is_trading_hours(): 判断当前是否在交易时段（9:30-15:00 北京时间）
- check_data_freshness(snapshot): 校验 snapshot 数据是否足够新鲜
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Set

import pytz

BEIJING = pytz.timezone("Asia/Shanghai")

logger = logging.getLogger("agent_platform.calendar")


@lru_cache(maxsize=1)
def _fetch_trading_dates() -> Set[str]:
    """从 AkShare 获取 A 股历史交易日列表（内存缓存，进程重启后重新拉取）。"""
    try:
        import akshare as ak  # type: ignore

        df = ak.tool_trade_date_hist_sina()
        return set(df["trade_date"].astype(str).tolist())
    except Exception as e:
        logger.warning("交易日历获取失败，降级为非周末判断: %s", e)
        return set()


def is_trading_day(date: str) -> bool:
    """
    判断 date（YYYY-MM-DD）是否为 A 股交易日。
    优先使用 AkShare 官方交易日历；获取失败时降级为「非周末」判断。
    """
    dates = _fetch_trading_dates()
    if dates:
        return date in dates

    # 降级：非周末视为交易日（不精确，但不会误拒）
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        return dt.weekday() < 5
    except ValueError:
        return False


def is_trading_hours() -> bool:
    """判断当前北京时间是否在 9:30-15:00 交易时段内。"""
    now_bj = datetime.now(BEIJING)
    start = now_bj.replace(hour=9, minute=30, second=0, microsecond=0)
    end = now_bj.replace(hour=15, minute=0, second=0, microsecond=0)
    return start <= now_bj <= end


def check_data_freshness(asof_utc_iso: str, warn_after_minutes: int = 30) -> bool:
    """
    校验数据是否足够新鲜（采集时间距当前不超过 warn_after_minutes 分钟）。
    Returns True if fresh, False if stale.
    """
    try:
        asof = datetime.fromisoformat(asof_utc_iso)
        if asof.tzinfo is None:
            asof = asof.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_minutes = (now - asof).total_seconds() / 60
        if age_minutes > warn_after_minutes:
            logger.warning(
                "数据已陈旧：采集时间 %s，已过 %.1f 分钟（阈值 %d 分钟）",
                asof_utc_iso,
                age_minutes,
                warn_after_minutes,
            )
            return False
        return True
    except Exception as e:
        logger.warning("数据新鲜度检查失败: %s", e)
        return True  # 无法判断时不阻断流程
