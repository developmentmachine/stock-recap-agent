"""Prompt 构建层。

负责将 snapshot + features + 历史记忆 + 进化笔记 组装成 LLM messages。
系统级长文本来自包内资源 ``resources/prompts``（manifest 版本化）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set

from stock_recap.domain.models import Features, MarketSnapshot, Mode, RecapDaily, RecapStrategy
from stock_recap.resources.prompts.loader import (
    PROMPT_BASE_VERSION,
    json_output_instruction,
    pattern_extraction_system,
    system_recap_base,
)
from stock_recap.skills.loader import load_skill_overlay_for_mode

# 涨跌幅为 0 是有效数据，不能过滤
_KEEP_ZERO_KEYS: Set[str] = {"涨跌幅", "净买入(亿)", "涨跌幅(%)"}


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _drop_empty(obj: Any) -> Any:
    """递归移除空值（None / {} / [] / ""），保留数值 0。"""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            cleaned = _drop_empty(v)
            if cleaned not in (None, {}, [], ""):
                result[k] = cleaned
            elif k in _KEEP_ZERO_KEYS and cleaned == 0.0:
                result[k] = cleaned
        return result
    if isinstance(obj, list):
        return [_drop_empty(i) for i in obj if i not in (None, {}, [], "")]
    return obj


# 元数据字段：对 LLM 分析无价值，过滤掉（futures 保留以便将来注入加密/期指等）
_META_KEYS = {"asof", "sources", "provider", "is_trading_day"}


def _clean_snapshot(snapshot: MarketSnapshot) -> Dict[str, Any]:
    raw = snapshot.model_dump()
    for key in _META_KEYS:
        raw.pop(key, None)
    return _drop_empty(raw)


def _snapshot_for_llm(mode: Mode, snapshot: MarketSnapshot) -> Dict[str, Any]:
    """日终复盘不向模型暴露北向字段（口径长期不可用），其余按 _clean_snapshot。"""
    raw = _clean_snapshot(snapshot)
    if mode == "daily":
        raw.pop("northbound_flow", None)
    return raw


def _llm_data_coverage(snapshot: MarketSnapshot) -> Dict[str, Any]:
    """告知模型当前有哪些输入主题，避免写「输入里不存在」的外盘/北向等。"""
    sent = snapshot.market_sentiment or {}
    sp = snapshot.sector_performance or {}
    idx = snapshot.a_share_indices or {}
    fut = snapshot.futures or {}
    cm = snapshot.cross_market or {}
    om = snapshot.us_market or {}
    movers = (om.get("movers") or {}) if isinstance(om, dict) else {}
    concept = sp.get("概念") or {}
    sff = snapshot.sector_fund_flow or {}
    lup = snapshot.limit_up_pool or {}
    iff = (sent.get("个股资金流") or {}) if isinstance(sent, dict) else {}
    cont = snapshot.continuity or {}
    style = snapshot.style_matrix or {}
    lhb = snapshot.lhb or {}
    fwl = snapshot.forward_watchlist or {}
    liq = snapshot.liquidity or {}
    leaders = snapshot.sector_leaders or {}
    s5d = snapshot.sector_5d_strength or {}
    has_board_leaders = False
    for arr in (sp.get("涨幅前10") or [], sp.get("跌幅前10") or []):
        for it in arr:
            if isinstance(it, dict) and it.get("领涨股票"):
                has_board_leaders = True
                break
        if has_board_leaders:
            break
    checks: Dict[str, Any] = {
        "a_share_indices": bool(idx),
        "liquidity_and_breadth": bool(
            sent.get("两市成交额(亿)") is not None
            or "上涨家数" in sent
            or "下跌家数" in sent
        ),
        "limit_up_down": ("涨停家数" in sent) or ("跌停家数" in sent),
        "main_fund_flow": bool(sent.get("大盘主力资金流")),
        "sector_leader_board": bool(sp.get("涨幅前10") or sp.get("跌幅前10")),
        "sector_concept_layer": bool(concept.get("涨幅前10") or concept.get("跌幅前10")),
        "sector_relative_benchmark": bool(sp.get("相对表现")),
        "sector_board_leading_stock": has_board_leaders,
        "sector_fund_flow": bool(
            (sff.get("行业") or {}).get("净流入前列")
            or (sff.get("概念") or {}).get("净流入前列")
        ),
        "sector_fund_flow_outflow": bool(
            (sff.get("行业") or {}).get("净流出前列")
            or (sff.get("概念") or {}).get("净流出前列")
        ),
        "sector_decline_board": bool(sp.get("跌幅前10")),
        "limit_up_pool": bool(lup.get("题材聚合") or lup.get("高位连板")),
        "individual_fund_flow": bool(iff.get("净流入前列") or iff.get("净流出前列")),
        "continuity": bool(cont.get("接力梯队_top") or cont.get("接力涨停率(%)") is not None),
        "style_matrix": bool(style.get("矩阵")),
        "lhb": bool(lhb.get("净买入前列") or lhb.get("净卖出前列")),
        "forward_watchlist": bool(fwl.get("高确信候选") or fwl.get("板块_涨幅与资金双重确认")),
        "liquidity": bool(liq.get("货币市场") or liq.get("中国10年国债") or liq.get("美元离岸人民币")),
        "sector_leaders_matrix": bool(leaders.get("强势行业龙头矩阵")),
        "sector_5d_strength": bool(s5d.get("样本")),
        "hot_rank": bool(sent.get("热度榜前列")),
        "us_market": bool(snapshot.us_market),
        "us_etf_proxies": bool(om.get("etf参考")),
        "us_mag7": bool(movers.get("mag7")),
        "us_china_adr": bool(movers.get("中概股_adr")),
        "commodities": bool(snapshot.commodities),
        "futures_block": bool(fut),
        "cross_market": bool(cm.get("paired_observations") or cm.get("adr_镜像")),
    }
    present = [k for k, v in checks.items() if v]
    absent = [k for k, v in checks.items() if not v]
    return {
        "topic_flags": checks,
        "present_topics": present,
        "absent_topics": absent,
        "writing_rules": (
            "只使用 snapshot 与 features 中已出现的字段与数字；"
            "absent_topics 中的主题在三大段正文中一律不要提及（不解释原因、不用常识补写）。"
            "第一大类（指数与风格与流动性）：在指数与量能之外，若 style_matrix 为真，必须用 snapshot.style_matrix.矩阵 中至少 1 条带数字的 spread 做风格定性（『大盘占优/小盘占优』『成长占优/价值占优』『微盘补涨/退潮』择一以上），不要只罗列指数涨跌。"
            "若 liquidity 为真：必须新增 1 条 bullet，引用 snapshot.liquidity 中至少 1 个利率/汇率指标的当日水平 + 环比变动（如 SHIBOR_O/N 利率与 bp 变动、10Y国债收益率 与 bp 变动、USD-CNH 中间价择一以上），并复用其『定性』字段做一句结论（短端松紧 / 长端方向 / CNH 强弱）；该 bullet 必须落在第一大类内，不要塞到第三大类外盘里。"
            "第二大类（主线与内资行为）必须包含「强势方向」与「退潮方向」的对照陈述（缺一不可，需各 ≥1 条 bullet 或一条 bullet 内并列）："
            "  · 强势方向：从 sector_performance.涨幅前10 选 2～3 个板块（写出板块名+涨跌幅%），并在 sector_fund_flow.行业.净流入前列 中找资金面同时验证的板块，写明『资金确认 / 仅情绪驱动』之一；若 sector_concept_layer 为真，再在 sector_performance.概念.涨幅前10 中点出领跑题材。"
            "  · 退潮方向：从 sector_performance.跌幅前10 选 2 个板块（写出板块名+跌幅%），若 sector_fund_flow_outflow 为真，结合 sector_fund_flow.行业.净流出前列 / 概念.净流出前列 的主力净流出(亿) 做资金面确认；若仅有跌幅榜则只写跌幅。"
            "  · 个股锚点：若 limit_up_pool 为真，从 题材聚合 与 高位连板 列表中点 1～2 只连板梯队个股名+连板高度；若 sector_board_leading_stock 为真，至少在『强势方向』中带上一只领涨股票名（来自 sector_performance.涨幅前10[*].领涨股票）；若 individual_fund_flow 为真，可补 1 只 个股资金流.净流入前列 中的个股名作为资金抢筹证据；若 sector_leaders_matrix 为真，必须在 强势方向 中至少引用 snapshot.sector_leaders.强势行业龙头矩阵 中 1 个板块的 成分股_top5 中 1～2 只个股名 + 涨跌幅，体现板内龙头扩散度（同步带出 板内涨停数）；个股名一律只能来自上述字段或 hot_rank、lhb、forward_watchlist。"
            "  · 持续性 vs 单日脉冲（若 sector_5d_strength 为真）：必须在 强势方向 或 退潮方向 中追加一句『新热点 vs 高位扩散』判断——把当日强势板块的『近5日累计涨跌幅』与『当日涨跌幅』对照（数据均来自 snapshot.sector_5d_strength.样本 与 sector_performance.涨幅前10），近5日累计 ≥ 8% 则定性『高位扩散』，≤ 1% 则定性『新热点启动』，介于其间为『持续主线』。"
            "  · 连续性（若 continuity 为真）：必须给出『昨涨停今表现』量化判断——引用 接力涨停率(%) 或 高位连板接力 数字；并在 接力梯队_top 中点 1 只接力个股名（含『今日涨跌幅』）；若 退潮个股_top 非空，对照点 1 只退潮个股，体现『情绪是否承接』。"
            "  · 龙虎榜（若 lhb 为真）：在『资金抢筹』或『主力出货』维度，至少引用 1 条 净买入前列（写出名称 + 净买额(亿)），优先选择『解读』或『上榜原因』含『机构』字样的条目并标注『机构席位』；若 净卖出前列 非空，可对照 1 条作为反向证据。"
            "  · 主力资金（若 main_fund_flow）：必须结合主力/大单/超大单等已给数字给出资金定性（进攻/防守/试探择一为主）；若 sector_relative_benchmark 为真，须引用相对表现里『超额涨跌幅_相对沪深300』解释行业是主动强势还是被动跟涨。"
            "  · 严格禁止使用『暂无 / 数据缺失 / 难以判断』等遁词；某项 flag 为假时直接跳过该项即可。"
            "禁止北向、沪深港通、外资通道等任何表述。"
            "若 forward_watchlist 为真：第二大类末尾或独立 bullet 必须新增『明日观察』一条，仅引用 snapshot.forward_watchlist.高确信候选 中 score ≥2 的 1～3 只个股（写出名称 + reasons 中前 2 条因子链），与 snapshot.forward_watchlist.板块_涨幅与资金双重确认 中 1～2 个板块；该条须明确标注『次日观察 / 非买入建议』，禁止给出价位或仓位。"
            "第三大类（外部与风险偏好）：仅依据 us_market（含 etf参考 若 us_etf_proxies 为真，含 movers.mag7 / movers.中概股_adr 若对应 flag 为真）、commodities、futures_block、以及 cross_market（若 cross_market 为真）；"
            "若 us_mag7 为真：必须点到至少 2 只 Mag7 个股的涨跌方向，写清美股内部分化（不要只复述三大指数）；"
            "若 us_china_adr 为真：必须用中概 ADR（如阿里、拼多多、京东、蔚来等）写出『海外投资者对中国资产』当日态度；"
            "若 cross_market 为真：至少 1 条 bullet 仅基于 paired_observations 与/或 adr_镜像 中的数字做中美同日对照，禁止外推隔夜因果；"
            "缺上述全部外盘字段且 cross_market 为假时，第三大类整段不写外盘子话题。"
        ),
    }


def _strategy_data_coverage(snapshot: MarketSnapshot) -> Dict[str, Any]:
    """次日策略模式：聚焦 forward_watchlist、连续性、龙虎榜、流动性、5日板块强弱。"""
    fwl = snapshot.forward_watchlist or {}
    cont = snapshot.continuity or {}
    lhb = snapshot.lhb or {}
    liq = snapshot.liquidity or {}
    leaders = snapshot.sector_leaders or {}
    s5d = snapshot.sector_5d_strength or {}
    sff = snapshot.sector_fund_flow or {}
    lup = snapshot.limit_up_pool or {}

    checks: Dict[str, Any] = {
        "forward_watchlist": bool(
            fwl.get("高确信候选") or fwl.get("板块_涨幅与资金双重确认")
        ),
        "continuity": bool(
            cont.get("接力梯队_top") or cont.get("接力涨停率(%)") is not None
        ),
        "lhb": bool(lhb.get("净买入前列") or lhb.get("净卖出前列")),
        "limit_up_pool": bool(lup.get("题材聚合") or lup.get("高位连板")),
        "sector_fund_flow": bool(
            (sff.get("行业") or {}).get("净流入前列")
            or (sff.get("概念") or {}).get("净流入前列")
        ),
        "liquidity": bool(
            liq.get("货币市场") or liq.get("中国10年国债") or liq.get("美元离岸人民币")
        ),
        "sector_leaders_matrix": bool(leaders.get("强势行业龙头矩阵")),
        "sector_5d_strength": bool(s5d.get("样本")),
    }
    present = [k for k, v in checks.items() if v]
    absent = [k for k, v in checks.items() if not v]
    return {
        "topic_flags": checks,
        "present_topics": present,
        "absent_topics": absent,
        "writing_rules": (
            "次日策略输出（RecapStrategy）有三个字段必须严格落地，所有名字均不得凭记忆补写：\n"
            " - mainline_focus（≥1 条）：若 forward_watchlist 为真，必须从 snapshot.forward_watchlist.板块_涨幅与资金双重确认 中至少选 1 个板块作为锚点（写成『板块名｜涨跌幅%｜主力净流入(亿)｜延续逻辑一句』）；若 sector_5d_strength 为真，再叠加 1 句『近5日累计涨幅 X%，定性新热点/持续主线/高位扩散』。\n"
            " - trading_logic（≥2 条）：若 forward_watchlist.高确信候选 非空，必须把 score ≥ 2 的 1～3 只个股按『名称｜信号链（reasons 中前 2 条）｜跟踪触发条件（如『次日竞价高开 ≤3% 且分时获 主力流入 持续』择一）』成条；若 continuity 为真，必须用『接力涨停率(%)』量化『情绪是否承接』并据此给出『进攻 / 谨慎 / 撤退』的三档定性；若 lhb 为真，机构席位净买入个股可单独成 1 条作为『资金侧确认』。\n"
            " - risk_warnings（≥1 条）：若 liquidity 为真，必须用 SHIBOR/10Y国债/USD-CNH 中至少 1 个的当日水平 + bp 变动量化『短端收紧 / 长端走高 / CNH 走弱』作为风险锚点；若 continuity.高位连板接力 ≤ 2 或 接力涨停率(%) ≤ 30，必须明确写出『高位股补跌 / 情绪退潮』风险。\n"
            "禁止使用『暂无 / 数据缺失』等遁词；某项 flag 为假时直接跳过，不要写占位句；禁止给出价位、目标价、仓位、止盈止损等具体交易指令；所有内容必须可追溯到 snapshot 已出现的字段与数字。"
        ),
    }


def build_system_prompt(
    mode: Mode,
    evolution_guidance: Optional[str] = None,
    feedback_summary: Optional[Dict[str, Any]] = None,
    skill_id_override: Optional[str] = None,
) -> str:
    base = system_recap_base()

    skill_doc = load_skill_overlay_for_mode(mode, override_skill_id=skill_id_override)
    if skill_doc is not None:
        label = skill_doc.name or skill_doc.skill_id
        base += f"\n\n【Agent Skill — {label}】\n{skill_doc.body.strip()}"

    if feedback_summary and feedback_summary.get("avg_rating") is not None:
        avg = feedback_summary["avg_rating"]
        low_tags = feedback_summary.get("low_rated_tags", [])
        praise_tags = feedback_summary.get("praise_tags", [])
        base += f"\n\n【用户反馈摘要】平均评分 {avg}/5。"
        if low_tags:
            base += f" 差评高频标签（请避免）：{', '.join(low_tags)}。"
        if praise_tags:
            base += f" 好评高频标签（请保持）：{', '.join(praise_tags)}。"

    if evolution_guidance:
        base += f"\n\n【历史进化指导】\n{evolution_guidance}"

    return base


def build_user_prompt(
    mode: Mode,
    snapshot: MarketSnapshot,
    features: Features,
    memory: List[Dict[str, Any]],
    prompt_version: str,
    backtest_context: Optional[str] = None,
    pattern_summary: Optional[str] = None,
) -> str:
    payload: Dict[str, Any] = {
        "prompt_version": prompt_version,
        "mode": mode,
        "date": snapshot.date,
        "snapshot": _snapshot_for_llm(mode, snapshot),
        "features": _drop_empty(features.model_dump()),
        "recent_memory": memory,
        "schema_hint": (
            RecapDaily.model_json_schema()
            if mode == "daily"
            else RecapStrategy.model_json_schema()
        ),
    }
    if mode == "daily":
        payload["data_coverage"] = _llm_data_coverage(snapshot)
    elif mode == "strategy":
        payload["data_coverage"] = _strategy_data_coverage(snapshot)

    if backtest_context:
        payload["backtest_context"] = backtest_context

    if pattern_summary:
        payload["market_pattern_summary"] = pattern_summary

    return _stable_json(payload)


def build_messages(
    mode: Mode,
    snapshot: MarketSnapshot,
    features: Features,
    memory: List[Dict[str, Any]],
    prompt_version: str,
    evolution_guidance: Optional[str] = None,
    feedback_summary: Optional[Dict[str, Any]] = None,
    backtest_context: Optional[str] = None,
    pattern_summary: Optional[str] = None,
    skill_id_override: Optional[str] = None,
) -> List[Dict[str, str]]:
    schema = (
        RecapDaily.model_json_schema()
        if mode == "daily"
        else RecapStrategy.model_json_schema()
    )

    return [
        {
            "role": "system",
            "content": build_system_prompt(
                mode=mode,
                evolution_guidance=evolution_guidance,
                feedback_summary=feedback_summary,
                skill_id_override=skill_id_override,
            ),
        },
        {
            "role": "user",
            "content": build_user_prompt(
                mode=mode,
                snapshot=snapshot,
                features=features,
                memory=memory,
                prompt_version=prompt_version,
                backtest_context=backtest_context,
                pattern_summary=pattern_summary,
            ),
        },
        {
            "role": "user",
            "content": _stable_json(
                {
                    "instruction": json_output_instruction(),
                    "schema": schema,
                }
            ),
        },
    ]


__all__ = [
    "PROMPT_BASE_VERSION",
    "build_messages",
    "build_system_prompt",
    "build_user_prompt",
    "pattern_extraction_system",
]
