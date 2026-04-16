"""LLM 后端调用层。

支持：
- OpenAI（Structured Outputs 优先，降级 json_object 模式）
- Ollama（本地模型）
- Cursor Agent（通过 subprocess）

所有后端统一返回 (Recap, LlmTokens)，调用方无需关心后端差异。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from stock_recap.models import (
    Features,
    LlmBackend,
    LlmError,
    LlmTokens,
    MarketSnapshot,
    Mode,
    Recap,
    RecapDaily,
    RecapStrategy,
)
from stock_recap.settings import Settings

logger = logging.getLogger("stock_recap.llm.backends")


# ─── 工具 ──────────────────────────────────────────────────────────────────────

def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _model_prefix_to_backend(prefix: str) -> Optional[LlmBackend]:
    p = prefix.strip().lower()
    if p in {"openai"}:
        return "openai"
    if p in {"ollama"}:
        return "ollama"
    if p in {"cursor", "cursor-agent", "agent"}:
        return "cursor-agent"
    return None


def _interpret_model_spec(model_spec: str) -> Tuple[Optional[LlmBackend], Optional[str]]:
    """统一模型表达：openai:<m> / ollama:<m> / cursor-agent / local:ollama:<m>"""
    s = model_spec.strip()
    if not s:
        return None, None
    parts = s.split(":")
    if len(parts) == 1:
        return None, s

    prefix = parts[0].lower()
    if prefix == "local":
        if len(parts) == 2:
            b = _model_prefix_to_backend(parts[1])
            return b, None
        b = _model_prefix_to_backend(parts[1])
        if b == "cursor-agent":
            return b, None
        if b in {"openai", "ollama"}:
            return b, ":".join(parts[2:]) if len(parts) > 2 else None
        return None, None

    b = _model_prefix_to_backend(prefix)
    if b == "cursor-agent":
        return b, None
    if b in {"openai", "ollama"}:
        return b, ":".join(parts[1:]) if len(parts) > 1 else None
    return None, s


def llm_backend_effective(model_spec: Optional[str]) -> LlmBackend:
    if model_spec:
        b, _ = _interpret_model_spec(model_spec)
        if b:
            return b
    return "openai"


def model_effective(settings: Settings, model_spec: Optional[str]) -> str:
    if model_spec:
        _, m = _interpret_model_spec(model_spec)
        if m:
            return m
    return settings.model


# ─── JSON 解析（容错） ─────────────────────────────────────────────────────────

def parse_json_from_text(text: str) -> Any:
    text = text.strip()

    # Cursor agent stream-json：从多行中提取最终 JSON 对象
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

    # 去除 markdown 代码块
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


# ─── OpenAI 后端 ────────────────────────────────────────────────────────────────

def _call_openai(
    settings: Settings,
    model: str,
    messages: List[Dict[str, str]],
    mode: Mode,
) -> Tuple[Recap, LlmTokens]:
    from openai import OpenAI

    if not settings.openai_api_key:
        raise LlmError("缺少 OPENAI_API_KEY")
    client = OpenAI(api_key=settings.openai_api_key)

    recap_cls = RecapDaily if mode == "daily" else RecapStrategy
    tokens = LlmTokens()

    # 优先：Structured Outputs（beta.chat.completions.parse）
    try:
        resp = client.beta.chat.completions.parse(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            response_format=recap_cls,  # type: ignore[arg-type]
            temperature=settings.temperature,
            timeout=settings.timeout_s,
        )
        parsed = resp.choices[0].message.parsed
        if parsed is not None:
            usage = getattr(resp, "usage", None)
            if usage:
                tokens.input_tokens = getattr(usage, "prompt_tokens", None)
                tokens.output_tokens = getattr(usage, "completion_tokens", None)
                tokens.total_tokens = getattr(usage, "total_tokens", None)
            return parsed, tokens
    except Exception as e:
        logger.warning(
            _stable_json({"event": "structured_outputs_failed", "error": str(e), "fallback": "json_object"})
        )

    # 降级：json_object 模式 + pydantic 校验
    try:
        resp2 = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            response_format={"type": "json_object"},
            temperature=settings.temperature,
            timeout=settings.timeout_s,
        )
    except Exception as e:
        logger.warning(_stable_json({"event": "openai_call_failed", "error": str(e)}))
        raise LlmError(str(e))

    content = resp2.choices[0].message.content or ""
    usage = getattr(resp2, "usage", None)
    if usage:
        tokens.input_tokens = getattr(usage, "prompt_tokens", None)
        tokens.output_tokens = getattr(usage, "completion_tokens", None)
        tokens.total_tokens = getattr(usage, "total_tokens", None)

    payload = _parse_and_validate(content, mode)
    return payload, tokens


# ─── Ollama 后端 ────────────────────────────────────────────────────────────────

def _call_ollama(
    settings: Settings,
    model: str,
    messages: List[Dict[str, str]],
    mode: Mode,
) -> Tuple[Recap, LlmTokens]:
    url = settings.ollama_base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": settings.temperature},
    }
    try:
        with httpx.Client(timeout=settings.timeout_s) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning(_stable_json({"event": "ollama_call_failed", "error": str(e)}))
        raise LlmError(str(e))

    content = (data.get("message") or {}).get("content") or ""
    tokens = LlmTokens(
        input_tokens=data.get("prompt_eval_count"),
        output_tokens=data.get("eval_count"),
        total_tokens=(
            (data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0)
            if data.get("prompt_eval_count") is not None and data.get("eval_count") is not None
            else None
        ),
    )
    recap = _parse_and_validate(content, mode)
    return recap, tokens


# ─── Cursor Agent 后端 ─────────────────────────────────────────────────────────

def _call_cursor_agent(
    settings: Settings,
    messages: List[Dict[str, str]],
    mode: Mode,
) -> Tuple[Recap, LlmTokens]:
    base_cmd = settings.cursor_agent_cmd.strip().split()
    if not base_cmd:
        raise LlmError("cursor-agent 命令为空，请设置 RECAP_CURSOR_AGENT_CMD")

    prompt = _stable_json({"messages": messages})
    cmd = (
        base_cmd
        + ["--print", "--output-format", "stream-json", "--stream-partial-output",
           "--trust", "--force", "--workspace", os.getcwd()]
        + [prompt]
    )

    t0 = time.time()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
    except Exception as e:
        raise LlmError(f"cursor-agent 启动失败: {e}")

    stdout_lines: List[str] = []
    final_result_text: Optional[str] = None
    assistant_text_parts: List[str] = []
    last_log = 0.0

    while True:
        now = time.time()
        if now - last_log >= 5:
            last_log = now
            logger.info(_stable_json({"event": "cursor_running", "elapsed_s": int(now - t0)}))

        if proc.poll() is not None:
            break

        if now - t0 > settings.cursor_timeout_s:
            try:
                proc.kill()
            except Exception:
                pass
            raise LlmError(f"cursor-agent 超时（>{settings.cursor_timeout_s}s）")

        got = False
        if proc.stdout:
            line = proc.stdout.readline()
            if line:
                got = True
                stdout_lines.append(line)
                try:
                    evt = json.loads(line)
                    if isinstance(evt, dict):
                        if evt.get("type") == "result" and isinstance(evt.get("result"), str):
                            final_result_text = evt["result"]
                        if evt.get("type") == "assistant":
                            msg = evt.get("message")
                            if isinstance(msg, dict):
                                for c in (msg.get("content") or []):
                                    if isinstance(c, dict) and c.get("type") == "text":
                                        assistant_text_parts.append(c.get("text", ""))
                except Exception:
                    pass
        if not got:
            time.sleep(0.2)

    rc = proc.returncode or 0
    if rc != 0:
        err_tail = "".join(stdout_lines).strip()[-800:]
        raise LlmError(f"cursor-agent 失败(code={rc}): {err_tail}")

    raw = final_result_text or "".join(assistant_text_parts) or "".join(stdout_lines)
    if not raw.strip():
        raise LlmError("cursor-agent 无输出")

    recap = _parse_and_validate(raw.strip(), mode)
    return recap, LlmTokens()


# ─── 解析 + 校验 ───────────────────────────────────────────────────────────────

def _parse_and_validate(content: str, mode: Mode) -> Recap:
    try:
        payload = parse_json_from_text(content)
    except Exception as e:
        logger.warning(_stable_json({"event": "json_parse_failed", "error": str(e), "raw": content[:500]}))
        raise LlmError("LLM 输出非 JSON/不可解析")
    try:
        if mode == "daily":
            return RecapDaily.model_validate(payload)
        else:
            return RecapStrategy.model_validate(payload)
    except Exception as e:
        logger.warning(_stable_json({"event": "schema_validate_failed", "error": str(e)}))
        raise LlmError("LLM 输出未通过 schema 校验")


# ─── 统一入口（带重试） ────────────────────────────────────────────────────────

@retry(
    retry=retry_if_exception_type(LlmError),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=12),
)
def call_llm(
    settings: Settings,
    mode: Mode,
    messages: List[Dict[str, str]],
    model_spec: Optional[str] = None,
) -> Tuple[Recap, LlmTokens]:
    backend = llm_backend_effective(model_spec)
    model = model_effective(settings, model_spec)

    logger.info(
        _stable_json({"event": "llm_call", "backend": backend, "model": model, "mode": mode})
    )

    if backend == "openai":
        return _call_openai(settings, model, messages, mode)
    elif backend == "ollama":
        return _call_ollama(settings, model, messages, mode)
    elif backend == "cursor-agent":
        return _call_cursor_agent(settings, messages, mode)
    else:
        raise LlmError(f"未知 backend: {backend}")
