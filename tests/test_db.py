"""Unit tests for db.py — covers both :memory: and file-based modes."""
import pytest

from agent_platform.infrastructure.persistence.db import (
    get_conn,
    init_db,
    insert_run,
    load_history,
    insert_feedback,
    load_feedback_summary,
)
from agent_platform.domain.models import (
    Features,
    LlmTokens,
    MarketSnapshot,
)


# ─── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mem_db():
    import agent_platform.infrastructure.persistence.db as db_module
    db_module._memory_conn = None
    init_db(":memory:")
    yield ":memory:"
    db_module._memory_conn = None


@pytest.fixture
def file_db(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    yield path


def _make_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        asof="2024-01-02T08:00:00+00:00",
        provider="mock",
        date="2024-01-02",
        is_trading_day=True,
    )


def _make_features() -> Features:
    return Features(index_view="平", sector_view="混", sentiment_view="中性", macro_view="稳")


def _insert_sample_run(db_path: str, request_id: str = "req-001") -> None:
    insert_run(
        db_path,
        request_id=request_id,
        created_at="2024-01-02T08:00:00+00:00",
        mode="daily",
        provider="mock",
        date="2024-01-02",
        prompt_version="v1",
        model=None,
        snapshot=_make_snapshot(),
        features=_make_features(),
        recap=None,
        rendered_markdown=None,
        rendered_wechat_text=None,
        eval_obj={},
        error=None,
        latency_ms=100,
        tokens=LlmTokens(),
    )


# ─── get_conn ─────────────────────────────────────────────────────────────────

def test_get_conn_memory_singleton(mem_db):
    import agent_platform.infrastructure.persistence.db as db_module
    with get_conn(":memory:"):
        pass
    conn1 = db_module._memory_conn
    with get_conn(":memory:"):
        pass
    assert db_module._memory_conn is conn1


def test_get_conn_file_creates_tables(file_db):
    with get_conn(file_db) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "recap_runs" in tables


# ─── init_db ──────────────────────────────────────────────────────────────────

def test_init_db_memory_creates_all_tables(mem_db):
    with get_conn(":memory:") as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert {
        "recap_runs",
        "recap_feedback",
        "evolution_notes",
        "backtest_results",
        "prompt_state",
    } <= tables


def test_init_db_idempotent(file_db):
    init_db(file_db)  # second call must not raise


# ─── insert_run / load_history ────────────────────────────────────────────────

def test_insert_and_load_history_memory(mem_db):
    _insert_sample_run(mem_db)
    history = load_history(mem_db, limit=10)
    assert len(history) == 1
    assert history[0]["request_id"] == "req-001"
    assert history[0]["mode"] == "daily"


def test_insert_and_load_history_file(file_db):
    _insert_sample_run(file_db, "req-file-001")
    history = load_history(file_db, limit=10)
    assert len(history) == 1
    assert history[0]["request_id"] == "req-file-001"


def test_load_history_limit(mem_db):
    for i in range(5):
        _insert_sample_run(mem_db, f"req-{i:03d}")
    assert len(load_history(mem_db, limit=3)) == 3


# ─── insert_feedback / load_feedback_summary ──────────────────────────────────

def test_feedback_roundtrip(mem_db):
    _insert_sample_run(mem_db)
    insert_feedback(
        mem_db,
        request_id="req-001",
        created_at="2024-01-02T09:00:00+00:00",
        rating=4,
        tags=["清晰"],
        comment="不错",
    )
    summary = load_feedback_summary(mem_db, limit=10)
    assert summary["avg_rating"] == 4.0
    assert "清晰" in summary["praise_tags"]


def test_feedback_empty(mem_db):
    summary = load_feedback_summary(mem_db, limit=10)
    assert summary["avg_rating"] is None
    assert summary["low_rated_tags"] == []


# ─── prompt_state ─────────────────────────────────────────────────────────────

def test_prompt_state_empty_returns_none(file_db):
    from agent_platform.infrastructure.persistence.db import get_active_prompt_version

    assert get_active_prompt_version(file_db) is None


def test_prompt_state_upsert(file_db):
    from agent_platform.infrastructure.persistence.db import (
        get_active_prompt_version,
        set_active_prompt_version,
    )

    set_active_prompt_version(file_db, "base.v1", updated_at="2024-01-02T08:00:00+00:00")
    assert get_active_prompt_version(file_db) == "base.v1"

    # second call updates, not duplicates
    set_active_prompt_version(file_db, "base.v2", updated_at="2024-01-02T09:00:00+00:00")
    assert get_active_prompt_version(file_db) == "base.v2"

    with get_conn(file_db) as conn:
        cnt = conn.execute("SELECT COUNT(*) AS c FROM prompt_state").fetchone()["c"]
    assert cnt == 1
