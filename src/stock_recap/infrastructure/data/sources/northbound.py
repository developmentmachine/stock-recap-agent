"""北向资金数据源：AkShareNorthboundSource。"""
from __future__ import annotations

import logging
from typing import Any, Dict

from stock_recap.infrastructure.data.sources import DataFetcher

logger = logging.getLogger("stock_recap.sources.northbound")


class AkShareNorthboundSource:
    """AkShare stock_hsgt_fund_flow_summary_em — 当日北向资金净买入（沪股通+深股通）。"""

    name = "akshare"

    def __init__(self, ak: Any) -> None:
        self._ak = ak

    def fetch(self) -> Dict[str, Any]:
        df = self._ak.stock_hsgt_fund_flow_summary_em()
        if df is None or df.empty:
            return {}

        dir_col = "资金方向" if "资金方向" in df.columns else None
        if dir_col is None:
            for c in df.columns:
                if "方向" in str(c):
                    dir_col = str(c)
                    break
        if dir_col is None:
            logger.warning("northbound: no 资金方向-like column in stock_hsgt_fund_flow_summary_em")
            return {}

        amt_col = "成交净买额" if "成交净买额" in df.columns else None
        if amt_col is None:
            for c in df.columns:
                if "净买" in str(c) or "净流" in str(c):
                    amt_col = str(c)
                    break
        if amt_col is None:
            return {}

        blk_col = "板块" if "板块" in df.columns else None
        if blk_col is None:
            return {}

        north = df[df[dir_col].astype(str).str.contains("北向", na=False)]
        if north.empty:
            return {}

        total = float(north[amt_col].sum())
        sh_row = north[north[blk_col].astype(str).str.contains("沪股通", na=False)]
        sz_row = north[north[blk_col].astype(str).str.contains("深股通", na=False)]

        result: Dict[str, Any] = {
            "净买入(亿)": round(total, 2),
            "数据来源": "akshare:stock_hsgt_fund_flow_summary_em",
        }
        if not sh_row.empty:
            result["沪股通净买入(亿)"] = round(float(sh_row[amt_col].iloc[0]), 2)
        if not sz_row.empty:
            result["深股通净买入(亿)"] = round(float(sz_row[amt_col].iloc[0]), 2)

        # 接口常返回 0：多为未披露/非终盘/口径占位，并非代码丢失；提示模型勿单独作北向趋势结论
        if total == 0.0 and len(north) > 0:
            result["净买额为零说明"] = (
                "数据源当日沪股通+深股通成交净买额合计为0，常见于披露空窗或非终盘快照；"
                "勿将「北向净买为0」单独写成观望结论，应改用两市成交、涨跌结构、板块与热度榜个股佐证资金方向。"
            )
            logger.info(
                "northbound: API returned zero net buy with %s north rows (upstream quirk, not filter bug)",
                len(north),
            )

        return result


def make_northbound_fetcher(ak: Any) -> DataFetcher:
    return DataFetcher([AkShareNorthboundSource(ak)], label="northbound")
