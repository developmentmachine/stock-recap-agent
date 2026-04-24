"""统一工具门面：OpenAI/Ollama 循环与预取注入共用同一策略与执行入口。

职责（自上而下）：
1. 按 ``Settings.tools_*`` + ``ToolPolicy.enabled`` 过滤可见工具集；
2. 调用前依次校验：注册 → enabled → 角色 → per-tool budget；
3. 执行加超时（``timeout_s>0`` 时用线程 ``concurrent.futures`` 包一层墙钟）；
4. 把全局 ``AgentBudget`` 一并扣减；
5. 任何结果（成功 / 失败 / 拒绝 / 超时）按 ``Settings.tool_audit_enabled`` 落
   ``tool_invocations`` 审计表。
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeoutError
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from stock_recap.config.settings import Settings
from stock_recap.observability.runtime_context import current_budget, current_run_context

from stock_recap.infrastructure.tools.registry import (
    TOOL_SCHEMAS,
    execute_tool,
    prefetch_for_prompt,
)
from stock_recap.policy.tools import (
    ToolBudgetExceeded,
    ToolDisabled,
    ToolForbidden,
    ToolNotRegistered,
    ToolPolicy,
    ToolPolicyError,
    ToolPolicyRegistry,
    ToolTimeout,
    build_default_registry,
)

logger = logging.getLogger("stock_recap.infrastructure.tools.runner")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# 与 Settings 字段名的映射；新增工具时这里也要登记一份，否则 enabled_tool_names
# 不会把它放进允许集合（与历史行为兼容）。
_SETTINGS_TOOL_FLAGS: Dict[str, str] = {
    "web_search": "tools_web_search",
    "query_market_data": "tools_market_data",
    "query_history": "tools_history",
}


class RecapToolRunner:
    """Agent 工具运行时：策略校验 + 审计 + 全局/单工具 budget。

    预取与 function-calling 共用同一套开关（``Settings.tools_*``），
    保证 cursor-cli/gemini-cli 的预注入上下文与 OpenAI/Ollama 的工具循环
    决策面严格对齐。
    """

    __slots__ = ("_settings", "_policy_registry", "_per_tool_used")

    def __init__(
        self,
        settings: Settings,
        policy_registry: Optional[ToolPolicyRegistry] = None,
    ) -> None:
        self._settings = settings
        # 默认走内置 policy；测试 / 多租户场景可注入自定义 registry。
        self._policy_registry = policy_registry or build_default_registry()
        # 单次 runner 实例内、按工具名计数；外层每次 generate 应新建 runner。
        self._per_tool_used: Dict[str, int] = {}

    # ─── 元信息 ───────────────────────────────────────────────────────────

    @property
    def tools_enabled(self) -> bool:
        return self._settings.tools_enabled

    @property
    def policy_registry(self) -> ToolPolicyRegistry:
        return self._policy_registry

    def _settings_flag_on(self, name: str) -> bool:
        flag = _SETTINGS_TOOL_FLAGS.get(name)
        if flag is None:
            # 未在 Settings 中登记的工具，默认听 policy.enabled。
            return True
        return bool(getattr(self._settings, flag, False))

    def enabled_tool_names(self) -> Set[str]:
        """当前生效的工具集合（总开关关闭时返回空集）。

        允许条件 = 总开关 ∩ Settings.tools_* ∩ ToolPolicy.enabled ∩ 角色满足。
        """
        if not self._settings.tools_enabled:
            return set()
        names: Set[str] = set()
        principal = self._settings.principal_role
        for name in self._policy_registry.names():
            if not self._settings_flag_on(name):
                continue
            policy = self._policy_registry.get(name)
            if policy is None or not policy.enabled:
                continue
            if not policy.is_role_allowed(principal):
                continue
            names.add(name)
        return names

    def openai_compatible_schemas(self) -> List[Dict[str, Any]]:
        allowed = self.enabled_tool_names()
        if not allowed:
            return []
        return [t for t in TOOL_SCHEMAS if t["function"]["name"] in allowed]

    # ─── 单次执行 ─────────────────────────────────────────────────────────

    def execute(self, name: str, arguments: Dict[str, Any], db_path: str) -> str:
        """执行工具：通过策略校验 + 全局/单工具 budget + 审计。

        失败语义：
        - ``ToolPolicyError`` 一族（含 ``ToolNotRegistered`` / ``ToolDisabled`` /
          ``ToolForbidden`` / ``ToolBudgetExceeded`` / ``ToolTimeout``）会向上抛，
          调用方（OpenAI/Ollama 工具循环）应把它转成「这条 tool_call 失败」结果
          反馈给 LLM，而非崩溃整次 generate。
        - 全局 ``LlmBudgetExceeded``（来自 ``current_budget``）继续向上抛，由
          pipeline 阶段切换处接住、终止整次 run。
        """
        principal = self._settings.principal_role
        ctx = current_run_context.get()
        request_id = ctx.request_id if ctx is not None else None

        # 1) 注册校验（拒绝立即审计）
        try:
            policy = self._policy_registry.require(name)
        except ToolNotRegistered as e:
            self._audit(
                request_id=request_id,
                tool_name=name,
                status="denied",
                read_only=True,
                principal_role=principal,
                arguments=arguments,
                latency_ms=0,
                error=str(e),
            )
            raise

        # 2) policy.enabled / Settings.tools_* 开关
        if not policy.enabled or not self._settings_flag_on(name) or not self._settings.tools_enabled:
            err = ToolDisabled(f"tool '{name}' is disabled by policy or settings")
            self._audit(
                request_id=request_id,
                tool_name=name,
                status="denied",
                read_only=policy.read_only,
                principal_role=principal,
                arguments=arguments,
                latency_ms=0,
                error=str(err),
            )
            raise err

        # 3) 角色校验
        if not policy.is_role_allowed(principal):
            err = ToolForbidden(
                f"tool '{name}' requires role '{policy.required_role}', "
                f"current principal_role='{principal}'"
            )
            self._audit(
                request_id=request_id,
                tool_name=name,
                status="denied",
                read_only=policy.read_only,
                principal_role=principal,
                arguments=arguments,
                latency_ms=0,
                error=str(err),
            )
            raise err

        # 4) per-tool 上限（不与全局 AgentBudget 冲突；先扣本工具，再扣全局）
        if policy.max_calls_per_run > 0:
            used = self._per_tool_used.get(name, 0)
            if used + 1 > policy.max_calls_per_run:
                err = ToolBudgetExceeded(name, policy.max_calls_per_run, used + 1)
                self._audit(
                    request_id=request_id,
                    tool_name=name,
                    status="denied",
                    read_only=policy.read_only,
                    principal_role=principal,
                    arguments=arguments,
                    latency_ms=0,
                    error=str(err),
                )
                raise err

        # 5) 全局 AgentBudget（超额抛 LlmBudgetExceeded → 上层 pipeline 接住）
        budget = current_budget.get()
        if budget is not None:
            try:
                budget.record_tool_call()
            except Exception as e:
                self._audit(
                    request_id=request_id,
                    tool_name=name,
                    status="denied",
                    read_only=policy.read_only,
                    principal_role=principal,
                    arguments=arguments,
                    latency_ms=0,
                    error=f"agent_budget: {e}",
                )
                raise

        # 6) 真正执行（可选 timeout）
        self._per_tool_used[name] = self._per_tool_used.get(name, 0) + 1
        t0 = time.monotonic()
        try:
            result = self._call_with_timeout(name, arguments, db_path, policy)
        except ToolTimeout as e:
            self._audit(
                request_id=request_id,
                tool_name=name,
                status="timeout",
                read_only=policy.read_only,
                principal_role=principal,
                arguments=arguments,
                latency_ms=int((time.monotonic() - t0) * 1000),
                error=str(e),
            )
            raise
        except Exception as e:
            self._audit(
                request_id=request_id,
                tool_name=name,
                status="failed",
                read_only=policy.read_only,
                principal_role=principal,
                arguments=arguments,
                latency_ms=int((time.monotonic() - t0) * 1000),
                error=str(e)[:500],
            )
            raise

        self._audit(
            request_id=request_id,
            tool_name=name,
            status="ok",
            read_only=policy.read_only,
            principal_role=principal,
            arguments=arguments,
            latency_ms=int((time.monotonic() - t0) * 1000),
            error=None,
        )
        return result

    def _call_with_timeout(
        self,
        name: str,
        arguments: Dict[str, Any],
        db_path: str,
        policy: ToolPolicy,
    ) -> str:
        """``timeout_s<=0`` 走原同步路径，避免 ThreadPool 开销。"""
        if policy.timeout_s and policy.timeout_s > 0:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{name}") as pool:
                fut = pool.submit(execute_tool, name, arguments, db_path)
                try:
                    return fut.result(timeout=policy.timeout_s)
                except FutTimeoutError:
                    fut.cancel()
                    raise ToolTimeout(name, policy.timeout_s)
        return execute_tool(name, arguments, db_path=db_path)

    # ─── 预取（cursor-cli / gemini-cli 注入） ─────────────────────────────

    def prefetch_for_prompt(self, date: str, db_path: str) -> str:
        """按当前启用工具集合预取；总开关关闭时返回空串。

        预取被视作 N 次工具调用（每个启用工具 1 次），统一计入 budget 与审计。
        per-tool 上限同样适用 —— 例如 ``web_search`` 上限为 3 时，预取占 1 次额度。
        """
        allowed = self.enabled_tool_names()
        if not allowed:
            return ""

        principal = self._settings.principal_role
        ctx = current_run_context.get()
        request_id = ctx.request_id if ctx is not None else None
        budget = current_budget.get()

        # 先按 per-tool 上限剔除超额；不再走 self.execute 是因为 prefetch 是
        # 内部预热，不需要返回给 LLM 单条「拒绝」响应，直接 skip 即可。
        actually_used: Set[str] = set()
        for name in allowed:
            policy = self._policy_registry.get(name)
            if policy is None:
                continue
            if policy.max_calls_per_run > 0:
                used = self._per_tool_used.get(name, 0)
                if used + 1 > policy.max_calls_per_run:
                    self._audit(
                        request_id=request_id,
                        tool_name=name,
                        status="denied",
                        read_only=policy.read_only,
                        principal_role=principal,
                        arguments={"phase": "prefetch", "date": date},
                        latency_ms=0,
                        error=f"per_tool_budget: limit={policy.max_calls_per_run}",
                    )
                    continue
            self._per_tool_used[name] = self._per_tool_used.get(name, 0) + 1
            actually_used.add(name)

        if not actually_used:
            return ""
        if budget is not None:
            budget.record_tool_call(n=len(actually_used))

        t0 = time.monotonic()
        try:
            text = prefetch_for_prompt(date, db_path=db_path, enabled_tools=actually_used)
        except Exception as e:
            for name in actually_used:
                self._audit(
                    request_id=request_id,
                    tool_name=name,
                    status="failed",
                    read_only=True,
                    principal_role=principal,
                    arguments={"phase": "prefetch", "date": date},
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    error=str(e)[:500],
                )
            raise

        # 预取批量成功，给每个参与的工具各落一行 ok 审计（按需聚合在 Wave 4）。
        elapsed_each = int(((time.monotonic() - t0) * 1000) / max(1, len(actually_used)))
        for name in actually_used:
            self._audit(
                request_id=request_id,
                tool_name=name,
                status="ok",
                read_only=True,
                principal_role=principal,
                arguments={"phase": "prefetch", "date": date},
                latency_ms=elapsed_each,
                error=None,
            )
        return text

    # ─── 审计 ─────────────────────────────────────────────────────────────

    def _audit(
        self,
        *,
        request_id: Optional[str],
        tool_name: str,
        status: str,
        read_only: bool,
        principal_role: Optional[str],
        arguments: Optional[Dict[str, Any]],
        latency_ms: Optional[int],
        error: Optional[str],
    ) -> None:
        """所有审计写入失败一律降级为 warning，不影响主调用链路。"""
        if not self._settings.tool_audit_enabled:
            return
        try:
            from stock_recap.infrastructure.persistence.db import insert_tool_invocation

            insert_tool_invocation(
                self._settings.db_path,
                request_id=request_id,
                tool_name=tool_name,
                status=status,
                read_only=read_only,
                principal_role=principal_role,
                arguments=arguments,
                latency_ms=latency_ms,
                error=error,
                created_at=_utc_now_iso(),
            )
        except Exception as e:
            logger.warning(
                _stable_json(
                    {
                        "event": "tool_audit_write_failed",
                        "tool": tool_name,
                        "status": status,
                        "error": str(e),
                    }
                )
            )


__all__ = [
    "RecapToolRunner",
    "ToolBudgetExceeded",
    "ToolDisabled",
    "ToolForbidden",
    "ToolNotRegistered",
    "ToolPolicyError",
    "ToolTimeout",
]
