"""SQLite-backed repositories：实现 ``domain/repositories.py`` 中定义的 Protocol。

设计原则（W5-1）：
- **零拷贝迁移**：repo 实现都是对现有 ``db.py`` 模块函数的薄包装，业务行为不变；
- **构造时绑定 db_path**：调用方一次构造一组 repos，不再到处传 ``db_path``；
- **可替换**：未来要换库（PostgreSQL）只需新增 ``postgres_repositories.py``，
  domain Protocol 不动；
- **可被 mock**：测试可以构造 ``InMemoryRunRepository`` 直接喂给业务函数；
- **后向兼容**：``db.py`` 里的旧函数继续可用，本 wave 不删，后续 wave 逐步收敛。

工厂入口：``build_default_repositories(db_path) -> Repositories``。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from stock_recap.domain.models import (
    BacktestResult,
    EvolutionNote,
    Features,
    LlmTokens,
    MarketSnapshot,
    Recap,
)
from stock_recap.domain.repositories import (
    BacktestRepository,
    EvolutionRepository,
    ExperimentRepository,
    FeedbackRepository,
    RecapAuditRepository,
    RunRepository,
)
from stock_recap.infrastructure.persistence import db as _db


# ─── Run ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SqliteRunRepository(RunRepository):
    db_path: str

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
    ) -> None:
        _db.insert_run(
            self.db_path,
            request_id=request_id,
            created_at=created_at,
            mode=mode,  # type: ignore[arg-type]
            provider=provider,  # type: ignore[arg-type]
            date=date,
            prompt_version=prompt_version,
            model=model,
            snapshot=snapshot,
            features=features,
            recap=recap,
            rendered_markdown=rendered_markdown,
            rendered_wechat_text=rendered_wechat_text,
            eval_obj=eval_obj,
            error=error,
            latency_ms=latency_ms,
            tokens=tokens,
            experiment_id=experiment_id,
            variant_id=variant_id,
            tenant_id=tenant_id,
        )

    def load_recent(
        self,
        *,
        date: str,
        mode: str,
        limit: int,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return _db.load_recent_runs(
            self.db_path, date, mode, limit, tenant_id=tenant_id  # type: ignore[arg-type]
        )

    def load_for_evolution(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        return _db.load_runs_for_evolution(self.db_path, limit=limit)

    def count_since_last_evolution(self) -> int:
        return _db.count_runs_since_last_evolution(self.db_path)

    def load_history(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        return _db.load_history(self.db_path, limit=limit)


# ─── Feedback ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SqliteFeedbackRepository(FeedbackRepository):
    db_path: str

    def insert(
        self,
        *,
        request_id: str,
        rating: int,
        tags: List[str],
        comment: str,
        created_at: str,
        tenant_id: Optional[str] = None,
    ) -> None:
        _db.insert_feedback(
            self.db_path,
            request_id=request_id,
            created_at=created_at,
            rating=rating,
            tags=tags,
            comment=comment,
            tenant_id=tenant_id,
        )

    def load_summary(
        self, *, limit: int = 30, tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        return _db.load_feedback_summary(self.db_path, limit=limit, tenant_id=tenant_id)


# ─── Evolution ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SqliteEvolutionRepository(EvolutionRepository):
    db_path: str

    def insert_note(
        self,
        *,
        created_at: str,
        trigger_run_id: Optional[str],
        note: EvolutionNote,
        prompt_version_suggested: Optional[str],
    ) -> None:
        _db.insert_evolution_note(
            self.db_path,
            created_at=created_at,
            trigger_run_id=trigger_run_id,
            note=note,
            prompt_version_suggested=prompt_version_suggested,
        )

    def load_latest_note(self) -> Optional[Dict[str, Any]]:
        return _db.load_latest_evolution_note(self.db_path)

    def load_history(self, *, limit: int = 10) -> List[Dict[str, Any]]:
        return _db.load_evolution_history(self.db_path, limit=limit)

    def get_active_prompt_version(self) -> Optional[str]:
        return _db.get_active_prompt_version(self.db_path)

    def set_active_prompt_version(self, version: str, *, updated_at: str) -> None:
        _db.set_active_prompt_version(self.db_path, version, updated_at=updated_at)


# ─── Backtest ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SqliteBacktestRepository(BacktestRepository):
    db_path: str

    def insert(self, *, result: BacktestResult, created_at: str) -> None:
        _db.insert_backtest(self.db_path, result=result, created_at=created_at)

    def load_recent(self, *, limit: int = 10) -> List[Dict[str, Any]]:
        return _db.load_recent_backtests(self.db_path, limit=limit)

    def get_pending(self, *, today: str) -> Optional[str]:
        return _db.get_pending_backtest(self.db_path, today)


# ─── Experiment ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SqliteExperimentRepository(ExperimentRepository):
    db_path: str

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
    ) -> None:
        _db.upsert_prompt_experiment(
            self.db_path,
            experiment_id=experiment_id,
            mode=mode,
            status=status,
            starts_at=starts_at,
            ends_at=ends_at,
            description=description,
            metadata=metadata,
            created_at=created_at,
        )

    def upsert_variant(
        self,
        *,
        experiment_id: str,
        variant_id: str,
        prompt_version: str,
        traffic_weight: int = 1,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: str,
    ) -> None:
        _db.upsert_prompt_experiment_variant(
            self.db_path,
            experiment_id=experiment_id,
            variant_id=variant_id,
            prompt_version=prompt_version,
            traffic_weight=traffic_weight,
            metadata=metadata,
            created_at=created_at,
        )

    def load_active(self, *, mode: str) -> Optional[Dict[str, Any]]:
        return _db.load_active_experiment(self.db_path, mode=mode)

    def load_variants(self, *, experiment_id: str) -> List[Dict[str, Any]]:
        return _db.load_experiment_variants(self.db_path, experiment_id=experiment_id)

    def list_experiments(
        self,
        *,
        mode: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return _db.list_prompt_experiments(
            self.db_path, mode=mode, status=status, limit=limit
        )


# ─── RecapAudit ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SqliteRecapAuditRepository(RecapAuditRepository):
    db_path: str

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
    ) -> None:
        _db.insert_recap_audit(
            self.db_path,
            request_id=request_id,
            created_at=created_at,
            mode=mode,
            provider=provider,
            prompt_version=prompt_version,
            model=model,
            trace_id=trace_id,
            session_id=session_id,
            messages=messages,
            recap=recap,
            eval_obj=eval_obj,
            tokens=tokens,
            llm_error=llm_error,
            budget_error=budget_error,
            critic_retries_used=critic_retries_used,
            experiment_id=experiment_id,
            variant_id=variant_id,
            tenant_id=tenant_id,
        )

    def load(
        self,
        *,
        request_id: Optional[str] = None,
        mode: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        return _db.load_recap_audit(
            self.db_path, request_id=request_id, mode=mode, limit=limit
        )


# ─── 工厂 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Repositories:
    """一组 repository 的聚合，单次构造、按需 inject 给业务函数。

    使用：
        repos = build_default_repositories(settings.db_path)
        repos.runs.insert(...)
        repos.audits.load(request_id=...)
    """

    runs: RunRepository
    feedback: FeedbackRepository
    evolution: EvolutionRepository
    backtests: BacktestRepository
    experiments: ExperimentRepository
    audits: RecapAuditRepository


def build_default_repositories(db_path: str) -> Repositories:
    """SQLite 的默认实现；其它后端可以提供 ``build_postgres_repositories(...)`` 等。"""
    return Repositories(
        runs=SqliteRunRepository(db_path),
        feedback=SqliteFeedbackRepository(db_path),
        evolution=SqliteEvolutionRepository(db_path),
        backtests=SqliteBacktestRepository(db_path),
        experiments=SqliteExperimentRepository(db_path),
        audits=SqliteRecapAuditRepository(db_path),
    )


__all__ = [
    "Repositories",
    "SqliteBacktestRepository",
    "SqliteEvolutionRepository",
    "SqliteExperimentRepository",
    "SqliteFeedbackRepository",
    "SqliteRecapAuditRepository",
    "SqliteRunRepository",
    "build_default_repositories",
]
