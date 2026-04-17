"""FastAPI 依赖项：鉴权、限流、通用工具函数。

保持「路由关心业务，依赖关心横切」。新路由一律用 ``Depends(require_api_key)``
/ ``Depends(require_rate_limit)`` 复用此处策略，无需复制粘贴。
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional

from fastapi import Depends, Header, HTTPException, Request

from stock_recap.config.settings import Settings, get_settings

logger = logging.getLogger("stock_recap.interfaces.api.deps")


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

def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    """X-API-Key 鉴权：未配置 ``RECAP_API_KEY`` 时放行（便于本地开发）。"""
    if not settings.recap_api_key:
        return
    if not x_api_key or x_api_key != settings.recap_api_key:
        raise HTTPException(status_code=401, detail="unauthorized")


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
