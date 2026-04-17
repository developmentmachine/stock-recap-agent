"""工具 schema 注册与统一调度（OpenAI / Ollama function calling）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from stock_recap.infrastructure.tools.handlers.history import run_query_history
from stock_recap.infrastructure.tools.handlers.market_data import run_query_market_data
from stock_recap.infrastructure.tools.handlers.web_search import run_web_search

logger = logging.getLogger("stock_recap.infrastructure.tools.registry")

ALL_TOOL_NAMES = ("web_search", "query_market_data", "query_history")

# ─── OpenAI-compatible tool schemas ──────────────────────────────────────────

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "搜索互联网获取实时市场信息，包括今日指数涨跌、板块热点、"
                "北向资金、美股行情、大宗商品、地缘政治等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，例如：今日上证指数收盘 2024-01-02",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_market_data",
            "description": "查询 A 股实时/历史行情数据，包括指数、板块涨跌幅、北向资金。",
            "parameters": {
                "type": "object",
                "properties": {
                    "data_type": {
                        "type": "string",
                        "enum": ["index", "sector", "northbound"],
                        "description": "index=主要指数, sector=板块涨跌, northbound=北向资金",
                    },
                    "date": {
                        "type": "string",
                        "description": "查询日期 YYYY-MM-DD，不传则取最新",
                    },
                },
                "required": ["data_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_history",
            "description": "查询项目内部历史复盘记录，用于对比今日与近期市场走势。",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["daily", "strategy"],
                        "description": "daily=日终复盘, strategy=次日策略",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数，默认 5",
                        "default": 5,
                    },
                },
                "required": ["mode"],
            },
        },
    },
]


def execute_tool(name: str, arguments: Dict[str, Any], db_path: str = ":memory:") -> str:
    """根据工具名执行对应 handler，返回字符串结果。"""
    from stock_recap.observability.runtime_context import current_run_context
    from stock_recap.observability.tracing import get_tracer

    logger.info("tool_call name=%s args=%s", name, arguments)
    ctx = current_run_context.get()
    tracer = get_tracer(__name__)
    attrs: Dict[str, Any] = {"tool.name": name}
    if ctx is not None:
        attrs["recap.request_id"] = ctx.request_id
        attrs["recap.trace_id"] = ctx.trace_id
    with tracer.start_as_current_span("llm.tool.execute", attributes=attrs):
        if name == "web_search":
            return run_web_search(arguments.get("query", ""))
        if name == "query_market_data":
            return run_query_market_data(
                arguments.get("data_type", "index"),
                arguments.get("date"),
            )
        if name == "query_history":
            return run_query_history(
                db_path,
                arguments.get("mode", "daily"),
                int(arguments.get("limit", 5)),
            )
        return f"未知工具: {name}"


def prefetch_for_prompt(
    date: str,
    db_path: str = ":memory:",
    enabled_tools: Optional[Iterable[str]] = None,
) -> str:
    """按 ``enabled_tools`` 预执行工具并拼接上下文（cursor-cli / gemini-cli 注入）。

    ``enabled_tools`` 为 ``None`` 时等价于所有工具开启；传空集合则返回空字符串。
    使用方应在 ``RecapToolRunner.prefetch_for_prompt`` 中按 ``Settings.tools_*``
    过滤后再传入，以保证与 function-calling 路径的策略一致。
    """
    allowed = set(ALL_TOOL_NAMES) if enabled_tools is None else set(enabled_tools)
    parts: List[str] = []
    if "web_search" in allowed:
        parts.append(
            f"【联网搜索结果】\n{run_web_search(f'A股行情 {date} 上证指数 北向资金 板块')}"
        )
    if "query_market_data" in allowed:
        for dt in ("index", "sector", "northbound"):
            parts.append(f"【{dt} 行情数据】\n{run_query_market_data(dt, date)}")
    if "query_history" in allowed:
        parts.append(
            f"【近期历史复盘】\n{run_query_history(db_path, 'daily', limit=3)}"
        )
    return "\n\n".join(parts)
