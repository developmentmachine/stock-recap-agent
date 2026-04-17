"""POST /v1/recap/stream NDJSON 契约（无 LLM）。"""
import json

import pytest
from fastapi.testclient import TestClient

import stock_recap.config.settings as settings_module
from stock_recap.domain.models import GenerateRequest
from stock_recap.interfaces.api.routes import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    settings_module._settings_instance = None
    monkeypatch.setenv("RECAP_DB_PATH", ":memory:")
    monkeypatch.delenv("RECAP_API_KEY", raising=False)
    monkeypatch.delenv("RECAP_OTEL_ENABLED", raising=False)
    return TestClient(app)


def test_recap_stream_ndjson_phases_and_result(client: TestClient) -> None:
    req = {
        "mode": "daily",
        "provider": "mock",
        "force_llm": False,
    }
    lines: list[str] = []
    with client.stream("POST", "/v1/recap/stream", json=req) as r:
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/x-ndjson")
        for raw in r.iter_lines():
            if raw:
                lines.append(raw)

    assert len(lines) >= 9
    events = [json.loads(line) for line in lines]
    assert events[0]["event"] == "meta"
    assert events[0]["mode"] == "daily"
    phases = [e["phase"] for e in events[1:-1] if e.get("event") == "phase"]
    assert phases == ["perceive", "recall", "plan", "act", "critique", "persist", "reflect"]
    last = events[-1]
    assert last["event"] == "result"
    assert last["http_status"] == 200
    assert "body" in last
    assert last["body"]["request_id"] == events[0]["request_id"]


def test_recap_stream_validates_date(client: TestClient) -> None:
    r = client.post(
        "/v1/recap/stream",
        json=GenerateRequest(date="not-a-date", provider="mock", force_llm=False).model_dump(),
    )
    assert r.status_code == 400


def test_recap_stream_error_event_on_phase_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object):
        raise RuntimeError("stream_phase_boom")

    monkeypatch.setattr(
        "stock_recap.application.orchestration.pipeline.collect_snapshot",
        boom,
    )
    lines: list[str] = []
    with client.stream(
        "POST",
        "/v1/recap/stream",
        json={"mode": "daily", "provider": "mock", "force_llm": False},
    ) as r:
        assert r.status_code == 200
        for raw in r.iter_lines():
            if raw:
                lines.append(raw)
    events = [json.loads(x) for x in lines]
    assert events[0]["event"] == "meta"
    assert events[-1]["event"] == "error"
    assert "stream_phase_boom" in events[-1].get("message", "")
    assert events[-1].get("phase") == "perceive"
