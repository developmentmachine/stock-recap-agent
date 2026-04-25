"""明日观察名单（forward_watchlist）：程序化交集生成可执行候选。

这不是让 LLM 凭语感推荐，而是用『多源信号交集』筛出今日已经被多重证据指认的个股，
并附带可追溯的 reason chain，供模型在第二大类末尾或独立段落引用。

信号来源（每命中一项加 1 分，并记入 reasons）：
  S1 出现在 limit_up_pool.高位连板（连板≥2）
  S2 出现在 limit_up_pool.封板金额前列
  S3 出现在 individual_fund_flow.净流入前列（主力净流入正且占比≥5%）
  S4 出现在 lhb.净买入前列 且 净买额>0（机构席位优先）
  S5 所属行业 / 所属题材 命中 sector_performance.涨幅前10 或 sector_fund_flow.行业.净流入前列
  S6 出现在 continuity.接力梯队_top（昨涨停今再封板/续涨）

输出 top N 候选，按总分降序；同时给出板块层面的"明日延续观察主线"。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple


def _safe_str(v: Any) -> str:
    return str(v or "").strip()


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _strong_sector_names(sector_perf: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for key in ("涨幅前10",):
        for it in (sector_perf.get(key) or []):
            n = _safe_str(it.get("板块名称"))
            if n:
                out.add(n)
    concept = sector_perf.get("概念") or {}
    for it in (concept.get("涨幅前10") or []):
        n = _safe_str(it.get("板块名称"))
        if n:
            out.add(n)
    return out


def _capital_clusters(sector_fund_flow: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for layer in ("行业", "概念"):
        for it in ((sector_fund_flow.get(layer) or {}).get("净流入前列") or []):
            n = _safe_str(it.get("板块名称"))
            if n:
                out.add(n)
    return out


def build_forward_watchlist(
    *,
    limit_up_pool: Dict[str, Any],
    individual_fund_flow: Dict[str, Any],
    lhb: Dict[str, Any],
    sector_performance: Dict[str, Any],
    sector_fund_flow: Dict[str, Any],
    continuity: Dict[str, Any],
    top: int = 6,
) -> Dict[str, Any]:
    candidates: Dict[str, Dict[str, Any]] = {}

    def _ensure(code: str, name: str, sector: str = "") -> Dict[str, Any]:
        key = code or name
        if not key:
            return {}
        slot = candidates.get(key)
        if slot is None:
            slot = {
                "代码": code,
                "名称": name,
                "所属": sector,
                "score": 0,
                "reasons": [],
                "信号集合": set(),
            }
            candidates[key] = slot
        else:
            if sector and not slot.get("所属"):
                slot["所属"] = sector
            if name and not slot.get("名称"):
                slot["名称"] = name
        return slot

    def _add(slot: Dict[str, Any], tag: str, reason: str) -> None:
        if not slot or tag in slot["信号集合"]:
            return
        slot["信号集合"].add(tag)
        slot["score"] += 1
        slot["reasons"].append(reason)

    strong_sectors = _strong_sector_names(sector_performance or {})
    capital_sectors = _capital_clusters(sector_fund_flow or {})

    # S1 高位连板
    for it in (limit_up_pool.get("高位连板") or []):
        s = _ensure(_safe_str(it.get("代码")), _safe_str(it.get("名称")), _safe_str(it.get("所属行业")))
        tier = it.get("涨停统计") or (f"{it.get('连板数')}板" if it.get("连板数") else "连板")
        _add(s, "S1", f"连板梯队（{tier}，所属{it.get('所属行业') or '—'}）")

    # S2 封板金额前列
    for it in (limit_up_pool.get("封板金额前列") or [])[:6]:
        s = _ensure(_safe_str(it.get("代码")), _safe_str(it.get("名称")), _safe_str(it.get("所属行业")))
        _add(s, "S2", f"封单 {it.get('封板金额(亿)')} 亿")

    # S3 个股资金抢筹
    for it in (individual_fund_flow.get("净流入前列") or [])[:8]:
        ratio = _safe_float(it.get("净占比(%)"))
        netbuy = _safe_float(it.get("主力净流入(亿)"))
        if netbuy <= 0:
            continue
        s = _ensure(_safe_str(it.get("代码")), _safe_str(it.get("股票名称")))
        _add(s, "S3", f"主力净买 {netbuy} 亿（占比 {ratio}%）")

    # S4 龙虎榜净买入
    for it in (lhb.get("净买入前列") or [])[:8]:
        netbuy = _safe_float(it.get("净买额(亿)"))
        if netbuy <= 0:
            continue
        s = _ensure(_safe_str(it.get("代码")), _safe_str(it.get("名称")))
        reason = f"龙虎榜净买 {netbuy} 亿"
        if "机构" in (it.get("上榜原因") or "") or "机构" in (it.get("解读") or ""):
            reason += "（机构席位）"
        _add(s, "S4", reason)

    # S5 板块加成
    for slot in candidates.values():
        sector = slot.get("所属") or ""
        if not sector:
            continue
        if sector in strong_sectors:
            _add(slot, "S5_strong", f"所属板块{sector}今日强势")
        if sector in capital_sectors:
            _add(slot, "S5_capital", f"所属板块{sector}主力净流入")

    # S6 连板接力
    for it in (continuity.get("接力梯队_top") or []):
        s = _ensure(_safe_str(it.get("代码")), _safe_str(it.get("名称")), _safe_str(it.get("所属行业")))
        pct = it.get("今日涨跌幅")
        ybc = it.get("昨日连板数") or 0
        tag = f"昨涨停今接力 +{pct}%（昨{ybc}板）" if pct is not None else f"昨涨停今再封板（昨{ybc}板）"
        _add(s, "S6", tag)

    # 排序：score 降序，再按是否含 S1（连板）/ S4（龙虎榜）做权重
    def _rank_key(slot: Dict[str, Any]) -> Tuple[int, int, int]:
        sigs = slot["信号集合"]
        return (slot["score"], int("S1" in sigs) + int("S4" in sigs), int("S6" in sigs))

    ranked = sorted(candidates.values(), key=_rank_key, reverse=True)
    # 至少要有 2 个独立信号才算"高确信"候选
    high_conf = [s for s in ranked if s["score"] >= 2][:top]

    # 板块层面：哪些板块"涨幅强 + 资金抢筹"双重确认
    sector_double_confirm = sorted(strong_sectors & capital_sectors)

    if not high_conf and not sector_double_confirm:
        return {}

    return {
        "高确信候选": [
            {
                "代码": s["代码"],
                "名称": s["名称"],
                "所属": s["所属"],
                "score": s["score"],
                "reasons": s["reasons"],
            }
            for s in high_conf
        ],
        "板块_涨幅与资金双重确认": sector_double_confirm,
        "口径": (
            "score = 命中 S1(高连板)/S2(大封单)/S3(主力净买)/S4(龙虎榜净买)/"
            "S5(所属板块强势/资金抢筹)/S6(昨涨停今接力) 的独立信号数；score≥2 进入高确信候选"
        ),
    }


__all__ = ["build_forward_watchlist"]
