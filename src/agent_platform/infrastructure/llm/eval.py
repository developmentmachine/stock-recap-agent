"""自动评测与回测评分层。

- auto_eval: 对生成的 Recap 做结构性自检
- compute_backtest: 对昨日策略预测与今日实际行情做命中率评分
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from agent_platform.domain.models import (
    BacktestResult,
    Features,
    MarketSnapshot,
    Recap,
    RecapDaily,
    RecapStrategy,
)
from agent_platform.presentation.render.renderers import (
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
    *,
    scoring_impl: Optional[str] = None,
) -> BacktestResult:
    """对比策略预测与实际板块表现；评分器由 ``RECAP_BACKTEST_SCORING`` 或参数选择。"""
    from agent_platform.application.backtest.registry import resolve_backtest_strategy
    from agent_platform.config.settings import get_settings

    name = (scoring_impl or get_settings().backtest_scoring or "keyword_substring").strip().lower()
    strat = resolve_backtest_strategy(name)
    return strat.evaluate(
        strategy_date=strategy_date,
        strategy_recap=strategy_recap,
        actual_date=actual_date,
        actual_snapshot=actual_snapshot,
    )
