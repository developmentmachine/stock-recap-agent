"""显式 Agent 阶段编排：感知 → 记忆 → 规划 → 行动 → 批判 → 持久化 → 副作用。"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, List, Optional, Tuple, cast

from opentelemetry import trace

from stock_recap.application.experiments import select_variant
from stock_recap.application.memory.manager import (
    check_and_run_evolution,
    extract_market_patterns,
    get_prompt_version,
    load_evolution_guidance,
    load_recent_memory,
)
from stock_recap.application.memory.vector_ops import (
    index_recap_for_memory,
    recall_vector_memory,
)
from stock_recap.application.orchestration.context import RecapAgentRunState
from stock_recap.domain.models import (
    GenerateResponse,
    LlmBudgetExceeded,
    LlmBusinessError,
)
from stock_recap.infrastructure.data.collector import collect_snapshot
from stock_recap.infrastructure.data.features import build_features
from stock_recap.infrastructure.llm.backends import call_llm, model_effective
from stock_recap.infrastructure.llm.eval import auto_eval
from stock_recap.infrastructure.llm.prompts import build_messages
from stock_recap.infrastructure.persistence.db import (
    insert_recap_audit,
    insert_run,
    load_feedback_summary,
)
from stock_recap.observability.metrics import (
    record_phase_duration,
    record_recap_run,
)
from stock_recap.observability.tracing import get_tracer
from stock_recap.policy.guardrails import clamp_llm_messages, coerce_recap_output
from stock_recap.presentation.render.renderers import render_markdown, render_wechat_text

from stock_recap.application.side_effects import (
    load_recent_backtests_simple,
    try_run_backtest,
)
from stock_recap.application.side_effects.push import push_recap as _push_recap

logger = logging.getLogger("stock_recap.application.orchestration.pipeline")

PhaseName = str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _span_phase(tracer: Any, name: str, attrs: Optional[dict[str, Any]] = None) -> Any:
    return tracer.start_as_current_span(name, attributes=attrs or {})


def _phase_perceive(state: RecapAgentRunState, tracer: Any) -> None:
    req = state.request
    with _span_phase(tracer, "recap.agent.perceive", {"agent.phase": "perceive"}):
        state.snapshot = collect_snapshot(
            req.provider,
            req.date,
            skip_trading_check=req.skip_trading_check,
        )
        state.features = build_features(state.snapshot)


def _phase_recall(state: RecapAgentRunState, tracer: Any) -> None:
    req = state.request
    settings = state.settings
    with _span_phase(tracer, "recap.agent.recall", {"agent.phase": "recall"}):
        assert state.snapshot is not None and state.features is not None
        tenant_id = state.run_ctx.tenant_id
        state.memory = load_recent_memory(
            settings.db_path,
            date=state.snapshot.date,
            mode=req.mode,
            limit=settings.max_history_for_context,
            tenant_id=tenant_id,
        )
        state.evolution_guidance = load_evolution_guidance(settings.db_path)
        state.feedback_summary = load_feedback_summary(settings.db_path, tenant_id=tenant_id)

        try:
            state.pattern_summary = extract_market_patterns(
                settings.db_path,
                days=settings.pattern_extraction_days,
                settings=settings,
                model_spec=req.model,
            )
        except Exception as e:
            logger.warning(
                _stable_json({"event": "pattern_extraction_skipped", "error": str(e)})
            )
            state.pattern_summary = None

        bt_history = load_recent_backtests_simple(settings.db_path, limit=3)
        if bt_history:
            state.backtest_context = "近期回测评分：" + " | ".join(
                f"{b['strategy_date']} 命中率={b.get('hit_rate', 0):.0%}" for b in bt_history
            )
        else:
            state.backtest_context = None

        state.prompt_version = get_prompt_version(settings.db_path)

        long_m, ent_m, vec_meta = recall_vector_memory(
            settings,
            tenant_id=tenant_id,
            mode=req.mode,
            snapshot=state.snapshot,
            features=state.features,
        )
        state.memory_long = long_m
        state.memory_entities = ent_m
        state.memory_recall_meta = vec_meta

        # 实验分桶：用 session_id 优先（同一用户黏性），其次 request_id（一次性）。
        # 命中后 prompt_version 被 variant 绑定的版本覆盖，但活跃全局版本仍记 trace。
        stickiness = state.run_ctx.session_id or state.run_ctx.request_id
        assignment = select_variant(
            settings.db_path, mode=req.mode, stickiness_key=stickiness
        )
        if assignment is not None:
            state.experiment_id = assignment.experiment_id
            state.variant_id = assignment.variant_id
            state.prompt_version = assignment.prompt_version
            logger.info(
                _stable_json(
                    {
                        "event": "prompt_variant_assigned",
                        "experiment_id": assignment.experiment_id,
                        "variant_id": assignment.variant_id,
                        "prompt_version": assignment.prompt_version,
                    }
                )
            )


def _phase_plan(state: RecapAgentRunState, tracer: Any) -> None:
    req = state.request
    settings = state.settings
    with _span_phase(tracer, "recap.agent.plan", {"agent.phase": "plan"}):
        assert state.snapshot is not None and state.features is not None
        raw_messages: List[dict[str, Any]] = list(
            build_messages(
                mode=req.mode,
                snapshot=state.snapshot,
                features=state.features,
                memory=state.memory,
                memory_long=state.memory_long,
                memory_entities=state.memory_entities,
                prompt_version=state.prompt_version,
                evolution_guidance=state.evolution_guidance,
                feedback_summary=state.feedback_summary,
                backtest_context=state.backtest_context,
                pattern_summary=state.pattern_summary,
                skill_id_override=settings.skill_id_override,
            )
        )
        state.messages = cast(List[dict[str, str]], clamp_llm_messages(raw_messages))


_CRITIC_FEEDBACK_TEMPLATE = (
    "你的上一次输出被自动校验拦截，原因如下：\n"
    "{reason}\n\n"
    "请严格按既定 JSON schema 重新输出。"
    "不要复述本条反馈，只输出符合 schema 的最终 JSON。"
)


def _inject_critic_feedback(state: RecapAgentRunState, reason: str) -> None:
    """把 schema/parse 失败原因结构化注入 messages，供下一次 LLM 调用消化。

    我们 **不** 拼接上一次的失败响应（避免污染 prompt 与暴露不可信文本），
    只发一条 ``user`` 反馈说明 + 提醒 schema。
    """
    state.messages.append(
        {
            "role": "user",
            "content": _CRITIC_FEEDBACK_TEMPLATE.format(reason=reason),
        }
    )


def _phase_act(state: RecapAgentRunState, tracer: Any) -> None:
    req = state.request
    settings = state.settings
    with _span_phase(tracer, "recap.agent.act", {"agent.phase": "act", "llm.forced": req.force_llm}):
        assert state.snapshot is not None and state.features is not None
        if not req.force_llm:
            return

        max_attempts = 1 + max(0, int(settings.agent_critic_max_retries))
        last_business_err: Optional[LlmBusinessError] = None

        for attempt in range(max_attempts):
            try:
                state.recap, state.tokens = call_llm(
                    settings=settings,
                    mode=req.mode,
                    messages=state.messages,
                    model_spec=req.model,
                    db_path=settings.db_path,
                    date=state.snapshot.date,
                )
                state.recap = coerce_recap_output(state.recap)
                state.rendered_markdown = render_markdown(state.recap)
                state.rendered_wechat_text = render_wechat_text(state.recap)
                state.llm_error = None
                if attempt > 0:
                    logger.info(
                        _stable_json(
                            {
                                "event": "critic_retry_succeeded",
                                "attempt": attempt + 1,
                                "max_attempts": max_attempts,
                            }
                        )
                    )
                return
            except LlmBudgetExceeded as e:
                # 预算耗尽：不再 critic 重入；优雅落库给后续阶段。
                state.budget_error = f"{e.kind}:{e.used}/{e.limit}"
                state.llm_error = f"budget_exceeded({e.kind}: used={e.used} limit={e.limit})"
                logger.warning(
                    _stable_json(
                        {
                            "event": "act_budget_exceeded",
                            "kind": e.kind,
                            "used": e.used,
                            "limit": e.limit,
                            "attempt": attempt + 1,
                        }
                    )
                )
                return
            except LlmBusinessError as e:
                last_business_err = e
                state.llm_error = f"business_error: {e}"
                if attempt + 1 < max_attempts:
                    state.critic_retries_used = attempt + 1
                    _inject_critic_feedback(state, str(e))
                    logger.warning(
                        _stable_json(
                            {
                                "event": "critic_retry",
                                "attempt": attempt + 1,
                                "max_attempts": max_attempts,
                                "reason": str(e),
                            }
                        )
                    )
                    continue
                logger.error(
                    _stable_json(
                        {
                            "event": "critic_retry_exhausted",
                            "attempts": max_attempts,
                            "reason": str(last_business_err),
                        }
                    )
                )
                return
            except Exception as e:
                # 传输类错误已被 tenacity 重试过；这里是最终结果，不再 critic 重入。
                state.llm_error = str(e)
                logger.error(_stable_json({"event": "generate_failed", "error": state.llm_error}))
                return


def _phase_critique(state: RecapAgentRunState, tracer: Any) -> None:
    with _span_phase(tracer, "recap.agent.critique", {"agent.phase": "critique"}):
        assert state.snapshot is not None and state.features is not None
        state.eval_result = auto_eval(state.recap, state.snapshot, state.features)


def _phase_persist(state: RecapAgentRunState, tracer: Any) -> None:
    req = state.request
    settings = state.settings
    run_ctx = state.run_ctx
    latency_ms = int((time.time() - state.t0) * 1000)
    with _span_phase(
        tracer,
        "recap.agent.persist",
        {"agent.phase": "persist", "recap.latency_ms": latency_ms},
    ):
        assert state.snapshot is not None and state.features is not None
        insert_run(
            settings.db_path,
            request_id=run_ctx.request_id,
            created_at=_utc_now_iso(),
            mode=req.mode,
            provider=req.provider,
            date=state.snapshot.date,
            prompt_version=state.prompt_version,
            model=model_effective(settings, req.model) if req.force_llm else None,
            snapshot=state.snapshot,
            features=state.features,
            recap=state.recap,
            rendered_markdown=state.rendered_markdown,
            rendered_wechat_text=state.rendered_wechat_text,
            eval_obj=state.eval_result,
            error=state.llm_error,
            latency_ms=latency_ms,
            tokens=state.tokens,
            experiment_id=state.experiment_id,
            variant_id=state.variant_id,
            tenant_id=run_ctx.tenant_id,
        )

        if settings.recap_audit_enabled:
            try:
                insert_recap_audit(
                    settings.db_path,
                    request_id=run_ctx.request_id,
                    created_at=_utc_now_iso(),
                    mode=str(req.mode),
                    provider=str(req.provider),
                    prompt_version=state.prompt_version,
                    model=model_effective(settings, req.model) if req.force_llm else None,
                    trace_id=run_ctx.trace_id,
                    session_id=run_ctx.session_id,
                    messages=state.messages or None,
                    recap=state.recap,
                    eval_obj=state.eval_result or None,
                    tokens=state.tokens,
                    llm_error=state.llm_error,
                    budget_error=state.budget_error,
                    critic_retries_used=state.critic_retries_used,
                    experiment_id=state.experiment_id,
                    variant_id=state.variant_id,
                    tenant_id=run_ctx.tenant_id,
                )
            except Exception as e:
                logger.warning(
                    _stable_json({"event": "recap_audit_write_failed", "error": str(e)})
                )


def _phase_index_memory(state: RecapAgentRunState, tracer: Any) -> None:
    """将本次 recap 写入向量库（可选；未配置 Qdrant/OpenAI 时跳过）。"""
    req = state.request
    run_ctx = state.run_ctx
    with _span_phase(tracer, "recap.agent.index_memory", {"agent.phase": "index_memory"}):
        if state.recap is None:
            return
        try:
            index_recap_for_memory(
                state.settings,
                tenant_id=run_ctx.tenant_id,
                request_id=run_ctx.request_id,
                mode=req.mode,
                recap=state.recap,
            )
        except Exception as e:
            logger.warning(
                _stable_json({"event": "index_memory_phase_failed", "error": str(e)})
            )


def _phase_reflect(state: RecapAgentRunState, tracer: Any) -> None:
    req = state.request
    settings = state.settings
    run_ctx = state.run_ctx
    request_id = run_ctx.request_id
    with _span_phase(
        tracer,
        "recap.agent.reflect",
        {
            "agent.phase": "reflect",
            "recap.defer_evolution_backtest": state.defer_evolution_backtest,
        },
    ):
        if not state.defer_evolution_backtest:
            try:
                check_and_run_evolution(
                    settings.db_path,
                    settings=settings,
                    trigger_run_id=request_id,
                    force=False,
                    model_spec=req.model,
                )
            except Exception as e:
                logger.warning(
                    _stable_json({"event": "evolution_check_failed", "error": str(e)})
                )

        if state.recap is not None:
            # 走 push_recap：内置 (request_id, channel) 幂等账本（push_log），
            # 重试 / outbox 兜底 / scheduler 重发都安全。
            try:
                state.push_result = _push_recap(
                    settings, state.recap, request_id=request_id
                )
            except Exception as e:
                logger.warning(_stable_json({"event": "push_failed", "error": str(e)}))
                state.push_result = False

        if (
            not state.defer_evolution_backtest
            and req.mode == "daily"
            and state.recap is not None
        ):
            assert state.snapshot is not None
            try_run_backtest(settings.db_path, state.snapshot.date)


_PHASE_ORDER: Tuple[Tuple[PhaseName, Callable[[RecapAgentRunState, Any], None]], ...] = (
    ("perceive", _phase_perceive),
    ("recall", _phase_recall),
    ("plan", _phase_plan),
    ("act", _phase_act),
    ("critique", _phase_critique),
    ("persist", _phase_persist),
    ("index_memory", _phase_index_memory),
    ("reflect", _phase_reflect),
)


def _build_generate_response(state: RecapAgentRunState) -> GenerateResponse:
    req = state.request
    settings = state.settings
    run_ctx = state.run_ctx
    request_id = run_ctx.request_id
    assert state.snapshot is not None and state.features is not None
    return GenerateResponse(
        request_id=request_id,
        created_at=_utc_now_iso(),
        prompt_version=state.prompt_version,
        model=model_effective(settings, req.model) if req.force_llm else None,
        provider=req.provider,
        snapshot=state.snapshot,
        features=state.features,
        recap=state.recap,
        rendered_markdown=state.rendered_markdown,
        rendered_wechat_text=state.rendered_wechat_text,
        eval=state.eval_result,
        memory_used=[
            {"date": m.get("date"), "prompt_version": m.get("prompt_version")}
            for m in state.memory
        ],
        memory_recall={
            **(state.memory_recall_meta or {}),
            "short_term_run_count": len(state.memory or []),
            "long_term_block_count": len(state.memory_long or []),
            "entity_block_count": len(state.memory_entities or []),
        },
        push_result=state.push_result,
    )


def _finalize_span_attributes(state: RecapAgentRunState) -> None:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("recap.prompt_version", state.prompt_version)
        if state.llm_error:
            span.set_attribute("recap.llm_error", True)


def _check_budget_between_phases(state: RecapAgentRunState, name: str) -> bool:
    """阶段间显式校验墙钟预算；超限则让后续阶段「轻量降级」。

    返回 ``False`` 表示后续阶段应跳过 LLM/工具相关的重活，但 persist/reflect
    仍要执行（落库带 budget_error 标记，便于离线分析）。
    """
    if state.budget is None:
        return True
    try:
        state.budget.check()
    except LlmBudgetExceeded as e:
        if state.llm_error is None:
            state.llm_error = f"budget_exceeded({e.kind}: used={e.used} limit={e.limit})"
        if state.budget_error is None:
            state.budget_error = f"{e.kind}:{e.used}/{e.limit}"
        logger.warning(
            _stable_json(
                {
                    "event": "phase_budget_exceeded",
                    "phase_about_to_run": name,
                    "kind": e.kind,
                    "used": e.used,
                    "limit": e.limit,
                }
            )
        )
        return False
    return True


def _run_phase_with_metrics(
    state: RecapAgentRunState, tracer: Any, name: str, fn: Callable[[RecapAgentRunState, Any], None]
) -> None:
    """单 phase 执行 + Histogram 计时；失败也记一次（标 status=error）。

    把计时放在最外层确保即便 phase 内部抛了未被自身吞掉的异常，histogram 仍被记录。
    """
    t0 = time.monotonic()
    try:
        fn(state, tracer)
    except Exception:
        record_phase_duration(f"{name}:error", (time.monotonic() - t0) * 1000.0)
        raise
    record_phase_duration(name, (time.monotonic() - t0) * 1000.0)


def _record_run_outcome(state: RecapAgentRunState) -> None:
    """统一记录 ``recap_runs_total``：ok=有 recap、failed=有 error 但无 recap、empty=非 LLM 路径。"""
    req = state.request
    if state.llm_error and state.recap is None:
        status = "failed"
    elif state.recap is not None:
        status = "ok"
    else:
        status = "empty"
    record_recap_run(mode=str(req.mode), provider=str(req.provider), status=status)


def _run_all_phases(state: RecapAgentRunState, tracer: Any) -> GenerateResponse:
    for name, fn in _PHASE_ORDER:
        ok = _check_budget_between_phases(state, name)
        if not ok and name in {"act", "critique", "index_memory"}:
            # 跳过 LLM、评测与向量索引；persist/reflect 仍执行
            continue
        _run_phase_with_metrics(state, tracer, name, fn)
    _finalize_span_attributes(state)
    _record_run_outcome(state)
    return _build_generate_response(state)


def execute_recap_pipeline(state: RecapAgentRunState) -> GenerateResponse:
    """在已建立的 ``recap.generate`` 父 span 与 RunContext 下执行各 Agent 阶段。"""
    tracer = get_tracer(__name__)
    return _run_all_phases(state, tracer)


def _ndjson_line(event: str, **fields: Any) -> str:
    row: dict[str, Any] = {"event": event, **fields}
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def iter_recap_agent_ndjson(state: RecapAgentRunState) -> Iterator[str]:
    """
    按阶段产出 NDJSON 行（便于客户端展示 Agent 进度）；最后一行为 ``result``。
    须在 ``recap.generate`` span 与 RunContext 已建立的环境下调用。
    """
    tracer = get_tracer(__name__)
    req = state.request
    run_ctx = state.run_ctx
    yield _ndjson_line(
        "meta",
        request_id=run_ctx.request_id,
        trace_id=run_ctx.trace_id,
        session_id=run_ctx.session_id,
        mode=req.mode,
        provider=str(req.provider),
        defer_evolution_backtest=state.defer_evolution_backtest,
    )

    last_phase: Optional[str] = None
    try:
        for name, fn in _PHASE_ORDER:
            last_phase = name
            ok = _check_budget_between_phases(state, name)
            if not ok and name in {"act", "critique", "index_memory"}:
                yield _ndjson_line(
                    "phase",
                    phase=name,
                    skipped=True,
                    reason="budget_exceeded",
                    budget_error=state.budget_error,
                )
                continue
            _run_phase_with_metrics(state, tracer, name, fn)
            extra: dict[str, Any] = {}
            if state.snapshot is not None:
                extra["date"] = state.snapshot.date
            if name == "act":
                extra["has_recap"] = state.recap is not None
                extra["llm_error"] = state.llm_error
                if state.budget_error:
                    extra["budget_error"] = state.budget_error
            yield _ndjson_line("phase", phase=name, **extra)
    except Exception as e:
        logger.exception(
            _stable_json(
                {
                    "event": "recap_stream_phase_failed",
                    "phase": last_phase,
                    "error": str(e),
                }
            )
        )
        yield _ndjson_line(
            "error",
            phase=last_phase,
            message=str(e),
            request_id=run_ctx.request_id,
            trace_id=run_ctx.trace_id,
        )
        return

    _finalize_span_attributes(state)
    _record_run_outcome(state)
    resp = _build_generate_response(state)
    http_status = 503 if (req.force_llm and resp.recap is None) else 200
    state.stream_pipeline_completed = True
    yield _ndjson_line("result", http_status=http_status, body=resp.model_dump())
