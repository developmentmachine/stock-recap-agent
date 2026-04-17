"""市场热度榜（个股维度），用于北向口径失效时的盘面佐证。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from stock_recap.infrastructure.data.sources import DataFetcher

logger = logging.getLogger("stock_recap.sources.hot_rank")


class AkShareHotRankSource:
    """AkShare stock_hot_rank_em — 当日热度排名前列个股（名称+涨跌幅）。"""

    name = "akshare"

    def __init__(self, ak: Any, top_n: int = 12) -> None:
        self._ak = ak
        self._top_n = top_n

    def fetch(self) -> Dict[str, Any]:
        try:
            df = self._ak.stock_hot_rank_em()
        except Exception as e:
            logger.warning("stock_hot_rank_em failed: %s", e)
            return {}
        if df is None or df.empty:
            return {}
        name_c = "股票名称" if "股票名称" in df.columns else None
        pct_c = "涨跌幅" if "涨跌幅" in df.columns else None
        code_c = "代码" if "代码" in df.columns else None
        if not name_c or not pct_c:
            return {}
        n = min(self._top_n, len(df))
        rows: List[Dict[str, Any]] = []
        for _, row in df.head(n).iterrows():
            item: Dict[str, Any] = {
                "股票名称": str(row[name_c]).strip(),
                "涨跌幅": float(row[pct_c]),
            }
            if code_c:
                item["代码"] = str(row[code_c]).strip()
            rows.append(item)
        return {"热度榜前列": rows, "数据来源": "akshare:stock_hot_rank_em"}


def make_hot_rank_fetcher(ak: Any, top_n: int = 12) -> DataFetcher:
    return DataFetcher([AkShareHotRankSource(ak, top_n=top_n)], label="hot_rank")
