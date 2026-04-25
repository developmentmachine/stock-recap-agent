"""推送幂等：同一 (request_id, channel) 不应触发二次推送。"""
from __future__ import annotations

from typing import List

import pytest

from agent_platform.application.side_effects import push as push_mod
from agent_platform.config.settings import Settings
from agent_platform.domain.models import RecapDaily, RecapDailySection
from agent_platform.infrastructure.persistence.db import get_push_log, init_db


@pytest.fixture
def file_db(tmp_path):
    path = str(tmp_path / "push.db")
    init_db(path)
    yield path


@pytest.fixture
def settings(file_db: str, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """启用推送（wxwork webhook 触发 channel='wxwork'）。

    Settings 字段都用了 alias → 必须从环境变量注入，构造器关键字会被忽略。
    """
    monkeypatch.setenv("RECAP_DB_PATH", file_db)
    monkeypatch.setenv("RECAP_WXWORK_WEBHOOK_URL", "https://example.com/webhook")
    monkeypatch.setenv("RECAP_PUSH_ENABLED", "true")
    return Settings()


def _stub_recap() -> RecapDaily:
    section = RecapDailySection(
        title="测试段落",
        core_conclusion="这是结论",
        bullets=["要点一", "要点二"],
    )
    return RecapDaily(
        mode="daily",
        date="2024-01-02",
        sections=[section, section, section],
    )


class _RecordingProvider:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: List[str] = []

    def push(self, recap) -> bool:  # type: ignore[no-untyped-def]
        self.calls.append("push")
        return self.ok


def test_push_records_log_and_skips_second_call(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    provider = _RecordingProvider(ok=True)
    monkeypatch.setattr(push_mod, "get_push_provider", lambda _s: provider)

    recap = _stub_recap()
    rid = "req-push-1"

    assert push_mod.push_recap(settings, recap, request_id=rid) is True
    assert provider.calls == ["push"]
    log = get_push_log(settings.db_path, request_id=rid, channel="wxwork")
    assert log is not None and log["status"] == "sent" and log["attempts"] == 1

    # 同一 request_id 第二次：必须直接命中幂等，不再触达 provider。
    assert push_mod.push_recap(settings, recap, request_id=rid) is True
    assert provider.calls == ["push"], "二次推送应被幂等账本拦截"


def test_push_failure_records_failed_and_can_retry(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    provider = _RecordingProvider(ok=False)
    monkeypatch.setattr(push_mod, "get_push_provider", lambda _s: provider)

    recap = _stub_recap()
    rid = "req-push-2"

    assert push_mod.push_recap(settings, recap, request_id=rid) is False
    log = get_push_log(settings.db_path, request_id=rid, channel="wxwork")
    assert log is not None and log["status"] == "failed" and log["attempts"] == 1

    # 失败状态不命中「sent/skipped」幂等分支 → 允许同 request_id 重试，
    # attempts 自增反映总尝试次数。
    provider.ok = True
    assert push_mod.push_recap(settings, recap, request_id=rid) is True
    log2 = get_push_log(settings.db_path, request_id=rid, channel="wxwork")
    assert log2 is not None and log2["status"] == "sent" and log2["attempts"] == 2


def test_push_without_request_id_is_non_idempotent(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """ad-hoc 调试场景下没有 request_id —— 行为退化为直接推送，每次都打。"""
    provider = _RecordingProvider(ok=True)
    monkeypatch.setattr(push_mod, "get_push_provider", lambda _s: provider)

    push_mod.push_recap(settings, _stub_recap(), request_id=None)
    push_mod.push_recap(settings, _stub_recap(), request_id=None)
    assert provider.calls == ["push", "push"]


def test_push_disabled_returns_false_without_log(
    monkeypatch: pytest.MonkeyPatch, file_db: str
) -> None:
    """没有 webhook → provider 为 None，立即返回 False，不写日志。"""
    monkeypatch.setenv("RECAP_DB_PATH", file_db)
    monkeypatch.delenv("RECAP_WXWORK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("RECAP_PUSH_ENABLED", "false")
    s = Settings()
    monkeypatch.setattr(push_mod, "get_push_provider", lambda _s: None)
    assert push_mod.push_recap(s, _stub_recap(), request_id="req-x") is False
    assert get_push_log(s.db_path, request_id="req-x", channel="noop") is None
