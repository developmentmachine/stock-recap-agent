"""Mock 数据 provider — 确定性随机数据（seed=日期），用于无网络/自测。"""
from __future__ import annotations

import hashlib
import random
from typing import Any, Dict

from stock_recap.domain.models import MarketSnapshot
from stock_recap.infrastructure.data.sources.cross_market import build_cross_market_hints
from stock_recap.infrastructure.data.sources.forward_watchlist import build_forward_watchlist
from stock_recap.infrastructure.data.sources.sector import apply_benchmark_excess
from stock_recap.infrastructure.data.sources.style_factors import build_style_matrix


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def collect_mock(d: str, asof: str) -> MarketSnapshot:
    rng = random.Random(_sha256(d)[:16])
    csi300_pct = round(rng.uniform(-1.2, 1.2), 2)
    indices = {
        "上证指数": {"最新价": 3100 + rng.randint(-30, 30), "涨跌幅": round(rng.uniform(-1.5, 1.5), 2)},
        "深证成指": {"最新价": 12000 + rng.randint(-80, 80), "涨跌幅": round(rng.uniform(-1.8, 1.8), 2)},
        "创业板指": {"最新价": 2500 + rng.randint(-60, 60), "涨跌幅": round(rng.uniform(-2.2, 2.2), 2)},
        "科创50": {"最新价": 1100 + rng.randint(-30, 30), "涨跌幅": round(rng.uniform(-2.0, 2.0), 2)},
        "沪深300": {"最新价": 4700 + rng.randint(-40, 40), "涨跌幅": csi300_pct},
        "上证50": {"最新价": 2900 + rng.randint(-30, 30), "涨跌幅": round(rng.uniform(-1.2, 1.2), 2)},
        "中证1000": {"最新价": 6800 + rng.randint(-60, 60), "涨跌幅": round(rng.uniform(-2.0, 2.0), 2)},
        "国证2000": {"最新价": 8500 + rng.randint(-80, 80), "涨跌幅": round(rng.uniform(-2.5, 2.5), 2)},
        "创成长": {"最新价": 1450 + rng.randint(-25, 25), "涨跌幅": round(rng.uniform(-2.5, 2.5), 2)},
    }
    sentiment = {
        "涨停家数": rng.randint(25, 85),
        "跌停家数": rng.randint(3, 30),
        "两市成交额(亿)": round(rng.uniform(6500, 12500), 0),
        "上涨家数": rng.randint(800, 4200),
        "下跌家数": rng.randint(800, 4200),
        "平盘家数": rng.randint(100, 600),
        "个股资金流": {
            "净流入前列": [
                {"股票名称": "Mock主升甲", "代码": "300011", "涨跌幅": 6.8, "主力净流入(亿)": round(rng.uniform(3, 8), 2), "净占比(%)": 12.4},
                {"股票名称": "Mock主升乙", "代码": "002021", "涨跌幅": 4.2, "主力净流入(亿)": round(rng.uniform(2, 6), 2), "净占比(%)": 9.1},
            ],
            "净流出前列": [
                {"股票名称": "Mock瓦解丙", "代码": "601001", "涨跌幅": -3.4, "主力净流入(亿)": round(rng.uniform(-6, -2), 2), "净占比(%)": -7.5},
            ],
        },
        "大盘主力资金流": {
            "数据日期": d,
            "主力净流入_亿": round(rng.uniform(-500, 500), 2),
            "主力净流入_净占比_%": round(rng.uniform(-2, 2), 2),
            "超大单净流入_亿": round(rng.uniform(-200, 200), 2),
            "大单净流入_亿": round(rng.uniform(-200, 200), 2),
            "中单净流入_亿": round(rng.uniform(-150, 150), 2),
            "小单净流入_亿": round(rng.uniform(-150, 150), 2),
            "数据来源": "mock",
        },
        "热度榜前列": [
            {"股票名称": "Mock龙头甲", "涨跌幅": round(rng.uniform(2, 8), 2), "代码": "SH600001"},
            {"股票名称": "Mock龙头乙", "涨跌幅": round(rng.uniform(-6, -1), 2), "代码": "SZ000001"},
        ],
        "热度榜数据来源": "mock",
    }
    northbound: Dict[str, Any] = {}
    sectors = {k: round(rng.uniform(-3.5, 3.5), 2) for k in ["新能源", "半导体", "人工智能", "医药", "军工"]}
    concepts = {k: round(rng.uniform(-4.0, 4.0), 2) for k in ["芯片概念", "固态电池", "低空经济", "数据要素"]}
    leader_pool = {
        "新能源": "宁德时代",
        "半导体": "寒武纪",
        "人工智能": "工业富联",
        "医药": "恒瑞医药",
        "军工": "中航沈飞",
    }
    sector_core = {
        "涨幅前10": [
            {
                "板块名称": k,
                "涨跌幅": v,
                "领涨股票": leader_pool.get(k, "MockLeader"),
                "领涨股票-涨跌幅": round(v + rng.uniform(0.5, 3.0), 2),
            }
            for k, v in sorted(sectors.items(), key=lambda x: -x[1])
        ],
        "跌幅前10": [{"板块名称": k, "涨跌幅": v} for k, v in sorted(sectors.items(), key=lambda x: x[1])],
        "概念": {
            "涨幅前10": [{"板块名称": k, "涨跌幅": v} for k, v in sorted(concepts.items(), key=lambda x: -x[1])],
            "跌幅前10": [{"板块名称": k, "涨跌幅": v} for k, v in sorted(concepts.items(), key=lambda x: x[1])],
        },
    }
    sector = apply_benchmark_excess(sector_core, indices)
    sector_fund_flow = {
        "行业": {
            "净流入前列": [
                {"板块名称": "电池", "板块代码": "BK1033", "涨跌幅": 1.2, "主力净流入(亿)": round(rng.uniform(8, 25), 2), "净占比(%)": round(rng.uniform(2, 6), 2)},
                {"板块名称": "半导体", "板块代码": "BK0480", "涨跌幅": 1.6, "主力净流入(亿)": round(rng.uniform(5, 18), 2), "净占比(%)": round(rng.uniform(2, 5), 2)},
            ],
            "净流出前列": [
                {"板块名称": "白酒", "板块代码": "BK0479", "涨跌幅": -0.7, "主力净流入(亿)": round(rng.uniform(-12, -3), 2), "净占比(%)": round(rng.uniform(-3, -1), 2)},
            ],
        },
        "概念": {
            "净流入前列": [
                {"板块名称": "固态电池", "板块代码": "BK1234", "涨跌幅": 2.1, "主力净流入(亿)": round(rng.uniform(4, 12), 2), "净占比(%)": round(rng.uniform(2, 5), 2)},
                {"板块名称": "AI算力", "板块代码": "BK5678", "涨跌幅": 1.4, "主力净流入(亿)": round(rng.uniform(3, 10), 2), "净占比(%)": round(rng.uniform(2, 4), 2)},
            ],
            "净流出前列": [],
        },
    }
    limit_up_pool = {
        "数据日期": d,
        "涨停总数": rng.randint(40, 90),
        "连板梯队_最高": 5,
        "高位连板": [
            {"代码": "300001", "名称": "Mock龙头甲", "连板数": 5, "涨停统计": "5天5板", "所属行业": "半导体", "封板金额(亿)": 1.2, "成交额(亿)": 8.0, "炸板次数": 0},
            {"代码": "002001", "名称": "Mock龙头乙", "连板数": 3, "涨停统计": "3天3板", "所属行业": "电池", "封板金额(亿)": 0.6, "成交额(亿)": 4.5, "炸板次数": 1},
        ],
        "题材聚合": [
            {"题材": "半导体", "涨停家数": 8, "最高连板": 5, "代表个股": ["Mock龙头甲", "MockA", "MockB"]},
            {"题材": "电池", "涨停家数": 5, "最高连板": 3, "代表个股": ["Mock龙头乙", "MockC"]},
            {"题材": "化学制品", "涨停家数": 3, "最高连板": 2, "代表个股": ["MockD"]},
        ],
        "封板金额前列": [],
        "数据来源": "mock",
    }
    us_market = {
        "纳斯达克": {"收盘价": 19000 + rng.randint(-200, 200), "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
        "标普500": {"收盘价": 5500 + rng.randint(-80, 80), "涨跌幅(%)": round(rng.uniform(-1.5, 1.5), 2)},
        "etf参考": {
            "QQQ": {"名称": "纳指ETF", "收盘价": 400.0, "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
            "XLK": {"名称": "科技ETF", "收盘价": 200.0, "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
            "SPY": {"名称": "标普ETF", "收盘价": 500.0, "涨跌幅(%)": round(rng.uniform(-1.5, 1.5), 2)},
            "IWM": {"名称": "罗素2000ETF", "收盘价": 200.0, "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
            "XLF": {"名称": "金融ETF", "收盘价": 40.0, "涨跌幅(%)": round(rng.uniform(-1, 1), 2)},
            "XLE": {"名称": "能源ETF", "收盘价": 85.0, "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
            "XLV": {"名称": "医疗ETF", "收盘价": 140.0, "涨跌幅(%)": round(rng.uniform(-1, 1), 2)},
            "XLY": {"名称": "可选消费ETF", "收盘价": 190.0, "涨跌幅(%)": round(rng.uniform(-1.5, 1.5), 2)},
        },
        "movers": {
            "mag7": [
                {"代码": "AAPL", "名称": "苹果", "收盘价": 270.0, "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
                {"代码": "MSFT", "名称": "微软", "收盘价": 420.0, "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
                {"代码": "NVDA", "名称": "英伟达", "收盘价": 200.0, "涨跌幅(%)": round(rng.uniform(-3, 3), 2)},
                {"代码": "GOOGL", "名称": "谷歌", "收盘价": 340.0, "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
                {"代码": "AMZN", "名称": "亚马逊", "收盘价": 250.0, "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
                {"代码": "META", "名称": "Meta", "收盘价": 660.0, "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
                {"代码": "TSLA", "名称": "特斯拉", "收盘价": 370.0, "涨跌幅(%)": round(rng.uniform(-3, 3), 2)},
            ],
            "中概股_adr": [
                {"代码": "BABA", "名称": "阿里巴巴", "收盘价": 130.0, "涨跌幅(%)": round(rng.uniform(-3, 3), 2)},
                {"代码": "PDD", "名称": "拼多多", "收盘价": 100.0, "涨跌幅(%)": round(rng.uniform(-3, 3), 2)},
                {"代码": "JD", "名称": "京东", "收盘价": 30.0, "涨跌幅(%)": round(rng.uniform(-3, 3), 2)},
                {"代码": "BIDU", "名称": "百度", "收盘价": 120.0, "涨跌幅(%)": round(rng.uniform(-3, 3), 2)},
            ],
        },
    }
    cross_market = build_cross_market_hints(sector, us_market)
    style_matrix = build_style_matrix(indices)
    continuity = {
        "数据日期": d,
        "昨日涨停样本数": rng.randint(40, 80),
        "今日上涨家数": rng.randint(20, 50),
        "今日下跌家数": rng.randint(15, 35),
        "今日平盘家数": rng.randint(0, 5),
        "今日接力涨停": rng.randint(8, 25),
        "接力涨停率(%)": round(rng.uniform(15, 45), 1),
        "高位连板样本": rng.randint(8, 18),
        "高位连板接力": rng.randint(2, 8),
        "昨涨停今日平均涨幅(%)": round(rng.uniform(-2, 4), 2),
        "接力梯队_top": [
            {"名称": "Mock龙头甲", "代码": "300001", "今日涨跌幅": 9.99, "昨日连板数": 4, "涨停统计": "5天5板", "所属行业": "半导体"},
            {"名称": "Mock龙头乙", "代码": "002001", "今日涨跌幅": 8.6, "昨日连板数": 2, "涨停统计": "3天3板", "所属行业": "电池"},
        ],
        "退潮个股_top": [
            {"名称": "Mock瓦解丙", "代码": "601001", "今日涨跌幅": -6.4, "昨日连板数": 1, "所属行业": "白酒"},
        ],
        "数据口径": "mock",
    }
    lhb = {
        "数据日期": d,
        "净买入前列": [
            {"名称": "Mock龙头甲", "代码": "300001", "涨跌幅": 9.99, "净买额(亿)": 2.1, "上榜原因": "日涨幅偏离值达7%", "解读": "机构席位买入"},
            {"名称": "Mock主升甲", "代码": "300011", "涨跌幅": 6.8, "净买额(亿)": 1.4, "上榜原因": "换手率达20%", "解读": "知名游资接力"},
        ],
        "净卖出前列": [
            {"名称": "Mock瓦解丙", "代码": "601001", "涨跌幅": -3.4, "净买额(亿)": -1.6, "上榜原因": "日跌幅偏离值达7%", "解读": "机构席位卖出"},
        ],
        "口径": "mock",
    }
    forward_watchlist = build_forward_watchlist(
        limit_up_pool=limit_up_pool,
        individual_fund_flow=sentiment.get("个股资金流", {}),
        lhb=lhb,
        sector_performance=sector,
        sector_fund_flow=sector_fund_flow,
        continuity=continuity,
    )
    liquidity = {
        "货币市场": {
            "SHIBOR_O/N": {"数据日期": d, "利率(%)": round(rng.uniform(1.4, 2.2), 3), "环比变动(bp)": round(rng.uniform(-15, 15), 1)},
            "SHIBOR_1W": {"数据日期": d, "利率(%)": round(rng.uniform(1.6, 2.4), 3), "环比变动(bp)": round(rng.uniform(-10, 10), 1)},
            "DR007": {"利率(%)": round(rng.uniform(1.7, 2.3), 3), "环比变动(bp)": round(rng.uniform(-12, 12), 1)},
        },
        "美元离岸人民币": {"中间价": round(rng.uniform(7.05, 7.30), 4), "来源": "mock"},
        "中国10年国债": {"数据日期": d, "10Y国债收益率(%)": round(rng.uniform(2.05, 2.45), 3), "环比变动(bp)": round(rng.uniform(-5, 5), 1)},
        "定性": "短端流动性平稳；长端利率小幅下行（利好成长估值）",
    }
    sector_leaders = {
        "强势行业龙头矩阵": [
            {
                "板块": next(iter(leader_pool.keys())),
                "成分股_top5": [
                    {"名称": "Mock龙头甲", "代码": "300001", "涨跌幅": 9.99},
                    {"名称": "MockA", "代码": "300002", "涨跌幅": 6.8},
                    {"名称": "MockB", "代码": "300003", "涨跌幅": 5.4},
                    {"名称": "MockC", "代码": "300004", "涨跌幅": 4.1},
                    {"名称": "MockD", "代码": "300005", "涨跌幅": 3.2},
                ],
                "板内涨停数": 4,
            },
        ],
        "口径": "mock",
    }
    sector_5d_strength = {
        "样本": [
            {"板块": "半导体", "近5日累计涨跌幅(%)": round(rng.uniform(2, 12), 2)},
            {"板块": "新能源", "近5日累计涨跌幅(%)": round(rng.uniform(-3, 6), 2)},
            {"板块": "人工智能", "近5日累计涨跌幅(%)": round(rng.uniform(0, 9), 2)},
        ],
        "口径": "mock",
    }
    return MarketSnapshot(
        asof=asof,
        provider="mock",
        date=d,
        is_trading_day=True,
        sources=[{"name": "mock", "note": "测试用随机数据，严禁用于发布"}],
        a_share_indices=indices,
        market_sentiment=sentiment,
        northbound_flow=northbound,
        sector_performance=sector,
        us_market=us_market,
        commodities={
            "黄金(美元/盎司)": {"收盘价": 2300 + rng.randint(-30, 30), "涨跌幅(%)": round(rng.uniform(-1, 1), 2)},
            "WTI原油(美元/桶)": {"收盘价": 75 + rng.randint(-5, 5), "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
        },
        futures={},
        cross_market=cross_market,
        sector_fund_flow=sector_fund_flow,
        limit_up_pool=limit_up_pool,
        continuity=continuity,
        style_matrix=style_matrix,
        lhb=lhb,
        forward_watchlist=forward_watchlist,
        liquidity=liquidity,
        sector_leaders=sector_leaders,
        sector_5d_strength=sector_5d_strength,
    )
