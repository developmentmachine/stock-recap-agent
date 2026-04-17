"""用户反馈：落库 + 条件触发进化循环。"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from stock_recap.application.memory.manager import check_and_run_evolution
from stock_recap.config.settings import Settings, get_settings
from stock_recap.domain.models import FeedbackRequest
from stock_recap.infrastructure.persistence.db import init_db, insert_feedback
from stock_recap.interfaces.api.deps import require_api_key, stable_json, utc_now_iso
from stock_recap.policy.guardrails import GuardrailError, validate_feedback_request

logger = logging.getLogger("stock_recap.interfaces.api.feedback")

router = APIRouter(tags=["recap"])


@router.post("/v1/feedback", dependencies=[Depends(require_api_key)])
def api_feedback(
    req: FeedbackRequest,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    try:
        validate_feedback_request(req)
    except GuardrailError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    init_db(settings.db_path)
    insert_feedback(
        settings.db_path,
        request_id=req.request_id,
        created_at=utc_now_iso(),
        rating=int(req.rating),
        tags=req.tags,
        comment=req.comment,
    )

    force = req.rating <= 2
    if force:
        logger.info(stable_json({"event": "low_rating_evolution", "rating": req.rating}))
    evolved = check_and_run_evolution(
        settings.db_path,
        settings=settings,
        trigger_run_id=req.request_id,
        force=force,
    )

    return {
        "ok": True,
        "evolved": evolved is not None,
        "new_prompt_version": evolved,
    }
