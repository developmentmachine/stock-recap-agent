"""资深信号层：connectivity / style / lhb / forward_watchlist 的单元测试。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from agent_platform.infrastructure.data.sources.continuity import fetch_continuity
from agent_platform.infrastructure.data.sources.forward_watchlist import build_forward_watchlist
from agent_platform.infrastructure.data.sources.lhb import _classify_seat, fetch_lhb
from agent_platform.infrastructure.data.sources.style_factors import build_style_matrix


def test_style_matrix_identifies_small_cap_and_growth():
    indices = {
        "上证50": {"涨跌幅": -0.6},
        "沪深300": {"涨跌幅": -0.4},
        "中证1000": {"涨跌幅": 1.5},
        "国证2000": {"涨跌幅": 2.4},
        "创业板指": {"涨跌幅": 1.1},
        "创成长": {"涨跌幅": 2.1},
        "科创50": {"涨跌幅": 1.8},
    }
    out = build_style_matrix(indices)
    dims = {row["维度"]: row for row in out["矩阵"]}
    assert dims["大小盘"]["spread"] < 0
    assert dims["大小盘"]["判定"] == "小盘占优"
    assert dims["微盘vs大盘"]["判定"] == "微盘补涨/活跃"
    assert dims["成长vs价值"]["判定"] == "成长占优"
    assert dims["硬科技vs宽基"]["判定"] == "硬科技走强"
    assert "小盘占优" in out["摘要"]


def test_continuity_summarizes_akshare_pool(monkeypatch):
    df = pd.DataFrame(
        [
            {"代码": "300001", "名称": "甲", "涨跌幅": 9.99, "昨日连板数": 4, "涨停统计": "5天5板", "所属行业": "半导体"},
            {"代码": "002001", "名称": "乙", "涨跌幅": -3.6, "昨日连板数": 1, "涨停统计": "1天1板", "所属行业": "白酒"},
            {"代码": "603001", "名称": "丙", "涨跌幅": 6.2, "昨日连板数": 2, "涨停统计": "3天2板", "所属行业": "电池"},
        ]
    )
    fake_ak = SimpleNamespace(stock_zt_pool_previous_em=lambda date: df)
    out = fetch_continuity("20260424", ak=fake_ak)
    assert out["昨日涨停样本数"] == 3
    assert out["今日接力涨停"] == 1  # 仅 ≥9.5 的甲算
    assert out["接力涨停率(%)"] == round(1 / 3 * 100, 1)
    assert out["高位连板样本"] == 2  # 甲 + 丙
    assert out["接力梯队_top"][0]["名称"] == "甲"
    assert out["退潮个股_top"][0]["名称"] == "乙"
    assert "ak.stock_zt_pool_previous_em" in out["数据口径"]


def test_lhb_classify_seat():
    assert _classify_seat("机构专用") == "机构"
    assert _classify_seat("中信证券上海溧阳路营业部") == "知名游资"
    assert _classify_seat("某不知名营业部") == "其他"


def test_lhb_fetch_with_fake_ak():
    df = pd.DataFrame(
        [
            {"名称": "甲", "代码": "300001", "涨跌幅": 9.99, "龙虎榜净买额": 2.1e8, "上榜原因": "日涨幅偏离值达7%", "解读": "机构席位买入"},
            {"名称": "乙", "代码": "002002", "涨跌幅": -5.6, "龙虎榜净买额": -1.6e8, "上榜原因": "日跌幅偏离值达7%", "解读": "知名游资抛售"},
        ]
    )
    fake_ak = SimpleNamespace(stock_lhb_detail_em=lambda start_date, end_date: df)
    out = fetch_lhb(fake_ak, "20260424")
    assert out["净买入前列"][0]["名称"] == "甲"
    assert out["净买入前列"][0]["净买额(亿)"] == 2.1
    assert out["净卖出前列"][0]["名称"] == "乙"


def test_forward_watchlist_intersects_signals():
    limit_up = {
        "高位连板": [
            {"代码": "300001", "名称": "甲", "连板数": 5, "涨停统计": "5天5板", "所属行业": "半导体"},
        ],
        "封板金额前列": [
            {"代码": "300001", "名称": "甲", "封板金额(亿)": 1.8, "所属行业": "半导体"},
        ],
    }
    individual = {
        "净流入前列": [
            {"股票名称": "甲", "代码": "300001", "主力净流入(亿)": 4.5, "净占比(%)": 12.4},
            {"股票名称": "丁", "代码": "600001", "主力净流入(亿)": 2.0, "净占比(%)": 8.0},
        ]
    }
    lhb = {
        "净买入前列": [
            {"代码": "300001", "名称": "甲", "净买额(亿)": 2.1, "上榜原因": "日涨幅偏离值达7%", "解读": "机构席位买入"}
        ]
    }
    sector_perf = {"涨幅前10": [{"板块名称": "半导体", "涨跌幅": 3.2}]}
    sector_fund = {"行业": {"净流入前列": [{"板块名称": "半导体", "主力净流入(亿)": 18.6}]}}
    continuity = {
        "接力梯队_top": [
            {"代码": "300001", "名称": "甲", "今日涨跌幅": 9.99, "昨日连板数": 4, "所属行业": "半导体"}
        ]
    }
    out = build_forward_watchlist(
        limit_up_pool=limit_up,
        individual_fund_flow=individual,
        lhb=lhb,
        sector_performance=sector_perf,
        sector_fund_flow=sector_fund,
        continuity=continuity,
    )
    assert out["板块_涨幅与资金双重确认"] == ["半导体"]
    top = out["高确信候选"]
    assert top
    leading = top[0]
    assert leading["名称"] == "甲"
    # 命中 S1 / S2 / S3 / S4 / S5_strong / S5_capital / S6 → score ≥ 6
    assert leading["score"] >= 6
    reasons_text = " | ".join(leading["reasons"])
    assert "连板梯队" in reasons_text
    assert "机构席位" in reasons_text
    assert "昨涨停今接力" in reasons_text
