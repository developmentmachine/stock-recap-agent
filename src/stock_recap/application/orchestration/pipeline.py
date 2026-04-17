"""显式 Agent 阶段编排：感知 → 记忆 → 规划 → 行动 → 批判 → 持久化 → 副作用。"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, List, Optional, Tuple, cast

from opentelemetry import trace

from stock_recap.application.memory.manager import (
    check_and_run_evolution,
    extract_market_patterns,
    get_prompt_version,
    load_evolution_guidance,
    load_recent_memory,
)
from stock_recap.application.orchestration.context import RecapAgentRunState
from stock_recap.domain.models import GenerateResponse
from stock_recap.infrastructure.data.collector import collect_snapshot
from stock_recap.infrastructure.data.features import build_features
from stock_recap.infrastructure.llm.backends import call_llm, model_effective
from stock_recap.infrastructure.llm.eval import auto_eval
from stock_recap.infrastructure.llm.prompts import build_messages
from stock_recap.infrastructure.persistence.db import insert_run, load_feedback_summary
from stock_recap.infrastructure.push import get_push_provider
from stock_recap.observability.tracing import get_tracer
from stock_recap.policy.guardrails import clamp_llm_messages, coerce_recap_output
from stock_recap.presentation.render.renderers import render_markdown, render_wechat_text

from stock_recap.application.side_effects import (
    load_recent_backtests_simple,
    try_run_backtest,
)

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
        state.memory = load_recent_memory(
            settings.db_path,
            date=state.snapshot.date,
            mode=req.mode,
            limit=settings.max_history_for_context,
        )
        state.evolution_guidance = load_evolution_guidance(settings.db_path)
        state.feedback_summary = load_feedback_summary(settings.db_path)

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
                prompt_version=state.prompt_version,
                evolution_guidance=state.evolution_guidance,
                feedback_summary=state.feedback_summary,
                backtest_context=state.backtest_context,
                pattern_summary=state.pattern_summary,
                skill_id_override=settings.skill_id_override,
            )
        )
        state.messages = cast(List[dict[str, str]], clamp_llm_messages(raw_messages))


def _phase_act(state: RecapAgentRunState, tracer: Any) -> None:
    req = state.request
    settings = state.settings
    with _span_phase(tracer, "recap.agent.act", {"agent.phase": "act", "llm.forced": req.force_llm}):
        assert state.snapshot is not None and state.features is not None
        if req.force_llm:
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
            except Exception as e:
                state.llm_error = str(e)
                logger.error(_stable_json({"event": "generate_failed", "error": state.llm_error}))


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
            provider = get_push_provider(settings)
            if provider is not None:
                try:
                    state.push_result = provider.push(state.recap)
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
        push_result=state.push_result,
    )


def _finalize_span_attributes(state: RecapAgentRunState) -> None:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("recap.prompt_version", state.prompt_version)
        if state.llm_error:
            span.set_attribute("recap.llm_error", True)


def _run_all_phases(state: RecapAgentRunState, tracer: Any) -> GenerateResponse:
    for _name, fn in _PHASE_ORDER:
        fn(state, tracer)
    _finalize_span_attributes(state)
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
            fn(state, tracer)
            extra: dict[str, Any] = {}
            if state.snapshot is not None:
                extra["date"] = state.snapshot.date
            if name == "act":
                extra["has_recap"] = state.recap is not None
                extra["llm_error"] = state.llm_error
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
    resp = _build_generate_response(state)
    http_status = 503 if (req.force_llm and resp.recap is None) else 200
    state.stream_pipeline_completed = True
    yield _ndjson_line("result", http_status=http_status, body=resp.model_dump())
