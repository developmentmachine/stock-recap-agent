"""Model Context Protocol（stdio）工具服务。

与进程内 LLM function calling 语义对齐，供 Cursor / Claude Desktop 等 MCP 宿主调用。
数据库路径使用环境变量 ``RECAP_DB_PATH``（默认 recap_system.db）。

注意：stdio 下 **stdout 仅允许 JSON-RPC**。日志只能走 stderr；不要在连接了 TTY
的终端里对本进程按回车（单独 ``\\n`` 会被误解析为 JSON，触发 Invalid JSON）。
"""
from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.logging import configure_logging as configure_mcp_logging

from agent_platform.infrastructure.tools.handlers.history import run_query_history
from agent_platform.infrastructure.tools.handlers.market_data import run_query_market_data
from agent_platform.infrastructure.tools.handlers.web_search import run_web_search

mcp = FastMCP("stock-recap-agent")


def _db_path() -> str:
    return os.environ.get("RECAP_DB_PATH", "recap_system.db")


@mcp.tool()
def web_search(query: str) -> str:
    """搜索互联网获取实时市场信息（指数、板块、资金、大宗等）。"""
    return run_web_search(query)


@mcp.tool()
def query_market_data(data_type: str, date: str | None = None) -> str:
    """查询 A 股行情：data_type 为 index | sector | northbound；date 可选 YYYY-MM-DD。"""
    return run_query_market_data(data_type, date)


@mcp.tool()
def query_history(mode: str, limit: int = 5) -> str:
    """查询项目库内历史复盘；mode 为 daily 或 strategy。"""
    return run_query_history(_db_path(), mode, limit)


def run_mcp_stdio() -> None:
    """阻塞运行 MCP stdio 服务。"""
    configure_mcp_logging("WARNING")
    print(
        "stock-recap MCP (stdio): JSON-RPC on stdout only — do not press Enter here; "
        "spawn this command from your MCP host (Cursor / Claude / Inspector).",
        file=sys.stderr,
    )
    mcp.run(transport="stdio")


def main() -> None:
    run_mcp_stdio()


if __name__ == "__main__":
    main()
