"""Ollama provider（需要模型支持 function calling）。"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

import httpx

from stock_recap.config.settings import Settings
from stock_recap.domain.models import LlmError, LlmTokens, LlmTransportError, Mode, Recap
from stock_recap.infrastructure.llm.parse import _stable_json, parse_and_validate
from stock_recap.observability.runtime_context import current_budget

logger = logging.getLogger("stock_recap.infrastructure.llm.providers.ollama")


def _tool_loop(
    base_url: str,
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

    url = base_url.rstrip("/") + "/api/chat"
    for _ in range(8):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "tools": active_tools,
            "options": {"temperature": settings.temperature},
        }
        try:
            with httpx.Client(timeout=settings.timeout_s) as client:
                r = client.post(url, json=payload)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.warning(_stable_json({"event": "ollama_tool_loop_failed", "error": str(e)}))
            break

        msg = data.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            break

        messages.append(
            {"role": "assistant", "content": msg.get("content", ""), "tool_calls": tool_calls}
        )
        from stock_recap.policy.tools import ToolPolicyError

        for tc in tool_calls:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            try:
                result = runner.execute(fn.get("name", ""), args, db_path)
            except ToolPolicyError as e:
                # 同 openai_provider：策略拒绝转 tool message，避免整次循环崩。
                result = f"[TOOL DENIED: {type(e).__name__}] {e}"
            messages.append({"role": "tool", "content": result})

    return messages, tokens


class OllamaProvider:
    name = "ollama"

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
        url = settings.ollama_base_url.rstrip("/") + "/api/chat"

        msgs: List[Dict[str, Any]] = list(messages)  # type: ignore[arg-type]
        tokens = LlmTokens()
        if settings.tools_enabled:
            msgs, tokens = _tool_loop(settings.ollama_base_url, model, msgs, settings, db_path)

        payload = {
            "model": model,
            "messages": msgs,
            "stream": False,
            "options": {"temperature": settings.temperature},
        }
        try:
            with httpx.Client(timeout=settings.timeout_s) as client:
                r = client.post(url, json=payload)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.warning(_stable_json({"event": "ollama_call_failed", "error": str(e)}))
            raise LlmTransportError(str(e)) from e

        content = (data.get("message") or {}).get("content") or ""
        delta_in = data.get("prompt_eval_count") or 0
        delta_out = data.get("eval_count") or 0
        merged = LlmTokens(
            input_tokens=(tokens.input_tokens or 0) + delta_in,
            output_tokens=(tokens.output_tokens or 0) + delta_out,
        )
        merged.total_tokens = (merged.input_tokens or 0) + (merged.output_tokens or 0)
        budget = current_budget.get()
        if budget is not None:
            budget.record_tokens(delta_in + delta_out)
        recap = parse_and_validate(content, mode)
        return recap, merged
