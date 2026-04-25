"""LLM 输出解析 + schema 校验（所有 provider 共享）。"""
from __future__ import annotations

import json
import logging
from typing import Any

from agent_platform.domain.models import (
    LlmError,
    LlmParseError,
    LlmSchemaError,
    Mode,
    Recap,
    RecapDaily,
    RecapStrategy,
)
from agent_platform.domain.registries import ModeRegistry, default_mode_registry

logger = logging.getLogger("agent_platform.infrastructure.llm.parse")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_json_from_text(text: str) -> Any:
    """尽力从自由文本/流式输出中抽出 JSON payload。

    优先级：
    1) Cursor stream-json：从末尾向前扫描，尝试解析每行 JSON 并从中取 Recap；
    2) 去 ``markdown`` 代码块标记后直接解析；
    3) 退化：取第一个 ``{``/``[`` 与最后一个 ``}``/``]`` 之间的子串解析。
    """
    text = text.strip()

    if "\n" in text:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for ln in reversed(lines[-200:]):
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if isinstance(obj, dict):
                for key in ("output", "content", "text"):
                    val = obj.get(key)
                    if isinstance(val, str) and ("{" in val or "[" in val):
                        try:
                            return json.loads(val.strip())
                        except Exception:
                            pass
                if "mode" in obj and ("sections" in obj or "mainline_focus" in obj):
                    return obj

    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json\n", "", 1).strip()

    try:
        return json.loads(text)
    except Exception:
        start = min(
            [i for i in [text.find("{"), text.find("[")] if i != -1] or [0]
        )
        end = max(text.rfind("}"), text.rfind("]"))
        if end > start:
            return json.loads(text[start : end + 1])
        raise


def parse_and_validate(
    content: str,
    mode: Mode,
    mode_registry: ModeRegistry | None = None,
) -> Recap:
    """将模型原始文本解析为 ``Recap``。

    解析失败 → ``LlmParseError``；schema 校验失败 → ``LlmSchemaError``。
    二者都属于 ``LlmBusinessError``：tenacity 不再盲重试，Critic 节点会用结构化
    反馈再调一次。

    ``mode_registry`` 不传时走进程默认注册表，新增 mode 时只在注册表里加 spec
    即可，本函数无需改动。
    """
    try:
        payload = parse_json_from_text(content)
    except Exception as e:
        logger.warning(
            _stable_json({"event": "json_parse_failed", "error": str(e), "raw": content[:500]})
        )
        raise LlmParseError("LLM 输出非 JSON/不可解析") from e
    reg = mode_registry or default_mode_registry()
    spec = reg.get(mode)
    if spec is None:
        # 未注册 mode 视为 schema 错（business error），上层 critic 不会重入未知 mode。
        raise LlmSchemaError(f"unknown mode '{mode}', not registered in ModeRegistry")
    try:
        return spec.recap_class.model_validate(payload)  # type: ignore[no-any-return]
    except Exception as e:
        logger.warning(_stable_json({"event": "schema_validate_failed", "error": str(e)}))
        raise LlmSchemaError(f"LLM 输出未通过 schema 校验: {e}") from e
