"""推送副作用（通过 ``PushProvider`` 抽象） + 幂等账本（push_log）。

幂等设计：
- 以 ``(request_id, channel)`` 为键，调用前查 push_log；若已 ``sent``/``skipped``
  → 直接返回 True/False 不再发；
- 推送结果（成功/失败）一律 upsert 回 push_log，attempts 自增。

为何在这里做：
- 调用方（``pipeline._phase_reflect`` / outbox handler / scheduler）任意一个被
  反复触发都不会重复打扰用户；
- 幂等键和业务实体（recap_runs.request_id）天然绑定，无需额外配置。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from stock_recap.config.settings import Settings
from stock_recap.domain.models import Recap
from stock_recap.infrastructure.persistence.db import (
    get_push_log,
    upsert_push_log,
)
from stock_recap.infrastructure.push import get_push_provider

logger = logging.getLogger("stock_recap.side_effects.push")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _channel_name(settings: Settings) -> str:
    """目前只支持 wxwork；之后接入更多通道时按 provider 区分。"""
    if settings.wxwork_webhook_url:
        return "wxwork"
    return "noop"


def push_recap(
    settings: Settings,
    recap: Recap,
    *,
    request_id: Optional[str] = None,
) -> bool:
    """按配置将 ``recap`` 推送到外部通道；未启用 / 失败 / 重复请求时返回 False。

    ``request_id`` 为空时退化为非幂等推送（仅用于 ad-hoc 调试场景）。
    """
    provider = get_push_provider(settings)
    if provider is None:
        return False

    channel = _channel_name(settings)

    # 幂等检查：仅当业务侧给了 request_id 才生效。
    if request_id:
        try:
            existing = get_push_log(settings.db_path, request_id=request_id, channel=channel)
        except Exception as e:
            # push_log 查询失败不应阻塞推送（兼容老库）；但要告警。
            logger.warning(
                _stable_json(
                    {
                        "event": "push_log_query_failed",
                        "request_id": request_id,
                        "channel": channel,
                        "error": str(e),
                    }
                )
            )
            existing = None

        if existing and existing.get("status") in ("sent", "skipped"):
            logger.info(
                _stable_json(
                    {
                        "event": "push_idempotent_skip",
                        "request_id": request_id,
                        "channel": channel,
                        "previous_status": existing.get("status"),
                        "previous_attempts": existing.get("attempts"),
                    }
                )
            )
            return existing.get("status") == "sent"

    ok = False
    last_error: Optional[str] = None
    try:
        ok = bool(provider.push(recap))
    except Exception as e:
        last_error = str(e)[:500]
        logger.warning(
            _stable_json(
                {
                    "event": "push_failed",
                    "request_id": request_id,
                    "channel": channel,
                    "error": last_error,
                }
            )
        )

    if request_id:
        try:
            upsert_push_log(
                settings.db_path,
                request_id=request_id,
                channel=channel,
                status="sent" if ok else "failed",
                now_iso=_utc_now_iso(),
                last_error=last_error,
            )
        except Exception as e:
            logger.warning(
                _stable_json(
                    {
                        "event": "push_log_upsert_failed",
                        "request_id": request_id,
                        "channel": channel,
                        "error": str(e),
                    }
                )
            )

    return ok
