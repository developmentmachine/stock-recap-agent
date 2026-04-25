"""LLM 后端统一入口（带重试 + 统一 tracing）。

实现已拆分：
- ``infrastructure/llm/parse.py``    —— 解析 + schema 校验
- ``infrastructure/llm/resolve.py``  —— backend/model 名解析
- ``infrastructure/llm/providers/``  —— ``LlmProvider`` Protocol + 各 provider

本模块保留 ``call_llm`` 的 tenacity 重试包装、span 注入、日志，以及对外暴露
兼容旧导入的公开符号（``_parse_and_validate`` / ``parse_json_from_text`` /
``_interpret_model_spec`` / ``llm_backend_effective`` / ``model_effective``）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from agent_platform.config.settings import Settings
from agent_platform.domain.models import (
    Features,
    LlmBackend,
    LlmBudgetExceeded,
    LlmBusinessError,
    LlmError,
    LlmTokens,
    LlmTransportError,
    MarketSnapshot,
    Mode,
    Recap,
    RecapDaily,
    RecapStrategy,
)
from agent_platform.infrastructure.llm.parse import (
    _stable_json,
    parse_and_validate as _parse_and_validate,
    parse_json_from_text,
)
from agent_platform.infrastructure.llm.providers import resolve_provider
from agent_platform.infrastructure.llm.providers._cli_shared import inject_prefetch as _inject_prefetch
from agent_platform.infrastructure.llm.resolve import (
    _interpret_model_spec,
    _model_prefix_to_backend,
    llm_backend_effective,
    model_effective,
)

logger = logging.getLogger("agent_platform.infrastructure.llm.backends")


__all__ = [
    "LlmBackend",
    "LlmError",
    "_inject_prefetch",
    "_interpret_model_spec",
    "_model_prefix_to_backend",
    "_parse_and_validate",
    "_stable_json",
    "call_llm",
    "llm_backend_effective",
    "model_effective",
    "parse_json_from_text",
]


@retry(
    retry=retry_if_exception_type(LlmTransportError),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=12),
    reraise=True,
)
def call_llm(
    settings: Settings,
    mode: Mode,
    messages: List[Dict[str, str]],
    model_spec: Optional[str] = None,
    db_path: str = ":memory:",
    date: str = "",
) -> Tuple[Recap, LlmTokens]:
    """选择 provider → 落入 ``llm.call`` span → 执行。重试策略 tenacity 包裹。"""
    from agent_platform.observability.runtime_context import current_run_context
    from agent_platform.observability.tracing import get_tracer

    backend = llm_backend_effective(model_spec, settings)
    model = model_effective(settings, model_spec)

    logger.info(
        _stable_json(
            {
                "event": "llm_call",
                "backend": backend,
                "model": model,
                "mode": mode,
                "tools": settings.tools_enabled,
            }
        )
    )

    ctx = current_run_context.get()
    tracer = get_tracer(__name__)
    span_attrs: Dict[str, Any] = {
        "llm.backend": backend,
        "llm.model": model,
        "llm.mode": mode,
        "llm.tools_enabled": settings.tools_enabled,
    }
    if ctx is not None:
        span_attrs["recap.request_id"] = ctx.request_id
        span_attrs["recap.trace_id"] = ctx.trace_id

    from agent_platform.observability.metrics import record_llm_call, record_llm_tokens

    with tracer.start_as_current_span("llm.call", attributes=span_attrs):
        provider = resolve_provider(backend)
        try:
            recap, tokens = provider.call(
                settings,
                mode,
                messages,
                model=model,
                db_path=db_path,
                date=date,
            )
        except LlmTransportError:
            record_llm_call(backend, "transport_error")
            raise
        except LlmBudgetExceeded:
            record_llm_call(backend, "budget_exceeded")
            raise
        except LlmBusinessError:
            record_llm_call(backend, "business_error")
            raise
        except Exception:
            record_llm_call(backend, "other")
            raise

        record_llm_call(backend, "ok")
        if tokens.input_tokens:
            record_llm_tokens(backend, "input", int(tokens.input_tokens))
        if tokens.output_tokens:
            record_llm_tokens(backend, "output", int(tokens.output_tokens))
        return recap, tokens
