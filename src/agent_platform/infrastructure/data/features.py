"""特征工程：从 MarketSnapshot 计算量化特征并生成文本摘要，注入 LLM prompt。"""
from __future__ import annotations

import json
from typing import Any, Dict

from agent_platform.domain.models import Features, MarketSnapshot


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

    limit_up = _safe_float(sentiment.get("涨停家数", 0))
    limit_down = _safe_float(sentiment.get("跌停家数", 0))
    volume_level = _safe_float(
        sentiment.get("两市成交额(亿)") or sentiment.get("两市成交额", 0)
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
        concept = sp.get("概念") or {}
        if concept.get("涨幅前10") or concept.get("跌幅前10"):
            sector_rotation["concept_top_bottom"] = {
                "涨幅前10": concept.get("涨幅前10", []),
                "跌幅前10": concept.get("跌幅前10", []),
            }
        if sp.get("相对表现"):
            sector_rotation["vs_benchmark"] = sp["相对表现"]

    # 宏观信号
    macro_signal: Dict[str, Any] = {}
    if snapshot.us_market:
        macro_signal["us_market"] = snapshot.us_market
    if snapshot.commodities:
        macro_signal["commodities"] = snapshot.commodities

    # 板块资金流摘要
    sff = snapshot.sector_fund_flow or {}
    if sff:
        sector_rotation["fund_flow"] = sff
    # 涨停池摘要（题材聚合 + 高位连板）
    lup = snapshot.limit_up_pool or {}
    if lup:
        sector_rotation["limit_up_pool"] = {
            "涨停总数": lup.get("涨停总数"),
            "题材聚合": lup.get("题材聚合"),
            "高位连板": lup.get("高位连板"),
        }
    # 连续性 + 风格 + 龙虎榜 + 明日观察 进文本摘要
    cont = snapshot.continuity or {}
    if cont:
        sector_rotation["continuity"] = {
            "接力涨停率(%)": cont.get("接力涨停率(%)"),
            "高位连板接力": cont.get("高位连板接力"),
            "高位连板样本": cont.get("高位连板样本"),
            "接力梯队_top": (cont.get("接力梯队_top") or [])[:4],
            "退潮个股_top": (cont.get("退潮个股_top") or [])[:3],
        }
    style = snapshot.style_matrix or {}
    if style.get("矩阵"):
        sector_rotation["style_matrix"] = {
            "矩阵": style["矩阵"],
            "摘要": style.get("摘要"),
        }
    lhb = snapshot.lhb or {}
    if lhb.get("净买入前列") or lhb.get("净卖出前列"):
        sector_rotation["lhb"] = {
            "净买入前列": (lhb.get("净买入前列") or [])[:5],
            "净卖出前列": (lhb.get("净卖出前列") or [])[:3],
        }
    fwl = snapshot.forward_watchlist or {}
    if fwl.get("高确信候选") or fwl.get("板块_涨幅与资金双重确认"):
        sector_rotation["forward_watchlist"] = fwl
    leaders = snapshot.sector_leaders or {}
    if leaders.get("强势行业龙头矩阵"):
        sector_rotation["sector_leaders"] = leaders
    s5d = snapshot.sector_5d_strength or {}
    if s5d.get("样本"):
        sector_rotation["sector_5d_strength"] = s5d
    liq = snapshot.liquidity or {}
    if liq:
        macro_signal["liquidity"] = liq

    f = Features(
        market_strength=limit_up - limit_down,
        volume_level=volume_level,
        northbound_signal=None,
        sector_rotation=sector_rotation,
        macro_signal=macro_signal,
    )

    ms = f.market_strength if f.market_strength is not None else 0.0
    up_count = int(sentiment.get("上涨家数", 0))
    down_count = int(sentiment.get("下跌家数", 0))
    flat_count = int(sentiment.get("平盘家数", 0))

    f.index_view = (
        f"指数/量能：成交额约 {volume_level:.0f} 亿；"
        f"上涨 {up_count} 家/下跌 {down_count} 家/平盘 {flat_count} 家；"
        f"情绪强度(涨停-跌停)={ms:.0f}"
    )
    f.sector_view = f"板块轮动：{_compact_json(sector_rotation, max_len=4500)}"

    hot_list = sentiment.get("热度榜前列") or []
    hot_hint = ""
    if isinstance(hot_list, list) and hot_list:
        parts = []
        for it in hot_list[:6]:
            if isinstance(it, dict) and it.get("股票名称") is not None:
                pct = it.get("涨跌幅", "")
                try:
                    pct_s = f"{float(pct):+.2f}%"
                except (TypeError, ValueError):
                    pct_s = str(pct)
                parts.append(f"{it['股票名称']}({pct_s})")
        if parts:
            hot_hint = "热度榜参考：" + "、".join(parts) + "。"

    mf = sentiment.get("大盘主力资金流") or {}
    mf_hint = ""
    if isinstance(mf, dict) and mf.get("主力净流入_亿") is not None:
        mf_hint = f"大盘主力净流入约 {mf['主力净流入_亿']:.2f} 亿（{mf.get('数据日期', '')}）；"

    f.sentiment_view = (
        f"资金（不含北向）：{mf_hint}"
        f"涨停={int(limit_up)}家，跌停={int(limit_down)}家。"
        f"{hot_hint}"
    )
    f.macro_view = f"宏观：{_compact_json(macro_signal)}"

    return f
