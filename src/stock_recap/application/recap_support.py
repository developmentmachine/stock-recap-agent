"""后向兼容 shim：原 ``recap_support`` 已拆分到 ``application.side_effects/``。

保留本文件只为了不让外部脚本/旧导入立刻断裂；新代码请直接 import
``stock_recap.application.side_effects`` 或其子模块。
"""
from __future__ import annotations

from stock_recap.application.side_effects import (
    load_recent_backtests_simple,
    run_deferred_post_recap,
    try_run_backtest,
)

__all__ = [
    "load_recent_backtests_simple",
    "run_deferred_post_recap",
    "try_run_backtest",
]
