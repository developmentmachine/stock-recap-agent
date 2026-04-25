"""单次 Agent 运行预算（max_tool_calls / max_tokens / max_wall_ms）。

设计要点：
- 任一维度超限 → 抛 ``LlmBudgetExceeded``（``LlmError`` 子类，但 ``call_llm``
  的 tenacity 不重试它，Critic 也不再重入）。
- 墙钟基准用 ``time.monotonic()``，不受系统时钟回拨影响。
- ``record_*`` 方法总是先累加再 ``check()``：保证「先扣额度再校验」的语义，
  即使临界值，也能稳定中止。
- ``0`` 表示「不限制」，便于关闭某一维度（如离线 batch 跑允许更长 wall_ms）。

不在此处做日志/遥测；调用方拦截 ``LlmBudgetExceeded`` 后再写 metric 更合适。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from agent_platform.config.settings import Settings
from agent_platform.domain.models import LlmBudgetExceeded


@dataclass
class AgentBudget:
    """单次运行内累计资源用量与上限。"""

    max_tool_calls: int
    max_tokens: int
    max_wall_ms: int
    started_at_monotonic: float = field(default_factory=time.monotonic)
    tool_calls_used: int = 0
    tokens_used: int = 0

    @classmethod
    def from_settings(cls, settings: Settings) -> "AgentBudget":
        return cls(
            max_tool_calls=int(settings.agent_max_tool_calls),
            max_tokens=int(settings.agent_max_tokens),
            max_wall_ms=int(settings.agent_max_wall_ms),
        )

    def wall_ms_used(self) -> int:
        return int((time.monotonic() - self.started_at_monotonic) * 1000)

    def remaining_wall_ms(self) -> int:
        if self.max_wall_ms <= 0:
            return -1
        return max(0, self.max_wall_ms - self.wall_ms_used())

    def record_tool_call(self, n: int = 1) -> None:
        """工具执行前调用：先扣额度再校验。"""
        self.tool_calls_used += int(n)
        self.check()

    def record_tokens(self, n: int) -> None:
        """LLM 调用结束后调用：把本次消耗的 token 累加，超额抛异常。"""
        self.tokens_used += int(n or 0)
        self.check()

    def check(self) -> None:
        """主动校验任一维度是否超限。供阶段切换时显式触发。"""
        if self.max_tool_calls > 0 and self.tool_calls_used > self.max_tool_calls:
            raise LlmBudgetExceeded(
                "tool_calls", limit=self.max_tool_calls, used=self.tool_calls_used
            )
        if self.max_tokens > 0 and self.tokens_used > self.max_tokens:
            raise LlmBudgetExceeded("tokens", limit=self.max_tokens, used=self.tokens_used)
        if self.max_wall_ms > 0:
            wall = self.wall_ms_used()
            if wall > self.max_wall_ms:
                raise LlmBudgetExceeded("wall_ms", limit=self.max_wall_ms, used=wall)


__all__ = ["AgentBudget"]
