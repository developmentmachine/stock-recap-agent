"""美股个股扩展：Mag7 + 中概 ADR（新浪行情，与 us_market 同源）。"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import httpx

# (sina list 代码, 中文名/标识, 分组)
_MAG7: List[Tuple[str, str]] = [
    ("gb_aapl", "苹果"),
    ("gb_msft", "微软"),
    ("gb_googl", "谷歌"),
    ("gb_amzn", "亚马逊"),
    ("gb_nvda", "英伟达"),
    ("gb_meta", "Meta"),
    ("gb_tsla", "特斯拉"),
]
_AI_PEERS: List[Tuple[str, str]] = [
    ("gb_avgo", "博通"),
    ("gb_amd", "AMD"),
    ("gb_tsm", "台积电ADR"),
]
_CHINA_ADR: List[Tuple[str, str]] = [
    ("gb_baba", "阿里巴巴"),
    ("gb_pdd", "拼多多"),
    ("gb_jd", "京东"),
    ("gb_bidu", "百度"),
    ("gb_nio", "蔚来"),
    ("gb_xpev", "小鹏"),
    ("gb_li", "理想"),
    ("gb_bili", "哔哩哔哩"),
]


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _parse_lines(text: str) -> Dict[str, Tuple[float, float]]:
    """解析新浪 hq 返回，键为 sina 代码（如 gb_aapl）→ (收盘价, 涨跌幅%)."""
    out: Dict[str, Tuple[float, float]] = {}
    for line in text.splitlines():
        if "hq_str_" not in line or '"' not in line:
            continue
        head, payload = line.split('"', 1)
        sym = head.split("hq_str_", 1)[1].rstrip("=")
        parts = payload.split('"')[0].split(",")
        if len(parts) >= 3 and parts[1]:
            out[sym] = (_safe_float(parts[1]), _safe_float(parts[2]))
    return out


def _fetch(symbols: List[Tuple[str, str]], parsed: Dict[str, Tuple[float, float]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for sym, label in symbols:
        if sym in parsed:
            price, pct = parsed[sym]
            ticker = sym.split("gb_", 1)[1].upper()
            rows.append(
                {
                    "代码": ticker,
                    "名称": label,
                    "收盘价": price,
                    "涨跌幅(%)": round(pct, 2),
                }
            )
    return rows


def fetch_us_movers() -> Dict[str, Any]:
    """返回 {mag7: [...], ai_peers: [...], china_adr: [...]}；网络失败返回 {}。"""
    syms = [s for s, _ in _MAG7 + _AI_PEERS + _CHINA_ADR]
    url = "https://hq.sinajs.cn/list=" + ",".join(syms)
    try:
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
    except Exception:
        return {}

    parsed = _parse_lines(text)
    out: Dict[str, Any] = {}
    mag7 = _fetch(_MAG7, parsed)
    ai = _fetch(_AI_PEERS, parsed)
    adr = _fetch(_CHINA_ADR, parsed)
    if mag7:
        out["mag7"] = mag7
    if ai:
        out["ai_链上"] = ai
    if adr:
        out["中概股_adr"] = adr
    return out


__all__ = ["fetch_us_movers"]
