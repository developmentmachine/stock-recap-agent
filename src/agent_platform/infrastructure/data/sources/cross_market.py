"""跨市场对照：仅用 snapshot 内已有数字，生成结构化观察（不作因果外推）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# 美股代理符号 → A 股板块名称关键词（命中涨幅前列即建立对照）
_THEME_US_CN: List[Dict[str, Any]] = [
    {
        "主题": "科技成长链",
        "us": [("QQQ", "纳指100"), ("XLK", "美股科技")],
        "cn_keywords": (
            "半导体",
            "芯片",
            "电子",
            "通信",
            "软件",
            "算力",
            "计算机",
            "互联网",
            "人工智能",
            "信创",
            "云计算",
        ),
    },
    {
        "主题": "周期与能源",
        "us": [("XLE", "美股能源")],
        # 避免「新能源」等泛成长主题误命中传统能源链
        "cn_keywords": ("煤炭", "石油", "油气", "电力", "有色", "化工", "钢铁", "石化", "天然气"),
    },
    {
        "主题": "金融",
        "us": [("XLF", "美股金融")],
        "cn_keywords": ("银行", "证券", "保险", "多元金融", "券商", "期货"),
    },
    {
        "主题": "医药健康",
        "us": [("XLV", "美股医疗")],
        "cn_keywords": ("医药", "医疗", "生物", "中药", "疫苗", "医美", "医院"),
    },
    {
        "主题": "消费与可选",
        "us": [("XLY", "美股可选消费")],
        "cn_keywords": ("消费", "汽车", "家电", "食品", "商贸", "旅游", "酒店", "零售"),
    },
]


def _etf_row(us_market: Dict[str, Any], ticker: str) -> Dict[str, Any]:
    etf = us_market.get("etf参考") or {}
    row = etf.get(ticker)
    return row if isinstance(row, dict) else {}


def _pick_us_proxy(us_market: Dict[str, Any], candidates: List[Tuple[str, str]]) -> Optional[Tuple[str, str, float]]:
    for ticker, label in candidates:
        row = _etf_row(us_market, ticker)
        raw = row.get("涨跌幅(%)")
        if raw is None:
            continue
        try:
            return ticker, label, float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _scan_board(
    rows: Any,
    keywords: Tuple[str, ...],
    used: set[str],
) -> Optional[Tuple[str, float]]:
    if not isinstance(rows, list):
        return None
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("板块名称") or item.get("name") or "").strip()
        if not name or name in used:
            continue
        try:
            pct = float(item.get("涨跌幅", 0))
        except (TypeError, ValueError):
            continue
        for kw in keywords:
            if kw in name:
                return name, pct
    return None


def _find_cn_match(
    sector_performance: Dict[str, Any],
    keywords: Tuple[str, ...],
    used_names: set[str],
) -> Optional[Tuple[str, float, str]]:
    """返回 (板块名, 涨跌幅, 分层标签)."""
    ind = sector_performance.get("涨幅前10") or []
    hit = _scan_board(ind, keywords, used_names)
    if hit:
        return hit[0], hit[1], "行业"
    concept = sector_performance.get("概念") or {}
    chit = _scan_board(concept.get("涨幅前10") or [], keywords, used_names)
    if chit:
        return chit[0], chit[1], "概念"
    return None


def _adr_average(us_market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    movers = (us_market or {}).get("movers") or {}
    adr = movers.get("中概股_adr") or []
    if not adr:
        return None
    pcts: List[float] = []
    samples: List[str] = []
    for it in adr:
        try:
            p = float(it.get("涨跌幅(%)"))
        except (TypeError, ValueError):
            continue
        pcts.append(p)
        samples.append(f"{it.get('名称')}({p:+.2f}%)")
    if not pcts:
        return None
    return {
        "样本数": len(pcts),
        "均值涨跌幅(%)": round(sum(pcts) / len(pcts), 2),
        "代表": samples[:5],
    }


def build_cross_market_hints(
    sector_performance: Dict[str, Any],
    us_market: Dict[str, Any],
) -> Dict[str, Any]:
    """基于行业/概念涨幅前列与美股 ETF/ADR 数值，生成同日结构对照（无数据则 hints 为空）。"""
    if not sector_performance or not us_market:
        return {}

    hints: List[Dict[str, Any]] = []
    used_cn: set[str] = set()

    for block in _THEME_US_CN:
        us_hit = _pick_us_proxy(us_market, list(block["us"]))
        if not us_hit:
            continue
        ticker, us_label, pct_us = us_hit
        cn = _find_cn_match(sector_performance, tuple(block["cn_keywords"]), used_cn)
        if not cn:
            continue
        cn_name, pct_cn, layer = cn
        used_cn.add(cn_name)
        same = (pct_us >= 0 and pct_cn >= 0) or (pct_us < 0 and pct_cn < 0)
        hints.append(
            {
                "主题": block["主题"],
                "美股代理": f"{ticker}（{us_label}）",
                "美股涨跌幅(%)": round(pct_us, 2),
                "A股对应": f"{cn_name}（{layer}涨幅前列）",
                "A股涨跌幅(%)": round(pct_cn, 2),
                "数值同向": same,
            }
        )

    adr = _adr_average(us_market)
    out: Dict[str, Any] = {}
    if hints:
        out["paired_observations"] = hints
    if adr:
        out["adr_镜像"] = adr

    if not out:
        return {}

    out["口径说明"] = (
        "美股 ETF/ADR 为新浪外盘报价，与 A 股交易时段不完全重叠；"
        "本字段仅罗列同日可核对数值与方向，不作隔夜因果定论。"
    )
    return out


__all__ = ["build_cross_market_hints"]
