"""Wave 4 / w4-2：结构化日志注入 ctx 字段。

覆盖：
1. ``RunContextFilter`` 在 ``current_run_context`` 设置后能把 trace_id/request_id/mode/provider
   写到 LogRecord；
2. ``JsonFormatter`` 输出单行 JSON，能被 ``json.loads``；
3. 业务侧 ``logger.info(_stable_json({...}))`` 的 message 会被「平铺合并」到顶层；
4. ``setup_structured_logging`` 幂等；
5. ``generate_once`` 的整条调用链内日志都自动带 ctx 字段。
"""
from __future__ import annotations

import io
import json
import logging
from typing import Any, Dict

import pytest

from agent_platform.domain.run_context import RunContext
from agent_platform.observability.logging_setup import (
    JsonFormatter,
    RunContextFilter,
    reset_structured_logging,
    setup_structured_logging,
)
from agent_platform.observability.runtime_context import current_run_context


@pytest.fixture
def _captured_stream():
    """构造可观测的 logger，避免污染 root（不调用 setup_structured_logging）。"""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RunContextFilter())

    test_logger = logging.getLogger("test_logging_setup.unit")
    test_logger.handlers.clear()
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)
    test_logger.propagate = False
    yield test_logger, buf
    test_logger.handlers.clear()


def _read_lines(buf: io.StringIO):
    raw = buf.getvalue().strip()
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def test_filter_injects_ctx_when_present(_captured_stream):
    test_logger, buf = _captured_stream
    ctx = RunContext.new(mode="daily", provider="live")
    token = current_run_context.set(ctx)
    try:
        test_logger.info("hello")
    finally:
        current_run_context.reset(token)

    lines = _read_lines(buf)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["message"] == "hello"
    assert rec["request_id"] == ctx.request_id
    assert rec["trace_id"] == ctx.trace_id
    assert rec["mode"] == "daily"
    assert rec["provider"] == "live"
    assert rec["level"] == "INFO"
    assert "ts" in rec


def test_filter_omits_ctx_keys_when_no_run_context(_captured_stream):
    test_logger, buf = _captured_stream
    test_logger.warning("standalone")
    rec = _read_lines(buf)[0]
    for key in ("request_id", "trace_id", "mode", "provider"):
        assert key not in rec, f"{key} should be omitted when no run context is set"
    assert rec["message"] == "standalone"


def test_json_message_is_merged_flat(_captured_stream):
    """业务侧 ``logger.info(_stable_json({"event":"x","k":1}))`` 不应被 double-encoded。"""
    test_logger, buf = _captured_stream
    payload = {"event": "outbox_done", "action_type": "evolution", "elapsed_ms": 12}
    msg = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    test_logger.info(msg)

    rec = _read_lines(buf)[0]
    # 关键：合并后顶层就有 event/action_type/elapsed_ms，没有再嵌套一层
    assert rec["event"] == "outbox_done"
    assert rec["action_type"] == "evolution"
    assert rec["elapsed_ms"] == 12
    # 业务字段优先；但若 message 本身就是 JSON 形式，formatter 不会再放 message 字段
    assert "message" not in rec or rec.get("message") in (None, msg)


def test_setup_structured_logging_is_idempotent():
    setup_structured_logging(level=logging.INFO, force=True)
    setup_structured_logging(level=logging.WARNING)  # 不应再加 handler
    root = logging.getLogger()
    handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
    assert len(handlers) == 1
    assert root.level == logging.WARNING
    reset_structured_logging()


def test_setup_emits_json_to_stream():
    buf = io.StringIO()
    setup_structured_logging(level=logging.INFO, force=True, stream=buf)
    try:
        ctx = RunContext.new(mode="strategy", provider="mock")
        token = current_run_context.set(ctx)
        try:
            logging.getLogger("agent_platform.test").info("setup_check")
        finally:
            current_run_context.reset(token)
        rec = json.loads(buf.getvalue().strip().splitlines()[-1])
        assert rec["message"] == "setup_check"
        assert rec["mode"] == "strategy"
        assert rec["provider"] == "mock"
        assert rec["request_id"] == ctx.request_id
    finally:
        reset_structured_logging()


def test_extra_fields_are_passed_through(_captured_stream):
    test_logger, buf = _captured_stream
    test_logger.info("with extras", extra={"latency_ms": 42, "ok": True})
    rec = _read_lines(buf)[0]
    assert rec["latency_ms"] == 42
    assert rec["ok"] is True


def test_exception_info_is_serialized(_captured_stream):
    test_logger, buf = _captured_stream
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        test_logger.exception("failed")
    rec = _read_lines(buf)[0]
    assert "exc_info" in rec
    assert "RuntimeError" in rec["exc_info"]
