"""统一工具门面：OpenAI/Ollama 循环与预取注入共用同一策略与执行入口。"""
from __future__ import annotations

from typing import Any, Dict, List, Set

from stock_recap.config.settings import Settings

from stock_recap.infrastructure.tools.registry import (
    TOOL_SCHEMAS,
    execute_tool,
    prefetch_for_prompt,
)


class RecapToolRunner:
    """Agent 工具运行时：按 Settings 过滤 schema，执行与预取语义一致。

    预取与 function-calling 使用同一套开关（``Settings.tools_*``），
    保证 cursor-cli/gemini-cli 的预注入上下文与 OpenAI/Ollama 的工具循环
    决策面严格对齐。
    """

    __slots__ = ("_settings",)

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def tools_enabled(self) -> bool:
        return self._settings.tools_enabled

    def enabled_tool_names(self) -> Set[str]:
        """当前生效的工具集合（总开关关闭时返回空集）。"""
        if not self._settings.tools_enabled:
            return set()
        names: Set[str] = set()
        if self._settings.tools_web_search:
            names.add("web_search")
        if self._settings.tools_market_data:
            names.add("query_market_data")
        if self._settings.tools_history:
            names.add("query_history")
        return names

    def openai_compatible_schemas(self) -> List[Dict[str, Any]]:
        allowed = self.enabled_tool_names()
        if not allowed:
            return []
        return [t for t in TOOL_SCHEMAS if t["function"]["name"] in allowed]

    def execute(self, name: str, arguments: Dict[str, Any], db_path: str) -> str:
        return execute_tool(name, arguments, db_path=db_path)

    def prefetch_for_prompt(self, date: str, db_path: str) -> str:
        """按当前启用工具集合预取；总开关关闭时返回空串。"""
        allowed = self.enabled_tool_names()
        if not allowed:
            return ""
        return prefetch_for_prompt(date, db_path=db_path, enabled_tools=allowed)
