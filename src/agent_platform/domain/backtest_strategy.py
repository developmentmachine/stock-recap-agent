"""回测评分策略（与 ``RecapStrategy`` 输出模型区分）。

企业级扩展：通过 **Protocol + 注册表** 接入多种实现；调用方只依赖
``resolve_backtest_strategy``，便于单测替换与未来接入 ML 评分器。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_platform.domain.models import BacktestResult, MarketSnapshot, RecapStrategy


@runtime_checkable
class BacktestStrategy(Protocol):
    """对「某日策略 recap」与「次日真实快照」做结构化评分。"""

    @property
    def name(self) -> str: ...

    def evaluate(
        self,
        *,
        strategy_date: str,
        strategy_recap: RecapStrategy,
        actual_date: str,
        actual_snapshot: MarketSnapshot,
    ) -> BacktestResult: ...


__all__ = ["BacktestStrategy"]
