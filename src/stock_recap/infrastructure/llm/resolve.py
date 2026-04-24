"""Backend / 模型名解析。

历史上 ``_BACKEND_ALIAS`` 是手动维护的 dict；Wave 3 改为从
``domain.registries.LlmBackendRegistry`` 派生 —— 新增 backend 只需在
``LlmBackendSpec.aliases`` 上声明即可被这里识别。
"""
from __future__ import annotations

from typing import Optional, Tuple

from stock_recap.config.settings import Settings
from stock_recap.domain.models import LlmBackend
from stock_recap.domain.registries import default_backend_registry


def _model_prefix_to_backend(prefix: str) -> Optional[LlmBackend]:
    """``cursor`` / ``cursor-agent`` / ``CURSOR-CLI`` 都归一到 canonical name。"""
    return default_backend_registry().resolve_alias(prefix)  # type: ignore[return-value]


def _interpret_model_spec(model_spec: str) -> Tuple[Optional[LlmBackend], Optional[str]]:
    """统一模型表达：``openai:<m>`` / ``ollama:<m>`` / ``cursor-cli`` / ``local:ollama:<m>``。

    返回 ``(backend, model_or_None)``。``cursor-cli`` / ``gemini-cli`` 的模型名一律返回 None
    （由 Settings 中的 cmd 决定实际调用的 CLI；模型选择在 CLI 侧）。

    扩展原则（W4-5）：第三方 backend（包括测试的 ``replay``）只要在
    ``LlmBackendRegistry`` 注册了别名，就能直接被识别 —— 我们不再硬编码
    backend 列表，而是按「是否需要 model 名」分两组：
      * ``cursor-cli`` 走 CLI（没有 model 字段，模型由 cmd 选）→ 返回 (b, None)；
      * 其他后端（含 openai / ollama / gemini-cli / replay / 第三方）→
        把 ``:`` 后所有内容当 model 名，没有则返回 None。
    """
    s = model_spec.strip()
    if not s:
        return None, None
    parts = s.split(":")
    if len(parts) == 1:
        b = _model_prefix_to_backend(s)
        # 单段：仅当 backend 不需要 model（CLI 类）才视作 backend；否则按 model 处理。
        if b in {"cursor-cli", "gemini-cli", "replay"}:
            return b, None  # type: ignore[return-value]
        return None, s

    prefix = parts[0].lower()
    if prefix == "local":
        if len(parts) == 2:
            return _model_prefix_to_backend(parts[1]), None
        b = _model_prefix_to_backend(parts[1])
        if b == "cursor-cli":
            return b, None
        if b is not None:
            return b, ":".join(parts[2:]) if len(parts) > 2 else None
        return None, None

    b = _model_prefix_to_backend(prefix)
    if b == "cursor-cli":
        return b, None
    if b is not None:
        # gemini-cli / openai / ollama / 任意第三方 backend：把剩下当 model
        return b, ":".join(parts[1:]) if len(parts) > 1 else None
    return None, s


def llm_backend_effective(
    model_spec: Optional[str], settings: Optional[Settings] = None
) -> LlmBackend:
    """优先级：``model_spec`` 前缀 > ``RECAP_LLM_BACKEND`` 环境变量 > 默认 openai。"""
    if model_spec:
        b, _ = _interpret_model_spec(model_spec)
        if b:
            return b
    if settings and settings.llm_backend:
        b = _model_prefix_to_backend(settings.llm_backend)
        if b:
            return b
    return "openai"


def model_effective(settings: Settings, model_spec: Optional[str]) -> str:
    if model_spec:
        _, m = _interpret_model_spec(model_spec)
        if m:
            return m
    return settings.model
