"""表驱动输出护栏：词表 / 必含词 / 一致性 / 白名单。

设计原则：
- **规则与代码分离**：所有可调项在 ``policy/rules.yaml``，运营 / 风控可不读 Python 直接迭代；
- **可观测**：违规时返回 ``Violation`` 列表，调用方决定 log / 落库 / 让 critic 重入；
- **可扩展**：``ForbiddenPhraseRule`` / ``RequirePhraseRule`` / ``ConsistencyRule`` 三套
  正交规则，新规则只加 dataclass + applier 函数，无须改主流程；
- **失败安全**：YAML 加载失败 → 退回到内置最小规则集（含 disclaimer 默认），
  绝不让护栏自身崩溃影响主响应。

集成点（见 ``policy/guardrails.coerce_recap_output``）：
- `_phase_act` 拿到 LLM 输出后立即跑 `apply_rules`，把违例写入日志，并把脱敏后
  的 recap 当作最终结果返回；
- 后续 Wave 4 会把 violations 写到 ``audit`` 表，作为 prompt 进化的反向信号。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import yaml

from agent_platform.domain.models import Recap, RecapDaily, RecapStrategy

logger = logging.getLogger("agent_platform.policy.output_rules")

Severity = Literal["low", "medium", "high"]
Action = Literal["redact", "warn"]


# ─── 数据结构 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ForbiddenPhraseRule:
    id: str
    pattern: str
    regex: bool = False
    action: Action = "redact"
    replacement: str = "[已脱敏]"
    severity: Severity = "medium"


@dataclass(frozen=True)
class RequirePhraseRule:
    id: str
    field: str  # 目前支持 "disclaimer"
    must_contain_any: Tuple[str, ...] = ()
    fix: bool = False  # 缺失时是否回填默认值
    severity: Severity = "medium"


@dataclass(frozen=True)
class ConsistencyRule:
    id: str
    kind: str  # 目前内置: date_in_section_titles
    severity: Severity = "medium"


@dataclass(frozen=True)
class RuleSet:
    version: int = 1
    disclaimer: str = "本内容仅供参考，不构成投资建议。投资有风险，入市需谨慎。"
    forbidden_phrases: Tuple[ForbiddenPhraseRule, ...] = ()
    require_phrases: Tuple[RequirePhraseRule, ...] = ()
    consistency_checks: Tuple[ConsistencyRule, ...] = ()


@dataclass
class Violation:
    rule_id: str
    severity: Severity
    field_path: str  # e.g. "sections[0].bullets[2]" / "disclaimer"
    detail: str
    redacted: bool = False


# ─── 加载 ───────────────────────────────────────────────────────────────────


_DEFAULT_RULES_PATH = Path(__file__).resolve().parent / "rules.yaml"


def _coerce_forbidden(d: Dict[str, Any]) -> ForbiddenPhraseRule:
    return ForbiddenPhraseRule(
        id=str(d.get("id") or d.get("pattern", "")),
        pattern=str(d["pattern"]),
        regex=bool(d.get("regex", False)),
        action=str(d.get("action", "redact")),  # type: ignore[arg-type]
        replacement=str(d.get("replacement", "[已脱敏]")),
        severity=str(d.get("severity", "medium")),  # type: ignore[arg-type]
    )


def _coerce_require(d: Dict[str, Any]) -> RequirePhraseRule:
    return RequirePhraseRule(
        id=str(d.get("id") or d.get("field", "")),
        field=str(d["field"]),
        must_contain_any=tuple(str(x) for x in (d.get("must_contain_any") or [])),
        fix=bool(d.get("fix", False)),
        severity=str(d.get("severity", "medium")),  # type: ignore[arg-type]
    )


def _coerce_consistency(d: Dict[str, Any]) -> ConsistencyRule:
    return ConsistencyRule(
        id=str(d.get("id") or d.get("kind", "")),
        kind=str(d["kind"]),
        severity=str(d.get("severity", "medium")),  # type: ignore[arg-type]
    )


def load_ruleset(path: Optional[Union[str, Path]] = None) -> RuleSet:
    """加载 yaml；缺失文件 / 解析失败 / schema 不全 → 走默认最小规则。

    保留这种「失败安全」语义是因为：护栏崩溃绝不能影响业务主响应。
    """
    target = Path(path) if path else _DEFAULT_RULES_PATH
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        logger.warning("policy rules file not found: %s, using built-in minimal", target)
        return RuleSet()
    except Exception as e:
        logger.warning("policy rules load failed (%s): %s — fallback to defaults", target, e)
        return RuleSet()

    try:
        return RuleSet(
            version=int(raw.get("version", 1)),
            disclaimer=str(
                raw.get("disclaimer")
                or "本内容仅供参考，不构成投资建议。投资有风险，入市需谨慎。"
            ),
            forbidden_phrases=tuple(
                _coerce_forbidden(d) for d in (raw.get("forbidden_phrases") or [])
            ),
            require_phrases=tuple(
                _coerce_require(d) for d in (raw.get("require_phrases") or [])
            ),
            consistency_checks=tuple(
                _coerce_consistency(d) for d in (raw.get("consistency_checks") or [])
            ),
        )
    except Exception as e:
        logger.warning("policy rules schema invalid (%s): %s — fallback to defaults", target, e)
        return RuleSet()


# ─── 应用规则 ───────────────────────────────────────────────────────────────


_DATE_PATTERN_IN_TEXT = re.compile(
    r"(\d{4}-\d{2}-\d{2}|\d{4}年\d{1,2}月\d{1,2}日)"
)


def _normalize_date(token: str) -> str:
    """把 ``2024年1月2日`` 归一化为 ``2024-01-02``，便于一致性比较。"""
    if "年" in token and "月" in token and "日" in token:
        try:
            y, rest = token.split("年", 1)
            m, rest = rest.split("月", 1)
            d = rest.rstrip("日")
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        except Exception:
            return token
    return token


def _compile_forbidden(rule: ForbiddenPhraseRule) -> "re.Pattern[str]":
    flags = re.IGNORECASE
    if rule.regex:
        return re.compile(rule.pattern, flags)
    return re.compile(re.escape(rule.pattern), flags)


def _apply_forbidden_to_text(
    text: str,
    rule: ForbiddenPhraseRule,
    field_path: str,
    violations: List[Violation],
) -> str:
    pattern = _compile_forbidden(rule)
    if not pattern.search(text):
        return text
    if rule.action == "warn":
        violations.append(
            Violation(
                rule_id=rule.id,
                severity=rule.severity,
                field_path=field_path,
                detail=f"forbidden phrase matched: {rule.pattern}",
                redacted=False,
            )
        )
        return text
    redacted = pattern.sub(rule.replacement, text)
    violations.append(
        Violation(
            rule_id=rule.id,
            severity=rule.severity,
            field_path=field_path,
            detail=f"redacted by '{rule.id}' ({rule.pattern})",
            redacted=True,
        )
    )
    return redacted


def _walk_strings(recap: Recap):
    """yield (field_path, text) for 所有字符串字段（含 list[str]）。"""
    if isinstance(recap, RecapDaily):
        yield "closing_summary", recap.closing_summary
        yield "disclaimer", recap.disclaimer
        for i, s in enumerate(recap.sections):
            yield f"sections[{i}].title", s.title
            yield f"sections[{i}].core_conclusion", s.core_conclusion
            for j, b in enumerate(s.bullets):
                yield f"sections[{i}].bullets[{j}]", b
        for i, r in enumerate(recap.risks):
            yield f"risks[{i}]", r
    elif isinstance(recap, RecapStrategy):
        yield "disclaimer", recap.disclaimer
        for i, t in enumerate(recap.mainline_focus):
            yield f"mainline_focus[{i}]", t
        for i, t in enumerate(recap.risk_warnings):
            yield f"risk_warnings[{i}]", t
        for i, t in enumerate(recap.trading_logic):
            yield f"trading_logic[{i}]", t


_SECTION_PATH = re.compile(r"^sections\[(\d+)\]\.(\w+)(?:\[(\d+)\])?$")
_LIST_PATH = re.compile(r"^(\w+)\[(\d+)\]$")


def _redact_text(
    text: str,
    rules: Tuple[ForbiddenPhraseRule, ...],
    field_path: str,
    violations: List[Violation],
) -> str:
    out = text
    for rule in rules:
        out = _apply_forbidden_to_text(out, rule, field_path, violations)
    return out


def _apply_forbidden(
    recap: Recap,
    rules: Tuple[ForbiddenPhraseRule, ...],
    violations: List[Violation],
) -> Recap:
    """对所有字符串字段批量过 forbidden 规则；通过 ``model_dump → 修改 → model_validate`` 重建。"""
    if not rules:
        return recap

    payload = recap.model_dump()
    changed = False

    for path, text in _walk_strings(recap):
        new_text = _redact_text(text, rules, path, violations)
        if new_text == text:
            continue
        changed = True
        if path in payload and isinstance(payload[path], str):
            payload[path] = new_text
            continue
        m = _SECTION_PATH.match(path)
        if m:
            i, sub, j = int(m.group(1)), m.group(2), m.group(3)
            if j is None:
                payload["sections"][i][sub] = new_text
            else:
                payload["sections"][i][sub][int(j)] = new_text
            continue
        lm = _LIST_PATH.match(path)
        if lm:
            payload[lm.group(1)][int(lm.group(2))] = new_text

    if not changed:
        return recap
    return type(recap).model_validate(payload)


def _apply_require(
    recap: Recap,
    rules: Tuple[RequirePhraseRule, ...],
    default_disclaimer: str,
    violations: List[Violation],
) -> Recap:
    if not rules:
        return recap
    updates: Dict[str, str] = {}
    for rule in rules:
        if rule.field != "disclaimer":
            continue  # 当前只支持 disclaimer；新增字段时按需扩展
        current = (getattr(recap, "disclaimer", None) or "").strip()
        if not rule.must_contain_any:
            continue
        if any(token in current for token in rule.must_contain_any):
            continue
        violations.append(
            Violation(
                rule_id=rule.id,
                severity=rule.severity,
                field_path="disclaimer",
                detail=f"missing any of {list(rule.must_contain_any)}",
                redacted=rule.fix,
            )
        )
        if rule.fix:
            updates["disclaimer"] = default_disclaimer
    if updates:
        return recap.model_copy(update=updates)
    return recap


def _apply_consistency(
    recap: Recap,
    rules: Tuple[ConsistencyRule, ...],
    violations: List[Violation],
) -> None:
    """一致性检查仅 **报告** 不改写（避免改坏语义）；后续 Wave 4 由 critic 重入处理。"""
    if not rules:
        return
    for rule in rules:
        if rule.kind == "date_in_section_titles" and isinstance(recap, RecapDaily):
            for i, s in enumerate(recap.sections):
                m = _DATE_PATTERN_IN_TEXT.search(s.title)
                if not m:
                    continue
                if _normalize_date(m.group(1)) != recap.date:
                    violations.append(
                        Violation(
                            rule_id=rule.id,
                            severity=rule.severity,
                            field_path=f"sections[{i}].title",
                            detail=(
                                f"section title date '{m.group(1)}' "
                                f"!= recap.date '{recap.date}'"
                            ),
                            redacted=False,
                        )
                    )


def apply_rules(
    recap: Optional[Recap],
    ruleset: Optional[RuleSet] = None,
) -> Tuple[Optional[Recap], List[Violation]]:
    """主入口：返回 ``(脱敏后 recap, violations)``；``recap is None`` 时直接透传。"""
    if recap is None:
        return None, []
    rs = ruleset or load_ruleset()
    violations: List[Violation] = []

    out = _apply_forbidden(recap, rs.forbidden_phrases, violations)
    out = _apply_require(out, rs.require_phrases, rs.disclaimer, violations)
    _apply_consistency(out, rs.consistency_checks, violations)
    return out, violations


__all__ = [
    "Action",
    "ConsistencyRule",
    "ForbiddenPhraseRule",
    "RequirePhraseRule",
    "RuleSet",
    "Severity",
    "Violation",
    "apply_rules",
    "load_ruleset",
]
