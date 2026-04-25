"""美股数据源：新浪财经 hq.sinajs.cn — 主要指数 + 风格/行业 ETF。"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import httpx

from agent_platform.infrastructure.data.sources import DataFetcher


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


# (新浪 list 代码, 输出键名)
_MAJOR: List[Tuple[str, str]] = [
    ("gb_$dji", "道琼斯"),
    ("gb_$ixic", "纳斯达克"),
    ("gb_$inx", "标普500"),
]

# ETF：键为行情引用/模型对齐用 ticker
_ETFS: List[Tuple[str, str]] = [
    ("gb_qqq", "QQQ"),
    ("gb_spy", "SPY"),
    ("gb_iwm", "IWM"),
    ("gb_xlk", "XLK"),
    ("gb_xlf", "XLF"),
    ("gb_xle", "XLE"),
    ("gb_xlv", "XLV"),
    ("gb_xly", "XLY"),
]


class SinaUsMarketSource:
    """新浪财经 — 美股三大指数 + 代表性 ETF。"""

    name = "sina"

    def fetch(self) -> Dict[str, Any]:
        parts_syms = [p[0] for p in _MAJOR] + [p[0] for p in _ETFS]
        url = "https://hq.sinajs.cn/list=" + ",".join(parts_syms)
        result: Dict[str, Any] = {}
        etf_block: Dict[str, Any] = {}
        with httpx.Client(timeout=12) as client:
            r = client.get(
                url,
                headers={
                    "Referer": "https://finance.sina.com.cn",
                    "User-Agent": "Mozilla/5.0",
                },
            )
            r.raise_for_status()
            text = r.content.decode("gb18030", errors="replace")
            for line in text.splitlines():
                self._parse_major(line, result)
                self._parse_etf(line, etf_block)
        if etf_block:
            result["etf参考"] = etf_block
        return result

    def _parse_major(self, line: str, out: Dict[str, Any]) -> None:
        for sym, name in _MAJOR:
            if sym in line and '"' in line:
                parts = line.split('"')[1].split(",")
                if len(parts) >= 3 and parts[1]:
                    out[name] = {
                        "收盘价": _safe_float(parts[1]),
                        "涨跌幅(%)": _safe_float(parts[2]),
                    }

    def _parse_etf(self, line: str, etf_block: Dict[str, Any]) -> None:
        for sym, ticker in _ETFS:
            if sym in line and '"' in line:
                parts = line.split('"')[1].split(",")
                if len(parts) >= 3 and parts[1]:
                    etf_block[ticker] = {
                        "名称": parts[0].strip(),
                        "收盘价": _safe_float(parts[1]),
                        "涨跌幅(%)": _safe_float(parts[2]),
                    }


def make_us_market_fetcher() -> DataFetcher:
    return DataFetcher([SinaUsMarketSource()], label="us_market")
