"""Repository Protocols：domain 层不知道任何 SQL，只声明业务需要什么持久化能力。

为什么：
- 当前 ``application/`` 层直接 import ``infrastructure.persistence.db.*``，让换库
  （sqlite → postgres）/ 单元测试 mock 都得改一大堆 import；
- Protocol 把「业务能力」和「实现细节」分离，未来可以同时存在
  ``SqliteRunRepository`` / ``PostgresRunRepository`` / ``InMemoryRunRepository``
  而上层无感；
- 也是 hex / clean / DDD 架构的标准做法。

迁移策略（非破坏）：
- 第一步（本 wave）：声明 Protocol + 提供 ``SqliteXxxRepository`` 委托给现有
  ``db.*`` 模块函数 + 工厂 ``build_default_repositories(db_path)``；
- 第二步（后续 wave）：把 ``application/`` 层调用 ``db.*`` 的地方逐步换成
  ``ctx.run_repo.insert(...)`` 这类调用；
- 第三步（远期）：``db.*`` 函数全部 inline 进对应 repo 模块，``db.py`` 只保留
  ``init_db / get_conn`` 这类基础设施；
- 任何阶段 backward-compat：旧 import 始终能用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from agent_platform.domain.models import (
    BacktestResult,
    EvolutionNote,
    Features,
    LlmTokens,
    MarketSnapshot,
    Recap,
)


@runtime_checkable
class RunRepository(Protocol):
    """``recap_runs`` 表 — 一次复盘运行的完整快照（含 snapshot/features/recap/eval）。"""

    def insert(
        self,
        *,
        request_id: str,
        created_at: str,
        mode: str,
        provider: str,
        date: str,
        prompt_version: str,
        model: Optional[str],
        snapshot: MarketSnapshot,
        features: Features,
        recap: Optional[Recap],
        rendered_markdown: Optional[str],
        rendered_wechat_text: Optional[str],
        eval_obj: Dict[str, Any],
        error: Optional[str],
        latency_ms: int,
        tokens: LlmTokens,
        experiment_id: Optional[str] = None,
        variant_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> None: ...

    def load_recent(
        self, *, date: str, mode: str, limit: int, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]: ...

    def load_for_evolution(self, *, limit: int = 20) -> List[Dict[str, Any]]: ...

    def count_since_last_evolution(self) -> int: ...

    def load_history(self, *, limit: int = 20) -> List[Dict[str, Any]]: ...


@runtime_checkable
class FeedbackRepository(Protocol):
    """``recap_feedback`` 表 — 用户对每次 recap 的评分/标签/留言。"""

    def insert(
        self,
        *,
        request_id: str,
        rating: int,
        tags: List[str],
        comment: str,
        created_at: str,
        tenant_id: Optional[str] = None,
    ) -> None: ...

    def load_summary(
        self, *, limit: int = 30, tenant_id: Optional[str] = None
    ) -> Dict[str, Any]: ...


@runtime_checkable
class EvolutionRepository(Protocol):
    """``evolution_notes`` + 活跃 prompt_version 管理。"""

    def insert_note(
        self,
        *,
        created_at: str,
        trigger_run_id: Optional[str],
        note: EvolutionNote,
        prompt_version_suggested: Optional[str],
    ) -> None: ...

    def load_latest_note(self) -> Optional[Dict[str, Any]]: ...

    def load_history(self, *, limit: int = 10) -> List[Dict[str, Any]]: ...

    def get_active_prompt_version(self) -> Optional[str]: ...

    def set_active_prompt_version(self, version: str, *, updated_at: str) -> None: ...


@runtime_checkable
class BacktestRepository(Protocol):
    """``backtests`` 表 — 次日策略 vs 实际行情的命中率验证。"""

    def insert(
        self,
        *,
        result: BacktestResult,
        created_at: str,
    ) -> None: ...

    def load_recent(self, *, limit: int = 10) -> List[Dict[str, Any]]: ...

    def get_pending(self, *, today: str) -> Optional[str]: ...


@runtime_checkable
class ExperimentRepository(Protocol):
    """``prompt_experiments`` + ``prompt_experiment_variants``：A/B 实验配置。"""

    def upsert_experiment(
        self,
        *,
        experiment_id: str,
        mode: str,
        status: str = "active",
        starts_at: Optional[str] = None,
        ends_at: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> None: ...

    def upsert_variant(
        self,
        *,
        experiment_id: str,
        variant_id: str,
        prompt_version: str,
        traffic_weight: int = 1,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: str,
    ) -> None: ...

    def load_active(self, *, mode: str) -> Optional[Dict[str, Any]]: ...

    def load_variants(self, *, experiment_id: str) -> List[Dict[str, Any]]: ...

    def list_experiments(
        self,
        *,
        mode: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]: ...


@runtime_checkable
class RecapAuditRepository(Protocol):
    """``recap_audit`` 表 — 完整 messages + recap，用于合规审计与 LLM replay。"""

    def insert(
        self,
        *,
        request_id: str,
        created_at: str,
        mode: str,
        provider: str,
        prompt_version: Optional[str],
        model: Optional[str],
        trace_id: Optional[str],
        session_id: Optional[str],
        messages: Optional[List[Dict[str, Any]]],
        recap: Optional[Recap],
        eval_obj: Optional[Dict[str, Any]],
        tokens: Optional[LlmTokens],
        llm_error: Optional[str],
        budget_error: Optional[str],
        critic_retries_used: int,
        experiment_id: Optional[str] = None,
        variant_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> None: ...

    def load(
        self,
        *,
        request_id: Optional[str] = None,
        mode: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]: ...


__all__ = [
    "BacktestRepository",
    "EvolutionRepository",
    "ExperimentRepository",
    "FeedbackRepository",
    "RecapAuditRepository",
    "RunRepository",
]
