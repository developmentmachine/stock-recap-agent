"""回测评分器注册与多实现。"""
from __future__ import annotations

from stock_recap.application.backtest.registry import resolve_backtest_strategy
from stock_recap.domain.models import MarketSnapshot, RecapStrategy


def test_normalized_overlap_handles_compound_mainline():
    strat = resolve_backtest_strategy("normalized_overlap")
    sr = RecapStrategy(
        mode="strategy",
        date="2025-01-02",
        mainline_focus=[
            "电力｜+1.6%｜主力净流入 12.4 亿｜延续低位红利防御",
            "虚构板块XYZ",
        ],
        risk_warnings=["流动性"],
        trading_logic=["逻辑一", "逻辑二"],
    )
    snap = MarketSnapshot(
        asof="2025-01-03T08:00:00+00:00",
        provider="mock",
        date="2025-01-03",
        sector_performance={
            "涨幅前10": [
                {"板块名称": "电力"},
                {"板块名称": "煤炭"},
            ]
        },
    )
    out = strat.evaluate(
        strategy_date="2025-01-02",
        strategy_recap=sr,
        actual_date="2025-01-03",
        actual_snapshot=snap,
    )
    assert out.scoring_impl == "normalized_overlap"
    assert out.hit_count == 1
    assert 0 < out.hit_rate < 1


def test_keyword_substring_backward_compat():
    strat = resolve_backtest_strategy("keyword_substring")
    sr = RecapStrategy(
        mode="strategy",
        date="2025-01-02",
        mainline_focus=["电力"],
        risk_warnings=["流动性"],
        trading_logic=["逻辑一", "逻辑二"],
    )
    snap = MarketSnapshot(
        asof="2025-01-03T08:00:00+00:00",
        provider="mock",
        date="2025-01-03",
        sector_performance={"涨幅前10": [{"板块名称": "电力行业"}]},
    )
    out = strat.evaluate(
        strategy_date="2025-01-02",
        strategy_recap=sr,
        actual_date="2025-01-03",
        actual_snapshot=snap,
    )
    assert out.hit_count == 1
    assert out.scoring_impl == "keyword_substring"
