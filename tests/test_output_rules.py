"""表驱动输出护栏：词表 / 必含词 / 一致性 / 失败安全。"""
from __future__ import annotations

from pathlib import Path

import pytest

from stock_recap.domain.models import (
    RecapDaily,
    RecapDailySection,
    RecapStrategy,
)
from stock_recap.policy.guardrails import (
    coerce_recap_output,
    reset_default_ruleset_cache,
)
from stock_recap.policy.output_rules import (
    ConsistencyRule,
    ForbiddenPhraseRule,
    RequirePhraseRule,
    RuleSet,
    apply_rules,
    load_ruleset,
)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _section(title: str, *bullets: str) -> RecapDailySection:
    return RecapDailySection(title=title, core_conclusion="结论", bullets=list(bullets))


def _daily(*, date: str = "2024-01-02", sections=None, risks=None) -> RecapDaily:
    return RecapDaily(
        mode="daily",
        date=date,
        sections=sections
        or [
            _section("板块轮动", "热点向新能源切换", "北向净流入"),
            _section("资金面", "成交温和放大", "主力高低切"),
            _section("情绪面", "题材股活跃", "防御板块退潮"),
        ],
        risks=risks or ["美元走强施压", "地缘冲突反复"],
    )


# ─── 默认 yaml 装载 ─────────────────────────────────────────────────────────


def test_default_yaml_loads_with_expected_rules() -> None:
    rs = load_ruleset()
    rule_ids = {r.id for r in rs.forbidden_phrases}
    assert {"guarantee_return", "sure_win", "must_rise_or_fall", "recommend_buy"} <= rule_ids
    assert any(r.field == "disclaimer" for r in rs.require_phrases)


def test_load_yaml_falls_back_on_missing_file(tmp_path: Path) -> None:
    rs = load_ruleset(tmp_path / "nope.yaml")
    assert isinstance(rs, RuleSet)
    assert "仅供参考" in rs.disclaimer  # 内置兜底


def test_load_yaml_falls_back_on_corrupt_file(tmp_path: Path) -> None:
    corrupt = tmp_path / "bad.yaml"
    corrupt.write_text(":::not yaml:::", encoding="utf-8")
    rs = load_ruleset(corrupt)
    assert isinstance(rs, RuleSet)


# ─── 词表脱敏 ───────────────────────────────────────────────────────────────


def test_forbidden_phrase_redacts_in_bullets() -> None:
    recap = _daily(
        sections=[
            _section("强势板块", "保证收益的赛道", "走势确认"),
            _section("情绪面", "题材活跃", "稳赚不赔的策略不存在"),
            _section("资金面", "主力流入", "防御退潮"),
        ]
    )
    out, violations = apply_rules(recap)
    assert isinstance(out, RecapDaily)
    text_blob = " ".join(b for s in out.sections for b in s.bullets)
    assert "保证收益" not in text_blob
    assert "稳赚不赔" not in text_blob
    rule_ids = {v.rule_id for v in violations}
    assert "guarantee_return" in rule_ids and "sure_win" in rule_ids
    assert all(v.redacted for v in violations if v.rule_id in {"guarantee_return", "sure_win"})


def test_forbidden_regex_match_must_rise_or_fall() -> None:
    recap = _daily(
        sections=[
            _section("强势板块", "新能源必涨", "锂电板块跟随"),
            _section("情绪面", "活跃", "退潮"),
            _section("资金面", "净流入", "高低切"),
        ]
    )
    out, violations = apply_rules(recap)
    assert "必涨" not in out.sections[0].bullets[0]  # type: ignore[union-attr]
    assert any(v.rule_id == "must_rise_or_fall" for v in violations)


def test_forbidden_phrase_redacts_in_strategy_lists() -> None:
    s = RecapStrategy(
        mode="strategy",
        date="2024-01-02",
        mainline_focus=["建议买入新能源", "关注半导体"],
        risk_warnings=["美元走强"],
        trading_logic=["逢低吸纳", "止损"],
    )
    out, violations = apply_rules(s)
    assert isinstance(out, RecapStrategy)
    assert "建议买入" not in out.mainline_focus[0]
    assert any(v.rule_id == "recommend_buy" for v in violations)


# ─── 一致性检查 ─────────────────────────────────────────────────────────────


def test_consistency_section_title_date_mismatch_reports_violation() -> None:
    recap = _daily(
        date="2024-01-02",
        sections=[
            _section("2024-01-03 板块复盘", "热点切换", "北向净流入"),
            _section("情绪面", "题材活跃", "防御退潮"),
            _section("资金面", "成交放大", "高低切"),
        ],
    )
    out, violations = apply_rules(recap)
    assert any(v.rule_id == "section_title_date_match" for v in violations)
    # 一致性规则只报告，不改写文本。
    assert out is not None and out.sections[0].title.startswith("2024-01-03")


def test_consistency_chinese_date_normalized_and_matches() -> None:
    """`2024年1月2日` 应被归一化为 `2024-01-02` —— 与 recap.date 一致，无 violation。"""
    recap = _daily(
        date="2024-01-02",
        sections=[
            _section("2024年1月2日板块复盘", "热点切换", "北向净流入"),
            _section("情绪面", "题材活跃", "防御退潮"),
            _section("资金面", "成交放大", "高低切"),
        ],
    )
    _, violations = apply_rules(recap)
    assert not any(v.rule_id == "section_title_date_match" for v in violations)


# ─── coerce_recap_output 集成 ──────────────────────────────────────────────


def test_coerce_uses_default_ruleset_and_redacts() -> None:
    reset_default_ruleset_cache()  # 强制重读 yaml
    recap = _daily(
        sections=[
            _section("强势板块", "稳赚不赔的方向", "板块轮动"),
            _section("情绪面", "活跃", "防御退潮"),
            _section("资金面", "净流入", "高低切"),
        ]
    )
    out = coerce_recap_output(recap)
    assert out is not None
    text_blob = " ".join(b for s in out.sections for b in s.bullets)
    assert "稳赚不赔" not in text_blob


def test_custom_ruleset_via_yaml_overrides_defaults(tmp_path: Path) -> None:
    """运营把规则文件改成自定义词表 → coerce_recap_output 立即生效。"""
    custom = tmp_path / "rules.yaml"
    custom.write_text(
        """
version: 1
disclaimer: "本内容仅供参考，不构成投资建议。"
forbidden_phrases:
  - id: ban_alpha
    pattern: alpha
    action: redact
    replacement: "[已脱敏]"
""",
        encoding="utf-8",
    )
    rs = load_ruleset(custom)
    recap = _daily(
        sections=[
            _section("alpha 强势", "alpha 板块走强", "题材活跃"),
            _section("beta 防御", "beta 退潮", "高低切"),
            _section("资金面", "净流入", "主力换手"),
        ]
    )
    out = coerce_recap_output(recap, ruleset=rs)
    assert out is not None
    text_blob = " ".join(s.title + " " + " ".join(s.bullets) for s in out.sections)
    assert "alpha" not in text_blob


# ─── 失败安全 ───────────────────────────────────────────────────────────────


def test_coerce_falls_back_on_apply_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """apply_rules 抛异常时 coerce 仍要能产出 disclaimer 兜底。"""
    from stock_recap.policy import guardrails as gr

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated rule crash")

    monkeypatch.setattr(gr, "apply_rules", boom)
    r = RecapStrategy(
        mode="strategy",
        date="2024-01-02",
        mainline_focus=["a"],
        risk_warnings=["b"],
        trading_logic=["1", "2"],
        disclaimer="",
    )
    out = coerce_recap_output(r)
    assert out is not None
    assert "仅供参考" in (out.disclaimer or "")


# ─── 自定义编程构造 RuleSet ─────────────────────────────────────────────────


def test_programmatic_ruleset_works_without_yaml() -> None:
    rs = RuleSet(
        forbidden_phrases=(
            ForbiddenPhraseRule(id="ban_a", pattern="A股", action="redact", replacement="X"),
        ),
        require_phrases=(
            RequirePhraseRule(
                id="dis_must",
                field="disclaimer",
                must_contain_any=("仅供参考",),
                fix=True,
            ),
        ),
        consistency_checks=(
            ConsistencyRule(id="sec_date", kind="date_in_section_titles"),
        ),
    )
    recap = _daily(
        sections=[
            _section("A股板块轮动", "热点切换", "北向净流入"),
            _section("情绪面", "题材活跃", "防御退潮"),
            _section("资金面", "成交放大", "高低切"),
        ]
    )
    out, violations = apply_rules(recap, rs)
    assert "A股" not in out.sections[0].title  # type: ignore[union-attr]
    assert any(v.rule_id == "ban_a" for v in violations)
