"""``PrincipalContext`` —— 「谁在调用」的请求级上下文（多租户 / RBAC）。

设计理由（W5-2）：
- 当前 ``Settings.principal_role`` 是进程级配置；多租户场景下不同请求的角色 / tenant
  应该能并存，靠请求级 ContextVar 隔离；
- 与 ``RunContext`` 分开是因为：``RunContext`` 关心「这次运行 trace 是什么」，
  ``PrincipalContext`` 关心「调用者是谁、能做什么」；二者生命周期 / 关注点不同；
- 所有 RBAC 校验（``ToolPolicy.required_role``）从读 ``settings.principal_role``
  改为读 ``current_principal.get().role``，未配置时 fallback 到 settings；
- 所有持久化 / 审计写入应该带 ``tenant_id``，落 ``recap_runs`` / ``recap_audit`` /
  ``recap_feedback`` / ``tool_invocations`` / ``pending_actions``。

ContextVar 使用：
- ``current_principal.set(p)`` 在 FastAPI 依赖里调用；
- 需要恢复时调 ``current_principal.reset(token)``；
- 后端线程池 / asyncio 任务不会自动继承，必要时显式 ``copy_context().run(fn)``。
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class PrincipalContext:
    """请求级调用者标识。

    字段：
    - ``tenant_id``    租户 ID（``tenants.tenant_id``）。``None`` = 单租户/legacy。
    - ``role``         RBAC 角色（``guest|user|operator|admin``）。
    - ``api_key_hash`` 命中的 API key 的 sha256 前缀，便于审计追溯。
    - ``source``       客户端来源（一般是 IP；用于风控 / 追溯）。
    """

    tenant_id: Optional[str] = None
    role: str = "user"
    api_key_hash: Optional[str] = None
    source: Optional[str] = None

    @staticmethod
    def system() -> "PrincipalContext":
        """系统级身份：内部任务（outbox sweep / scheduled job）使用，绕过 RBAC。"""
        return PrincipalContext(tenant_id=None, role="admin", api_key_hash="system")

    @staticmethod
    def anonymous() -> "PrincipalContext":
        """无身份：本地开发 / 未配置 API key 时的兜底。"""
        return PrincipalContext(tenant_id=None, role="user", api_key_hash=None)


current_principal: ContextVar[PrincipalContext] = ContextVar(
    "current_principal", default=PrincipalContext.anonymous()
)


def set_principal(principal: PrincipalContext) -> object:
    """返回 token，调用方可在 finally 里 ``current_principal.reset(token)``。"""
    return current_principal.set(principal)


def get_principal() -> PrincipalContext:
    """读取当前 principal；未设置时返回 anonymous。"""
    return current_principal.get()


__all__ = [
    "PrincipalContext",
    "current_principal",
    "get_principal",
    "set_principal",
]
