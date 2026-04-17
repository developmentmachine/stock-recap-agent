"""AkShare provider — 委托给 sources/ 下各 DataFetcher。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from stock_recap.infrastructure.data.sources.indices import make_indices_fetcher
from stock_recap.infrastructure.data.sources.sentiment import make_sentiment_fetcher
from stock_recap.infrastructure.data.sources.continuity import fetch_continuity
from stock_recap.infrastructure.data.sources.cross_market import build_cross_market_hints
from stock_recap.infrastructure.data.sources.forward_watchlist import build_forward_watchlist
from stock_recap.infrastructure.data.sources.individual_fund_flow import fetch_individual_fund_flow
from stock_recap.infrastructure.data.sources.lhb import fetch_lhb
from stock_recap.infrastructure.data.sources.limit_up_pool import fetch_limit_up_pool
from stock_recap.infrastructure.data.sources.sector import apply_benchmark_excess, make_sector_fetcher
from stock_recap.infrastructure.data.sources.sector_fund_flow import fetch_sector_fund_flow
from stock_recap.infrastructure.data.sources.sector_leaders import (
    _top_strong_industry_names,
    fetch_industry_5d_strength,
    fetch_sector_leaders,
)
from stock_recap.infrastructure.data.sources.liquidity import fetch_liquidity
from stock_recap.infrastructure.data.sources.style_factors import build_style_matrix
from stock_recap.infrastructure.data.sources.us_movers import fetch_us_movers
from stock_recap.infrastructure.data.sources.us_market import make_us_market_fetcher
from stock_recap.infrastructure.data.sources.commodities import make_commodities_fetcher
from stock_recap.infrastructure.data.sources.hot_rank import make_hot_rank_fetcher
from stock_recap.infrastructure.data.sources.market_fund_flow import (
    fetch_market_main_fund_summary,
)
from stock_recap.domain.models import MarketSnapshot

logger = logging.getLogger("stock_recap.providers.akshare")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def collect_akshare(ak: Any, d: str, date_short: str, asof: str) -> MarketSnapshot:
    indices = make_indices_fetcher().fetch()
    sentiment = make_sentiment_fetcher(ak, date_short).fetch()
    hot = make_hot_rank_fetcher(ak).fetch()
    if hot.get("热度榜前列"):
        sentiment["热度榜前列"] = hot["热度榜前列"]
        sentiment["热度榜数据来源"] = hot.get("数据来源", "")
    sector_raw = make_sector_fetcher(ak).fetch()
    sector = apply_benchmark_excess(sector_raw, indices)
    sector_fund_flow = fetch_sector_fund_flow(top=8)
    individual_flow = fetch_individual_fund_flow(top=10)
    if individual_flow:
        sentiment["个股资金流"] = individual_flow
    limit_up_pool = fetch_limit_up_pool(date_short)
    us_market_base = make_us_market_fetcher().fetch()
    us_movers = fetch_us_movers()
    if us_movers:
        us_market_base["movers"] = us_movers
    us_market = us_market_base
    cross_market = build_cross_market_hints(sector, us_market)
    commodities = make_commodities_fetcher().fetch()

    mf = fetch_market_main_fund_summary(ak, d)
    if mf:
        sentiment["大盘主力资金流"] = mf

    sse = indices.get("上证指数") or {}
    if not sse.get("最新价") and not sentiment.get("两市成交额(亿)"):
        raise RuntimeError(
            "数据严重不足：上证指数和成交额均未获取到。"
            "建议使用 --provider mock 测试，或检查网络后重试。"
        )

    continuity = fetch_continuity(date_short, ak=ak)
    style_matrix = build_style_matrix(indices)
    lhb_data = fetch_lhb(ak, date_short)
    liquidity = fetch_liquidity(ak)

    sector_leaders = fetch_sector_leaders(ak, sector or {}, top_industries=3)
    top_strong_names = _top_strong_industry_names(sector or {}, n=5)
    sector_5d = fetch_industry_5d_strength(ak, top_strong_names, days=5)

    forward_watchlist = build_forward_watchlist(
        limit_up_pool=limit_up_pool or {},
        individual_fund_flow=individual_flow or {},
        lhb=lhb_data or {},
        sector_performance=sector or {},
        sector_fund_flow=sector_fund_flow or {},
        continuity=continuity or {},
    )

    return MarketSnapshot(
        asof=asof,
        provider="akshare",
        date=d,
        is_trading_day=True,
        sources=[{"name": "tencent+sina+akshare", "asof": asof}],
        a_share_indices=indices,
        market_sentiment=sentiment,
        northbound_flow={},
        sector_performance=sector,
        us_market=us_market,
        commodities=commodities,
        futures={},
        cross_market=cross_market,
        sector_fund_flow=sector_fund_flow,
        limit_up_pool=limit_up_pool,
        continuity=continuity,
        style_matrix=style_matrix,
        lhb=lhb_data,
        forward_watchlist=forward_watchlist,
        liquidity=liquidity,
        sector_leaders=sector_leaders,
        sector_5d_strength=sector_5d,
    )
