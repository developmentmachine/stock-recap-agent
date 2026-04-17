"""次日策略回测：生成 T+1 之后对比真实行情，落库 ``backtest_results``。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, List

from stock_recap.domain.models import RecapStrategy
from stock_recap.infrastructure.data.collector import collect_snapshot
from stock_recap.infrastructure.llm.eval import compute_backtest
from stock_recap.infrastructure.persistence.db import (
    get_pending_backtest,
    insert_backtest,
    load_recent_backtests,
    load_recent_runs,
)

logger = logging.getLogger("stock_recap.side_effects.backtest")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_recent_backtests_simple(db_path: str, limit: int = 3) -> List[dict]:
    """读取最近 N 条回测结果；任何异常（如表尚未创建）返回空列表。"""
    try:
        return load_recent_backtests(db_path, limit=limit)
    except Exception:
        return []


def try_run_backtest(db_path: str, today: str) -> None:
    """如存在昨日 ``strategy`` 记录且未回测，则计算并落库。"""
    try:
        strategy_date = get_pending_backtest(db_path, today)
        if strategy_date is None:
            return

        runs = load_recent_runs(db_path, today, "strategy", limit=1)
        if not runs or not runs[0].get("recap"):
            return

        recap_data = runs[0]["recap"]
        strategy_recap = RecapStrategy.model_validate(recap_data)

        today_snapshot = collect_snapshot("live", today, skip_trading_check=True)

        result = compute_backtest(
            strategy_date=strategy_date,
            strategy_recap=strategy_recap,
            actual_date=today,
            actual_snapshot=today_snapshot,
        )

        insert_backtest(db_path, result=result, created_at=_utc_now_iso())
        logger.info(
            _stable_json(
                {
                    "event": "backtest_complete",
                    "strategy_date": strategy_date,
                    "hit_rate": result.hit_rate,
                }
            )
        )
    except Exception as e:
        logger.warning(_stable_json({"event": "backtest_failed", "error": str(e)}))
