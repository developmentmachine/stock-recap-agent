"""``LlmProvider`` Protocol：所有后端的最小契约。

设计原则：
- **无状态**：provider 不持有 ``Settings``，所有参数通过 ``call()`` 传入；
  便于同一进程多租户各持一套 ``Settings``。
- **返回 (Recap, LlmTokens)**：统一类型让上层（``call_llm``）不关心差异。
- **不处理重试**：重试/熔断/预算在上层 ``call_llm`` 统一包装。
"""
from __future__ import annotations

from typing import Dict, List, Protocol, Tuple, runtime_checkable

from agent_platform.config.settings import Settings
from agent_platform.domain.models import LlmTokens, Mode, Recap


@runtime_checkable
class LlmProvider(Protocol):
    """所有 LLM 后端实现的最小接口。"""

    name: str

    def call(
        self,
        settings: Settings,
        mode: Mode,
        messages: List[Dict[str, str]],
        *,
        model: str,
        db_path: str,
        date: str,
    ) -> Tuple[Recap, LlmTokens]:
        """执行一次 LLM 调用并返回 (Recap, LlmTokens)；失败请抛 ``LlmError``。"""
        ...
