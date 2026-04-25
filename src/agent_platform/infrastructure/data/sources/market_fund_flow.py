"""全市场（大盘）主力资金流 — 东财日级序列，取与复盘日匹配或最近一行。"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict

from agent_platform.infrastructure.data.ak_retry import ak_call

logger = logging.getLogger("agent_platform.sources.market_fund_flow")


def _parse_row_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def fetch_market_main_fund_summary(ak: Any, trade_date: str) -> Dict[str, Any]:
    """返回写入 `market_sentiment['大盘主力资金流']` 的字典；失败返回 {}。"""
    try:
        df = ak_call(lambda: ak.stock_market_fund_flow(), label="market_fund_flow")
    except Exception as e:
        logger.warning("stock_market_fund_flow failed: %s", e)
        return {}
    if df is None or df.empty or "日期" not in df.columns:
        return {}

    target = None
    try:
        target = datetime.strptime(trade_date, "%Y-%m-%d").date()
    except Exception:
        pass

    row = None
    if target is not None:
        for _, r in df.iterrows():
            rd = _parse_row_date(r.get("日期"))
            if rd == target:
                row = r
                break
    if row is None:
        row = df.iloc[-1]

    def _f(name: str) -> float | None:
        if name not in row.index:
            return None
        try:
            return float(row[name])
        except Exception:
            return None

    main_net = _f("主力净流入-净额")
    out: Dict[str, Any] = {
        "数据来源": "akshare:stock_market_fund_flow",
    }
    rd = _parse_row_date(row.get("日期"))
    if rd is not None:
        out["数据日期"] = rd.isoformat()
    if main_net is not None:
        out["主力净流入_亿"] = round(main_net / 1e8, 2)
    p = _f("主力净流入-净占比")
    if p is not None:
        out["主力净流入_净占比_%"] = round(p, 2)
    for label, col in (
        ("超大单净流入_亿", "超大单净流入-净额"),
        ("大单净流入_亿", "大单净流入-净额"),
        ("中单净流入_亿", "中单净流入-净额"),
        ("小单净流入_亿", "小单净流入-净额"),
    ):
        v = _f(col)
        if v is not None:
            out[label] = round(v / 1e8, 2)
    return out if len(out) > 1 else {}
