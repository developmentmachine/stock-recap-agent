"""Cursor/Gemini CLI 共享工具：预执行工具结果注入 prompt。"""
from __future__ import annotations

from typing import Dict, List

from agent_platform.config.settings import Settings


def inject_prefetch(
    messages: List[Dict[str, str]],
    settings: Settings,
    db_path: str,
    date: str,
) -> List[Dict[str, str]]:
    """为不支持 function-calling 的后端预执行工具并注入 prompt。

    仅当总开关 + 至少一个子工具开关为 True 时才真正注入；与 function-calling
    路径保持可见性一致，避免「flag 关了但 prefetch 仍在跑」。
    """
    from agent_platform.infrastructure.tools.runner import RecapToolRunner

    runner = RecapToolRunner(settings)
    if not runner.enabled_tool_names():
        return messages
    context = runner.prefetch_for_prompt(date, db_path)
    if not context:
        return messages
    injected = list(messages)
    injected.insert(
        1,
        {
            "role": "user",
            "content": f"【工具预执行结果，请结合以下实时数据进行分析】\n\n{context}",
        },
    )
    return injected
