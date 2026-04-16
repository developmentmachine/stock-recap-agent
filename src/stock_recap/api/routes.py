"""FastAPI 路由层。

特性：
- API Key 鉴权（X-API-Key header）
- 滑动窗口速率限制（内存实现，按 IP）
- /metrics 端点（JSON 格式系统运行状态）
- /v1/feedback 接收用户评分并触发进化检查
- /v1/backtest 返回回测历史
- Settings 单例注入（不在每次请求中重复实例化）
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Annotated, Deque, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from stock_recap.db import (
    init_db,
    insert_feedback,
    insert_run,
    load_history,
    load_metrics,
    load_recent_backtests,
)
from stock_recap.models import (
    FeedbackRequest,
    GenerateRequest,
    GenerateResponse,
    LlmTokens,
)
from stock_recap.settings import Settings, get_settings

logger = logging.getLogger("stock_recap.api")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ─── 速率限制（滑动窗口，内存实现） ───────────────────────────────────────────────

class _RateLimiter:
    def __init__(self, rpm: int):
        self.rpm = rpm
        self._windows: Dict[str, Deque[float]] = defaultdict(deque)

    def check(self, client_id: str) -> bool:
        """返回 True 表示放行，False 表示限流。"""
        now = time.time()
        window = self._windows[client_id]
        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.rpm:
            return False
        window.append(now)
        return True


_limiter: Optional[_RateLimiter] = None


def get_limiter() -> _RateLimiter:
    global _limiter
    if _limiter is None:
        s = get_settings()
        _limiter = _RateLimiter(s.rate_limit_rpm)
    return _limiter


# ─── 依赖项 ────────────────────────────────────────────────────────────────────

def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.recap_api_key:
        return  # 未配置时不强制（本地开发），生产必须设置
    if not x_api_key or x_api_key != settings.recap_api_key:
        raise HTTPException(status_code=401, detail="unauthorized")


def require_rate_limit(
    request: Request,
    limiter: _RateLimiter = Depends(get_limiter),
) -> None:
    client_id = request.client.host if request.client else "unknown"
    if not limiter.check(client_id):
        raise HTTPException(
            status_code=429,
            detail=f"速率超限，每分钟最多 {limiter.rpm} 次请求",
        )


# ─── FastAPI 应用 ──────────────────────────────────────────────────────────────

from stock_recap.memory.manager import get_prompt_version  # noqa: E402 — 延迟导入避免循环

app = FastAPI(
    title="Stock Daily Recap API",
    description="企业级 A 股日终复盘智能体 API",
    version="1.0.0",
)


# ─── 健康检查 ──────────────────────────────────────────────────────────────────

@app.get("/healthz", tags=["ops"])
def healthz(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    return {
        "ok": True,
        "time": _utc_now_iso(),
        "prompt_version": get_prompt_version(settings.db_path),
    }


# ─── 指标 ──────────────────────────────────────────────────────────────────────

@app.get("/metrics", tags=["ops"])
def metrics(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    m = load_metrics(
        settings.db_path,
        today=_today_str(),
        prompt_version=get_prompt_version(settings.db_path),
    )
    return m.model_dump()


# ─── 生成复盘 ──────────────────────────────────────────────────────────────────

@app.post(
    "/v1/recap",
    response_model=GenerateResponse,
    dependencies=[Depends(require_api_key), Depends(require_rate_limit)],
    tags=["recap"],
)
def api_generate(
    req: GenerateRequest,
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    from stock_recap.core import generate_once  # 延迟导入避免循环

    init_db(settings.db_path)
    resp = generate_once(req, settings)

    status = 200
    if req.force_llm and resp.recap is None:
        status = 503

    return JSONResponse(status_code=status, content=resp.model_dump())


# ─── 历史记录 ──────────────────────────────────────────────────────────────────

@app.get(
    "/v1/history",
    dependencies=[Depends(require_api_key)],
    tags=["recap"],
)
def api_history(
    limit: int = 20,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    init_db(settings.db_path)
    return {"items": load_history(settings.db_path, limit=limit)}


# ─── 用户反馈 ──────────────────────────────────────────────────────────────────

@app.post(
    "/v1/feedback",
    dependencies=[Depends(require_api_key)],
    tags=["recap"],
)
def api_feedback(
    req: FeedbackRequest,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    from stock_recap.memory.manager import check_and_run_evolution

    init_db(settings.db_path)
    insert_feedback(
        settings.db_path,
        request_id=req.request_id,
        created_at=_utc_now_iso(),
        rating=int(req.rating),
        tags=req.tags,
        comment=req.comment,
    )

    evolved = None
    # 低分立即触发进化
    if req.rating <= 2:
        logger.info(
            _stable_json({"event": "low_rating_evolution", "rating": req.rating})
        )
        evolved = check_and_run_evolution(
            settings.db_path,
            settings=settings,
            trigger_run_id=req.request_id,
            force=True,
        )
    else:
        # 普通反馈：检查是否达到阈值
        evolved = check_and_run_evolution(
            settings.db_path,
            settings=settings,
            trigger_run_id=req.request_id,
            force=False,
        )

    return {
        "ok": True,
        "evolved": evolved is not None,
        "new_prompt_version": evolved,
    }


# ─── 回测历史 ──────────────────────────────────────────────────────────────────

@app.get(
    "/v1/backtest",
    dependencies=[Depends(require_api_key)],
    tags=["recap"],
)
def api_backtest(
    limit: int = 10,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    init_db(settings.db_path)
    return {"items": load_recent_backtests(settings.db_path, limit=limit)}


# ─── 进化历史 ──────────────────────────────────────────────────────────────────

@app.get(
    "/v1/evolution",
    dependencies=[Depends(require_api_key)],
    tags=["ops"],
)
def api_evolution(
    limit: int = 10,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    from stock_recap.db import load_evolution_history

    init_db(settings.db_path)
    return {"items": load_evolution_history(settings.db_path, limit=limit)}
