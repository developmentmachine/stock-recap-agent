"""OpenAI provider：Structured Outputs 优先，降级 json_object。"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

from stock_recap.config.settings import Settings
from stock_recap.domain.models import (
    LlmError,
    LlmTokens,
    LlmTransportError,
    Mode,
    Recap,
    RecapDaily,
    RecapStrategy,
)
from stock_recap.infrastructure.llm.parse import _stable_json, parse_and_validate
from stock_recap.observability.runtime_context import current_budget

logger = logging.getLogger("stock_recap.infrastructure.llm.providers.openai")


def _merge_tokens(tokens: LlmTokens, usage: Any) -> None:
    if not usage:
        return
    delta_in = getattr(usage, "prompt_tokens", 0) or 0
    delta_out = getattr(usage, "completion_tokens", 0) or 0
    tokens.input_tokens = (tokens.input_tokens or 0) + delta_in
    tokens.output_tokens = (tokens.output_tokens or 0) + delta_out
    tokens.total_tokens = (tokens.input_tokens or 0) + (tokens.output_tokens or 0)
    budget = current_budget.get()
    if budget is not None:
        budget.record_tokens(delta_in + delta_out)  # 超额抛 LlmBudgetExceeded


def _tool_loop(
    client: Any,
    model: str,
    messages: List[Dict[str, Any]],
    settings: Settings,
    db_path: str,
) -> Tuple[List[Dict[str, Any]], LlmTokens]:
    from stock_recap.infrastructure.tools.runner import RecapToolRunner

    tokens = LlmTokens()
    runner = RecapToolRunner(settings)
    active_tools = runner.openai_compatible_schemas()
    if not active_tools:
        return messages, tokens

    for _ in range(8):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            tools=active_tools,  # type: ignore[arg-type]
            tool_choice="auto",
            temperature=settings.temperature,
            timeout=settings.timeout_s,
        )
        _merge_tokens(tokens, getattr(resp, "usage", None))

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            break

        messages.append(
            msg.model_dump() if hasattr(msg, "model_dump") else {"role": "assistant", "content": msg.content}
        )
        from stock_recap.policy.tools import ToolPolicyError

        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            try:
                result = runner.execute(tc.function.name, args, db_path)
            except ToolPolicyError as e:
                # 策略拒绝（disabled / forbidden / per-tool budget / timeout）
                # 反馈给 LLM 单条「tool 失败」结果，让它根据 schema 选择别的工具
                # 或直接给最终答案；但不能让整次 LLM 循环崩。
                # 全局 LlmBudgetExceeded 不属于 ToolPolicyError，会向上抛由 pipeline 接住。
                result = f"[TOOL DENIED: {type(e).__name__}] {e}"
                logger.info(
                    _stable_json(
                        {"event": "tool_denied", "tool": tc.function.name, "reason": str(e)[:200]}
                    )
                )
            logger.info(
                _stable_json({"event": "tool_result", "tool": tc.function.name, "len": len(result)})
            )
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )

    return messages, tokens


class OpenAiProvider:
    name = "openai"

    def call(
        self,
        settings: Settings,
        mode: Mode,
        messages: List[Dict[str, str]],
        *,
        model: str,
        db_path: str,
        date: str,
    ) -> Tuple[Recap, LlmTokens]:
        from openai import OpenAI

        if not settings.openai_api_key:
            raise LlmError("缺少 OPENAI_API_KEY")
        client = OpenAI(api_key=settings.openai_api_key)
        recap_cls = RecapDaily if mode == "daily" else RecapStrategy

        msgs: List[Dict[str, Any]] = list(messages)  # type: ignore[arg-type]
        tokens = LlmTokens()
        if settings.tools_enabled:
            msgs, tokens = _tool_loop(client, model, msgs, settings, db_path)

        # Structured Outputs 优先
        try:
            resp = client.beta.chat.completions.parse(
                model=model,
                messages=msgs,  # type: ignore[arg-type]
                response_format=recap_cls,  # type: ignore[arg-type]
                temperature=settings.temperature,
                timeout=settings.timeout_s,
            )
            parsed = resp.choices[0].message.parsed
            if parsed is not None:
                _merge_tokens(tokens, getattr(resp, "usage", None))
                return parsed, tokens
        except Exception as e:
            logger.warning(
                _stable_json(
                    {"event": "structured_outputs_failed", "error": str(e), "fallback": "json_object"}
                )
            )

        # 降级：json_object
        try:
            resp2 = client.chat.completions.create(
                model=model,
                messages=msgs,  # type: ignore[arg-type]
                response_format={"type": "json_object"},
                temperature=settings.temperature,
                timeout=settings.timeout_s,
            )
        except Exception as e:
            logger.warning(_stable_json({"event": "openai_call_failed", "error": str(e)}))
            raise LlmTransportError(str(e)) from e

        content = resp2.choices[0].message.content or ""
        _merge_tokens(tokens, getattr(resp2, "usage", None))
        payload = parse_and_validate(content, mode)
        return payload, tokens
