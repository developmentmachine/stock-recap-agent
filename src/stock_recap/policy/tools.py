"""工具治理：``ToolPolicy`` + 注册表 + 拒绝异常。

为什么单独立一层 policy（而不是直接散在 runner 里）：
- 「能不能调」与「怎么调」是两件事：能力治理是策略层，分发执行是基础设施层；
- 业务方（CLI / API / 调度器）只能修改 policy（环境变量 / yaml），看不到也改不了
  handler；
- 与 Wave 5 的 ``PrincipalContext`` / 多租户对接时，policy 是天然的拦截点。

每个工具一份 ``ToolPolicy``：
- ``enabled``               总开关（与 Settings.tools_* 协同：任一为 False 即关）
- ``read_only``             只读 vs 有副作用（仅作元数据，便于审计 / Wave 4 可视化）
- ``max_calls_per_run``     单次 generate 内本工具的最大调用次数（0 / 负数 = 不限）
- ``timeout_s``             单次工具调用的墙钟上限（0 / 负数 = 不限）
- ``required_role``         需要的最低角色；为 ``None`` 时不限
- ``description``           面向运维的可读说明（出现在审计记录里）

异常：
- ``ToolNotRegistered``     工具名未在注册表 → 直接拒绝
- ``ToolDisabled``          policy.enabled / Settings 关闭 → 拒绝
- ``ToolForbidden``         角色不足 → 拒绝
- ``ToolBudgetExceeded``    本次运行内本工具已超额 → 拒绝
- ``ToolTimeout``           执行超时 → 拒绝（运行体真正超时由 runner 触发）

所有 ``ToolPolicyError`` 一律继承 ``RuntimeError``，**不**继承 ``LlmBusinessError`` /
``LlmTransportError`` —— 工具被拒不应该触发 critic 重入或 tenacity 重试。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


# ─── 角色等级（Wave 5 PrincipalContext 接入前的轻量占位） ────────────────────

# 越大权限越高；同级即视为「满足」。
_ROLE_LEVEL: Dict[str, int] = {
    "guest": 0,
    "user": 10,
    "operator": 20,
    "admin": 30,
}


def _role_level(role: Optional[str]) -> int:
    if not role:
        return _ROLE_LEVEL["user"]  # 缺省视为普通 user
    return _ROLE_LEVEL.get(role, _ROLE_LEVEL["user"])


# ─── 异常层级 ───────────────────────────────────────────────────────────────


class ToolPolicyError(RuntimeError):
    """工具治理层拒绝执行的统一基类。"""


class ToolNotRegistered(ToolPolicyError):
    """请求的工具名未在 ToolPolicyRegistry 注册。"""


class ToolDisabled(ToolPolicyError):
    """工具被 policy 或 Settings 关闭。"""


class ToolForbidden(ToolPolicyError):
    """principal 角色不足以调用该工具。"""


class ToolBudgetExceeded(ToolPolicyError):
    """单次运行内本工具调用次数已达 ``max_calls_per_run``。"""

    def __init__(self, tool: str, limit: int, used: int) -> None:
        super().__init__(f"tool budget exceeded: {tool} limit={limit} used={used}")
        self.tool = tool
        self.limit = limit
        self.used = used


class ToolTimeout(ToolPolicyError):
    """工具执行超过 ``timeout_s``。"""

    def __init__(self, tool: str, timeout_s: float) -> None:
        super().__init__(f"tool {tool} timed out after {timeout_s:.1f}s")
        self.tool = tool
        self.timeout_s = timeout_s


# ─── ToolPolicy + Registry ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    enabled: bool = True
    read_only: bool = True
    max_calls_per_run: int = 0  # 0 = 不限（仍受 AgentBudget 全局上限约束）
    timeout_s: float = 0.0  # 0 = 不限
    required_role: Optional[str] = None  # None = 任意角色
    description: str = ""

    def is_role_allowed(self, principal_role: Optional[str]) -> bool:
        if self.required_role is None:
            return True
        return _role_level(principal_role) >= _role_level(self.required_role)


@dataclass
class ToolPolicyRegistry:
    """进程内注册表；测试可以传入空 registry 或自定义 registry。"""

    _policies: Dict[str, ToolPolicy] = field(default_factory=dict)

    def register(self, policy: ToolPolicy) -> None:
        self._policies[policy.name] = policy

    def get(self, name: str) -> Optional[ToolPolicy]:
        return self._policies.get(name)

    def require(self, name: str) -> ToolPolicy:
        p = self._policies.get(name)
        if p is None:
            raise ToolNotRegistered(f"tool '{name}' is not registered in ToolPolicyRegistry")
        return p

    def names(self) -> list[str]:
        return sorted(self._policies.keys())


# ─── 默认 policy（与 ALL_TOOL_NAMES 对齐） ─────────────────────────────────


def build_default_registry() -> ToolPolicyRegistry:
    """与 ``infrastructure.tools.registry.ALL_TOOL_NAMES`` 对齐的内置 policy。

    所有内置工具均 ``read_only=True``：它们只查询数据，不会改库 / 推送 / 调外部。
    默认 ``required_role`` 留空 —— 任何 principal 都能用；运维通过 Settings 覆盖。
    """
    reg = ToolPolicyRegistry()
    reg.register(
        ToolPolicy(
            name="web_search",
            description="联网搜索（DuckDuckGo），有外部网络出口；只读但有外网延迟。",
            read_only=True,
            max_calls_per_run=3,  # 单次复盘内最多 3 次外网搜索，防止失控
            timeout_s=20.0,
        )
    )
    reg.register(
        ToolPolicy(
            name="query_market_data",
            description="查询 A 股实时/历史行情数据（指数/板块/北向）。",
            read_only=True,
            max_calls_per_run=6,
            timeout_s=15.0,
        )
    )
    reg.register(
        ToolPolicy(
            name="query_history",
            description="查询本地 SQLite 中近期复盘记录。",
            read_only=True,
            max_calls_per_run=3,
            timeout_s=5.0,
        )
    )
    return reg


__all__ = [
    "ToolBudgetExceeded",
    "ToolDisabled",
    "ToolForbidden",
    "ToolNotRegistered",
    "ToolPolicy",
    "ToolPolicyError",
    "ToolPolicyRegistry",
    "ToolTimeout",
    "build_default_registry",
]
