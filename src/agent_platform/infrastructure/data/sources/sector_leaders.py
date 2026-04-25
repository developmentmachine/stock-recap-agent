"""板块龙头矩阵 + 多日板块强弱叠加。

资深视角：
  - 强势板块只看『板块涨跌幅』容易被指数权重污染（如银行/中字头），还得看
    『板块内部 top5 个股的涨幅 + 涨停个数』才能判断是不是『真扩散』；
  - 单日板块强弱不够稳健，需要叠加 5 日累计涨跌幅 — 用于把『新热点』和
    『高位扩散』区分开。

数据来源：
  - 行业成分股：AkShare ak.stock_board_industry_cons_em(symbol=...)
  - 5 日累计：通过 AkShare ak.stock_board_industry_hist_em（日频）过去 5 个交易日
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agent_platform.sources.sector_leaders")


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _top_strong_industry_names(sector: Dict[str, Any], n: int = 3) -> List[str]:
    rows = (sector or {}).get("涨幅前10") or []
    names = []
    for r in rows[:n]:
        if isinstance(r, dict):
            name = r.get("板块名称") or r.get("名称")
            if name:
                names.append(str(name))
    return names


def _industry_constituents_top5(ak: Any, industry_name: str) -> List[Dict[str, Any]]:
    """单个行业取涨幅前 5 个股 + 是否涨停。"""
    if ak is None:
        return []
    try:
        df = ak.stock_board_industry_cons_em(symbol=industry_name)
    except Exception as e:
        logger.debug("stock_board_industry_cons_em(%s) failed: %s", industry_name, e)
        return []
    if df is None or df.empty:
        return []
    pct_col = None
    for c in ("涨跌幅", "今开涨跌幅", "涨幅"):
        if c in df.columns:
            pct_col = c
            break
    name_col = "名称" if "名称" in df.columns else ("股票名称" if "股票名称" in df.columns else None)
    code_col = "代码" if "代码" in df.columns else ("股票代码" if "股票代码" in df.columns else None)
    if not pct_col or not name_col:
        return []

    try:
        df = df.sort_values(pct_col, ascending=False)
    except Exception:
        return []

    top5: List[Dict[str, Any]] = []
    limit_up_count = 0
    for _, row in df.head(5).iterrows():
        pct = _safe_float(row.get(pct_col))
        item = {
            "名称": str(row.get(name_col, "")).strip(),
            "代码": str(row.get(code_col, "") or "").strip(),
            "涨跌幅": round(pct, 2) if pct is not None else None,
        }
        if pct is not None and pct >= 9.7:
            limit_up_count += 1
        top5.append(item)
    # 全板块涨停数量（用于扩散度判断）
    try:
        full_pct_series = df[pct_col].astype(float)
        full_lu = int((full_pct_series >= 9.7).sum())
    except Exception:
        full_lu = limit_up_count
    return [{
        "板块": industry_name,
        "成分股_top5": top5,
        "板内涨停数": full_lu,
    }]


def fetch_sector_leaders(
    ak: Optional[Any],
    sector: Dict[str, Any],
    top_industries: int = 3,
) -> Dict[str, Any]:
    """对当日强势 top3 行业，分别取板内涨幅前 5 个股 + 涨停数。"""
    if ak is None or not sector:
        return {}
    names = _top_strong_industry_names(sector, n=top_industries)
    if not names:
        return {}

    matrix: List[Dict[str, Any]] = []
    for name in names:
        ret = _industry_constituents_top5(ak, name)
        if ret:
            matrix.extend(ret)
    if not matrix:
        return {}
    return {
        "强势行业龙头矩阵": matrix,
        "口径": "akshare:stock_board_industry_cons_em，按涨幅排序取板内 top5",
    }


# ─── 5 日板块强弱叠加 ────────────────────────────────────────────────────

def fetch_industry_5d_strength(
    ak: Optional[Any],
    industry_names: List[str],
    *,
    days: int = 5,
) -> Dict[str, Any]:
    """对指定行业，取近 5 个交易日累计涨跌幅，便于区分『单日脉冲』vs『持续扩散』。"""
    if ak is None or not industry_names:
        return {}
    rows: List[Dict[str, Any]] = []
    for name in industry_names:
        try:
            # AkShare 行业历史：默认日频，period 必填
            df = ak.stock_board_industry_hist_em(
                symbol=name,
                period="日k",
                adjust="",
            )
        except Exception as e:
            logger.debug("industry_hist_em(%s) failed: %s", name, e)
            continue
        if df is None or df.empty:
            continue
        try:
            tail = df.tail(days)
            if tail.empty:
                continue
            close_col = "收盘" if "收盘" in tail.columns else None
            if close_col is None:
                continue
            first = float(tail.iloc[0][close_col])
            last = float(tail.iloc[-1][close_col])
            cum_pct = round((last / first - 1) * 100, 2) if first else None
            rows.append({
                "板块": name,
                f"近{days}日累计涨跌幅(%)": cum_pct,
            })
        except Exception as e:
            logger.debug("industry_hist_em parse(%s) failed: %s", name, e)
            continue
    if not rows:
        return {}
    rows.sort(key=lambda r: (r.get(f"近{days}日累计涨跌幅(%)") or -999), reverse=True)
    return {
        "样本": rows,
        "口径": f"akshare:stock_board_industry_hist_em 近{days}日累计涨幅",
    }


__all__ = [
    "fetch_sector_leaders",
    "fetch_industry_5d_strength",
    "_top_strong_industry_names",
]
