"""核心业务逻辑：generate_once。

将数据采集、特征工程、prompt → LLM → 评测、持久化、推送串联。
供 CLI、API、调度器共用；可选 RunContext 与 OpenTelemetry 关联。
"""
from __future__ import annotations

import time
from typing import Iterator, Optional

from opentelemetry import trace

from stock_recap.application.orchestration.context import RecapAgentRunState
from stock_recap.application.orchestration.pipeline import (
    execute_recap_pipeline,
    iter_recap_agent_ndjson,
)
from stock_recap.application.side_effects import run_deferred_post_recap, try_run_backtest
from stock_recap.config.settings import Settings
from stock_recap.domain.models import GenerateRequest, GenerateResponse
from stock_recap.domain.run_context import RunContext
from stock_recap.observability.runtime_context import current_run_context
from stock_recap.observability.tracing import configure_tracing, get_tracer
from stock_recap.policy.guardrails import validate_generate_request


def generate_once(
    req: GenerateRequest,
    settings: Settings,
    ctx: Optional[RunContext] = None,
    *,
    defer_evolution_backtest: bool = False,
) -> GenerateResponse:
    """
    单次生成流程：采集 → 特征 → prompt → LLM → 评测 → 持久化 → 推送。
    具体阶段见 ``application.orchestration.pipeline.execute_recap_pipeline``。

    ``defer_evolution_backtest=True`` 时不在本调用内执行进化检查与策略回测（供 HTTP
    层用 BackgroundTasks 延后执行，以缩短响应尾部延迟）；推送仍在请求内完成。
    """
    configure_tracing(settings)
    validate_generate_request(req)

    run_ctx = ctx or RunContext.new()
    request_id = run_ctx.request_id
    t0 = time.time()
    ctx_token = current_run_context.set(run_ctx)
    tracer = get_tracer(__name__)

    try:
        with tracer.start_as_current_span(
            "recap.generate",
            attributes={
                "recap.request_id": request_id,
                "recap.trace_id": run_ctx.trace_id,
                "recap.mode": req.mode,
                "recap.provider": str(req.provider),
            },
        ):
            if run_ctx.session_id:
                span = trace.get_current_span()
                span.set_attribute("recap.session_id", run_ctx.session_id)

            state = RecapAgentRunState(
                request=req,
                settings=settings,
                run_ctx=run_ctx,
                t0=t0,
                defer_evolution_backtest=defer_evolution_backtest,
            )
            return execute_recap_pipeline(state)
    finally:
        current_run_context.reset(ctx_token)


def iter_generate_ndjson(
    req: GenerateRequest,
    settings: Settings,
    ctx: Optional[RunContext] = None,
    *,
    defer_evolution_backtest: bool = True,
) -> Iterator[str]:
    """
    产出 NDJSON 行（``meta``、各 ``phase``、``result``），供 HTTP 流式端点使用。
    若 ``defer_evolution_backtest=True``，在流结束后于当前 worker 内执行进化与回测。

    不在此路径上设置 ``ContextVar``/父 span：``StreamingResponse`` 可能在线程池中
    迭代生成器，跨线程 attach/detach 会失败；``meta``/``result`` 中仍含 request_id。
    """
    configure_tracing(settings)
    validate_generate_request(req)

    run_ctx = ctx or RunContext.new()
    request_id = run_ctx.request_id
    t0 = time.time()

    state = RecapAgentRunState(
        request=req,
        settings=settings,
        run_ctx=run_ctx,
        t0=t0,
        defer_evolution_backtest=defer_evolution_backtest,
    )
    yield from iter_recap_agent_ndjson(state)
    if (
        defer_evolution_backtest
        and state.stream_pipeline_completed
        and state.snapshot is not None
    ):
        run_deferred_post_recap(
            request_id,
            req.mode,
            state.snapshot.date,
            state.recap is not None,
        )


# 对 cli/scheduler 的后向兼容别名（保持旧导入 `from application.recap import _try_run_backtest` 有效）。
_try_run_backtest = try_run_backtest
