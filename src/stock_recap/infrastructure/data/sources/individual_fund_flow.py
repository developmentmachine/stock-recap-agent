"""个股主力资金流前列：东方财富 push2，让模型可以点出"今日资金真正抢筹的个股"。"""
from __future__ import annotations

from typing import Any, Dict, List

from stock_recap.infrastructure.data.sources.eastmoney_http import push2_clist


# A 股全市场过滤（剔除 ETF/REITs：f:!2）
_STOCK_FS = (
    "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,"
    "m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2"
)
# f12 代码; f14 名称; f3 涨跌幅; f62 主力净流入(元); f184 净占比%
_FIELDS = "f12,f14,f3,f62,f184"


def _normalize(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in rows:
        try:
            net_yi = round(float(it.get("f62", 0)) / 1e8, 2)
        except (TypeError, ValueError):
            net_yi = 0.0
        try:
            pct = float(it.get("f3", 0))
        except (TypeError, ValueError):
            pct = 0.0
        try:
            ratio = float(it.get("f184", 0))
        except (TypeError, ValueError):
            ratio = 0.0
        out.append(
            {
                "股票名称": str(it.get("f14") or "").strip(),
                "代码": str(it.get("f12") or "").strip(),
                "涨跌幅": round(pct, 2),
                "主力净流入(亿)": net_yi,
                "净占比(%)": ratio,
            }
        )
    return out


def fetch_individual_fund_flow(top: int = 10) -> Dict[str, Any]:
    """返回 {净流入前列: [...], 净流出前列: [...]}；失败时为空。"""
    rows_in = push2_clist(fs=_STOCK_FS, fields=_FIELDS, fid="f62", pz=top, po="1")
    rows_out = push2_clist(fs=_STOCK_FS, fields=_FIELDS, fid="f62", pz=top, po="0")
    out: Dict[str, Any] = {}
    if rows_in:
        out["净流入前列"] = _normalize(rows_in)
    if rows_out:
        out["净流出前列"] = _normalize(rows_out)
    return out


__all__ = ["fetch_individual_fund_flow"]
