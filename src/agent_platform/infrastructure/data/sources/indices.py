"""指数数据源：TencentIndexSource → EastMoneyIndexSource。"""
from __future__ import annotations

from typing import Any, Dict

import httpx

from agent_platform.infrastructure.data.sources import DataFetcher, DataSource


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


class TencentIndexSource:
    """腾讯财经 qt.gtimg.cn — 稳定，无反爬。"""

    name = "tencent"

    def fetch(self) -> Dict[str, Any]:
        symbols = {
            "s_sh000001": "上证指数",
            "s_sz399001": "深证成指",
            "s_sz399006": "创业板指",
            "s_sh000688": "科创50",
            "s_sh000300": "沪深300",
            "s_sh000016": "上证50",
            "s_sh000852": "中证1000",
            "s_sz399303": "国证2000",
            "s_sz399296": "创成长",
        }
        url = "https://qt.gtimg.cn/q=" + ",".join(symbols.keys())
        indices: Dict[str, Any] = {}
        with httpx.Client(timeout=10) as client:
            r = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            for line in r.text.splitlines():
                for sym, name in symbols.items():
                    if sym in line and '"' in line:
                        parts = line.split('"')[1].split("~")
                        if len(parts) >= 6:
                            indices[name] = {
                                "最新价": _safe_float(parts[3]),
                                "涨跌幅": _safe_float(parts[5]),
                                "成交额(亿)": round(_safe_float(parts[9]) / 1e4, 2) if len(parts) > 9 else None,
                            }
        return indices


class EastMoneyIndexSource:
    """东方财富 push2 单股接口 — 备用。"""

    name = "eastmoney"

    def fetch(self) -> Dict[str, Any]:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        reqs = {
            "上证指数": "1.000001",
            "深证成指": "0.399001",
            "创业板指": "0.399006",
            "科创50": "1.000688",
            "沪深300": "1.000300",
            "上证50": "1.000016",
            "中证1000": "1.000852",
            "国证2000": "0.399303",
            "创成长": "0.399296",
        }
        indices: Dict[str, Any] = {}
        with httpx.Client(timeout=10) as client:
            for name, secid in reqs.items():
                r = client.get(url,
                               params={"secid": secid, "fields": "f43,f170,f47,f48,f58"},
                               headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
                data = r.json().get("data") or {}
                price = (data.get("f43") or 0) / 100
                pct = (data.get("f170") or 0) / 100
                amount = data.get("f48")
                if price > 0:
                    indices[name] = {
                        "最新价": float(price),
                        "涨跌幅": float(pct),
                        "成交额(亿)": float(amount) / 1e8 if isinstance(amount, (int, float)) and amount > 0 else None,
                    }
        return indices


def make_indices_fetcher() -> DataFetcher:
    return DataFetcher(
        [TencentIndexSource(), EastMoneyIndexSource()],
        label="indices",
    )
