"""ContextVar：在 tool / llm 等调用栈中读取当前 RunContext。"""
from __future__ import annotations

import contextvars
from typing import Optional

from stock_recap.domain.run_context import RunContext

current_run_context: contextvars.ContextVar[Optional[RunContext]] = contextvars.ContextVar(
    "stock_recap_run_context",
    default=None,
)
