import pytest

from agent_platform.domain.models import (
    FeedbackRequest,
    GenerateRequest,
    RecapDaily,
    RecapDailySection,
    RecapStrategy,
)
from agent_platform.policy.guardrails import (
    GuardrailError,
    coerce_recap_output,
    validate_feedback_request,
    validate_generate_request,
)


def test_validate_generate_date_ok():
    validate_generate_request(GenerateRequest(date="2024-01-02"))


def test_validate_generate_date_bad():
    with pytest.raises(GuardrailError):
        validate_generate_request(GenerateRequest(date="24-01-02"))


def test_validate_feedback_comment_too_long():
    with pytest.raises(GuardrailError):
        validate_feedback_request(
            FeedbackRequest(request_id="x", rating=3, comment="x" * 9000)
        )


def test_coerce_recap_output_none():
    assert coerce_recap_output(None) is None


def test_coerce_recap_output_fills_empty_disclaimer_daily():
    r = RecapDaily(
        mode="daily",
        date="2024-01-02",
        sections=[
            RecapDailySection(title="A", core_conclusion="c", bullets=["【复盘基准日：2024年01月02日 星期二】", "x"]),
            RecapDailySection(title="B", core_conclusion="c", bullets=["【复盘基准日：2024年01月02日 星期二】", "y"]),
            RecapDailySection(title="C", core_conclusion="c", bullets=["【复盘基准日：2024年01月02日 星期二】", "z"]),
        ],
        disclaimer="",
    )
    out = coerce_recap_output(r)
    assert out is not None
    assert "投资有风险" in (out.disclaimer or "")


def test_coerce_recap_output_keeps_compliant_disclaimer():
    """已包含必含词（"仅供参考" / "不构成投资建议"）的自定义 disclaimer 不被改写。"""
    r = RecapStrategy(
        mode="strategy",
        date="2024-01-02",
        mainline_focus=["a"],
        risk_warnings=["b"],
        trading_logic=["1", "2"],
        disclaimer="本文仅供参考，请独立判断。",
    )
    out = coerce_recap_output(r)
    assert out is not None
    assert out.disclaimer == "本文仅供参考，请独立判断。"


def test_coerce_recap_output_overrides_noncompliant_disclaimer():
    """不含必含词的自定义 disclaimer 被 require_phrases(fix=true) 覆盖回默认。"""
    r = RecapStrategy(
        mode="strategy",
        date="2024-01-02",
        mainline_focus=["a"],
        risk_warnings=["b"],
        trading_logic=["1", "2"],
        disclaimer="自定义免责",
    )
    out = coerce_recap_output(r)
    assert out is not None
    assert "仅供参考" in out.disclaimer or "不构成投资建议" in out.disclaimer
