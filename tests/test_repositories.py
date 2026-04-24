"""W5-1：Repository Protocol + SQLite 实现的契约测试。

为什么不复用 ``test_db.py`` 的覆盖？ —— 这里测的是「Protocol 契约」而非 SQL 细节：
- Protocol 与实现解耦，未来加 PostgreSQL impl 也要跑同一组用例；
- 业务层依赖的是 Protocol，因此契约稳了上层就不会被换库打破。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stock_recap.domain.models import (
    BacktestResult,
    EvolutionNote,
    Features,
    LlmTokens,
    MarketSnapshot,
    RecapDaily,
    RecapDailySection,
)
from stock_recap.domain.repositories import (
    BacktestRepository,
    EvolutionRepository,
    ExperimentRepository,
    FeedbackRepository,
    RecapAuditRepository,
    RunRepository,
)
from stock_recap.infrastructure.persistence.db import init_db
from stock_recap.infrastructure.persistence.repositories import (
    Repositories,
    build_default_repositories,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _build_recap() -> RecapDaily:
    section = RecapDailySection(
        title="t", core_conclusion="c", bullets=["【复盘基准日：2025年01月02日 星期四】", "x", "y"]
    )
    return RecapDaily(
        mode="daily",
        date="2025-01-02",
        sections=[section, section, section],
        risks=["不构成投资建议"],
    )


@pytest.fixture
def repos(tmp_path) -> Repositories:
    db = tmp_path / "repos.db"
    init_db(str(db))
    return build_default_repositories(str(db))


# ─── Protocol 一致性 ───────────────────────────────────────────────────────


def test_default_repositories_satisfy_all_protocols(repos: Repositories):
    """``isinstance`` + Protocol 检查 = 编译期之外的契约校验。"""
    assert isinstance(repos.runs, RunRepository)
    assert isinstance(repos.feedback, FeedbackRepository)
    assert isinstance(repos.evolution, EvolutionRepository)
    assert isinstance(repos.backtests, BacktestRepository)
    assert isinstance(repos.experiments, ExperimentRepository)
    assert isinstance(repos.audits, RecapAuditRepository)


# ─── RunRepository ────────────────────────────────────────────────────────


def test_run_repo_insert_and_history(repos: Repositories):
    snapshot = MarketSnapshot(
        date="2025-01-02",
        asof=_now(),
        provider="mock",
    )
    features = Features()
    repos.runs.insert(
        request_id="r-1",
        created_at=_now(),
        mode="daily",
        provider="mock",
        date="2025-01-02",
        prompt_version="v1",
        model=None,
        snapshot=snapshot,
        features=features,
        recap=_build_recap(),
        rendered_markdown="# md",
        rendered_wechat_text="text",
        eval_obj={"ok": True},
        error=None,
        latency_ms=42,
        tokens=LlmTokens(input_tokens=10, output_tokens=20),
        experiment_id="exp-1",
        variant_id="A",
    )

    history = repos.runs.load_history(limit=10)
    assert len(history) == 1
    assert history[0]["request_id"] == "r-1"

    recent = repos.runs.load_recent(date="2025-01-03", mode="daily", limit=5)
    assert any(item["date"] == "2025-01-02" for item in recent)


# ─── FeedbackRepository ───────────────────────────────────────────────────


def test_feedback_repo_summary(repos: Repositories):
    repos.feedback.insert(
        request_id="r-1",
        rating=5,
        tags=["clear", "accurate"],
        comment="ok",
        created_at=_now(),
    )
    repos.feedback.insert(
        request_id="r-2",
        rating=2,
        tags=["vague"],
        comment="too generic",
        created_at=_now(),
    )
    summary = repos.feedback.load_summary(limit=10)
    assert summary["avg_rating"] == 3.5
    assert "vague" in summary["low_rated_tags"]
    assert "clear" in summary["praise_tags"]


# ─── EvolutionRepository ──────────────────────────────────────────────────


def test_evolution_repo_note_and_prompt_version(repos: Repositories):
    note = EvolutionNote(
        summary="改进结构",
        problems=["概述空洞"],
        prompt_suggestions=["添加资金主线段落"],
        should_bump_version=True,
    )
    repos.evolution.insert_note(
        created_at=_now(),
        trigger_run_id="r-trigger",
        note=note,
        prompt_version_suggested="v-next",
    )
    latest = repos.evolution.load_latest_note()
    assert latest is not None
    assert latest["prompt_version_suggested"] == "v-next"
    history = repos.evolution.load_history(limit=5)
    assert len(history) == 1

    repos.evolution.set_active_prompt_version("v-active", updated_at=_now())
    assert repos.evolution.get_active_prompt_version() == "v-active"


# ─── BacktestRepository ───────────────────────────────────────────────────


def test_backtest_repo_round_trip(repos: Repositories):
    res = BacktestResult(
        strategy_date="2025-01-02",
        actual_date="2025-01-03",
        predicted_sectors=["AI", "电力"],
        actual_top_sectors=["电力", "煤炭", "AI"],
        hit_count=2,
        hit_rate=2 / 3,
        detail="2 命中",
    )
    repos.backtests.insert(result=res, created_at=_now())
    rows = repos.backtests.load_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["strategy_date"] == "2025-01-02"


# ─── ExperimentRepository ─────────────────────────────────────────────────


def test_experiment_repo_active_and_variants(repos: Repositories):
    now = _now()
    repos.experiments.upsert_experiment(
        experiment_id="exp-x",
        mode="daily",
        starts_at=now,
        created_at=now,
    )
    repos.experiments.upsert_variant(
        experiment_id="exp-x",
        variant_id="A",
        prompt_version="vA",
        traffic_weight=3,
        created_at=now,
    )
    repos.experiments.upsert_variant(
        experiment_id="exp-x",
        variant_id="B",
        prompt_version="vB",
        traffic_weight=7,
        created_at=now,
    )
    active = repos.experiments.load_active(mode="daily")
    assert active is not None and active["experiment_id"] == "exp-x"

    variants = repos.experiments.load_variants(experiment_id="exp-x")
    assert {v["variant_id"] for v in variants} == {"A", "B"}

    listed = repos.experiments.list_experiments(mode="daily")
    assert len(listed) == 1


# ─── RecapAuditRepository ─────────────────────────────────────────────────


def test_audit_repo_round_trip(repos: Repositories):
    repos.audits.insert(
        request_id="aud-1",
        created_at=_now(),
        mode="daily",
        provider="mock",
        prompt_version="v1",
        model=None,
        trace_id="t1",
        session_id="s1",
        messages=[{"role": "system", "content": "x"}],
        recap=_build_recap(),
        eval_obj={"ok": True},
        tokens=LlmTokens(input_tokens=5, output_tokens=10),
        llm_error=None,
        budget_error=None,
        critic_retries_used=0,
        experiment_id=None,
        variant_id=None,
    )
    rows = repos.audits.load(request_id="aud-1")
    assert len(rows) == 1
    assert rows[0]["mode"] == "daily"
    assert rows[0]["recap"]["date"] == "2025-01-02"
