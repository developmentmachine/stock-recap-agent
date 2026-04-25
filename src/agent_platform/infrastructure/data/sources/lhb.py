"""龙虎榜：识别机构席位 vs 知名游资席位，给出当日真金白银的资金标签。

资深视角：龙虎榜上『机构净买』和『一线游资接力』的含义截然不同。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agent_platform.sources.lhb")

# 一线知名游资席位关键字（覆盖上交所/深交所常见命名）
_KNOWN_HOT_MONEY = (
    "上海溧阳路", "拉萨", "宁波桑田路", "深圳益田路荣超商务中心",
    "上海中山东一路", "光大证券绍兴", "财通证券绍兴", "国泰君安南京太平南路",
    "华鑫证券上海茅台路", "国泰君安顺德德胜东路", "华泰证券深圳益田路荣超",
    "华泰证券厦门厦禾路", "东方财富证券拉萨", "方正证券绍兴",
    "中信证券上海溧阳路", "中信证券杭州延安路",
)

_INST_KEYS = ("机构专用", "机构席位")


def _classify_seat(name: str) -> str:
    if not name:
        return "其他"
    if any(k in name for k in _INST_KEYS):
        return "机构"
    if any(k in name for k in _KNOWN_HOT_MONEY):
        return "知名游资"
    if "拉萨" in name or "宁波" in name or "绍兴" in name:
        return "知名游资"
    return "其他"


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def fetch_lhb(ak: Optional[Any], date_yyyymmdd: str, top: int = 8) -> Dict[str, Any]:
    """主路：AkShare stock_lhb_detail_em。失败返回 {}。

    输出包含：
    - 净买额前列个股（带 主营/解读/连涨天数 if 可得）
    - 机构集中买入（净买 > 0 且席位含『机构专用』）
    - 知名游资进出
    """
    if ak is None:
        return {}
    try:
        df = ak.stock_lhb_detail_em(start_date=date_yyyymmdd, end_date=date_yyyymmdd)
    except Exception as e:
        # AkShare 在无龙虎榜数据时会抛 'NoneType' object is not subscriptable
        # 在节假日/断网时也可能抛各种内部异常，统一兜底
        logger.warning("stock_lhb_detail_em failed: %s", e)
        return {}
    try:
        if df is None or getattr(df, "empty", True):
            return {}
        # 注意：pandas 的 Index 不能直接和 [] / `or` 进行布尔短路（会触发
        # "The truth value of a Index is ambiguous"）。先取出 columns，再 list 化。
        raw_cols = getattr(df, "columns", None)
        if raw_cols is None:
            return {}
        cols = list(raw_cols)
        if not cols:
            return {}
    except Exception as e:
        logger.warning("stock_lhb_detail_em returned invalid frame: %s", e)
        return {}

    name_c = "名称" if "名称" in cols else ("股票名称" if "股票名称" in cols else None)
    code_c = "代码" if "代码" in cols else ("股票代码" if "股票代码" in cols else None)
    pct_c = "涨跌幅" if "涨跌幅" in cols else None
    netbuy_c = None
    netbuy_in_yi = False
    for cand in ("龙虎榜净买额", "净买额", "龙虎榜净买额(元)", "净买额(元)"):
        if cand in cols:
            netbuy_c = cand
            break
    if not netbuy_c:
        for cand in ("龙虎榜净买额(亿)", "净买额(亿)"):
            if cand in cols:
                netbuy_c = cand
                netbuy_in_yi = True
                break
    reason_c = "上榜原因" if "上榜原因" in cols else None
    interpret_c = "解读" if "解读" in cols else None
    if not name_c or not netbuy_c:
        return {}

    rows: List[Dict[str, Any]] = []
    try:
        for _, r in df.iterrows():
            raw_net = _safe_float(r.get(netbuy_c))
            net_yi = round(raw_net, 3) if netbuy_in_yi else round(raw_net / 1e8, 3)
            rows.append({
                "名称": str(r.get(name_c, "")).strip(),
                "代码": str(r.get(code_c, "") or "").strip(),
                "涨跌幅": round(_safe_float(r.get(pct_c)), 2) if pct_c else None,
                "净买额(亿)": net_yi,
                "上榜原因": str(r.get(reason_c, "") or "").strip() if reason_c else "",
                "解读": str(r.get(interpret_c, "") or "").strip() if interpret_c else "",
            })
    except Exception as e:
        logger.warning("stock_lhb_detail_em row parse failed: %s", e)
        return {}

    if not rows:
        return {}
    rows.sort(key=lambda x: x["净买额(亿)"], reverse=True)
    inflow_top = rows[:top]
    outflow_top = sorted(rows, key=lambda x: x["净买额(亿)"])[:top]

    return {
        "数据日期": date_yyyymmdd,
        "净买入前列": inflow_top,
        "净卖出前列": outflow_top,
        "口径": "akshare:stock_lhb_detail_em (单日)",
    }


__all__ = ["fetch_lhb", "_classify_seat"]
