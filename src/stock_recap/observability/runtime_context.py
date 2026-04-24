"""ContextVar：在 tool / llm 等调用栈中读取当前 RunContext 与 Budget。

为什么用 ContextVar 而不是参数下穿：
- ``LlmProvider.call`` 由注册表分发，强行加 budget 参数会污染所有实现。
- 工具执行链（``RecapToolRunner.execute`` → registry handler）已经较深，
  逐层透传 budget 难维护。
- ContextVar 只在「同一线程的同一调用栈」内可见，stream 路径下也成立
  （phase 函数在迭代器线程内同步执行）。
"""
from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Optional

from stock_recap.domain.run_context import RunContext

if TYPE_CHECKING:
    from stock_recap.application.orchestration.budget import AgentBudget

current_run_context: contextvars.ContextVar[Optional[RunContext]] = contextvars.ContextVar(
    "stock_recap_run_context",
    default=None,
)

current_budget: contextvars.ContextVar[Optional["AgentBudget"]] = contextvars.ContextVar(
    "stock_recap_agent_budget",
    default=None,
)
