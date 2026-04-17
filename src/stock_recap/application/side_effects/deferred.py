"""组合副作用：在主响应完成后执行的进化 + 日终回测。"""
from __future__ import annotations

import json
import logging
from typing import Any

from stock_recap.application.side_effects.backtest import try_run_backtest
from stock_recap.application.side_effects.evolution import run_deferred_evolution

logger = logging.getLogger("stock_recap.side_effects.deferred")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def run_deferred_post_recap(
    trigger_run_id: str,
    mode: str,
    trade_date: str,
    has_recap: bool,
) -> None:
    """HTTP 流式 / JSON 响应返回后执行：进化检查 + 日终回测。

    独立于请求线程，任何失败仅记录 warning，不向上抛出——保证对主响应的
    可观察性与时延都是稳定的。
    """
    from stock_recap.config.settings import get_settings

    s = get_settings()
    run_deferred_evolution(s.db_path, s, trigger_run_id=trigger_run_id)
    if mode == "daily" and has_recap:
        try_run_backtest(s.db_path, trade_date)
