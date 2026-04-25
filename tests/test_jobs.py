"""POST /v1/jobs + GET /v1/jobs/{id} + 幂等键（mock provider，无真实 LLM）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import agent_platform.config.settings as settings_module
from agent_platform.infrastructure.persistence.db import init_db, upsert_tenant
from agent_platform.interfaces.api.routes import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    settings_module._settings_instance = None
    db = str(tmp_path / "jobs_api.db")
    monkeypatch.setenv("RECAP_DB_PATH", db)
    monkeypatch.setenv("RECAP_RATE_LIMIT_RPM", "10000")
    monkeypatch.delenv("RECAP_API_KEY", raising=False)
    monkeypatch.delenv("RECAP_OTEL_ENABLED", raising=False)
    init_db(db)
    return TestClient(app)


def _post_job(client: TestClient, **headers: str):
    req = {"mode": "daily", "provider": "mock", "force_llm": False}
    return client.post("/v1/jobs", json=req, headers=headers or {})


def test_jobs_submit_then_get_done(client: TestClient) -> None:
    r = _post_job(client)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["idempotent_hit"] is False
    assert body["job_id"].startswith("job-")

    gid = client.get(f"/v1/jobs/{body['job_id']}")
    assert gid.status_code == 200
    job = gid.json()
    assert job["job_id"] == body["job_id"]
    assert job["status"] in ("queued", "running", "done", "failed")
    assert job["request"]["mode"] == "daily"

    # BackgroundTasks 在 TestClient 内于响应后执行，轮询直至终态
    for _ in range(50):
        st = client.get(f"/v1/jobs/{body['job_id']}").json()["status"]
        if st in ("done", "failed"):
            break
    assert st == "done", st
    final = client.get(f"/v1/jobs/{body['job_id']}").json()
    assert final["result"] is not None
    assert final["result"]["request_id"]
    assert final["error"] is None


def test_jobs_idempotency_returns_same_job(client: TestClient) -> None:
    h = {"X-Idempotency-Key": "idem-1"}
    r1 = _post_job(client, **h)
    r2 = _post_job(client, **h)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["job_id"] == r2.json()["job_id"]
    assert r2.json()["idempotent_hit"] is True


def test_jobs_list_filter_by_status(client: TestClient) -> None:
    r = _post_job(client)
    assert r.status_code == 200
    jid = r.json()["job_id"]
    for _ in range(50):
        if client.get(f"/v1/jobs/{jid}").json()["status"] == "done":
            break
    lst = client.get("/v1/jobs", params={"status": "done", "limit": 5})
    assert lst.status_code == 200
    ids = {it["job_id"] for it in lst.json()["items"]}
    assert jid in ids


def test_jobs_tenant_isolation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings_module._settings_instance = None
    db = str(tmp_path / "jobs_mt.db")
    monkeypatch.setenv("RECAP_DB_PATH", db)
    monkeypatch.setenv("RECAP_RATE_LIMIT_RPM", "10000")
    monkeypatch.delenv("RECAP_API_KEY", raising=False)
    init_db(db)

    import hashlib

    def h(k: str) -> str:
        return hashlib.sha256(k.encode()).hexdigest()

    upsert_tenant(
        db,
        tenant_id="ta",
        name="A",
        api_key_hash=h("ka"),
        role="user",
        status="active",
    )
    upsert_tenant(
        db,
        tenant_id="tb",
        name="B",
        api_key_hash=h("kb"),
        role="user",
        status="active",
    )

    from agent_platform.config.settings import Settings, get_settings
    from agent_platform.interfaces.api.app import create_app

    settings_module._settings_instance = None
    settings_one = Settings()
    app_mt = create_app()
    app_mt.dependency_overrides[get_settings] = lambda: settings_one

    c1 = TestClient(app_mt)
    r1 = c1.post(
        "/v1/jobs",
        json={"mode": "daily", "provider": "mock", "force_llm": False},
        headers={"X-API-Key": "ka"},
    )
    assert r1.status_code == 200
    job_a = r1.json()["job_id"]

    c2 = TestClient(app_mt)
    nf = c2.get(f"/v1/jobs/{job_a}", headers={"X-API-Key": "kb"})
    assert nf.status_code == 404

    app_mt.dependency_overrides.clear()
    settings_module._settings_instance = None
