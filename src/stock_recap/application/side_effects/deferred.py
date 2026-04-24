"""组合副作用：在主响应完成后执行的进化 + 日终回测。

走 outbox（``application.side_effects.outbox``）：
- enqueue ``evolution`` 与（仅 daily）``backtest`` 两个动作；
- 立刻 best-effort ``process_due()`` 一次，覆盖「响应即处理」的快路径；
- 失败的任务留在 outbox 里，由 APScheduler 周期 sweep（见
  ``interfaces.scheduler.jobs``）按指数退避兜底。

为什么要 outbox 而不是直接调用：见 ``application/side_effects/outbox.py`` 顶端注释。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from stock_recap.application.side_effects import outbox
from stock_recap.application.side_effects.backtest import try_run_backtest
from stock_recap.application.side_effects.evolution import run_deferred_evolution

logger = logging.getLogger("stock_recap.side_effects.deferred")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ─── outbox handler 注册 ─────────────────────────────────────────────────────


def _handle_evolution(payload: Dict[str, Any]) -> None:
    from stock_recap.config.settings import get_settings

    s = get_settings()
    run_deferred_evolution(
        s.db_path,
        s,
        trigger_run_id=str(payload.get("trigger_run_id", "")) or None,
        force=bool(payload.get("force", False)),
    )


def _handle_backtest(payload: Dict[str, Any]) -> None:
    from stock_recap.config.settings import get_settings

    s = get_settings()
    trade_date = str(payload.get("trade_date", ""))
    if not trade_date:
        raise ValueError("backtest payload missing trade_date")
    try_run_backtest(s.db_path, trade_date)


outbox.register_handler("evolution", _handle_evolution)
outbox.register_handler("backtest", _handle_backtest)


# ─── 主入口 ──────────────────────────────────────────────────────────────────


def run_deferred_post_recap(
    trigger_run_id: str,
    mode: str,
    trade_date: str,
    has_recap: bool,
) -> None:
    """HTTP 流式 / JSON 响应返回后执行：进化检查 + 日终回测。

    入队即返回；任何失败仅记录 warning，不向上抛——保证对主响应的可观察性与时延稳定。
    """
    from stock_recap.config.settings import get_settings

    s = get_settings()
    outbox.enqueue(
        s.db_path,
        request_id=trigger_run_id,
        action_type="evolution",
        payload={"trigger_run_id": trigger_run_id},
    )
    if mode == "daily" and has_recap:
        outbox.enqueue(
            s.db_path,
            request_id=trigger_run_id,
            action_type="backtest",
            payload={"trade_date": trade_date},
        )

    # 快路径：在当前 worker 内立刻处理一遍；失败的留在 outbox 给周期 sweep。
    try:
        summary = outbox.process_due(s.db_path)
        logger.info(
            _stable_json(
                {
                    "event": "deferred_outbox_sweep",
                    "trigger_run_id": trigger_run_id,
                    "claimed": summary.claimed,
                    "done": summary.done,
                    "failed_retry": summary.failed_retry,
                    "failed_final": summary.failed_final,
                }
            )
        )
    except Exception as e:
        # outbox 自身崩溃也不能阻塞主响应链路。
        logger.warning(_stable_json({"event": "deferred_outbox_failed", "error": str(e)}))
