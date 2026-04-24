"""单次运行上下文：跨层传递 request/trace/session/mode/provider，供日志与遥测关联。"""
from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RunContext:
    """一次 generate 运行的稳定标识（不依赖具体传输层）。

    ``mode`` / ``provider`` 是 Wave 4 引入的额外维度，专供日志注入与指标关联；
    数据收集 / LLM 路径已经显式拿到这些信息，把它们冗余在 RunContext 内只是为了
    logging filter 能在不依赖参数下穿的情况下读到。
    """

    request_id: str
    trace_id: str
    span_id: str
    session_id: Optional[str] = None
    mode: Optional[str] = None
    provider: Optional[str] = None
    tenant_id: Optional[str] = None  # W5-2：多租户标识，None 表示「单租户/legacy」

    @staticmethod
    def new(
        session_id: Optional[str] = None,
        *,
        mode: Optional[str] = None,
        provider: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> RunContext:
        rid = str(uuid.uuid4())
        tid = uuid.uuid4().hex
        sid = uuid.uuid4().hex[:16]
        return RunContext(
            request_id=rid,
            trace_id=tid,
            span_id=sid,
            session_id=session_id,
            mode=mode,
            provider=provider,
            tenant_id=tenant_id,
        )

    def with_overrides(
        self,
        *,
        mode: Optional[str] = None,
        provider: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> "RunContext":
        """返回一个填好 mode/provider/tenant_id 的副本；任意 None 维持原值。"""
        return dataclasses.replace(
            self,
            mode=mode if mode is not None else self.mode,
            provider=provider if provider is not None else self.provider,
            tenant_id=tenant_id if tenant_id is not None else self.tenant_id,
        )
