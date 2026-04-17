"""运维端点：健康检查、指标、历史、回测、进化历史。"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from stock_recap.application.memory.manager import get_prompt_version
from stock_recap.config.settings import Settings, get_settings
from stock_recap.infrastructure.persistence.db import (
    init_db,
    load_evolution_history,
    load_history,
    load_metrics,
    load_recent_backtests,
)
from stock_recap.interfaces.api.deps import require_api_key, today_str, utc_now_iso

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
    m = load_metrics(
        settings.db_path,
        today=today_str(),
        prompt_version=get_prompt_version(settings.db_path),
    )
    return m.model_dump()


@router.get("/v1/history", dependencies=[Depends(require_api_key)])
def api_history(
    limit: int = 20,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    init_db(settings.db_path)
    return {"items": load_history(settings.db_path, limit=limit)}


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
