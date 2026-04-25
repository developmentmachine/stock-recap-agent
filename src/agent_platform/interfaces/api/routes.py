"""FastAPI 应用装配入口。

实际实现已拆分：
- ``interfaces/api/app.py``       —— FastAPI 工厂 + lifespan
- ``interfaces/api/middleware.py`` —— CORS 安装
- ``interfaces/api/deps.py``       —— 鉴权 / 限流 / 工具函数
- ``interfaces/api/v1/ops``        —— /healthz /metrics /v1/history /v1/backtest /v1/evolution
- ``interfaces/api/v1/recap``      —— /v1/recap /v1/recap/stream
- ``interfaces/api/v1/feedback``   —— /v1/feedback

本模块只保留 ``app`` 供 uvicorn / TestClient 使用，并对外再导出依赖符号，
让现存引用（例如测试或第三方脚本 ``from ...routes import require_api_key``）
继续可用。
"""
from __future__ import annotations

from agent_platform.interfaces.api.app import create_app
from agent_platform.interfaces.api.deps import (
    RateLimiter as _RateLimiter,
    get_limiter,
    require_api_key,
    require_rate_limit,
)

app = create_app()

__all__ = [
    "_RateLimiter",
    "app",
    "get_limiter",
    "require_api_key",
    "require_rate_limit",
]
