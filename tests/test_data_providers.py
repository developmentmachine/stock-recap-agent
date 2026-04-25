"""DataProviderRegistry + collect_snapshot 路由（W5-4）。"""
from __future__ import annotations

import pytest

from agent_platform.domain.data_providers import DataProviderSpec
from agent_platform.domain.models import MarketSnapshot
from agent_platform.infrastructure.data.collector import (
    collect_snapshot,
    default_data_provider_registry,
    list_data_provider_ids,
    reset_default_data_provider_registry,
    register_data_provider,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_default_data_provider_registry()
    yield
    reset_default_data_provider_registry()


def test_builtin_provider_ids():
    ids = list_data_provider_ids()
    assert set(ids) >= {"mock", "live", "akshare"}


def test_collect_mock_via_registry():
    snap = collect_snapshot("mock", "2024-06-01", skip_trading_check=True)
    assert isinstance(snap, MarketSnapshot)
    assert snap.provider == "mock"
    assert snap.date == "2024-06-01"


def test_unknown_provider_runtime_error():
    with pytest.raises(RuntimeError, match="not registered"):
        collect_snapshot("no-such-provider-xyz", None, skip_trading_check=True)


def test_register_custom_provider_routes_collect():
    def _fixture_collect(d: str, asof: str, skip: bool) -> MarketSnapshot:
        return MarketSnapshot(
            asof=asof,
            provider="fixture",
            date=d,
            is_trading_day=True,
        )

    register_data_provider(
        DataProviderSpec(name="fixture", collect=_fixture_collect, display_name="test")
    )
    snap = collect_snapshot("fixture", "2024-01-15", skip_trading_check=True)
    assert snap.provider == "fixture"
    assert "fixture" in default_data_provider_registry().names()


def test_registry_name_case_insensitive():
    reg = default_data_provider_registry()
    assert reg.get("MOCK") is not None
    assert reg.require("Live").name == "live"
