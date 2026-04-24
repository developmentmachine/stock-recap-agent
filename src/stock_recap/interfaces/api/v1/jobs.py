"""长任务原语：POST /v1/jobs / GET /v1/jobs/{job_id} / GET /v1/jobs。

异步生成 recap 的 RPC 替身：
- POST 立即返回 ``job_id`` + ``status=queued``；
- BackgroundTasks 在响应后真正执行 ``run_recap_job``；
- 客户端用 GET 轮询直到 ``status in ('done','failed','cancelled')``。

幂等：可选 ``X-Idempotency-Key`` Header；同 (tenant_id, idem) 命中已有 job 直接返回。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException

from stock_recap.application.jobs import get_job, run_recap_job, submit_recap_job
from stock_recap.config.settings import Settings, get_settings
from stock_recap.domain.models import GenerateRequest
from stock_recap.domain.principal import PrincipalContext
from stock_recap.infrastructure.persistence.db import init_db, list_jobs
from stock_recap.interfaces.api.deps import require_api_key
from stock_recap.policy.guardrails import GuardrailError, validate_generate_request

router = APIRouter(tags=["jobs"])


@router.post("/v1/jobs")
def api_jobs_submit(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    principal: PrincipalContext = Depends(require_api_key),
    x_session_id: Optional[str] = Header(default=None, alias="X-Session-Id"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> Dict[str, Any]:
    """登记一个 recap 异步 job，并把执行挂到 BackgroundTasks。"""
    try:
        validate_generate_request(req)
    except GuardrailError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    init_db(settings.db_path)
    submission = submit_recap_job(
        req,
        settings,
        principal=principal,
        idempotency_key=x_idempotency_key,
        session_id=x_session_id,
    )

    if not submission["idempotent_hit"]:
        background_tasks.add_task(
            run_recap_job, submission["job_id"], settings, principal=principal
        )

    return {
        "job_id": submission["job_id"],
        "status": submission["status"],
        "idempotent_hit": submission["idempotent_hit"],
        "tenant_id": principal.tenant_id,
    }


@router.get("/v1/jobs/{job_id}")
def api_jobs_get(
    job_id: str,
    settings: Settings = Depends(get_settings),
    principal: PrincipalContext = Depends(require_api_key),
) -> Dict[str, Any]:
    init_db(settings.db_path)
    job = get_job(settings, job_id=job_id, tenant_id=principal.tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/v1/jobs")
def api_jobs_list(
    status: str | None = None,
    limit: int = 50,
    settings: Settings = Depends(get_settings),
    principal: PrincipalContext = Depends(require_api_key),
) -> Dict[str, Any]:
    init_db(settings.db_path)
    items = list_jobs(
        settings.db_path,
        tenant_id=principal.tenant_id,
        status=status,
        limit=int(max(1, min(limit, 200))),
    )
    return {"items": items}
