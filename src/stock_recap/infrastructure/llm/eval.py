"""自动评测与回测评分层。

- auto_eval: 对生成的 Recap 做结构性自检
- compute_backtest: 对昨日策略预测与今日实际行情做命中率评分
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

from stock_recap.domain.models import (
    BacktestResult,
    Features,
    MarketSnapshot,
    Recap,
    RecapDaily,
    RecapStrategy,
)
from stock_recap.presentation.render.renderers import (
    daily_headline_and_bullet_matrix,
    is_benchmark_bullet_line,
    render_markdown,
)


# ─── 自动评测 ─────────────────────────────────────────────────────────────────

_EMOJI_RE = re.compile(
    "[\U0001F600-\U0001F64F"  # 表情符号
    "\U0001F300-\U0001F5FF"  # 符号和图标
    "\U0001F680-\U0001F6FF"  # 交通和地图
    "\U0001F700-\U0001F77F"  # 炼金符号
    "\U0001F780-\U0001F7FF"  # 几何图形扩展
    "\U0001F800-\U0001F8FF"  # 补充箭头-C
    "\U0001F900-\U0001F9FF"  # 补充符号和图形
    "\U0001FA00-\U0001FA6F"  # 象棋符号
    "\U0001FA70-\U0001FAFF"  # 符号和图标扩展-A
    "\U00002702-\U000027B0"  # 杂项符号（Dingbats）
    "\U00002600-\U000026FF"  # 杂项符号
    "\U0001F1E0-\U0001F1FF]",  # 区域指示符（旗帜）
    re.UNICODE,
)


def _has_emoji(text: str) -> bool:
    return bool(_EMOJI_RE.search(text))


def auto_eval(
    recap: Optional[Recap],
    snapshot: MarketSnapshot,
    features: Features,
) -> Dict[str, Any]:
    if recap is None:
        return {"ok": False, "reason": "no_recap"}

    checks: Dict[str, Any] = {"ok": True, "checks": []}

    def add(name: str, ok: bool, detail: Any = None) -> None:
        checks["checks"].append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            checks["ok"] = False

    rendered = render_markdown(recap)

    # ── 通用检查 ────────────────────────────────────────────────────────────────
    add("no_emoji", not _has_emoji(rendered), None)
    add("has_disclaimer", recap.disclaimer != "", None)

    # ── Daily 检查 ──────────────────────────────────────────────────────────────
    if recap.mode == "daily":
        assert isinstance(recap, RecapDaily)
        add("sections_count==3", len(recap.sections) == 3, len(recap.sections))
        _, bmat = daily_headline_and_bullet_matrix(recap)
        s0_raw = recap.sections[0].bullets
        s0_first = str(s0_raw[0]).strip() if s0_raw else ""
        bench_like = is_benchmark_bullet_line(s0_first) if s0_raw else False
        min_sec0 = 3 if bench_like else 2
        sec0_ok = len(s0_raw) >= min_sec0
        others_ok = all(len(s.bullets) >= 2 for s in recap.sections[1:])
        add(
            "bullets_min_len",
            sec0_ok and others_ok and all(len(row) >= 2 for row in bmat),
            {
                "section0_raw": len(s0_raw),
                "section0_rendered": len(bmat[0]),
                "others": [len(s.bullets) for s in recap.sections[1:]],
            },
        )
        add(
            "conclusions_nonempty",
            all(s.core_conclusion.strip() for s in recap.sections),
            None,
        )
        risk_len = len(recap.risks or [])

    # ── Strategy 检查 ───────────────────────────────────────────────────────────
    else:
        assert isinstance(recap, RecapStrategy)
        add("mainline_nonempty", len(recap.mainline_focus) >= 1, len(recap.mainline_focus))
        add("risk_nonempty", len(recap.risk_warnings) >= 1, len(recap.risk_warnings))
        add("logic_nonempty", len(recap.trading_logic) >= 2, len(recap.trading_logic))
        risk_len = len(recap.risk_warnings or [])

    # ── 弱情绪下必须有风险提示 ─────────────────────────────────────────────────
    strength = features.market_strength if features.market_strength is not None else 0
    add(
        "risk_present_when_weak_sentiment",
        (strength >= 10) or (risk_len >= 1),
        {"market_strength": strength, "risk_len": risk_len},
    )

    # ── 数据来源可追溯 ──────────────────────────────────────────────────────────
    add("snapshot_has_sources", len(snapshot.sources) > 0, len(snapshot.sources))

    return checks


# ─── 回测评分 ─────────────────────────────────────────────────────────────────

def compute_backtest(
    strategy_date: str,
    strategy_recap: RecapStrategy,
    actual_date: str,
    actual_snapshot: MarketSnapshot,
) -> BacktestResult:
    """对比策略预测的主线方向与实际板块涨幅，计算命中率。"""
    predicted = strategy_recap.mainline_focus  # 如 ["新能源", "半导体"]

    # 从 actual_snapshot 提取实际涨幅前 10 板块名称
    sp = actual_snapshot.sector_performance or {}
    top_list: List[str] = []
    for item in sp.get("涨幅前10", []):
        name = item.get("板块名称") or item.get("name") or ""
        if name:
            top_list.append(name)

    if not top_list:
        return BacktestResult(
            strategy_date=strategy_date,
            actual_date=actual_date,
            predicted_sectors=predicted,
            actual_top_sectors=[],
            hit_count=0,
            hit_rate=0.0,
            detail="实际板块数据不足，无法回测",
        )

    # 简单关键词匹配（预测词出现在实际板块名中）
    hit_count = 0
    hit_detail: List[str] = []
    for pred in predicted:
        # 提取核心词（去掉"板块"/"行业"等后缀）
        core = pred.replace("板块", "").replace("行业", "").replace("概念", "").strip()
        matched = [act for act in top_list if core in act or act in core]
        if matched:
            hit_count += 1
            hit_detail.append(f"✓ {pred} → {matched[0]}")
        else:
            hit_detail.append(f"✗ {pred}")

    hit_rate = hit_count / len(predicted) if predicted else 0.0
    detail = f"预测 {len(predicted)} 个方向，命中 {hit_count} 个（{hit_rate:.0%}）\n" + "\n".join(hit_detail)

    return BacktestResult(
        strategy_date=strategy_date,
        actual_date=actual_date,
        predicted_sectors=predicted,
        actual_top_sectors=top_list,
        hit_count=hit_count,
        hit_rate=round(hit_rate, 3),
        detail=detail,
    )
