"""特征工程：从 MarketSnapshot 计算量化特征并生成文本摘要，注入 LLM prompt。"""
from __future__ import annotations

import json
from typing import Any, Dict

from stock_recap.models import Features, MarketSnapshot


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _compact_json(obj: Any, max_len: int = 800) -> str:
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return s[:max_len] + "…" if len(s) > max_len else s


def build_features(snapshot: MarketSnapshot) -> Features:
    sentiment = snapshot.market_sentiment or {}
    northbound = snapshot.northbound_flow or {}

    limit_up = _safe_float(sentiment.get("涨停家数", 0))
    limit_down = _safe_float(sentiment.get("跌停家数", 0))
    volume_level = _safe_float(
        sentiment.get("两市成交额(亿)") or sentiment.get("两市成交额", 0)
    )

    # 北向资金：优先从独立字段取，没有则从情绪字段取（兼容旧数据）
    northbound_net = _safe_float(
        northbound.get("净买入(亿)") or sentiment.get("北向资金(亿)", 0)
    )

    # 板块轮动
    sp = snapshot.sector_performance or {}
    if "轮动" in sp:
        sector_rotation: Dict[str, Any] = sp["轮动"]
    else:
        sector_rotation = {
            "top": sp.get("涨幅前10", []),
            "bottom": sp.get("跌幅前10", []),
        }

    # 宏观信号
    macro_signal: Dict[str, Any] = {}
    if snapshot.us_market:
        macro_signal["us_market"] = snapshot.us_market
    if snapshot.commodities:
        macro_signal["commodities"] = snapshot.commodities
    if northbound:
        macro_signal["northbound_flow"] = northbound

    f = Features(
        market_strength=limit_up - limit_down,
        volume_level=volume_level,
        northbound_signal=northbound_net,
        sector_rotation=sector_rotation,
        macro_signal=macro_signal,
    )

    ms = f.market_strength if f.market_strength is not None else 0.0
    up_count = int(sentiment.get("上涨家数", 0))
    down_count = int(sentiment.get("下跌家数", 0))

    # 文字摘要（注入 prompt）
    f.index_view = (
        f"指数/量能：成交额约 {volume_level:.0f} 亿；"
        f"上涨 {up_count} 家/下跌 {down_count} 家；"
        f"情绪强度(涨停-跌停)={ms:.0f}"
    )
    f.sector_view = f"板块轮动：{_compact_json(sector_rotation)}"
    f.sentiment_view = (
        f"资金：北向净买入={northbound_net:.1f}亿；"
        f"涨停={int(limit_up)}家，跌停={int(limit_down)}家"
    )
    f.macro_view = f"宏观：{_compact_json(macro_signal)}"

    return f
