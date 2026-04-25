"""FastAPI 依赖项：鉴权、限流、通用工具函数。

保持「路由关心业务，依赖关心横切」。新路由一律用 ``Depends(require_api_key)``
/ ``Depends(require_rate_limit)`` 复用此处策略，无需复制粘贴。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional

from fastapi import Depends, Header, HTTPException, Request

from agent_platform.config.settings import Settings, get_settings
from agent_platform.domain.principal import (
    PrincipalContext,
    current_principal,
    set_principal,
)

logger = logging.getLogger("agent_platform.interfaces.api.deps")


# ─── 时间 / JSON 小工具 ────────────────────────────────────────────────────────

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ─── 速率限制（滑动窗口，单进程内存实现） ─────────────────────────────────────────

class RateLimiter:
    """按 IP 的滑动窗口限流；单进程线程安全（deque 操作足够原子）。"""

    def __init__(self, rpm: int):
        self.rpm = rpm
        self._windows: Dict[str, Deque[float]] = defaultdict(deque)

    def check(self, client_id: str) -> bool:
        now = time.time()
        window = self._windows[client_id]
        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.rpm:
            return False
        window.append(now)
        return True


_limiter: Optional[RateLimiter] = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        s = get_settings()
        _limiter = RateLimiter(s.rate_limit_rpm)
    return _limiter


def _reset_limiter_for_tests() -> None:
    """仅测试用：重置限流器（让修改后的 rate_limit_rpm 生效）。"""
    global _limiter
    _limiter = None


# ─── 依赖注入函数 ──────────────────────────────────────────────────────────────

def _hash_api_key(api_key: str) -> str:
    """SHA-256 摘要，用于与 ``tenants.api_key_hash`` 比对（原始 key 不落库）。"""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def require_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> PrincipalContext:
    """X-API-Key 鉴权 + 多租户身份解析（W5-2）。

    决策顺序：
    1. ``tenants`` 表非空 → 严格走多租户：必须命中 active 租户，否则 401；
    2. ``RECAP_API_KEY`` 设置 → 单租户固定 key；
    3. 都没设 → 本地开发匿名放行（写匿名 PrincipalContext）。

    无论哪条路径，都会把当前 ``PrincipalContext`` 写入 ``current_principal``
    ContextVar，供下游 ``ToolPolicy`` / 持久化层使用。
    """
    source = request.client.host if request.client else None

    # 多租户：tenants 表存在数据时优先
    try:
        from agent_platform.infrastructure.persistence.db import (
            count_tenants,
            load_tenant_by_api_key_hash,
        )

        if count_tenants(settings.db_path, status="active") > 0:
            if not x_api_key:
                raise HTTPException(status_code=401, detail="unauthorized")
            digest = _hash_api_key(x_api_key)
            tenant = load_tenant_by_api_key_hash(
                settings.db_path, api_key_hash=digest
            )
            if tenant is None:
                raise HTTPException(status_code=401, detail="unauthorized")
            principal = PrincipalContext(
                tenant_id=str(tenant["tenant_id"]),
                role=str(tenant.get("role") or "user"),
                api_key_hash=digest[:12],
                source=source,
            )
            set_principal(principal)
            return principal
    except HTTPException:
        raise
    except Exception as e:
        # tenants 表不存在或查询异常：降级为单租户/匿名，记一条日志
        logger.warning(
            stable_json({"event": "tenants_lookup_failed", "error": str(e)})
        )

    # 单租户固定 key
    if settings.recap_api_key:
        if not x_api_key or x_api_key != settings.recap_api_key:
            raise HTTPException(status_code=401, detail="unauthorized")
        principal = PrincipalContext(
            tenant_id=None,
            role=settings.principal_role or "user",
            api_key_hash=_hash_api_key(x_api_key)[:12],
            source=source,
        )
        set_principal(principal)
        return principal

    # 本地开发：匿名（不强制 key）
    principal = PrincipalContext(
        tenant_id=None,
        role=settings.principal_role or "user",
        api_key_hash=None,
        source=source,
    )
    set_principal(principal)
    return principal


def require_rate_limit(
    request: Request,
    limiter: RateLimiter = Depends(get_limiter),
) -> None:
    client_id = request.client.host if request.client else "unknown"
    if not limiter.check(client_id):
        raise HTTPException(
            status_code=429,
            detail=f"速率超限，每分钟最多 {limiter.rpm} 次请求",
        )
