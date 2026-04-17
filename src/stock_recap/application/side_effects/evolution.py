"""Prompt 进化触发的副作用封装。

把 ``check_and_run_evolution`` 的「异常安全调用」包一层，专供
``BackgroundTasks`` / 流式响应后的延后执行。主管线里的 evolution 仍通过
``application/memory/manager.check_and_run_evolution`` 直接调用。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger("stock_recap.side_effects.evolution")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def run_deferred_evolution(
    db_path: str,
    settings: Any,
    *,
    trigger_run_id: Optional[str],
    force: bool = False,
) -> None:
    """安全触发进化检查；失败只写 warning，不向上抛。"""
    from stock_recap.application.memory.manager import check_and_run_evolution

    try:
        check_and_run_evolution(
            db_path,
            settings=settings,
            trigger_run_id=trigger_run_id,
            force=force,
        )
    except Exception as e:
        logger.warning(
            _stable_json({"event": "deferred_evolution_failed", "error": str(e)})
        )
