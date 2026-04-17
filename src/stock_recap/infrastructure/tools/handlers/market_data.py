"""AkShare 行情查询工具实现。"""
from __future__ import annotations

import logging

logger = logging.getLogger("stock_recap.infrastructure.tools.market_data")


def run_query_market_data(data_type: str, date: str | None = None) -> str:
    """通过 akshare 查询行情数据。"""
    try:
        import akshare as ak

        if data_type == "index":
            df = ak.stock_zh_index_daily(symbol="sh000001")
            if date:
                row = df[df["date"] == date]
                if row.empty:
                    row = df.tail(1)
            else:
                row = df.tail(1)
            return row.to_json(orient="records", force_ascii=False)

        if data_type == "sector":
            df = ak.stock_board_industry_name_em()
            return df.head(20).to_json(orient="records", force_ascii=False)

        if data_type == "northbound":
            df = ak.stock_connect_hist_em(symbol="北向资金")
            return df.tail(5).to_json(orient="records", force_ascii=False)

        return f"未知 data_type: {data_type}"
    except Exception as e:
        logger.warning("query_market_data 失败: %s", e)
        return f"查询失败: {e}"
