"""大宗商品数据源：SinaCommoditiesSource。"""
from __future__ import annotations

from typing import Any, Dict

import httpx

from stock_recap.infrastructure.data.sources import DataFetcher


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


class SinaCommoditiesSource:
    """新浪财经 hq.sinajs.cn — 黄金(COMEX) + WTI原油。"""

    name = "sina"

    def fetch(self) -> Dict[str, Any]:
        url = "https://hq.sinajs.cn/list=hf_GC,hf_CL"
        result: Dict[str, Any] = {}
        with httpx.Client(timeout=10) as client:
            r = client.get(url, headers={
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0",
            })
            r.raise_for_status()
            for line in r.text.splitlines():
                if '"' not in line:
                    continue
                parts = line.split('"')[1].split(",")
                if len(parts) < 14:
                    continue
                name_cn = parts[13]
                price = _safe_float(parts[2])
                prev = _safe_float(parts[7])
                pct = round((price - prev) / prev * 100, 2) if prev else 0.0
                if price <= 0:
                    continue
                if "黄金" in name_cn:
                    result["黄金(美元/盎司)"] = {"收盘价": price, "涨跌幅(%)": pct}
                elif "原油" in name_cn:
                    result["WTI原油(美元/桶)"] = {"收盘价": price, "涨跌幅(%)": pct}
        return result


def make_commodities_fetcher() -> DataFetcher:
    return DataFetcher([SinaCommoditiesSource()], label="commodities")
