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

from stock_recap.config.settings import Settings
from stock_recap.domain.models import (
    Features,
    LlmBackend,
    LlmError,
    LlmTokens,
    LlmTransportError,
    MarketSnapshot,
    Mode,
    Recap,
    RecapDaily,
    RecapStrategy,
)
from stock_recap.infrastructure.llm.parse import (
    _stable_json,
    parse_and_validate as _parse_and_validate,
    parse_json_from_text,
)
from stock_recap.infrastructure.llm.providers import resolve_provider
from stock_recap.infrastructure.llm.providers._cli_shared import inject_prefetch as _inject_prefetch
from stock_recap.infrastructure.llm.resolve import (
    _interpret_model_spec,
    _model_prefix_to_backend,
    llm_backend_effective,
    model_effective,
)

logger = logging.getLogger("stock_recap.infrastructure.llm.backends")


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
    from stock_recap.observability.runtime_context import current_run_context
    from stock_recap.observability.tracing import get_tracer

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

    with tracer.start_as_current_span("llm.call", attributes=span_attrs):
        provider = resolve_provider(backend)
        return provider.call(
            settings,
            mode,
            messages,
            model=model,
            db_path=db_path,
            date=date,
        )
