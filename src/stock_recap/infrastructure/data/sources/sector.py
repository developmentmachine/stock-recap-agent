"""板块数据源：行业 + 概念（AkShare 主路 + Eastmoney push2 备路），并叠加相对沪深300超额。"""
from __future__ import annotations

from typing import Any, Dict, List

from stock_recap.infrastructure.data.sources import DataFetcher
from stock_recap.infrastructure.data.sources.eastmoney_http import push2_clist


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _board_top_bottom(df: Any, name_col: str = "板块名称") -> Dict[str, Any]:
    if df is None or getattr(df, "empty", True):
        return {}
    df = df.sort_values("涨跌幅", ascending=False)
    base_cols = [c for c in (name_col, "涨跌幅") if c in df.columns]
    if len(base_cols) < 2:
        return {}
    extra_cols = [c for c in ("领涨股票", "领涨股票-涨跌幅", "换手率") if c in df.columns]
    cols = base_cols + extra_cols
    slim = df[cols].rename(columns={name_col: "板块名称"})
    return {
        "涨幅前10": slim.head(10).to_dict("records"),
        "跌幅前10": slim.tail(10).to_dict("records"),
    }


class AkShareBoardsSource:
    """东方财富行业板块 + 概念板块涨跌幅（AkShare）。"""

    name = "akshare"

    def __init__(self, ak: Any) -> None:
        self._ak = ak

    def fetch(self) -> Dict[str, Any]:
        industry_df = self._ak.stock_board_industry_name_em()
        ind = _board_top_bottom(industry_df)
        if not ind:
            return {}

        out: Dict[str, Any] = {
            "涨幅前10": ind["涨幅前10"],
            "跌幅前10": ind["跌幅前10"],
        }
        try:
            concept_df = self._ak.stock_board_concept_name_em()
            c = _board_top_bottom(concept_df)
            if c:
                out["概念"] = c
        except Exception:
            pass
        return out


# Eastmoney push2 字段：f3 涨跌幅; f8 换手率; f12 代码; f14 名称
# f128 领涨股名称; f136 领涨股涨跌幅
_BOARD_FIELDS = "f12,f14,f3,f8,f128,f136"


def _push2_rows(fs: str, po: str, top: int) -> List[Dict[str, Any]]:
    rows = push2_clist(fs=fs, fields=_BOARD_FIELDS, fid="f3", pz=top, po=po)
    out: List[Dict[str, Any]] = []
    for it in rows:
        try:
            pct = float(it.get("f3", 0))
        except (TypeError, ValueError):
            pct = 0.0
        try:
            turn = float(it.get("f8", 0))
        except (TypeError, ValueError):
            turn = 0.0
        try:
            leader_pct = float(it.get("f136", 0))
        except (TypeError, ValueError):
            leader_pct = 0.0
        leader_name = str(it.get("f128") or "").strip()
        row: Dict[str, Any] = {
            "板块名称": str(it.get("f14") or "").strip(),
            "涨跌幅": round(pct, 2),
            "换手率": round(turn, 2),
        }
        if leader_name:
            row["领涨股票"] = leader_name
            row["领涨股票-涨跌幅"] = round(leader_pct, 2)
        out.append(row)
    return out


class EastmoneyPush2BoardsSource:
    """直连东方财富 push2：AkShare 故障时的备路（行业 + 概念）。"""

    name = "eastmoney_push2"

    def fetch(self) -> Dict[str, Any]:
        ind_top = _push2_rows("m:90+t:2", po="1", top=10)
        ind_bot = _push2_rows("m:90+t:2", po="0", top=10)
        if not ind_top and not ind_bot:
            return {}
        out: Dict[str, Any] = {}
        if ind_top:
            out["涨幅前10"] = ind_top
        if ind_bot:
            out["跌幅前10"] = ind_bot
        c_top = _push2_rows("m:90+t:3", po="1", top=10)
        c_bot = _push2_rows("m:90+t:3", po="0", top=10)
        if c_top or c_bot:
            cdict: Dict[str, Any] = {}
            if c_top:
                cdict["涨幅前10"] = c_top
            if c_bot:
                cdict["跌幅前10"] = c_bot
            out["概念"] = cdict
        return out


def apply_benchmark_excess(sector: Dict[str, Any], indices: Dict[str, Any]) -> Dict[str, Any]:
    """在行业涨幅前10上叠加相对沪深300的超额（仅追加字段，不删改原列表）。"""
    bench = (indices or {}).get("沪深300") or {}
    b = bench.get("涨跌幅")
    if b is None:
        return sector
    try:
        b_pct = float(b)
    except (TypeError, ValueError):
        return sector

    top = sector.get("涨幅前10") or []
    enriched: list[dict[str, Any]] = []
    for item in top[:10]:
        if not isinstance(item, dict):
            continue
        name = item.get("板块名称")
        p = _safe_float(item.get("涨跌幅"))
        row = dict(item)
        row["超额涨跌幅_相对沪深300"] = round(p - b_pct, 2)
        enriched.append(row)

    merged = dict(sector)
    merged["相对表现"] = {
        "基准": "沪深300",
        "基准涨跌幅": round(b_pct, 2),
        "行业涨幅前10含超额": enriched,
    }
    return merged


def make_sector_fetcher(ak: Any) -> DataFetcher:
    return DataFetcher(
        [AkShareBoardsSource(ak), EastmoneyPush2BoardsSource()],
        label="sector",
    )
