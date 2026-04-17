"""跨市场 hints 与板块超额工具函数。"""
from stock_recap.infrastructure.data.sources.cross_market import build_cross_market_hints
from stock_recap.infrastructure.data.sources.sector import apply_benchmark_excess


def test_apply_benchmark_excess_adds_relative_block():
    sector = {
        "涨幅前10": [{"板块名称": "半导体", "涨跌幅": 2.0}, {"板块名称": "银行", "涨跌幅": 0.5}],
        "跌幅前10": [],
    }
    indices = {"沪深300": {"涨跌幅": 0.5}}
    out = apply_benchmark_excess(sector, indices)
    rel = out.get("相对表现") or {}
    assert rel.get("基准") == "沪深300"
    assert rel.get("基准涨跌幅") == 0.5
    rows = rel.get("行业涨幅前10含超额") or []
    assert rows[0]["超额涨跌幅_相对沪深300"] == 1.5


def test_build_cross_market_hints_pairs_tech():
    sector = {
        "涨幅前10": [{"板块名称": "半导体", "涨跌幅": 1.8}],
        "跌幅前10": [],
    }
    us = {
        "etf参考": {
            "QQQ": {"涨跌幅(%)": 1.2, "名称": "纳指ETF", "收盘价": 1.0},
            "XLK": {"涨跌幅(%)": 1.5, "名称": "科技ETF", "收盘价": 1.0},
        }
    }
    cm = build_cross_market_hints(sector, us)
    obs = cm.get("paired_observations") or []
    assert obs, "expected at least one paired observation"
    assert obs[0]["主题"] == "科技成长链"
    assert "数值同向" in obs[0]


def test_build_cross_market_hints_empty_without_etf():
    sector = {"涨幅前10": [{"板块名称": "半导体", "涨跌幅": 1.0}], "跌幅前10": []}
    assert build_cross_market_hints(sector, {}) == {}
    assert build_cross_market_hints(sector, {"道琼斯": {"涨跌幅(%)": 1.0}}) == {}
