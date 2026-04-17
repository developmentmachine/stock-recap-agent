"""生成与反馈 API 的输入护栏（防异常 payload、过长文本）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from stock_recap.domain.models import FeedbackRequest, GenerateRequest, Recap

_DEFAULT_RECAP_DISCLAIMER = (
    "本内容仅供参考，不构成投资建议。投资有风险，入市需谨慎。"
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_COMMENT = 8000
_MAX_TAGS = 32
_MAX_TAG_LEN = 64


class GuardrailError(ValueError):
    """护栏拒绝的请求。"""


def validate_generate_request(req: GenerateRequest) -> None:
    if req.date is not None:
        if not _DATE_RE.match(req.date):
            raise GuardrailError("date 必须为 YYYY-MM-DD")
    if req.model is not None and len(req.model) > 256:
        raise GuardrailError("model 表达过长")


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


def coerce_recap_output(recap: Recap | None) -> Recap | None:
    """输出侧护栏：免责声明为空时回填默认文案（与 schema 默认一致）。"""
    if recap is None:
        return None
    d = (getattr(recap, "disclaimer", None) or "").strip()
    if d:
        return recap
    return recap.model_copy(update={"disclaimer": _DEFAULT_RECAP_DISCLAIMER})
