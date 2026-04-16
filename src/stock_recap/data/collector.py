"""市场数据采集层。

Provider 说明：
- mock   : 确定性随机数据（seed=日期），用于无网络/自测
- akshare: 全量使用 AkShare
- live   : 关键指数走东方财富 push2（无 key），其余补充数据走 AkShare

新增（原版缺失）：
- 北向资金（live/akshare 均补全）
- 美股收盘行情（前日）
- 大宗商品（黄金/原油）
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

from stock_recap.models import MarketSnapshot, Provider

logger = logging.getLogger("stock_recap.collector")


# ─── 工具函数 ───────────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    from datetime import timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ─── 东方财富实时指数 ───────────────────────────────────────────────────────────

def _eastmoney_index_spot(timeout: int = 10) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """东方财富 push2 接口：获取主要 A 股指数实时行情。"""
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    reqs = {
        "上证指数": "1.000001",
        "深证成指": "0.399001",
        "创业板指": "0.399006",
        "科创50": "1.000688",
    }
    indices: Dict[str, Any] = {}
    meta: Dict[str, Any] = {
        "name": "eastmoney:push2",
        "url": url,
        "asof": _utc_now_iso(),
    }
    with httpx.Client(timeout=timeout) as client:
        for name, secid in reqs.items():
            try:
                r = client.get(
                    url,
                    params={"secid": secid, "fields": "f43,f170,f47,f48,f58"},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                r.raise_for_status()
                data = r.json().get("data") or {}
                price = (data.get("f43") or 0) / 100
                pct = (data.get("f170") or 0) / 100
                amount_yuan = data.get("f48")
                if price > 0:
                    indices[name] = {
                        "最新价": float(price),
                        "涨跌幅": float(pct),
                        "成交额(亿)": (
                            float(amount_yuan) / 1e8
                            if isinstance(amount_yuan, (int, float)) and amount_yuan > 0
                            else None
                        ),
                    }
            except Exception as e:
                logger.warning(
                    _stable_json({"event": "eastmoney_index_failed", "name": name, "error": str(e)})
                )
    return indices, meta


# ─── 北向资金 ───────────────────────────────────────────────────────────────────

def _fetch_northbound(ak: Any, date_short: str) -> Dict[str, Any]:
    """采集北向资金净流入数据（AkShare）。"""
    try:
        # 当日北向净买入（实时或收盘）
        df = ak.stock_em_hsgt_north_acc_flow_in_day(symbol="北向资金")
        if df is not None and not df.empty:
            # 取最新一行
            latest = df.iloc[-1]
            return {
                "净买入(亿)": _safe_float(latest.get("当日净买入") or latest.get("净买入")),
                "沪股通净买入(亿)": _safe_float(latest.get("沪股通净买入", 0)),
                "深股通净买入(亿)": _safe_float(latest.get("深股通净买入", 0)),
                "数据来源": "akshare:stock_em_hsgt_north_acc_flow_in_day",
            }
    except Exception as e:
        logger.warning(_stable_json({"event": "northbound_flow_failed", "error": str(e)}))

    # 降级：尝试另一个接口
    try:
        df2 = ak.stock_hsgt_hist_em(symbol="北向资金")
        if df2 is not None and not df2.empty:
            latest = df2.iloc[-1]
            return {
                "净买入(亿)": _safe_float(latest.get("当日净买入", 0)),
                "数据来源": "akshare:stock_hsgt_hist_em (降级)",
            }
    except Exception as e2:
        logger.warning(_stable_json({"event": "northbound_flow_fallback_failed", "error": str(e2)}))

    return {}


# ─── 美股收盘行情 ─────────────────────────────────────────────────────────────

def _fetch_us_market(ak: Any) -> Dict[str, Any]:
    """采集美股主要指数（前日收盘或实时）。"""
    result: Dict[str, Any] = {}
    symbols = {
        "纳斯达克": "^IXIC",
        "标普500": "^GSPC",
        "道琼斯": "^DJI",
    }
    for name, sym in symbols.items():
        try:
            df = ak.index_us_stock_sina(symbol=sym)
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                result[name] = {
                    "收盘价": _safe_float(latest.get("close") or latest.get("收盘")),
                    "涨跌幅(%)": _safe_float(latest.get("change_p") or latest.get("涨跌幅")),
                }
        except Exception as e:
            logger.warning(
                _stable_json({"event": "us_market_failed", "symbol": sym, "error": str(e)})
            )
    return result


# ─── 大宗商品 ─────────────────────────────────────────────────────────────────

def _fetch_commodities(ak: Any) -> Dict[str, Any]:
    """采集黄金/原油价格（前日收盘）。"""
    result: Dict[str, Any] = {}

    # 黄金（COMEX 期货）
    try:
        df_gold = ak.futures_foreign_detail(symbol="GC0")  # 黄金主力
        if df_gold is not None and not df_gold.empty:
            latest = df_gold.iloc[-1]
            result["黄金(美元/盎司)"] = {
                "收盘价": _safe_float(latest.get("收盘价") or latest.get("close")),
                "涨跌幅(%)": _safe_float(latest.get("涨跌幅") or latest.get("change_p")),
            }
    except Exception as e:
        logger.warning(_stable_json({"event": "gold_failed", "error": str(e)}))

    # 原油（WTI 期货）
    try:
        df_oil = ak.futures_foreign_detail(symbol="CL0")  # WTI 主力
        if df_oil is not None and not df_oil.empty:
            latest = df_oil.iloc[-1]
            result["WTI原油(美元/桶)"] = {
                "收盘价": _safe_float(latest.get("收盘价") or latest.get("close")),
                "涨跌幅(%)": _safe_float(latest.get("涨跌幅") or latest.get("change_p")),
            }
    except Exception as e:
        logger.warning(_stable_json({"event": "oil_failed", "error": str(e)}))

    return result


# ─── AkShare 公共采集（情绪 + 板块） ─────────────────────────────────────────

def _fetch_sentiment_and_sector(
    ak: Any, date_short: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """采集市场情绪（涨跌停、成交额）和板块轮动数据。"""
    sentiment: Dict[str, Any] = {}
    sector: Dict[str, Any] = {}

    # 涨停
    try:
        limit_up = ak.stock_zt_pool_em(date=date_short)
        sentiment["涨停家数"] = int(len(limit_up)) if limit_up is not None else 0
    except Exception as e:
        logger.warning(_stable_json({"event": "limit_up_failed", "error": str(e)}))

    # 跌停
    try:
        try:
            limit_down = ak.stock_zt_pool_dtgc_em(date=date_short)
        except Exception:
            limit_down = None
        sentiment["跌停家数"] = int(len(limit_down)) if limit_down is not None else 0
    except Exception as e:
        logger.warning(_stable_json({"event": "limit_down_failed", "error": str(e)}))

    # 全市场概览（成交额、涨跌家数）
    try:
        overview = ak.stock_zh_a_spot_em()
        if overview is not None and not overview.empty:
            if "成交额" in overview.columns:
                sentiment["两市成交额(亿)"] = float(overview["成交额"].sum() / 1e8)
            if "涨跌幅" in overview.columns:
                sentiment["上涨家数"] = int((overview["涨跌幅"] > 0).sum())
                sentiment["下跌家数"] = int((overview["涨跌幅"] < 0).sum())
    except Exception as e:
        logger.warning(_stable_json({"event": "overview_failed", "error": str(e)}))

    # 板块轮动
    try:
        sector_df = ak.stock_board_industry_name_em()
        if sector_df is not None and not sector_df.empty:
            sector_df = sector_df.sort_values("涨跌幅", ascending=False)
            sector = {
                "涨幅前10": sector_df.head(10)[["板块名称", "涨跌幅"]].to_dict("records"),
                "跌幅前10": sector_df.tail(10)[["板块名称", "涨跌幅"]].to_dict("records"),
            }
    except Exception as e:
        logger.warning(_stable_json({"event": "sector_failed", "error": str(e)}))

    return sentiment, sector


# ─── 主采集入口 ────────────────────────────────────────────────────────────────

def collect_snapshot(
    provider: Provider,
    date: Optional[str],
    skip_trading_check: bool = False,
) -> MarketSnapshot:
    """采集市场快照。

    Args:
        provider: 数据源（mock / akshare / live）
        date: 目标交易日（YYYY-MM-DD），None 则取今天
        skip_trading_check: 是否跳过非交易日检查
    """
    from stock_recap.data.calendar import is_trading_day

    d = date or _today_str()
    asof = _utc_now_iso()
    trading_day = is_trading_day(d) if not skip_trading_check else True

    if not trading_day and not skip_trading_check:
        logger.warning(
            _stable_json({"event": "non_trading_day", "date": d, "note": "使用 --skip-trading-check 强制生成"})
        )

    # ── MOCK ────────────────────────────────────────────────────────────────────
    if provider == "mock":
        return _collect_mock(d, asof)

    # ── 需要 AkShare ─────────────────────────────────────────────────────────
    try:
        import akshare as ak  # type: ignore
    except Exception:
        raise RuntimeError(
            "AkShare 未能导入。请确认已安装（uv sync），或切换 --provider mock"
        )

    date_short = d.replace("-", "")

    if provider == "akshare":
        return _collect_akshare(ak, d, date_short, asof)

    if provider == "live":
        return _collect_live(ak, d, date_short, asof)

    raise ValueError(f"未知 provider: {provider}")


# ─── Mock ─────────────────────────────────────────────────────────────────────

def _collect_mock(d: str, asof: str) -> MarketSnapshot:
    rng = random.Random(_sha256(d)[:16])
    indices = {
        "上证指数": {"最新价": 3100 + rng.randint(-30, 30), "涨跌幅": round(rng.uniform(-1.5, 1.5), 2)},
        "深证成指": {"最新价": 12000 + rng.randint(-80, 80), "涨跌幅": round(rng.uniform(-1.8, 1.8), 2)},
        "创业板指": {"最新价": 2500 + rng.randint(-60, 60), "涨跌幅": round(rng.uniform(-2.2, 2.2), 2)},
        "科创50": {"最新价": 1100 + rng.randint(-30, 30), "涨跌幅": round(rng.uniform(-2.0, 2.0), 2)},
    }
    sentiment = {
        "涨停家数": rng.randint(25, 85),
        "跌停家数": rng.randint(3, 30),
        "两市成交额(亿)": round(rng.uniform(6500, 12500), 0),
        "上涨家数": rng.randint(800, 4200),
        "下跌家数": rng.randint(800, 4200),
    }
    northbound = {
        "净买入(亿)": round(rng.uniform(-80, 80), 1),
        "沪股通净买入(亿)": round(rng.uniform(-50, 50), 1),
        "深股通净买入(亿)": round(rng.uniform(-40, 40), 1),
        "数据来源": "mock",
    }
    sectors = {k: round(rng.uniform(-3.5, 3.5), 2) for k in ["新能源", "半导体", "人工智能", "医药", "军工"]}
    return MarketSnapshot(
        asof=asof,
        provider="mock",
        date=d,
        is_trading_day=True,
        sources=[{"name": "mock", "note": "测试用随机数据，严禁用于发布"}],
        a_share_indices=indices,
        market_sentiment=sentiment,
        northbound_flow=northbound,
        sector_performance={
            "涨幅前10": [{"板块名称": k, "涨跌幅": v} for k, v in sorted(sectors.items(), key=lambda x: -x[1])],
            "跌幅前10": [{"板块名称": k, "涨跌幅": v} for k, v in sorted(sectors.items(), key=lambda x: x[1])],
        },
        us_market={
            "纳斯达克": {"收盘价": 19000 + rng.randint(-200, 200), "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
            "标普500": {"收盘价": 5500 + rng.randint(-80, 80), "涨跌幅(%)": round(rng.uniform(-1.5, 1.5), 2)},
        },
        commodities={
            "黄金(美元/盎司)": {"收盘价": 2300 + rng.randint(-30, 30), "涨跌幅(%)": round(rng.uniform(-1, 1), 2)},
            "WTI原油(美元/桶)": {"收盘价": 75 + rng.randint(-5, 5), "涨跌幅(%)": round(rng.uniform(-2, 2), 2)},
        },
        futures={},
    )


# ─── AkShare（全量） ──────────────────────────────────────────────────────────

def _collect_akshare(ak: Any, d: str, date_short: str, asof: str) -> MarketSnapshot:
    sources: List[Dict[str, Any]] = [{"name": "akshare", "asof": asof}]

    # 指数
    indices: Dict[str, Any] = {}
    try:
        df = ak.stock_zh_index_spot_em()
        indices_map = {
            "上证指数": "sh000001",
            "深证成指": "sz399001",
            "创业板指": "sz399006",
            "科创50": "sh000688",
        }
        for name, code in indices_map.items():
            matching = df[df["代码"] == code]
            if not matching.empty:
                row = matching.iloc[0]
                indices[name] = {
                    "最新价": _safe_float(row.get("最新价")),
                    "涨跌幅": _safe_float(row.get("涨跌幅")),
                    "成交额(亿)": (
                        _safe_float(row.get("成交额")) / 1e8
                        if row.get("成交额") is not None
                        else None
                    ),
                }
    except Exception as e:
        logger.warning(_stable_json({"event": "akshare_indices_failed", "error": str(e)}))

    sentiment, sector = _fetch_sentiment_and_sector(ak, date_short)
    northbound = _fetch_northbound(ak, date_short)
    us_market = _fetch_us_market(ak)
    commodities = _fetch_commodities(ak)

    return MarketSnapshot(
        asof=asof,
        provider="akshare",
        date=d,
        is_trading_day=True,
        sources=sources,
        a_share_indices=indices,
        market_sentiment=sentiment,
        northbound_flow=northbound,
        sector_performance=sector,
        us_market=us_market,
        commodities=commodities,
        futures={},
    )


# ─── Live（东方财富指数 + AkShare 补充） ──────────────────────────────────────

def _collect_live(ak: Any, d: str, date_short: str, asof: str) -> MarketSnapshot:
    sources: List[Dict[str, Any]] = []

    # 关键指数：东方财富（实时、无 key）
    indices: Dict[str, Any] = {}
    try:
        indices, em_meta = _eastmoney_index_spot()
        sources.append(em_meta)
    except Exception as e:
        logger.warning(_stable_json({"event": "eastmoney_indices_failed", "error": str(e)}))

    # 严格检查：至少要有上证指数实时点位，否则禁止生成（防幻觉）
    sse = indices.get("上证指数") or {}
    if not sse.get("最新价") or not sse.get("涨跌幅") is not None:
        if not sse.get("最新价"):
            raise RuntimeError(
                "严格模式：未能从东方财富获取上证指数最新价（可能非交易时段或网络故障）。"
                "请使用 --skip-trading-check 或 --provider akshare。"
            )

    # AkShare 补充数据（情绪/板块/北向/美股/大宗）
    sources.append({"name": "akshare", "note": "情绪/板块/北向/海外补充", "asof": _utc_now_iso()})
    sentiment, sector = _fetch_sentiment_and_sector(ak, date_short)
    northbound = _fetch_northbound(ak, date_short)
    us_market = _fetch_us_market(ak)
    commodities = _fetch_commodities(ak)

    # 补充两市成交额：用指数口径估算
    if "两市成交额(亿)" not in sentiment:
        sh_amt = sse.get("成交额(亿)")
        sz_amt = (indices.get("深证成指") or {}).get("成交额(亿)")
        if isinstance(sh_amt, (int, float)) and isinstance(sz_amt, (int, float)):
            sentiment["两市成交额(亿)"] = float(sh_amt) + float(sz_amt)
            sentiment["两市成交额口径"] = "指数口径估算（上证+深证）"

    return MarketSnapshot(
        asof=asof,
        provider="live",
        date=d,
        is_trading_day=True,
        sources=sources,
        a_share_indices=indices,
        market_sentiment=sentiment,
        northbound_flow=northbound,
        sector_performance=sector,
        us_market=us_market,
        commodities=commodities,
        futures={},
    )
