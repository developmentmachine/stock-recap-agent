"""Outbox / pending_actions：幂等入队 + 抢占消费 + 指数退避。"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from stock_recap.application.side_effects import outbox
from stock_recap.infrastructure.persistence.db import init_db


@pytest.fixture
def file_db(tmp_path):
    path = str(tmp_path / "outbox.db")
    init_db(path)
    yield path


@pytest.fixture(autouse=True)
def _restore_handlers():
    """每个用例独占 _HANDLERS 注册表，互不污染。"""
    snapshot = dict(outbox._HANDLERS)  # type: ignore[attr-defined]
    yield
    outbox._HANDLERS.clear()  # type: ignore[attr-defined]
    outbox._HANDLERS.update(snapshot)  # type: ignore[attr-defined]


def test_enqueue_is_idempotent_per_request_action(file_db: str) -> None:
    assert outbox.enqueue(
        file_db, request_id="req-1", action_type="evolution", payload={"a": 1}
    ) is True
    # 同一 (request_id, action_type) 第二次入队应返回 False，且只剩 1 条。
    assert outbox.enqueue(
        file_db, request_id="req-1", action_type="evolution", payload={"a": 2}
    ) is False
    rows = outbox.list_actions(file_db)
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


def test_process_due_runs_handler_and_marks_done(file_db: str) -> None:
    received: List[Dict[str, Any]] = []
    outbox.register_handler(
        "demo", lambda payload: received.append(payload)  # type: ignore[arg-type]
    )

    outbox.enqueue(file_db, request_id="req-x", action_type="demo", payload={"k": "v"})
    summary = outbox.process_due(file_db)

    assert summary.claimed == 1 and summary.done == 1 and summary.failed_retry == 0
    assert received == [{"k": "v"}]
    rows = outbox.list_actions(file_db, status="done")
    assert len(rows) == 1


def test_process_due_unknown_handler_marks_final_failed(file_db: str) -> None:
    outbox.enqueue(file_db, request_id="req-2", action_type="ghost")
    summary = outbox.process_due(file_db)
    assert summary.failed_final == 1
    rows = outbox.list_actions(file_db, status="failed")
    assert len(rows) == 1
    assert "no_handler" in (rows[0]["last_error"] or "")


def test_process_due_handler_failure_uses_backoff(file_db: str) -> None:
    """非最终失败应：状态回到 pending、attempts+1、next_attempt_at 推后。"""
    calls = {"n": 0}

    def boom(_payload: Dict[str, Any]) -> None:
        calls["n"] += 1
        raise RuntimeError("transient")

    outbox.register_handler("flaky", boom)
    outbox.enqueue(file_db, request_id="req-3", action_type="flaky")

    summary = outbox.process_due(file_db)
    assert summary.failed_retry == 1 and summary.failed_final == 0
    assert calls["n"] == 1

    rows = outbox.list_actions(file_db)
    row = rows[0]
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert "transient" in (row["last_error"] or "")
    # 指数退避把 next_attempt_at 推到了未来 → 立刻再 sweep 不会被抢到。
    summary2 = outbox.process_due(file_db)
    assert summary2.claimed == 0


def test_process_due_eventually_marks_final_after_max_attempts(
    file_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """直接把 attempts 跳到 _MAX_ATTEMPTS-1，下一次失败应标 final。"""
    outbox.register_handler(
        "always_fail",
        lambda _p: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    outbox.enqueue(file_db, request_id="req-4", action_type="always_fail")

    # 直接把 attempts 推到极限-1，下一次失败应被标 final。
    from stock_recap.infrastructure.persistence.db import get_conn

    with get_conn(file_db) as conn:
        conn.execute(
            "UPDATE pending_actions SET attempts = ? WHERE request_id = ?",
            (outbox._MAX_ATTEMPTS - 1, "req-4"),  # type: ignore[attr-defined]
        )

    summary = outbox.process_due(file_db)
    assert summary.failed_final == 1 and summary.failed_retry == 0
    rows = outbox.list_actions(file_db, status="failed")
    assert len(rows) == 1
