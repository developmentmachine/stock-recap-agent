"""风格因子矩阵：基于现有 indices 计算 大小盘 / 成长价值 / 微盘 spread。

资深视角：单看指数涨跌没用，要看『风格剪刀差』——今天到底是大票还是小票占优、
价值还是成长占优。这给『风格切换』提供量化抓手。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _pct(indices: Dict[str, Any], name: str) -> Optional[float]:
    item = indices.get(name) if isinstance(indices, dict) else None
    if not isinstance(item, dict):
        return None
    v = item.get("涨跌幅")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _classify(spread: Optional[float], pos_label: str, neg_label: str, threshold: float = 0.3) -> str:
    if spread is None:
        return ""
    if spread >= threshold:
        return pos_label
    if spread <= -threshold:
        return neg_label
    return "持平"


def build_style_matrix(indices: Dict[str, Any]) -> Dict[str, Any]:
    """基于已收集的指数计算风格 spread；缺失字段则自动跳过。"""
    if not isinstance(indices, dict) or not indices:
        return {}

    sh50 = _pct(indices, "上证50")
    hs300 = _pct(indices, "沪深300")
    csi1000 = _pct(indices, "中证1000")
    gz2000 = _pct(indices, "国证2000")
    cyb = _pct(indices, "创业板指")
    cycz = _pct(indices, "创成长")
    star50 = _pct(indices, "科创50")

    items: List[Dict[str, Any]] = []
    summary_lines: List[str] = []

    # 1) 大小盘
    if sh50 is not None and csi1000 is not None:
        spread = round(sh50 - csi1000, 2)
        verdict = _classify(spread, "大盘占优", "小盘占优")
        items.append({
            "维度": "大小盘",
            "对比": "上证50 vs 中证1000",
            "上证50涨跌幅": sh50,
            "中证1000涨跌幅": csi1000,
            "spread": spread,
            "判定": verdict,
        })
        if verdict:
            summary_lines.append(f"大小盘：{verdict}（spread {spread:+.2f}pct）")
    elif hs300 is not None and csi1000 is not None:
        spread = round(hs300 - csi1000, 2)
        verdict = _classify(spread, "大盘占优", "小盘占优")
        items.append({
            "维度": "大小盘",
            "对比": "沪深300 vs 中证1000",
            "沪深300涨跌幅": hs300,
            "中证1000涨跌幅": csi1000,
            "spread": spread,
            "判定": verdict,
        })
        if verdict:
            summary_lines.append(f"大小盘：{verdict}（spread {spread:+.2f}pct）")

    # 2) 微盘 vs 大盘 — 国证2000 是真小微盘最常用代理
    if gz2000 is not None and (sh50 is not None or hs300 is not None):
        big = sh50 if sh50 is not None else hs300
        big_label = "上证50" if sh50 is not None else "沪深300"
        spread = round(gz2000 - big, 2)
        verdict = _classify(spread, "微盘补涨/活跃", "微盘退潮")
        items.append({
            "维度": "微盘vs大盘",
            "对比": f"国证2000 vs {big_label}",
            "国证2000涨跌幅": gz2000,
            f"{big_label}涨跌幅": big,
            "spread": spread,
            "判定": verdict,
        })
        if verdict:
            summary_lines.append(f"微盘：{verdict}（国证2000 {gz2000:+.2f}%, {big_label} {big:+.2f}%）")

    # 3) 成长 vs 价值
    if cycz is not None and sh50 is not None:
        spread = round(cycz - sh50, 2)
        verdict = _classify(spread, "成长占优", "价值占优")
        items.append({
            "维度": "成长vs价值",
            "对比": "创成长 vs 上证50",
            "创成长涨跌幅": cycz,
            "上证50涨跌幅": sh50,
            "spread": spread,
            "判定": verdict,
        })
        if verdict:
            summary_lines.append(f"成长vs价值：{verdict}（spread {spread:+.2f}pct）")
    elif cyb is not None and hs300 is not None:
        spread = round(cyb - hs300, 2)
        verdict = _classify(spread, "成长占优", "价值占优")
        items.append({
            "维度": "成长vs价值",
            "对比": "创业板指 vs 沪深300",
            "创业板指涨跌幅": cyb,
            "沪深300涨跌幅": hs300,
            "spread": spread,
            "判定": verdict,
        })
        if verdict:
            summary_lines.append(f"成长vs价值：{verdict}（spread {spread:+.2f}pct）")

    # 4) 硬科技（科创50） vs 沪深300
    if star50 is not None and hs300 is not None:
        spread = round(star50 - hs300, 2)
        verdict = _classify(spread, "硬科技走强", "硬科技退潮")
        items.append({
            "维度": "硬科技vs宽基",
            "对比": "科创50 vs 沪深300",
            "科创50涨跌幅": star50,
            "沪深300涨跌幅": hs300,
            "spread": spread,
            "判定": verdict,
        })
        if verdict:
            summary_lines.append(f"硬科技：{verdict}（spread {spread:+.2f}pct）")

    if not items:
        return {}

    return {
        "矩阵": items,
        "摘要": "；".join(summary_lines) if summary_lines else "",
        "口径": "spread = 前者涨跌幅 - 后者涨跌幅，单位百分点；阈值 0.3pct 视为非持平",
    }


__all__ = ["build_style_matrix"]
