"""单次复盘 Agent 运行状态：跨阶段传递的可变上下文。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent_platform.application.orchestration.budget import AgentBudget
from agent_platform.config.settings import Settings
from agent_platform.domain.models import Features, GenerateRequest, LlmTokens, MarketSnapshot, Recap
from agent_platform.domain.run_context import RunContext


@dataclass
class RecapAgentRunState:
    """Observe（感知）→ Recall（记忆）→ Plan（组 prompt）→ Act（LLM）→ Critique（评测）→ Persist。"""

    request: GenerateRequest
    settings: Settings
    run_ctx: RunContext
    t0: float
    defer_evolution_backtest: bool = False
    stream_pipeline_completed: bool = False

    budget: Optional[AgentBudget] = None  # 由 application/recap.py 在入口处注入

    snapshot: Optional[MarketSnapshot] = None
    features: Optional[Features] = None
    memory: List[Dict[str, Any]] = field(default_factory=list)
    memory_long: List[Dict[str, Any]] = field(default_factory=list)
    memory_entities: List[Dict[str, Any]] = field(default_factory=list)
    memory_recall_meta: Dict[str, Any] = field(default_factory=dict)
    evolution_guidance: Optional[str] = None
    feedback_summary: Optional[Dict[str, Any]] = None
    pattern_summary: Optional[str] = None
    backtest_context: Optional[str] = None
    prompt_version: str = ""
    experiment_id: Optional[str] = None
    variant_id: Optional[str] = None
    messages: List[Dict[str, str]] = field(default_factory=list)

    recap: Optional[Recap] = None
    rendered_markdown: Optional[str] = None
    rendered_wechat_text: Optional[str] = None
    tokens: LlmTokens = field(default_factory=LlmTokens)
    llm_error: Optional[str] = None
    budget_error: Optional[str] = None  # LlmBudgetExceeded 的 kind/limit/used，便于落库 & 报指标
    critic_retries_used: int = 0  # Critic 重入实际触发次数（0 表示一次性通过/未触发）
    eval_result: Dict[str, Any] = field(default_factory=dict)
    push_result: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.budget is None:
            self.budget = AgentBudget.from_settings(self.settings)
