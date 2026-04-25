import json

from agent_platform.domain.models import Features, MarketSnapshot
from agent_platform.infrastructure.llm.prompts import build_user_prompt


def test_daily_user_prompt_strips_northbound_and_adds_coverage():
    snap = MarketSnapshot(
        asof="2026-04-25T00:00:00+00:00",
        provider="mock",
        date="2026-04-25",
        northbound_flow={"净买入(亿)": 1.0, "数据来源": "x"},
        market_sentiment={
            "两市成交额(亿)": 9000.0,
            "上涨家数": 1000,
            "下跌家数": 2000,
            "大盘主力资金流": {"主力净流入_亿": -12.3, "数据日期": "2026-04-25"},
        },
        sector_performance={
            "涨幅前10": [{"板块名称": "半导体", "涨跌幅": 1.2}],
            "概念": {"涨幅前10": [{"板块名称": "芯片概念", "涨跌幅": 0.9}], "跌幅前10": []},
            "相对表现": {"基准": "沪深300", "基准涨跌幅": 0.1, "行业涨幅前10含超额": []},
        },
        us_market={
            "标普500": {"涨跌幅(%)": 0.5},
            "etf参考": {"QQQ": {"涨跌幅(%)": 0.6, "名称": "纳指ETF", "收盘价": 1.0}},
        },
        commodities={},
        cross_market={
            "paired_observations": [
                {
                    "主题": "科技成长链",
                    "美股代理": "QQQ（纳指100）",
                    "美股涨跌幅(%)": 0.6,
                    "A股对应": "半导体（行业涨幅前列）",
                    "A股涨跌幅(%)": 1.2,
                    "数值同向": True,
                }
            ],
            "口径说明": "mock",
        },
    )
    payload = json.loads(
        build_user_prompt(
            mode="daily",
            snapshot=snap,
            features=Features(),
            memory=[],
            prompt_version="test",
        )
    )
    assert "northbound_flow" not in payload["snapshot"]
    assert "data_coverage" in payload
    assert "present_topics" in payload["data_coverage"]
    assert "absent_topics" in payload["data_coverage"]
    present = payload["data_coverage"]["present_topics"]
    assert "cross_market" in present
    assert "us_etf_proxies" in present
    assert "sector_concept_layer" in present
    assert "sector_relative_benchmark" in present
    flags = payload["data_coverage"]["topic_flags"]
    # 基础测试用 snapshot 不含 mag7/adr/资金流/涨停池字段，应为缺失
    assert flags["us_mag7"] is False
    assert flags["us_china_adr"] is False
    assert flags["sector_fund_flow"] is False
    assert flags["limit_up_pool"] is False
