"""副作用收件箱（outbox / pending_actions）。

为什么需要它：
- 主响应已经写库 + 返回，但「进化检查 / 回测 / 推送」如果就地执行失败，
  没有持久化痕迹也没有重试机制，事故时只能靠日志苦苦回溯。
- ``BackgroundTasks`` 在进程崩溃 / 重启 / 多 worker 部署下并不可靠：
  任务被吃掉就吃掉，没有再次消费的机会。
- outbox 模式提供：
    1. **幂等键** —— ``UNIQUE(request_id, action_type)``，主路径反复触发不会重复推送；
    2. **持久化** —— 进程重启后未完成的任务仍在 DB 里；
    3. **指数退避重试** —— 失败带 next_attempt_at；
    4. **解耦消费者** —— 同一份 enqueue 接口，可被 BackgroundTasks、APScheduler
       周期 sweep、独立 worker 进程任意一种消费。

本模块只关心 enqueue / 调度 / 注册 handler；具体动作语义由 handler 子模块
（``backtest`` / ``evolution`` / ``push``）实现。
"""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from agent_platform.infrastructure.persistence.db import (
    claim_due_pending_actions,
    enqueue_pending_action,
    list_pending_actions,
    mark_pending_action_done,
    mark_pending_action_failed,
)
from agent_platform.observability.metrics import record_outbox_action

logger = logging.getLogger("agent_platform.side_effects.outbox")

ActionHandler = Callable[[Dict[str, Any]], None]
"""签名：``handler(payload_dict) -> None``；抛异常视为失败，触发指数退避重试。"""

_HANDLERS: Dict[str, ActionHandler] = {}

# 指数退避：base 30s，cap 1h，最大尝试次数 6（≈ 30s, 60s, 2m, 4m, 8m, 16m）
_BACKOFF_BASE_S = 30.0
_BACKOFF_CAP_S = 3600.0
_BACKOFF_JITTER = 0.2  # ±20%
_MAX_ATTEMPTS = 6


@dataclass
class ProcessSummary:
    claimed: int = 0
    done: int = 0
    failed_retry: int = 0
    failed_final: int = 0
    errors: List[str] = field(default_factory=list)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _backoff_seconds(next_attempt_no: int) -> float:
    """next_attempt_no 是「即将尝试的第几次」（1-based）。"""
    raw = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** max(0, next_attempt_no - 1)))
    jitter = raw * random.uniform(-_BACKOFF_JITTER, _BACKOFF_JITTER)
    return max(1.0, raw + jitter)


def register_handler(action_type: str, handler: ActionHandler) -> None:
    """同一 action_type 重复注册会覆盖；测试中常用。"""
    _HANDLERS[action_type] = handler


def get_registered_handlers() -> Dict[str, ActionHandler]:
    """供运维/调试观察当前已注册的动作类型。"""
    return dict(_HANDLERS)


def _resolve_tenant_id() -> Optional[str]:
    """Outbox 入队时显式没传就从 ``current_run_context`` / ``current_principal`` 推断。"""
    try:
        from agent_platform.observability.runtime_context import current_run_context

        ctx = current_run_context.get()
        if ctx is not None and getattr(ctx, "tenant_id", None):
            return ctx.tenant_id
    except Exception:
        pass
    try:
        from agent_platform.domain.principal import get_principal

        return get_principal().tenant_id
    except Exception:
        return None


def enqueue(
    db_path: str,
    *,
    request_id: str,
    action_type: str,
    payload: Optional[Dict[str, Any]] = None,
    tenant_id: Optional[str] = None,
) -> bool:
    """幂等入队；返回 True 表示新建，False 表示已存在（不视为失败）。

    ``tenant_id`` 未显式传入时，会按 RunContext / PrincipalContext 顺序推断。
    """
    now = _iso(_utc_now())
    effective_tenant = tenant_id if tenant_id is not None else _resolve_tenant_id()
    inserted = enqueue_pending_action(
        db_path,
        request_id=request_id,
        action_type=action_type,
        payload_json=_stable_json(payload or {}),
        now_iso=now,
        tenant_id=effective_tenant,
    )
    logger.info(
        _stable_json(
            {
                "event": "outbox_enqueue",
                "request_id": request_id,
                "action_type": action_type,
                "tenant_id": effective_tenant,
                "inserted": inserted,
            }
        )
    )
    return inserted


def process_due(db_path: str, *, batch: int = 16) -> ProcessSummary:
    """消费一批到期任务（单次 sweep）；调度器/BackgroundTasks 都可循环调用。"""
    summary = ProcessSummary()
    claimed = claim_due_pending_actions(db_path, now_iso=_iso(_utc_now()), limit=batch)
    summary.claimed = len(claimed)
    if not claimed:
        return summary

    for row in claimed:
        action_id = int(row["id"])
        action_type = str(row["action_type"])
        attempts_so_far = int(row["attempts"])
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except Exception as e:  # 受损 payload：直接 final fail，避免无限重试
            mark_pending_action_failed(
                db_path,
                action_id=action_id,
                now_iso=_iso(_utc_now()),
                next_attempt_at_iso=None,
                last_error=f"corrupt_payload: {e}",
                final=True,
            )
            summary.failed_final += 1
            record_outbox_action(action_type, "failed")
            continue

        handler = _HANDLERS.get(action_type)
        if handler is None:
            mark_pending_action_failed(
                db_path,
                action_id=action_id,
                now_iso=_iso(_utc_now()),
                next_attempt_at_iso=None,
                last_error=f"no_handler:{action_type}",
                final=True,
            )
            summary.failed_final += 1
            summary.errors.append(f"no_handler:{action_type}")
            record_outbox_action(action_type, "failed")
            continue

        t0 = time.monotonic()
        try:
            handler(payload)
            mark_pending_action_done(
                db_path, action_id=action_id, now_iso=_iso(_utc_now())
            )
            summary.done += 1
            record_outbox_action(action_type, "done")
            logger.info(
                _stable_json(
                    {
                        "event": "outbox_done",
                        "action_id": action_id,
                        "action_type": action_type,
                        "elapsed_ms": int((time.monotonic() - t0) * 1000),
                    }
                )
            )
        except Exception as e:
            next_attempt_no = attempts_so_far + 1
            is_final = next_attempt_no >= _MAX_ATTEMPTS
            backoff = _backoff_seconds(next_attempt_no + 1) if not is_final else 0.0
            next_at = _utc_now() + timedelta(seconds=backoff) if not is_final else None
            mark_pending_action_failed(
                db_path,
                action_id=action_id,
                now_iso=_iso(_utc_now()),
                next_attempt_at_iso=_iso(next_at) if next_at else None,
                last_error=str(e)[:500],
                final=is_final,
            )
            if is_final:
                summary.failed_final += 1
                record_outbox_action(action_type, "failed")
            else:
                summary.failed_retry += 1
                record_outbox_action(action_type, "retry")
            summary.errors.append(f"{action_type}:{e}")
            logger.warning(
                _stable_json(
                    {
                        "event": "outbox_handler_failed",
                        "action_id": action_id,
                        "action_type": action_type,
                        "attempts_so_far": next_attempt_no,
                        "final": is_final,
                        "next_attempt_in_s": int(backoff),
                        "error": str(e)[:200],
                    }
                )
            )

    return summary


def list_actions(
    db_path: str, *, status: Optional[str] = None, limit: int = 50
) -> List[Dict[str, Any]]:
    """调试/运维查询。"""
    return list_pending_actions(db_path, status=status, limit=limit)


__all__ = [
    "ActionHandler",
    "ProcessSummary",
    "enqueue",
    "get_registered_handlers",
    "list_actions",
    "process_due",
    "register_handler",
]
