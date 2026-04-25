"""Agent 管线的副作用（侧效）层。

与 ``orchestration/pipeline`` 的纯管线编排相对应，这里承载所有可以「在主响应
返回后」异步执行、或者在某些路径被降级/跳过的动作：回测、进化、推送。

各子模块职责：
- ``backtest``  —— 次日策略命中率计算（仅日终）
- ``evolution`` —— LLM 驱动的 prompt 自进化触发
- ``push``      —— 企业微信等外部通道推送
- ``deferred``  —— 将上述动作组合为「请求响应后」单次任务

供 API ``BackgroundTasks`` / 调度器 / CLI 共用，避免调用方散落再实现一份。
"""
from agent_platform.application.side_effects.backtest import (
    load_recent_backtests_simple,
    try_run_backtest,
)
from agent_platform.application.side_effects.deferred import run_deferred_post_recap
from agent_platform.application.side_effects.evolution import run_deferred_evolution
from agent_platform.application.side_effects import outbox

__all__ = [
    "load_recent_backtests_simple",
    "outbox",
    "run_deferred_evolution",
    "run_deferred_post_recap",
    "try_run_backtest",
]
