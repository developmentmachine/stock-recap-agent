"""数据源解析与跨市场 ADR 镜像的单元测试（不真发请求）。"""
from __future__ import annotations

from unittest.mock import patch

from agent_platform.infrastructure.data.sources.cross_market import build_cross_market_hints
from agent_platform.infrastructure.data.sources.individual_fund_flow import fetch_individual_fund_flow
from agent_platform.infrastructure.data.sources.limit_up_pool import fetch_limit_up_pool
from agent_platform.infrastructure.data.sources.sector import EastmoneyPush2BoardsSource
from agent_platform.infrastructure.data.sources.sector_fund_flow import fetch_sector_fund_flow
from agent_platform.infrastructure.data.sources.us_movers import _parse_lines


def test_sector_fund_flow_normalizes(monkeypatch):
    fake_rows_in = [
        {"f12": "BK1033", "f14": "电池", "f3": 1.2, "f62": 1.5e9, "f184": 3.4},
        {"f12": "BK0480", "f14": "半导体", "f3": 1.6, "f62": 8.0e8, "f184": 2.6},
    ]
    fake_rows_out = [
        {"f12": "BK0479", "f14": "白酒", "f3": -0.8, "f62": -5.0e8, "f184": -2.1},
    ]

    def _fake(fs, **kw):
        if fs == "m:90+t:2" and kw.get("po") == "1":
            return fake_rows_in
        if fs == "m:90+t:2" and kw.get("po") == "0":
            return fake_rows_out
        return []

    with patch("agent_platform.infrastructure.data.sources.sector_fund_flow.push2_clist", side_effect=_fake):
        out = fetch_sector_fund_flow(top=5)
    assert "行业" in out
    inflow = out["行业"]["净流入前列"]
    assert inflow[0]["板块名称"] == "电池"
    assert inflow[0]["主力净流入(亿)"] == 15.0
    outflow = out["行业"]["净流出前列"]
    assert outflow[0]["主力净流入(亿)"] == -5.0
    # 概念分支无数据，整段不应出现
    assert "概念" not in out


def test_limit_up_pool_aggregates_themes_and_high_tier():
    fake_pool = [
        {"c": "300001", "n": "甲", "lbc": 5, "zttj": {"days": 5, "ct": 5}, "hybk": "半导体", "fund": 1.2e8, "amount": 8e8, "zbc": 0},
        {"c": "300002", "n": "乙", "lbc": 3, "zttj": {"days": 3, "ct": 3}, "hybk": "半导体", "fund": 0.5e8, "amount": 4e8, "zbc": 0},
        {"c": "002001", "n": "丙", "lbc": 1, "zttj": {"days": 1, "ct": 1}, "hybk": "电池", "fund": 0.3e8, "amount": 2e8, "zbc": 1},
    ]
    with patch("agent_platform.infrastructure.data.sources.limit_up_pool.push2ex_zt_pool", return_value=fake_pool):
        out = fetch_limit_up_pool("20260423")
    assert out["涨停总数"] == 3
    assert out["连板梯队_最高"] == 5
    themes = out["题材聚合"]
    assert themes[0]["题材"] == "半导体"
    assert themes[0]["最高连板"] == 5
    high = out["高位连板"]
    assert high and high[0]["名称"] == "甲" and high[0]["连板数"] == 5


def test_us_movers_parser_handles_sina_payload():
    payload = (
        'var hq_str_gb_aapl="苹果,273.43,0.10,2026-04-24";\n'
        'var hq_str_gb_baba="阿里巴巴,131.70,-3.46,2026-04-24";\n'
    )
    parsed = _parse_lines(payload)
    assert parsed["gb_aapl"] == (273.43, 0.10)
    assert parsed["gb_baba"][1] == -3.46


def test_eastmoney_push2_boards_fallback_returns_industry_and_concept():
    industry_top = [
        {"f12": "BK0480", "f14": "半导体", "f3": 2.1, "f8": 4.5, "f128": "寒武纪", "f136": 9.8},
        {"f12": "BK1033", "f14": "电池", "f3": 1.5, "f8": 3.0, "f128": "宁德时代", "f136": 6.2},
    ]
    industry_bot = [
        {"f12": "BK0479", "f14": "白酒", "f3": -1.2, "f8": 1.0, "f128": "贵州茅台", "f136": -2.1},
    ]
    concept_top = [
        {"f12": "BK1234", "f14": "固态电池", "f3": 3.0, "f8": 7.0, "f128": "MockA", "f136": 8.0},
    ]

    def _fake(fs, **kw):
        po = kw.get("po")
        if fs == "m:90+t:2" and po == "1":
            return industry_top
        if fs == "m:90+t:2" and po == "0":
            return industry_bot
        if fs == "m:90+t:3" and po == "1":
            return concept_top
        return []

    with patch("agent_platform.infrastructure.data.sources.sector.push2_clist", side_effect=_fake):
        out = EastmoneyPush2BoardsSource().fetch()

    assert out["涨幅前10"][0]["板块名称"] == "半导体"
    assert out["涨幅前10"][0]["领涨股票"] == "寒武纪"
    assert out["涨幅前10"][0]["领涨股票-涨跌幅"] == 9.8
    assert out["跌幅前10"][0]["板块名称"] == "白酒"
    assert out["概念"]["涨幅前10"][0]["板块名称"] == "固态电池"


def test_individual_fund_flow_normalizes(monkeypatch):
    inflow = [
        {"f12": "300011", "f14": "Mock甲", "f3": 5.6, "f62": 4.8e8, "f184": 12.4},
    ]
    outflow = [
        {"f12": "601001", "f14": "Mock乙", "f3": -2.4, "f62": -3.0e8, "f184": -7.5},
    ]

    def _fake(fs, **kw):
        if kw.get("po") == "1":
            return inflow
        return outflow

    with patch("agent_platform.infrastructure.data.sources.individual_fund_flow.push2_clist", side_effect=_fake):
        out = fetch_individual_fund_flow(top=5)
    assert out["净流入前列"][0]["股票名称"] == "Mock甲"
    assert out["净流入前列"][0]["主力净流入(亿)"] == 4.8
    assert out["净流出前列"][0]["主力净流入(亿)"] == -3.0


def test_cross_market_hints_includes_adr_mirror():
    sector = {
        "涨幅前10": [{"板块名称": "半导体", "涨跌幅": 1.6}],
        "跌幅前10": [],
    }
    us = {
        "etf参考": {"QQQ": {"涨跌幅(%)": 1.0, "名称": "纳指ETF", "收盘价": 1.0}},
        "movers": {
            "中概股_adr": [
                {"代码": "BABA", "名称": "阿里巴巴", "涨跌幅(%)": 1.5},
                {"代码": "PDD", "名称": "拼多多", "涨跌幅(%)": -0.4},
            ],
        },
    }
    cm = build_cross_market_hints(sector, us)
    assert "paired_observations" in cm
    mirror = cm["adr_镜像"]
    assert mirror["样本数"] == 2
    assert mirror["均值涨跌幅(%)"] == 0.55
    assert "阿里巴巴(+1.50%)" in mirror["代表"]
