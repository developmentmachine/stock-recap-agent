"""情绪数据源（成交额/涨跌停）：SinaOverviewSource → AkShareSentimentSource。"""
from __future__ import annotations

import math
from typing import Any, Dict

import httpx

from agent_platform.infrastructure.data.sources import DataFetcher, DataSource


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


class SinaOverviewSource:
    """新浪财经 hq.sinajs.cn — 从上证+深证成交额估算两市总量。"""

    name = "sina"

    def fetch(self) -> Dict[str, Any]:
        url = "https://hq.sinajs.cn/list=s_sh000001,s_sz399001"
        with httpx.Client(timeout=10) as client:
            r = client.get(url, headers={
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0",
            })
            r.raise_for_status()
            total = 0.0
            for line in r.text.splitlines():
                parts = line.split('"')[1].split(",") if '"' in line else []
                if len(parts) >= 6:
                    try:
                        total += float(parts[5])  # 万元
                    except Exception:
                        pass
            if total > 0:
                return {"两市成交额(亿)": round(total / 10000, 1)}
        return {}


class AkShareSentimentSource:
    """AkShare 涨跌停 + 全市场概览（可能因 urllib3/Python 3.14 兼容性失败）。"""

    name = "akshare"

    def __init__(self, ak: Any, date_short: str) -> None:
        self._ak = ak
        self._date_short = date_short

    def fetch(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        ak = self._ak
        date_short = self._date_short

        try:
            df = ak.stock_zt_pool_em(date=date_short)
            result["涨停家数"] = int(len(df)) if df is not None else 0
        except Exception:
            pass

        try:
            df = ak.stock_zt_pool_dtgc_em(date=date_short)
            result["跌停家数"] = int(len(df)) if df is not None else 0
        except Exception:
            pass

        try:
            df = ak.stock_zh_a_spot_em()
            if df is not None and not df.empty:
                if "成交额" in df.columns:
                    result["两市成交额(亿)"] = float(df["成交额"].sum() / 1e8)
                if "涨跌幅" in df.columns:
                    s = df["涨跌幅"]
                    result["上涨家数"] = int((s > 0).sum())
                    result["下跌家数"] = int((s < 0).sum())
                    result["平盘家数"] = int((s == 0).sum())
        except Exception:
            pass

        return result


def make_sentiment_fetcher(ak: Any, date_short: str) -> DataFetcher:
    return DataFetcher(
        [SinaOverviewSource(), AkShareSentimentSource(ak, date_short)],
        label="sentiment",
    )
