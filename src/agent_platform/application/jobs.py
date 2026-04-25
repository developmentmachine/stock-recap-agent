"""长任务原语（W5-3）：把 ``generate_once`` 的同步路径包装成可异步轮询的 Job。

为什么不复用 ``BackgroundTasks`` 直接返回 200：
- 同步 ``/v1/recap`` 在重 LLM 模型 + 工具循环下可能 ≥ 60s，HTTP 客户端容易超时；
- 即使流式 ``/v1/recap/stream`` 也要求客户端持有长连接；移动端 / Webhook 场景更适合
  「先拿 job_id 再轮询结果」的模式；
- 还能借助幂等键 ``X-Idempotency-Key`` 做客户端重试去重，避免重复触发昂贵生成。

执行模型：
- POST /v1/jobs：在请求线程内 ``insert_job(status='queued')``，再用 FastAPI 的
  ``BackgroundTasks`` 在响应返回后启动 ``_run_recap_job``；
- _run_recap_job 内部更新 status: queued → running → done|failed；
- GET /v1/jobs/{job_id}：纯读 SQLite 行，按 ``tenant_id`` 隔离。

局限（W5-3 显式说明）：
1. 仅同进程内 BackgroundTasks，多 worker 部署需要补一个独立 sweeper（已预留
   ``claim_due_queued_jobs``，由 W4 的 outbox sweeper 进程兼职即可）；
2. 没做超时强制中断（依赖 ``AgentBudget.max_wall_ms`` 软超时）；
3. result 结果存 SQLite，单 row 上限 ≈ 数 MB；超大 recap 应改存对象存储。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from agent_platform.application.recap import generate_once
from agent_platform.config.settings import Settings
from agent_platform.domain.models import GenerateRequest
from agent_platform.domain.principal import PrincipalContext, get_principal
from agent_platform.domain.run_context import RunContext
from agent_platform.infrastructure.persistence.db import (
    insert_job,
    list_jobs,
    load_job,
    load_job_by_idem,
    mark_job_done,
    mark_job_failed,
    update_job_running,
)
from agent_platform.observability.runtime_context import current_run_context

logger = logging.getLogger("agent_platform.application.jobs")


def _stable_json(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def submit_recap_job(
    req: GenerateRequest,
    settings: Settings,
    *,
    principal: Optional[PrincipalContext] = None,
    idempotency_key: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """登记一个 recap job 行；返回 ``{job_id, status, idempotent_hit}``。

    幂等：``(tenant_id, idempotency_key)`` 命中已有 job 时，不会新建，原样返回旧 job。
    """
    tenant_id = (principal or get_principal()).tenant_id

    if idempotency_key:
        existing = load_job_by_idem(
            settings.db_path,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            logger.info(
                _stable_json(
                    {
                        "event": "job_idempotent_hit",
                        "job_id": existing["job_id"],
                        "tenant_id": tenant_id,
                    }
                )
            )
            return {
                "job_id": existing["job_id"],
                "status": existing["status"],
                "idempotent_hit": True,
            }

    job_id = f"job-{uuid.uuid4().hex[:16]}"
    payload = {
        "request": req.model_dump(),
        "session_id": session_id,
    }
    inserted = insert_job(
        settings.db_path,
        job_id=job_id,
        kind="recap",
        request_payload=payload,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
    )
    if not inserted and idempotency_key:
        existing = load_job_by_idem(
            settings.db_path,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return {
                "job_id": existing["job_id"],
                "status": existing["status"],
                "idempotent_hit": True,
            }

    logger.info(
        _stable_json(
            {
                "event": "job_submitted",
                "job_id": job_id,
                "kind": "recap",
                "tenant_id": tenant_id,
                "mode": req.mode,
            }
        )
    )
    return {"job_id": job_id, "status": "queued", "idempotent_hit": False}


def run_recap_job(
    job_id: str,
    settings: Settings,
    *,
    principal: Optional[PrincipalContext] = None,
) -> None:
    """供 ``BackgroundTasks`` / 独立 worker 调用：执行单个 queued job。

    幂等：重复调用会因 ``update_job_running`` 的 WHERE 条件被跳过；
    完成后更新 status 为 done/failed，附带 result/error。
    """
    job = load_job(settings.db_path, job_id=job_id)
    if job is None:
        logger.warning(_stable_json({"event": "job_not_found", "job_id": job_id}))
        return
    if job["status"] not in ("queued", "running"):
        logger.info(
            _stable_json(
                {
                    "event": "job_skip_already_finished",
                    "job_id": job_id,
                    "status": job["status"],
                }
            )
        )
        return

    payload = job.get("request") or {}
    try:
        req = GenerateRequest.model_validate(payload.get("request", {}))
    except Exception as e:
        mark_job_failed(
            settings.db_path,
            job_id=job_id,
            error=f"bad_request_payload: {e}",
        )
        return
    session_id = payload.get("session_id")

    update_job_running(settings.db_path, job_id=job_id)

    # 让 worker 内的日志 / 工具 / 持久化读到正确的 principal + RunContext；
    # 结束后必须 reset principal，避免 BackgroundTasks 污染后续 HTTP 请求。
    from agent_platform.domain.principal import current_principal, set_principal

    effective_principal = principal or PrincipalContext(
        tenant_id=job.get("tenant_id"),
        role=settings.principal_role,
        api_key_hash=None,
        source="job-worker",
    )
    principal_token = set_principal(effective_principal)

    ctx = RunContext.new(session_id=session_id, tenant_id=job.get("tenant_id"))
    prev_ctx = current_run_context.get()
    current_run_context.set(ctx)
    try:
        resp = generate_once(req, settings, ctx=ctx)
        mark_job_done(
            settings.db_path,
            job_id=job_id,
            result_payload=resp.model_dump(),
            request_id=resp.request_id,
        )
        logger.info(
            _stable_json(
                {
                    "event": "job_done",
                    "job_id": job_id,
                    "request_id": resp.request_id,
                }
            )
        )
    except Exception as e:
        mark_job_failed(settings.db_path, job_id=job_id, error=str(e))
        logger.warning(
            _stable_json(
                {
                    "event": "job_failed",
                    "job_id": job_id,
                    "error": str(e)[:300],
                }
            )
        )
    finally:
        current_run_context.set(prev_ctx)
        try:
            current_principal.reset(principal_token)
        except Exception:
            pass


def get_job(
    settings: Settings,
    *,
    job_id: str,
    tenant_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """API 层查询：按 tenant_id 隔离，返回精简后的 job 表示。"""
    job = load_job(settings.db_path, job_id=job_id, tenant_id=tenant_id)
    if job is None:
        return None
    return _project_job(job)


def list_jobs_for_api(
    settings: Settings,
    *,
    tenant_id: Optional[str],
    status: Optional[str] = None,
    limit: int = 50,
) -> list[Dict[str, Any]]:
    """与 ``GET /v1/jobs`` 对齐：同租户列表 + 字段投影。"""
    rows = list_jobs(
        settings.db_path,
        tenant_id=tenant_id,
        status=status,
        limit=limit,
    )
    return [_project_job(r) for r in rows]


def _project_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """对外只暴露需要的字段，request_json 也精简成 ``request`` 子树。"""
    return {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": job["status"],
        "tenant_id": job.get("tenant_id"),
        "request_id": job.get("request_id"),
        "idempotency_key": job.get("idempotency_key"),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "request": (job.get("request") or {}).get("request"),
        "result": job.get("result"),
        "error": job.get("error"),
    }


__all__ = [
    "get_job",
    "list_jobs_for_api",
    "run_recap_job",
    "submit_recap_job",
]
