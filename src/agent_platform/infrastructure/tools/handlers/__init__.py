"""LLM 可调用的工具实现（副作用与 I/O 隔离在 handlers）。"""

from agent_platform.infrastructure.tools.handlers.history import run_query_history
from agent_platform.infrastructure.tools.handlers.market_data import run_query_market_data
from agent_platform.infrastructure.tools.handlers.web_search import run_web_search

__all__ = ["run_query_history", "run_query_market_data", "run_web_search"]
