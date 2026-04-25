"""Agent Platform CLI 入口 — 平台级分发器。

用法：
  agent_platform <agent-name> [agent-specific args]
  agent_platform --mcp-tools

可用 agent：
  stock-recap    A股日终复盘 / 次日策略智能体

示例：
  agent_platform stock-recap --mode daily --provider live
  agent_platform stock-recap --help
  agent_platform --mcp-tools
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from agent_platform.config.settings import Settings, get_settings
from agent_platform.infrastructure.persistence.db import init_db
from agent_platform.interfaces.agents import stock_recap_cli

# ── Agent 注册表 ──────────────────────────────────────────────────────────────
# 新增 agent 时：在 interfaces/agents/ 下新建模块并在此注册一行即可。
AGENTS: dict[str, Any] = {
    "stock-recap": stock_recap_cli,
}


def _setup_logger(level: str) -> logging.Logger:
    from agent_platform.observability.logging_setup import setup_structured_logging

    setup_structured_logging(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
    )
    return logging.getLogger("agent_platform")


def cli_main() -> int:
    parser = argparse.ArgumentParser(
        prog="agent_platform",
        description="Agent Platform — 多智能体运行平台",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
可用 agent：
  stock-recap    A股日终复盘 / 次日策略智能体

示例：
  agent_platform stock-recap --mode daily --provider live
  agent_platform stock-recap --help
  agent_platform --mcp-tools
""",
    )

    parser.add_argument(
        "--mcp-tools",
        action="store_true",
        help="启动 MCP stdio 工具服务（与进程内 function calling 语义一致）",
    )

    subparsers = parser.add_subparsers(dest="agent", metavar="AGENT")
    _subparser_map: dict[str, argparse.ArgumentParser] = {}
    for name, module in AGENTS.items():
        sub = subparsers.add_parser(name, help=module.__doc__ or name)
        module.register_subparser(sub)
        _subparser_map[name] = sub

    args = parser.parse_args()
    settings = get_settings()

    # MCP stdio 必须独占 stdout；先于其他日志/init，避免污染 JSON-RPC 流
    if args.mcp_tools:
        from agent_platform.observability.tracing import configure_tracing
        from agent_platform.interfaces.mcp_stdio import run_mcp_stdio

        configure_tracing(settings)
        init_db(settings.db_path)
        run_mcp_stdio()
        return 0

    if not args.agent:
        parser.print_help()
        return 1

    _setup_logger(settings.log_level)

    from agent_platform.observability.tracing import configure_tracing

    configure_tracing(settings)
    init_db(settings.db_path)

    return AGENTS[args.agent].run(args, settings, _subparser_map[args.agent])
