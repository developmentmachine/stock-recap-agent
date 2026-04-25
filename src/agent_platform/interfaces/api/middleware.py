"""ASGI middleware 安装位。当前只管 CORS。"""
from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from agent_platform.config.settings import get_settings


def install_cors(app: FastAPI) -> None:
    """按 ``RECAP_CORS_ORIGINS`` 条件挂载 CORS；空值跳过。"""
    s = get_settings()
    if not s.cors_origins:
        return
    origins = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
    if not origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
