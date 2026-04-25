"""统一结构化日志：JSON 行 + 自动注入 trace_id/request_id/mode/provider。

为什么自己写 Filter+Formatter 而不是用 ``structlog`` / ``python-json-logger``：
- 只需要把 ``RunContext`` 几个字段拍到每条日志即可，引入第三方 logger 框架
  会和现有 ``logger.warning(_stable_json({...}))`` 习惯打架；
- 我们的业务日志 message 大量已经是 JSON 字符串，formatter 需要识别这种情况
  把它「合并」而不是再次包一层；
- 测试与排错时一行就能 ``json.loads`` 解析出来，对 Loki / VictoriaLogs 也很友好。

约定：
1. ``setup_structured_logging(level, force=False)`` 在 FastAPI lifespan / CLI 入口幂等调用一次；
2. Filter 从 ``current_run_context`` 读 ``request_id / trace_id / mode / provider``；
3. Formatter 输出 ``{"ts","level","logger","message",...,"request_id","trace_id","mode","provider"}``，
   message 若本身是 JSON dict，会被 ``_merge_json_message`` 平铺（保留原 message 同时把字段提到顶层）。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agent_platform.observability.runtime_context import current_run_context


_STD_LOGRECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
}

_CTX_ATTRS = ("request_id", "trace_id", "span_id", "session_id", "mode", "provider", "tenant_id")


class RunContextFilter(logging.Filter):
    """把 ``current_run_context`` 写到 ``LogRecord``，formatter 之后能直接读到。"""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = current_run_context.get()
        if ctx is None:
            for attr in _CTX_ATTRS:
                setattr(record, attr, None)
        else:
            record.request_id = ctx.request_id
            record.trace_id = ctx.trace_id
            record.span_id = ctx.span_id
            record.session_id = ctx.session_id
            record.mode = ctx.mode
            record.provider = ctx.provider
            record.tenant_id = getattr(ctx, "tenant_id", None)
        return True


def _try_json(message: str) -> Optional[Dict[str, Any]]:
    """业务侧大多用 ``_stable_json({...})`` 写日志，这里把它解开方便聚合。"""
    if not message or not message.startswith("{") or not message.endswith("}"):
        return None
    try:
        obj = json.loads(message)
    except Exception:
        return None
    if isinstance(obj, dict):
        return obj
    return None


class JsonFormatter(logging.Formatter):
    """单行 JSON：``{ts, level, logger, message, ...ctx, ...extra}``。

    既能被 ``json.loads`` 直接解析，也能被肉眼快速读懂。
    """

    def format(self, record: logging.LogRecord) -> str:
        base: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
        }
        # 业务 ctx
        for attr in _CTX_ATTRS:
            value = getattr(record, attr, None)
            if value is not None:
                base[attr] = value

        msg = record.getMessage()
        merged = _try_json(msg)
        if merged is not None:
            # 嵌套字段提级；冲突时业务字段优先（base 已有 keys 不被覆盖）。
            for k, v in merged.items():
                if k not in base:
                    base[k] = v
            base.setdefault("event", merged.get("event") or "log")
        else:
            base["message"] = msg

        # 用户额外 extra
        for k, v in record.__dict__.items():
            if k in _STD_LOGRECORD_ATTRS or k in _CTX_ATTRS:
                continue
            if k.startswith("_"):
                continue
            if k in base:
                continue
            try:
                json.dumps(v, default=str)
            except Exception:
                v = str(v)
            base[k] = v

        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            base["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(base, ensure_ascii=False, sort_keys=True, default=str)


_CONFIGURED = False


def setup_structured_logging(
    level: int = logging.INFO,
    *,
    force: bool = False,
    stream=None,
) -> None:
    """幂等安装结构化日志到 root logger。

    - ``force=True`` 时清掉已有 handler（用于测试切换格式）。
    - 不接管 uvicorn 自身的 access log；只接管 root + ``agent_platform.*``。
    """
    global _CONFIGURED
    root = logging.getLogger()
    if _CONFIGURED and not force:
        # 仍要更新 level，方便运行中调高/调低。
        root.setLevel(level)
        return

    if force:
        for h in list(root.handlers):
            root.removeHandler(h)

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RunContextFilter())
    root.addHandler(handler)
    root.setLevel(level)

    # agent_platform.* 下面的 logger 通常 propagate=True（默认），
    # 所以走 root handler 即可；但如果有人单独设了 handler，也给加 filter。
    pkg_logger = logging.getLogger("agent_platform")
    pkg_logger.setLevel(level)
    for h in pkg_logger.handlers:
        if not any(isinstance(f, RunContextFilter) for f in h.filters):
            h.addFilter(RunContextFilter())

    _CONFIGURED = True


def reset_structured_logging() -> None:
    """仅供测试：恢复默认 logging 配置。"""
    global _CONFIGURED
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    _CONFIGURED = False


__all__ = [
    "JsonFormatter",
    "RunContextFilter",
    "reset_structured_logging",
    "setup_structured_logging",
]
