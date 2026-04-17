"""生成复盘（同步 JSON）+ NDJSON 流式复盘 端点。

编排：FastAPI 依赖注入 → 输入护栏 → init_db → RunContext →
``generate_once``/``iter_generate_ndjson``；响应后将进化/回测挂到 BackgroundTasks。
"""
from __future__ import annotations

from typing import Iterator, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from stock_recap.application.recap import generate_once, iter_generate_ndjson
from stock_recap.application.side_effects import run_deferred_post_recap
from stock_recap.config.settings import Settings, get_settings
from stock_recap.domain.models import GenerateRequest, GenerateResponse
from stock_recap.domain.run_context import RunContext
from stock_recap.infrastructure.persistence.db import init_db
from stock_recap.interfaces.api.deps import require_api_key, require_rate_limit
from stock_recap.policy.guardrails import GuardrailError, validate_generate_request

router = APIRouter(tags=["recap"])


@router.post(
    "/v1/recap",
    response_model=GenerateResponse,
    dependencies=[Depends(require_api_key), Depends(require_rate_limit)],
)
def api_generate(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    x_session_id: Optional[str] = Header(default=None, alias="X-Session-Id"),
) -> JSONResponse:
    try:
        validate_generate_request(req)
    except GuardrailError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    init_db(settings.db_path)
    ctx = RunContext.new(session_id=x_session_id)
    resp = generate_once(
        req,
        settings,
        ctx=ctx,
        defer_evolution_backtest=True,
    )
    background_tasks.add_task(
        run_deferred_post_recap,
        resp.request_id,
        req.mode,
        resp.snapshot.date,
        resp.recap is not None,
    )

    status = 200
    if req.force_llm and resp.recap is None:
        status = 503
    return JSONResponse(status_code=status, content=resp.model_dump())


@router.post(
    "/v1/recap/stream",
    dependencies=[Depends(require_api_key), Depends(require_rate_limit)],
)
def api_generate_stream(
    req: GenerateRequest,
    settings: Settings = Depends(get_settings),
    x_session_id: Optional[str] = Header(default=None, alias="X-Session-Id"),
) -> StreamingResponse:
    """NDJSON 流：``meta`` → 各 ``phase`` → ``result``；进化与回测在流结束后执行。"""
    try:
        validate_generate_request(req)
    except GuardrailError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    init_db(settings.db_path)
    ctx = RunContext.new(session_id=x_session_id)

    def body() -> Iterator[str]:
        yield from iter_generate_ndjson(
            req,
            settings,
            ctx=ctx,
            defer_evolution_backtest=True,
        )

    return StreamingResponse(
        body(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
