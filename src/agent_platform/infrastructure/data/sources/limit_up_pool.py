"""涨停板池（push2ex）：连板高度 + 所属行业题材汇总，用于「涨停潮」分析。"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from agent_platform.infrastructure.data.sources.eastmoney_http import push2ex_zt_pool


def _safe_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _safe_float(v: Any, scale: float = 1.0) -> float:
    try:
        return float(v) * scale
    except (TypeError, ValueError):
        return 0.0


def _normalize_one(it: Dict[str, Any]) -> Dict[str, Any]:
    lbc = _safe_int(it.get("lbc"))  # 连板数
    zttj = it.get("zttj") or {}
    days = _safe_int(zttj.get("days"))
    ct = _safe_int(zttj.get("ct"))
    return {
        "代码": str(it.get("c") or "").strip(),
        "名称": str(it.get("n") or "").strip(),
        "连板数": lbc,
        "涨停统计": f"{days}天{ct}板" if days or ct else "",
        "所属行业": str(it.get("hybk") or "").strip(),
        "封板金额(亿)": round(_safe_float(it.get("fund")) / 1e8, 2),
        "成交额(亿)": round(_safe_float(it.get("amount")) / 1e8, 2),
        "炸板次数": _safe_int(it.get("zbc")),
    }


def _pick_high_tier(rows: List[Dict[str, Any]], min_lbc: int = 2, top: int = 8) -> List[Dict[str, Any]]:
    candidates = [r for r in rows if r["连板数"] >= min_lbc]
    candidates.sort(key=lambda x: (x["连板数"], x["封板金额(亿)"]), reverse=True)
    return candidates[:top]


def _theme_counter(rows: List[Dict[str, Any]], top: int = 6) -> List[Dict[str, Any]]:
    """按所属行业聚合涨停家数 + 平均连板高度，返回涨停潮所在题材。"""
    bucket: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        sec = r.get("所属行业") or "其他"
        bucket.setdefault(sec, []).append(r)
    summary: List[Dict[str, Any]] = []
    for sec, items in bucket.items():
        names = [it["名称"] for it in items[:5]]
        summary.append(
            {
                "题材": sec,
                "涨停家数": len(items),
                "最高连板": max((it["连板数"] for it in items), default=0),
                "代表个股": names,
            }
        )
    summary.sort(key=lambda x: (x["涨停家数"], x["最高连板"]), reverse=True)
    return summary[:top]


def fetch_limit_up_pool(date_yyyymmdd: str) -> Dict[str, Any]:
    """返回涨停板池摘要：总数 / 高位连板 / 题材聚合 / 封板金额前列。"""
    pool_raw = push2ex_zt_pool(date_yyyymmdd, pagesize=120)
    if not pool_raw:
        return {}

    rows = [_normalize_one(it) for it in pool_raw]
    rows = [r for r in rows if r["代码"]]
    if not rows:
        return {}

    fund_top = sorted(rows, key=lambda x: x["封板金额(亿)"], reverse=True)[:8]
    return {
        "数据日期": date_yyyymmdd,
        "涨停总数": len(rows),
        "连板梯队_最高": max(r["连板数"] for r in rows),
        "高位连板": _pick_high_tier(rows),
        "题材聚合": _theme_counter(rows),
        "封板金额前列": fund_top,
        "数据来源": "eastmoney push2ex",
    }


__all__ = ["fetch_limit_up_pool"]
