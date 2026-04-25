"""prompt_version 的跨进程事实源 + 本地 TTL 缓存语义。"""
import pytest

from agent_platform.application.memory import manager as mgr
from agent_platform.infrastructure.llm.prompts import PROMPT_BASE_VERSION
from agent_platform.infrastructure.persistence.db import (
    get_active_prompt_version,
    init_db,
    set_active_prompt_version,
)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "prompt.db")
    init_db(path)
    mgr._invalidate_prompt_version_cache()
    yield path
    mgr._invalidate_prompt_version_cache()


def test_get_prompt_version_initializes_prompt_state(db_path):
    assert get_active_prompt_version(db_path) is None
    v = mgr.get_prompt_version(db_path)
    assert v == f"{PROMPT_BASE_VERSION}.v1"
    # prompt_state 已被回填
    assert get_active_prompt_version(db_path) == v


def test_get_prompt_version_respects_prompt_state(db_path):
    set_active_prompt_version(db_path, "base.v9", updated_at="2024-01-02T00:00:00+00:00")
    mgr._invalidate_prompt_version_cache()
    assert mgr.get_prompt_version(db_path) == "base.v9"


def test_set_prompt_version_persists_and_invalidates_cache(db_path):
    mgr.get_prompt_version(db_path)  # 触发缓存
    mgr._set_prompt_version(db_path, "base.v42")
    assert get_active_prompt_version(db_path) == "base.v42"
    # 不 invalidate 也能马上读到，因为 _set_prompt_version 已写入缓存
    assert mgr.get_prompt_version(db_path) == "base.v42"


def test_cache_picks_up_external_change_after_ttl(db_path, monkeypatch):
    first = mgr.get_prompt_version(db_path)
    # 模拟另一个 worker 直接改 DB
    set_active_prompt_version(db_path, f"{PROMPT_BASE_VERSION}.v99", updated_at="2024-01-02T00:00:01+00:00")
    # 未过 TTL：仍返回旧值
    assert mgr.get_prompt_version(db_path) == first
    # 强制让缓存过期（mock monotonic）
    real_monotonic = mgr.time.monotonic
    bumped = real_monotonic() + mgr._PROMPT_VERSION_CACHE_TTL_S + 1
    monkeypatch.setattr(mgr.time, "monotonic", lambda: bumped)
    assert mgr.get_prompt_version(db_path) == f"{PROMPT_BASE_VERSION}.v99"
