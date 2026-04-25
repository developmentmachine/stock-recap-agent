"""流动性面板数据源：DR007 / SHIBOR / USD-CNH / 10Y 国债收益率。

资深视角：A股短端定价的真实锚是货币市场利率（DR007/SHIBOR），中长端是 10Y 国债，
跨境流动性的旁证是离岸 USD/CNH。组合在一起，可以判断"水"的方向：
  - DR007 大幅上行 → 短端收紧 → 风险资产承压；
  - 10Y 国债收益率下行 → 久期溢价压缩 → 利好成长股估值；
  - USD/CNH 走强（CNH 贬值）→ 北向资金更易流出 / 外资定价权资产承压。

数据来源：
  - DR007 / SHIBOR：AkShare ak.rate_interbank（同业拆借）；fallback 使用
    新浪 / 人行公开接口。
  - USD/CNH：AkShare ak.currency_pair_map_sina + ak.currency_history_fx_spot_sina
    或更直接的 ak.fx_spot_quote。
  - 10Y 国债：AkShare ak.bond_china_yield。

所有取数都做 try/except，单源失败不影响其他指标。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agent_platform.sources.liquidity")


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return float(v)
    except Exception:
        return None


def _last_two(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    if len(rows) == 1:
        return {"latest": rows[-1], "prev": None}
    return {"latest": rows[-1], "prev": rows[-2]}


# ─── DR007 / SHIBOR ───────────────────────────────────────────────────────

def _fetch_money_market(ak: Any) -> Dict[str, Any]:
    """通过 AkShare 抓 DR007 / SHIBOR。"""
    out: Dict[str, Any] = {}
    if ak is None:
        return out

    # SHIBOR — ak.rate_interbank(market='上海银行同业拆借市场', symbol='Shibor人民币', indicator='隔夜'/'1周'/'1月')
    for indicator, key in (("隔夜", "SHIBOR_O/N"), ("1周", "SHIBOR_1W"), ("1月", "SHIBOR_1M")):
        try:
            df = ak.rate_interbank(
                market="上海银行同业拆借市场",
                symbol="Shibor人民币",
                indicator=indicator,
            )
            if df is None or df.empty:
                continue
            tail = df.tail(2).to_dict("records")
            latest = tail[-1]
            prev = tail[0] if len(tail) > 1 else None
            rate = _safe_float(latest.get("利率"))
            chg_bp = None
            if prev is not None:
                p_rate = _safe_float(prev.get("利率"))
                if rate is not None and p_rate is not None:
                    chg_bp = round((rate - p_rate) * 100, 1)
            out[key] = {
                "数据日期": str(latest.get("报告日"))[:10],
                "利率(%)": rate,
                "环比变动(bp)": chg_bp,
            }
        except Exception as e:
            logger.debug("rate_interbank %s failed: %s", indicator, e)

    # DR007 — ak.macro_china_shibor（含 DR007）/ ak.bond_repo_zh_summary
    try:
        df = ak.bond_repo_zh_summary()  # 银行间债券回购：包含 DR007/R007
        if df is not None and not df.empty:
            # 找 DR007 行
            for col_candidate in ("项目名称", "回购代码", "代码", "名称"):
                if col_candidate in df.columns:
                    name_col = col_candidate
                    break
            else:
                name_col = df.columns[0]
            mask = df[name_col].astype(str).str.contains("DR007", na=False)
            sub = df[mask]
            if not sub.empty:
                row = sub.iloc[0].to_dict()
                rate = None
                chg_bp = None
                for k in ("加权平均利率", "加权平均价", "利率", "weighted_rate"):
                    if k in row:
                        rate = _safe_float(row[k])
                        break
                for k in ("涨跌BP", "涨跌(BP)", "变动BP", "涨跌"):
                    if k in row:
                        chg_bp = _safe_float(row[k])
                        break
                if rate is not None:
                    out["DR007"] = {
                        "利率(%)": rate,
                        "环比变动(bp)": chg_bp,
                    }
    except Exception as e:
        logger.debug("bond_repo_zh_summary failed: %s", e)

    return out


# ─── USD-CNH ────────────────────────────────────────────────────────────

def _fetch_usdcnh(ak: Any) -> Optional[Dict[str, Any]]:
    if ak is None:
        return None
    # 优先：ak.fx_spot_quote 即时报价
    for fn_name in ("fx_spot_quote", "currency_boc_safe"):
        try:
            fn = getattr(ak, fn_name, None)
            if fn is None:
                continue
            df = fn()
            if df is None or df.empty:
                continue
            # 找 USD/CNH 行
            for col_candidate in ("货币对", "代码", "name", "symbol"):
                if col_candidate in df.columns:
                    name_col = col_candidate
                    break
            else:
                name_col = df.columns[0]
            mask = df[name_col].astype(str).str.contains("USD/?CNH", na=False, regex=True)
            sub = df[mask]
            if sub.empty:
                continue
            row = sub.iloc[0].to_dict()
            mid = None
            for k in ("中间价", "卖出价", "现汇卖出价", "卖出"):
                if k in row:
                    mid = _safe_float(row[k])
                    break
            if mid is not None:
                return {"中间价": mid, "来源": fn_name}
        except Exception as e:
            logger.debug("fx fetch %s failed: %s", fn_name, e)
    return None


# ─── 10Y 国债收益率 ──────────────────────────────────────────────────────

def _fetch_china_10y(ak: Any) -> Optional[Dict[str, Any]]:
    if ak is None:
        return None
    try:
        df = ak.bond_china_yield(start_date="", end_date="")
        if df is None or df.empty:
            return None
    except Exception:
        # 不带参数版本
        try:
            df = ak.bond_china_yield()
        except Exception as e:
            logger.debug("bond_china_yield failed: %s", e)
            return None
    if df is None or df.empty:
        return None
    # 期望含 '曲线名称'='中债国债收益率曲线' + '10年' 列
    try:
        col = "10年" if "10年" in df.columns else None
        if col is None:
            for c in df.columns:
                if "10" in str(c) and "年" in str(c):
                    col = c
                    break
        if col is None:
            return None
        # 取最近两行
        tail = df.tail(2).to_dict("records")
        latest = tail[-1]
        prev = tail[0] if len(tail) > 1 else None
        rate = _safe_float(latest.get(col))
        chg_bp = None
        if prev is not None:
            p_rate = _safe_float(prev.get(col))
            if rate is not None and p_rate is not None:
                chg_bp = round((rate - p_rate) * 100, 1)
        return {
            "数据日期": str(latest.get("日期", ""))[:10],
            "10Y国债收益率(%)": rate,
            "环比变动(bp)": chg_bp,
        }
    except Exception as e:
        logger.debug("bond_china_yield parse failed: %s", e)
        return None


# ─── 总入口 ─────────────────────────────────────────────────────────────

def _interpret(
    *,
    money_market: Dict[str, Any],
    usdcnh: Optional[Dict[str, Any]],
    cn_10y: Optional[Dict[str, Any]],
) -> str:
    """生成一句话定性。"""
    parts: List[str] = []

    o_n = money_market.get("SHIBOR_O/N", {}).get("环比变动(bp)") if money_market else None
    if o_n is not None:
        if o_n >= 5:
            parts.append("短端流动性收紧")
        elif o_n <= -5:
            parts.append("短端流动性宽松")
        else:
            parts.append("短端流动性平稳")

    if cn_10y:
        bp = cn_10y.get("环比变动(bp)")
        if bp is not None:
            if bp <= -2:
                parts.append("长端利率下行（利好成长估值）")
            elif bp >= 2:
                parts.append("长端利率上行（压制成长估值）")
            else:
                parts.append("长端利率平稳")

    if usdcnh and usdcnh.get("中间价"):
        m = usdcnh["中间价"]
        if m >= 7.30:
            parts.append("CNH 偏弱（外资风险偏好受压）")
        elif m <= 7.05:
            parts.append("CNH 偏强（外资潜在回流）")

    return "；".join(parts) if parts else "数据不全，定性谨慎使用"


def fetch_liquidity(ak: Optional[Any] = None) -> Dict[str, Any]:
    """取『流动性面板』所需各类指标，单源失败不影响其他。"""
    money_market = _fetch_money_market(ak)
    usdcnh = _fetch_usdcnh(ak)
    cn_10y = _fetch_china_10y(ak)

    if not money_market and not usdcnh and not cn_10y:
        return {}

    return {
        "货币市场": money_market,
        "美元离岸人民币": usdcnh,
        "中国10年国债": cn_10y,
        "定性": _interpret(money_market=money_market, usdcnh=usdcnh, cn_10y=cn_10y),
    }


__all__ = ["fetch_liquidity"]
