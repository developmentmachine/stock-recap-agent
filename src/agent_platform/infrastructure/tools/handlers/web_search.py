"""联网搜索工具实现。"""
from __future__ import annotations

import logging
from typing import Any, List

logger = logging.getLogger("agent_platform.infrastructure.tools.web_search")


def run_web_search(query: str, max_results: int = 5) -> str:
    """使用 duckduckgo-search 联网搜索。"""
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results: List[Any] = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "未找到相关结果"
        parts = []
        for r in results:
            parts.append(f"标题：{r.get('title', '')}\n摘要：{r.get('body', '')}")
        return "\n\n".join(parts)
    except ImportError:
        return "web_search 不可用：请安装 duckduckgo-search"
    except Exception as e:
        logger.warning("web_search 失败: %s", e)
        return f"搜索失败: {e}"
