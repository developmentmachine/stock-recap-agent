"""FastAPI 应用工厂。

装配步骤：
1. ``create_app()`` 建立 FastAPI 实例并绑定 lifespan（启动时 configure_tracing）；
2. 安装 middleware（CORS 按配置条件挂载）；
3. 挂载各 ``v1/*`` 子路由。

``interfaces/api/routes.py`` 只做一件事：``app = create_app()`` 暴露 uvicorn 入口。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from stock_recap.config.settings import get_settings
from stock_recap.interfaces.api.middleware import install_cors
from stock_recap.interfaces.api.v1 import (
    feedback_router,
    jobs_router,
    ops_router,
    recap_router,
)


@asynccontextmanager
async def _app_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    import logging

    from stock_recap.observability.logging_setup import setup_structured_logging
    from stock_recap.observability.tracing import configure_tracing

    setup_structured_logging(level=logging.INFO)
    configure_tracing(get_settings())
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Stock Daily Recap API",
        description="企业级 A 股日终复盘智能体 API（含 NDJSON 流式 /v1/recap/stream）",
        version="1.0.0",
        lifespan=_app_lifespan,
    )
    install_cors(app)
    app.include_router(ops_router)
    app.include_router(recap_router)
    app.include_router(feedback_router)
    app.include_router(jobs_router)
    return app
