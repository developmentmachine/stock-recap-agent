"""运维端点：健康检查、业务指标、Prometheus 指标、历史、回测、进化历史、prompt 实验。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from agent_platform.application.memory.manager import get_prompt_version
from agent_platform.config.settings import Settings, get_settings
from agent_platform.infrastructure.persistence.db import (
    init_db,
    list_prompt_experiments,
    load_evolution_history,
    load_experiment_variants,
    load_history,
    load_metrics,
    load_recap_audit,
    load_recent_backtests,
    upsert_prompt_experiment,
    upsert_prompt_experiment_variant,
)
from agent_platform.domain.principal import PrincipalContext
from agent_platform.interfaces.api.deps import require_api_key, today_str, utc_now_iso
from agent_platform.observability.metrics import get_metrics

router = APIRouter(tags=["ops"])


@router.get("/healthz")
def healthz(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    return {
        "ok": True,
        "time": utc_now_iso(),
        "prompt_version": get_prompt_version(settings.db_path),
    }


@router.get("/metrics")
def metrics(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    """业务指标 JSON：runs / 成功率 / prompt_version 等，与 Prometheus 风格分离。"""
    m = load_metrics(
        settings.db_path,
        today=today_str(),
        prompt_version=get_prompt_version(settings.db_path),
    )
    return m.model_dump()


@router.get(
    "/metrics/prom",
    response_class=PlainTextResponse,
    responses={
        200: {
            "content": {"text/plain; version=0.0.4": {}},
            "description": "Prometheus exposition format（counter + histogram）。",
        }
    },
)
def metrics_prometheus() -> PlainTextResponse:
    """Prometheus 抓取端点：进程内累计的 recap_runs/llm_calls/tool_invocations 等。"""
    body = get_metrics().render_prometheus()
    return PlainTextResponse(content=body, media_type="text/plain; version=0.0.4")


@router.get("/v1/history")
def api_history(
    limit: int = 20,
    settings: Settings = Depends(get_settings),
    principal: PrincipalContext = Depends(require_api_key),
) -> Dict[str, Any]:
    init_db(settings.db_path)
    return {
        "items": load_history(
            settings.db_path, limit=limit, tenant_id=principal.tenant_id
        )
    }


@router.get("/v1/backtest", dependencies=[Depends(require_api_key)])
def api_backtest(
    limit: int = 10,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    init_db(settings.db_path)
    return {"items": load_recent_backtests(settings.db_path, limit=limit)}


@router.get("/v1/evolution", dependencies=[Depends(require_api_key)])
def api_evolution(
    limit: int = 10,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    init_db(settings.db_path)
    return {"items": load_evolution_history(settings.db_path, limit=limit)}


@router.get("/v1/audit/{request_id}")
def api_audit_one(
    request_id: str,
    settings: Settings = Depends(get_settings),
    principal: PrincipalContext = Depends(require_api_key),
) -> Dict[str, Any]:
    """合规/排错：按 request_id 取出完整 messages + recap，便于线下 replay。

    多租户场景下，仅允许访问自己的 audit；越权直接 404（避免泄漏存在性）。
    """
    init_db(settings.db_path)
    items = load_recap_audit(
        settings.db_path,
        request_id=request_id,
        limit=1,
        tenant_id=principal.tenant_id,
    )
    if not items:
        raise HTTPException(status_code=404, detail="audit not found")
    return items[0]


@router.get("/v1/audit")
def api_audit_list(
    mode: str | None = None,
    limit: int = 20,
    settings: Settings = Depends(get_settings),
    principal: PrincipalContext = Depends(require_api_key),
) -> Dict[str, Any]:
    init_db(settings.db_path)
    return {
        "items": load_recap_audit(
            settings.db_path, mode=mode, limit=limit, tenant_id=principal.tenant_id
        )
    }


# ─── Prompt 实验管理（管理员接口，写需 API key） ─────────────────────────


class _VariantPayload(BaseModel):
    variant_id: str = Field(..., min_length=1, max_length=64)
    prompt_version: str = Field(..., min_length=1, max_length=64)
    traffic_weight: int = Field(1, ge=0, le=10000)
    metadata: Optional[Dict[str, Any]] = None


class _ExperimentPayload(BaseModel):
    experiment_id: str = Field(..., min_length=1, max_length=64)
    mode: str = Field(..., min_length=1, max_length=32)
    status: str = Field("active", pattern=r"^(active|paused|archived)$")
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    description: Optional[str] = Field(None, max_length=1024)
    metadata: Optional[Dict[str, Any]] = None
    variants: List[_VariantPayload] = Field(default_factory=list)


@router.post("/v1/experiments", dependencies=[Depends(require_api_key)])
def api_experiments_upsert(
    payload: _ExperimentPayload,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """upsert 一个实验 + 它的全部 variants（同 ``experiment_id`` 重入是幂等的）。

    至少要有一个 traffic_weight>0 的 variant，否则分桶必失败 → 直接 400。
    """
    init_db(settings.db_path)
    if not payload.variants or sum(v.traffic_weight for v in payload.variants) <= 0:
        raise HTTPException(
            status_code=400,
            detail="experiment requires at least one variant with traffic_weight > 0",
        )
    now = utc_now_iso()
    upsert_prompt_experiment(
        settings.db_path,
        experiment_id=payload.experiment_id,
        mode=payload.mode,
        status=payload.status,
        starts_at=payload.starts_at or now,
        ends_at=payload.ends_at,
        description=payload.description,
        metadata=payload.metadata,
        created_at=now,
    )
    for v in payload.variants:
        upsert_prompt_experiment_variant(
            settings.db_path,
            experiment_id=payload.experiment_id,
            variant_id=v.variant_id,
            prompt_version=v.prompt_version,
            traffic_weight=v.traffic_weight,
            metadata=v.metadata,
            created_at=now,
        )
    return {"ok": True, "experiment_id": payload.experiment_id}


@router.get("/v1/experiments", dependencies=[Depends(require_api_key)])
def api_experiments_list(
    mode: str | None = None,
    status: str | None = None,
    limit: int = 50,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    init_db(settings.db_path)
    items = list_prompt_experiments(
        settings.db_path, mode=mode, status=status, limit=limit
    )
    for it in items:
        it["variants"] = load_experiment_variants(
            settings.db_path, experiment_id=it["experiment_id"]
        )
    return {"items": items}
