"""生成与反馈 API 的输入护栏（防异常 payload、过长文本） + 输出脱敏。

输出脱敏（``coerce_recap_output``）走 ``policy.output_rules`` 的表驱动
``RuleSet``：词表 / 必含词 / 一致性三类规则全部从 ``policy/rules.yaml`` 加载，
运营 / 风控可单独迭代规则文件，无需触碰 Python 代码。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from stock_recap.domain.models import FeedbackRequest, GenerateRequest, Recap
from stock_recap.policy.output_rules import RuleSet, Violation, apply_rules, load_ruleset

logger = logging.getLogger("stock_recap.policy.guardrails")

_DEFAULT_RECAP_DISCLAIMER = (
    "本内容仅供参考，不构成投资建议。投资有风险，入市需谨慎。"
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_COMMENT = 8000
_MAX_TAGS = 32
_MAX_TAG_LEN = 64

_DEFAULT_RULESET: Optional[RuleSet] = None


class GuardrailError(ValueError):
    """护栏拒绝的请求。"""


def validate_generate_request(req: GenerateRequest) -> None:
    if req.date is not None:
        if not _DATE_RE.match(req.date):
            raise GuardrailError("date 必须为 YYYY-MM-DD")
    if req.model is not None and len(req.model) > 256:
        raise GuardrailError("model 表达过长")
    if req.provider and len(req.provider) > 64:
        raise GuardrailError("provider id 过长（最多 64 字符）")


def validate_feedback_request(req: FeedbackRequest) -> None:
    if req.comment and len(req.comment) > _MAX_COMMENT:
        raise GuardrailError(f"comment 长度不得超过 {_MAX_COMMENT}")
    if len(req.tags) > _MAX_TAGS:
        raise GuardrailError(f"tags 数量不得超过 {_MAX_TAGS}")
    for t in req.tags:
        if len(t) > _MAX_TAG_LEN:
            raise GuardrailError(f"单个 tag 长度不得超过 {_MAX_TAG_LEN}")


def clamp_llm_messages(messages: List[Dict[str, Any]], max_total_chars: int = 1_200_000) -> List[Dict[str, Any]]:
    """防止极端 payload 撑爆上下文：超长时截断最后一条 user 文本。"""
    total = sum(
        len(m.get("content") or "")
        for m in messages
        if isinstance(m.get("content"), str)
    )
    if total <= max_total_chars:
        return messages
    out = [dict(m) for m in messages]
    over = total - max_total_chars + 64
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") != "user":
            continue
        content = out[i].get("content")
        if not isinstance(content, str) or not content:
            continue
        keep = max(0, len(content) - over)
        out[i]["content"] = content[:keep] + ("\n…[truncated]" if keep < len(content) else "")
        break
    return out


def _get_default_ruleset() -> RuleSet:
    """进程级懒加载缓存 —— 避免每次 generate 都重读 yaml。"""
    global _DEFAULT_RULESET
    if _DEFAULT_RULESET is None:
        _DEFAULT_RULESET = load_ruleset()
    return _DEFAULT_RULESET


def reset_default_ruleset_cache() -> None:
    """供 SIGHUP / 测试用：清除内存缓存，下次取用会重新读 yaml。"""
    global _DEFAULT_RULESET
    _DEFAULT_RULESET = None


def coerce_recap_output(
    recap: Recap | None,
    ruleset: Optional[RuleSet] = None,
) -> Recap | None:
    """输出侧护栏：跑表驱动 ``RuleSet`` 做词表脱敏 + 必含词 + 一致性，并回填默认 disclaimer。

    - 任何脱敏命中只 warning 落日志（``violations`` 列表用 stable JSON），由调用方决定是否
      进一步透传给 critic 或落 audit；
    - ``ruleset`` 不传时走 ``policy/rules.yaml`` 默认；
    - 失败安全：若 RuleSet 应用过程中抛异常，仍按老逻辑兜底回填 disclaimer，不让护栏自身
      把响应弄崩。
    """
    if recap is None:
        return None
    rs = ruleset or _get_default_ruleset()
    try:
        out, violations = apply_rules(recap, rs)
    except Exception as e:
        logger.warning(
            json.dumps(
                {"event": "output_rules_apply_failed", "error": str(e)},
                ensure_ascii=False,
            )
        )
        out = recap
        violations = []

    if violations:
        logger.warning(
            json.dumps(
                {
                    "event": "recap_output_violations",
                    "count": len(violations),
                    "violations": [
                        {
                            "rule_id": v.rule_id,
                            "severity": v.severity,
                            "field_path": v.field_path,
                            "redacted": v.redacted,
                            "detail": v.detail,
                        }
                        for v in violations
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    if out is None:
        return None
    d = (getattr(out, "disclaimer", None) or "").strip()
    if d:
        return out
    return out.model_copy(update={"disclaimer": rs.disclaimer or _DEFAULT_RECAP_DISCLAIMER})
