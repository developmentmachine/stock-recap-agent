"""板块资金流：行业 + 概念（东方财富 push2 主力净流入）。"""
from __future__ import annotations

from typing import Any, Dict, List

from agent_platform.infrastructure.data.sources.eastmoney_http import push2_clist


# f3:涨跌幅%, f12:板块代码, f14:板块名, f62:今日主力净流入(元), f184:净占比%
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
                "板块名称": str(it.get("f14") or "").strip(),
                "板块代码": str(it.get("f12") or "").strip(),
                "涨跌幅": pct,
                "主力净流入(亿)": net_yi,
                "净占比(%)": ratio,
            }
        )
    return out


def fetch_sector_fund_flow(top: int = 10) -> Dict[str, Any]:
    """返回 {行业: {净流入前N, 净流出前N}, 概念: {...}}；任一档失败时该档为空字典。"""
    out: Dict[str, Any] = {}

    def _scan(fs: str, key: str) -> None:
        rows_in = push2_clist(fs=fs, fields=_FIELDS, fid="f62", pz=top, po="1")
        rows_out = push2_clist(fs=fs, fields=_FIELDS, fid="f62", pz=top, po="0")
        block: Dict[str, Any] = {}
        if rows_in:
            block["净流入前列"] = _normalize(rows_in)
        if rows_out:
            block["净流出前列"] = _normalize(rows_out)
        if block:
            out[key] = block

    _scan("m:90+t:2", "行业")
    _scan("m:90+t:3", "概念")
    return out


__all__ = ["fetch_sector_fund_flow"]
