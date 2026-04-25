"""显式 Agent 抽象：封装配置与单次运行入口（编排见 orchestration.pipeline）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from agent_platform.application.recap import generate_once
from agent_platform.config.settings import Settings
from agent_platform.domain.models import GenerateRequest, GenerateResponse
from agent_platform.domain.run_context import RunContext


@dataclass
class RecapAgent:
    """A 股复盘智能体（单类用例，便于测试替换与遥测挂载）。"""

    settings: Settings
    name: str = "recap"

    def run(self, req: GenerateRequest, ctx: Optional[RunContext] = None) -> GenerateResponse:
        return generate_once(req, self.settings, ctx=ctx)
