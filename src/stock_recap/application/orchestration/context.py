"""单次复盘 Agent 运行状态：跨阶段传递的可变上下文。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from stock_recap.config.settings import Settings
from stock_recap.domain.models import Features, GenerateRequest, LlmTokens, MarketSnapshot, Recap
from stock_recap.domain.run_context import RunContext


@dataclass
class RecapAgentRunState:
    """Observe（感知）→ Recall（记忆）→ Plan（组 prompt）→ Act（LLM）→ Critique（评测）→ Persist。"""

    request: GenerateRequest
    settings: Settings
    run_ctx: RunContext
    t0: float
    defer_evolution_backtest: bool = False
    stream_pipeline_completed: bool = False

    snapshot: Optional[MarketSnapshot] = None
    features: Optional[Features] = None
    memory: List[Dict[str, Any]] = field(default_factory=list)
    evolution_guidance: Optional[str] = None
    feedback_summary: Optional[Dict[str, Any]] = None
    pattern_summary: Optional[str] = None
    backtest_context: Optional[str] = None
    prompt_version: str = ""
    messages: List[Dict[str, str]] = field(default_factory=list)

    recap: Optional[Recap] = None
    rendered_markdown: Optional[str] = None
    rendered_wechat_text: Optional[str] = None
    tokens: LlmTokens = field(default_factory=LlmTokens)
    llm_error: Optional[str] = None
    eval_result: Dict[str, Any] = field(default_factory=dict)
    push_result: Optional[bool] = None
