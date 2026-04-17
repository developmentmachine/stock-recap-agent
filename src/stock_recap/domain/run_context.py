"""单次运行上下文：跨层传递 request/trace/session，供日志与遥测关联。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RunContext:
    """一次 generate 运行的稳定标识（不依赖具体传输层）。"""

    request_id: str
    trace_id: str
    span_id: str
    session_id: Optional[str] = None

    @staticmethod
    def new(session_id: Optional[str] = None) -> RunContext:
        rid = str(uuid.uuid4())
        tid = uuid.uuid4().hex
        sid = uuid.uuid4().hex[:16]
        return RunContext(request_id=rid, trace_id=tid, span_id=sid, session_id=session_id)
