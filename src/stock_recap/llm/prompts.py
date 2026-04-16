"""Prompt 构建层。

负责将 snapshot + features + 历史记忆 + 进化笔记 组装成 LLM messages。
注意：prompt_version 由记忆层管理，此处只使用。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from stock_recap.models import Features, MarketSnapshot, Mode, RecapDaily, RecapStrategy

PROMPT_BASE_VERSION = "2026-04-10"


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_system_prompt(
    evolution_guidance: Optional[str] = None,
    feedback_summary: Optional[Dict[str, Any]] = None,
) -> str:
    """构建 system prompt，注入进化指导和反馈摘要。"""
    base = (
        "你是资深A股交易员与研究员，输出必须可直接用于交易复盘会。\n"
        "风格要求：结论先行、因果链清晰、避免空话、禁止使用表情符号。\n"
        "必须严格输出 JSON，字段名与结构必须完全匹配给定 schema，不要输出额外字段。\n"
        "强规范：不得编造任何数字或事实，只能使用输入 snapshot/features 中给出的数据；"
        "若数据不足，明确写入风险提示而非补造。\n"
        "所有涉及价格/涨跌幅的数据必须来自 snapshot，不得凭记忆填写。"
    )

    if feedback_summary and feedback_summary.get("avg_rating") is not None:
        avg = feedback_summary["avg_rating"]
        low_tags = feedback_summary.get("low_rated_tags", [])
        praise_tags = feedback_summary.get("praise_tags", [])
        base += (
            f"\n\n【用户反馈摘要】平均评分 {avg}/5。"
        )
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
    """构建用户 prompt，包含所有上下文信息。"""
    payload: Dict[str, Any] = {
        "prompt_version": prompt_version,
        "mode": mode,
        "date": snapshot.date,
        "snapshot": snapshot.model_dump(),
        "features": features.model_dump(),
        "recent_memory": memory,
        "schema_hint": (
            RecapDaily.model_json_schema()
            if mode == "daily"
            else RecapStrategy.model_json_schema()
        ),
    }

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
) -> List[Dict[str, str]]:
    """组装完整的 messages 列表（system + user + instruction）。"""
    schema = (
        RecapDaily.model_json_schema()
        if mode == "daily"
        else RecapStrategy.model_json_schema()
    )

    return [
        {
            "role": "system",
            "content": build_system_prompt(
                evolution_guidance=evolution_guidance,
                feedback_summary=feedback_summary,
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
                    "instruction": (
                        "请仅返回一个 JSON 对象，严格符合 schema。"
                        "不得包含 markdown 代码块、不得包含解释文字。"
                    ),
                    "schema": schema,
                }
            ),
        },
    ]
