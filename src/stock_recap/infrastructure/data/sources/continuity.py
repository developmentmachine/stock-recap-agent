"""涨停连续性分析：昨日涨停今日表现 → 接力率 / 炸板率 / 高位连板接力情况。

资深复盘的核心信号：「妖股是否接力」「连板梯队是否退潮」。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from stock_recap.infrastructure.data.sources.eastmoney_http import push2ex_zt_pool
from stock_recap.infrastructure.data.sources.limit_up_pool import _normalize_one

logger = logging.getLogger("stock_recap.sources.continuity")


def _yesterday_yyyymmdd(today_yyyymmdd: str) -> str:
    try:
        d = datetime.strptime(today_yyyymmdd, "%Y%m%d")
    except ValueError:
        return ""
    # 简单倒推 1 天；若昨天不是交易日，pool 为空，调用方需兜底
    return (d - timedelta(days=1)).strftime("%Y%m%d")


def _akshare_previous_pool(ak: Any, today_yyyymmdd: str) -> List[Dict[str, Any]]:
    """昨涨停今日表现（AkShare 主路），失败时返回空列表。"""
    try:
        df = ak.stock_zt_pool_previous_em(date=today_yyyymmdd)
    except Exception as e:
        logger.warning("stock_zt_pool_previous_em failed: %s", e)
        return []
    if df is None or df.empty:
        return []
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        try:
            pct = float(r.get("涨跌幅", 0))
        except Exception:
            pct = 0.0
        try:
            yes_lbc = int(r.get("昨日连板数", 0))
        except Exception:
            yes_lbc = 0
        rows.append(
            {
                "代码": str(r.get("代码", "")).strip(),
                "名称": str(r.get("名称", "")).strip(),
                "今日涨跌幅": round(pct, 2),
                "昨日连板数": yes_lbc,
                "涨停统计": str(r.get("涨停统计", "")).strip(),
                "所属行业": str(r.get("所属行业", "")).strip(),
            }
        )
    return rows


def _push2ex_compare(today_yyyymmdd: str) -> List[Dict[str, Any]]:
    """AkShare 不可用时的最低兜底：用 push2ex 拉昨/今 pool，做集合交集来判断"接力涨停"。

    这条路缺少"今日涨幅"，仅能给出"昨日涨停今日是否再次封板"。
    """
    yest = _yesterday_yyyymmdd(today_yyyymmdd)
    if not yest:
        return []
    raw_yest = push2ex_zt_pool(yest, pagesize=120)
    raw_today = push2ex_zt_pool(today_yyyymmdd, pagesize=120)
    if not raw_yest:
        return []
    yest_rows = [_normalize_one(it) for it in raw_yest if it.get("c")]
    today_codes = {str((it or {}).get("c") or "").strip() for it in (raw_today or [])}
    out: List[Dict[str, Any]] = []
    for r in yest_rows:
        out.append(
            {
                "代码": r["代码"],
                "名称": r["名称"],
                "今日涨跌幅": None,  # push2ex 无个股涨幅，仅记录是否再次封板
                "昨日连板数": r["连板数"],
                "涨停统计": r["涨停统计"],
                "所属行业": r["所属行业"],
                "今日是否再封板": r["代码"] in today_codes,
            }
        )
    return out


def _summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    if total == 0:
        return {}
    has_pct = any(r.get("今日涨跌幅") is not None for r in rows)
    up = sum(1 for r in rows if (r.get("今日涨跌幅") or 0) > 0)
    flat = sum(1 for r in rows if (r.get("今日涨跌幅") or 0) == 0)
    down = sum(1 for r in rows if (r.get("今日涨跌幅") or 0) < 0)
    # 接力涨停（再次涨停近似阈值：≥9.5%）
    relimit = sum(
        1 for r in rows
        if (r.get("今日涨跌幅") is not None and r["今日涨跌幅"] >= 9.5)
        or r.get("今日是否再封板") is True
    )
    avg_pct = (
        round(sum((r.get("今日涨跌幅") or 0) for r in rows) / total, 2)
        if has_pct else None
    )
    high_tier = [r for r in rows if r.get("昨日连板数", 0) >= 2]
    high_relimit = sum(
        1 for r in high_tier
        if (r.get("今日涨跌幅") is not None and r["今日涨跌幅"] >= 9.5)
        or r.get("今日是否再封板") is True
    )

    follow_through = [r for r in rows if (r.get("今日涨跌幅") or 0) >= 5]
    follow_through.sort(key=lambda x: x.get("今日涨跌幅") or 0, reverse=True)

    breakers = [r for r in rows if (r.get("今日涨跌幅") or 0) <= -3]
    breakers.sort(key=lambda x: x.get("今日涨跌幅") or 0)

    summary: Dict[str, Any] = {
        "昨日涨停样本数": total,
        "今日上涨家数": up,
        "今日平盘家数": flat,
        "今日下跌家数": down,
        "今日接力涨停": relimit,
        "接力涨停率(%)": round(relimit / total * 100, 1),
        "高位连板样本": len(high_tier),
        "高位连板接力": high_relimit,
        "接力梯队_top": [
            {
                "名称": r["名称"],
                "代码": r["代码"],
                "今日涨跌幅": r.get("今日涨跌幅"),
                "昨日连板数": r.get("昨日连板数"),
                "涨停统计": r.get("涨停统计"),
                "所属行业": r.get("所属行业"),
            }
            for r in follow_through[:6]
        ],
        "退潮个股_top": [
            {
                "名称": r["名称"],
                "代码": r["代码"],
                "今日涨跌幅": r.get("今日涨跌幅"),
                "昨日连板数": r.get("昨日连板数"),
                "所属行业": r.get("所属行业"),
            }
            for r in breakers[:6]
        ],
    }
    if avg_pct is not None:
        summary["昨涨停今日平均涨幅(%)"] = avg_pct
    return summary


def fetch_continuity(today_yyyymmdd: str, ak: Optional[Any] = None) -> Dict[str, Any]:
    """主路 AkShare（含个股涨幅），降级 push2ex 集合对比。"""
    rows: List[Dict[str, Any]] = []
    if ak is not None:
        rows = _akshare_previous_pool(ak, today_yyyymmdd)
    if not rows:
        rows = _push2ex_compare(today_yyyymmdd)
    if not rows:
        return {}
    summary = _summarize(rows)
    if summary:
        summary["数据日期"] = today_yyyymmdd
        summary["数据口径"] = (
            "ak.stock_zt_pool_previous_em" if rows and rows[0].get("今日涨跌幅") is not None
            else "push2ex_pool 集合对比（无个股涨幅，仅判定再次封板）"
        )
    return summary


__all__ = ["fetch_continuity"]
