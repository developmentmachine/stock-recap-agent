"""``ReplayProvider``：测试 / 离线 replay 用的 LlmProvider。

设计理由（W4-5）：
1. 我们已经有 ``recap_audit`` 把每次真实 LLM 调用的 messages + recap 完整落库；
2. 真实场景下，我们希望「只改 prompt / 渲染 / 守护规则」时回放历史输入，
   验证下游链路（解析→Critic→render→push）不会因小改动产生不期望的副作用；
3. ``ReplayProvider`` 与真实 LLM 字面无关 —— 给定一个预录的 ``Recap``，
   它就直接返回；并把收到的 messages 记录下来供测试断言（验证上层确实把
   完整 prompt 喂给了 provider）。

不放进 ``src/`` 是因为它只在测试 / 离线脚本里使用，不需要进产物。
真要长期把 replay 当一等公民运行时也可以零成本下沉，因为它实现了正式 ``LlmProvider`` Protocol。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from agent_platform.config.settings import Settings
from agent_platform.domain.models import LlmTokens, Mode, Recap


@dataclass
class ReplayProvider:
    """实现 ``LlmProvider`` Protocol。

    - ``recap_to_return``：测试期望被回放的 Recap（不修改，原样返还）。
    - ``tokens_to_return``：可选 token 统计；默认 0。
    - ``calls``：每次 ``call()`` 收到的 ``(messages, model, mode)`` 入参，
      供测试断言「上层确实把这套 messages 给了 provider」。
    """

    name: str = "replay"
    recap_to_return: Optional[Recap] = None
    tokens_to_return: LlmTokens = field(default_factory=lambda: LlmTokens(0, 0, 0))
    calls: List[Dict[str, object]] = field(default_factory=list)

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
        if self.recap_to_return is None:
            raise RuntimeError(
                "ReplayProvider 未设置 recap_to_return；请在测试 setup 时赋值。"
            )
        self.calls.append(
            {"messages": list(messages), "model": model, "mode": mode, "date": date}
        )
        return self.recap_to_return, self.tokens_to_return


__all__ = ["ReplayProvider"]
