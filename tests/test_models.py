"""Unit tests for Pydantic models."""
import pytest
from pydantic import ValidationError

from agent_platform.domain.models import (
    RecapDaily,
    RecapDailySection,
    RecapStrategy,
    Features,
    MarketSnapshot,
    GenerateRequest,
    FeedbackRequest,
    LlmTokens,
)


# ─── RecapDaily ───────────────────────────────────────────────────────────────

def _make_section(title: str = "指数") -> RecapDailySection:
    return RecapDailySection(
        title=title,
        core_conclusion="结论",
        bullets=["点1", "点2"],
    )


def test_recap_daily_valid():
    r = RecapDaily(
        mode="daily",
        date="2024-01-02",
        sections=[_make_section("A"), _make_section("B"), _make_section("C")],
    )
    assert r.mode == "daily"
    assert len(r.sections) == 3


def test_recap_daily_requires_3_sections():
    with pytest.raises(ValidationError):
        RecapDaily(
            mode="daily",
            date="2024-01-02",
            sections=[_make_section()],  # only 1
        )


def test_recap_daily_section_requires_2_bullets():
    with pytest.raises(ValidationError):
        RecapDailySection(title="X", core_conclusion="Y", bullets=["only one"])


# ─── RecapStrategy ────────────────────────────────────────────────────────────

def test_recap_strategy_valid():
    r = RecapStrategy(
        mode="strategy",
        date="2024-01-02",
        mainline_focus=["科技"],
        risk_warnings=["注意回调"],
        trading_logic=["逻辑1", "逻辑2"],
    )
    assert r.mode == "strategy"


def test_recap_strategy_requires_2_trading_logic():
    with pytest.raises(ValidationError):
        RecapStrategy(
            mode="strategy",
            date="2024-01-02",
            mainline_focus=["科技"],
            risk_warnings=["注意"],
            trading_logic=["只有一条"],
        )


# ─── Features ─────────────────────────────────────────────────────────────────

def test_features_defaults():
    f = Features()
    assert f.market_strength is None
    assert f.index_view == ""


def test_features_partial():
    f = Features(market_strength=0.7, index_view="强势")
    assert f.market_strength == 0.7


# ─── MarketSnapshot ───────────────────────────────────────────────────────────

def test_market_snapshot_defaults():
    s = MarketSnapshot(asof="2024-01-02T08:00:00+00:00", provider="mock", date="2024-01-02")
    assert s.is_trading_day is True
    assert s.a_share_indices == {}
    assert s.cross_market == {}


# ─── GenerateRequest ──────────────────────────────────────────────────────────

def test_generate_request_defaults():
    r = GenerateRequest()
    assert r.mode == "daily"
    assert r.provider == "live"
    assert r.force_llm is True


def test_generate_request_invalid_mode():
    with pytest.raises(ValidationError):
        GenerateRequest(mode="invalid")


# ─── FeedbackRequest ──────────────────────────────────────────────────────────

def test_feedback_rating_bounds():
    with pytest.raises(ValidationError):
        FeedbackRequest(request_id="x", rating=0)
    with pytest.raises(ValidationError):
        FeedbackRequest(request_id="x", rating=6)


def test_feedback_valid():
    f = FeedbackRequest(request_id="abc", rating=3)
    assert f.tags == []
    assert f.comment == ""


# ─── LlmTokens ────────────────────────────────────────────────────────────────

def test_llm_tokens_defaults():
    t = LlmTokens()
    assert t.input_tokens is None
    assert t.total_tokens is None


def test_llm_tokens_set():
    t = LlmTokens(input_tokens=100, output_tokens=50, total_tokens=150)
    assert t.total_tokens == 150
