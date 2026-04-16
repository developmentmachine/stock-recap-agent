"""核心业务逻辑：generate_once。

将数据采集、特征工程、LLM 生成、评测、持久化、推送串联。
供 CLI、API、调度器共用。
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from stock_recap.data.collector import collect_snapshot
from stock_recap.data.features import build_features
from stock_recap.db import (
    get_pending_backtest,
    init_db,
    insert_backtest,
    insert_run,
    load_feedback_summary,
)
from stock_recap.llm.backends import call_llm, llm_backend_effective, model_effective
from stock_recap.llm.eval import auto_eval, compute_backtest
from stock_recap.llm.prompts import build_messages
from stock_recap.memory.manager import (
    check_and_run_evolution,
    extract_market_patterns,
    get_prompt_version,
    load_evolution_guidance,
    load_recent_memory,
)
from stock_recap.models import (
    GenerateRequest,
    GenerateResponse,
    LlmTokens,
    RecapStrategy,
)
from stock_recap.render.renderers import render_markdown, render_wechat_text
from stock_recap.settings import Settings

logger = logging.getLogger("stock_recap.core")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def generate_once(req: GenerateRequest, settings: Settings) -> GenerateResponse:
    """
    单次生成流程：采集 → 特征 → prompt → LLM → 评测 → 持久化 → 推送。
    """
    request_id = str(uuid.uuid4())
    t0 = time.time()

    # 1. 数据采集
    snapshot = collect_snapshot(
        req.provider,
        req.date,
        skip_trading_check=req.skip_trading_check,
    )

    # 2. 特征工程
    features = build_features(snapshot)

    # 3. 记忆加载
    memory = load_recent_memory(
        settings.db_path,
        date=snapshot.date,
        mode=req.mode,
        limit=settings.max_history_for_context,
    )

    # 4. 进化指导 + 反馈摘要
    evolution_guidance = load_evolution_guidance(settings.db_path)
    feedback_summary = load_feedback_summary(settings.db_path)

    # 5. 市场模式提炼（可选，失败不阻断）
    pattern_summary: Optional[str] = None
    try:
        pattern_summary = extract_market_patterns(
            settings.db_path, days=settings.pattern_extraction_days, settings=settings
        )
    except Exception as e:
        logger.warning(_stable_json({"event": "pattern_extraction_skipped", "error": str(e)}))

    # 6. 回测上下文（昨日策略 vs 今日实际）
    backtest_context: Optional[str] = None
    bt_history = load_recent_backtests_simple(settings.db_path, limit=3)
    if bt_history:
        backtest_context = "近期回测评分：" + " | ".join(
            f"{b['strategy_date']} 命中率={b.get('hit_rate', 0):.0%}" for b in bt_history
        )

    # 7. 获取当前 prompt 版本
    prompt_version = get_prompt_version(settings.db_path)

    # 8. 构建 messages
    messages = build_messages(
        mode=req.mode,
        snapshot=snapshot,
        features=features,
        memory=memory,
        prompt_version=prompt_version,
        evolution_guidance=evolution_guidance,
        feedback_summary=feedback_summary,
        backtest_context=backtest_context,
        pattern_summary=pattern_summary,
    )

    # 9. LLM 调用
    recap = None
    rendered_md = None
    rendered_wechat = None
    tokens = LlmTokens()
    err: Optional[str] = None

    if req.force_llm:
        try:
            recap, tokens = call_llm(
                settings=settings,
                mode=req.mode,
                messages=messages,
                model_spec=req.model,
            )
            rendered_md = render_markdown(recap)
            rendered_wechat = render_wechat_text(recap)
        except Exception as e:
            err = str(e)
            logger.error(_stable_json({"event": "generate_failed", "error": err}))

    # 10. 自动评测
    eval_obj = auto_eval(recap, snapshot, features)

    latency_ms = int((time.time() - t0) * 1000)

    # 11. 持久化
    insert_run(
        settings.db_path,
        request_id=request_id,
        created_at=_utc_now_iso(),
        mode=req.mode,
        provider=req.provider,
        date=snapshot.date,
        prompt_version=prompt_version,
        model=model_effective(settings, req.model) if req.force_llm else None,
        snapshot=snapshot,
        features=features,
        recap=recap,
        rendered_markdown=rendered_md,
        rendered_wechat_text=rendered_wechat,
        eval_obj=eval_obj,
        error=err,
        latency_ms=latency_ms,
        tokens=tokens,
    )

    # 12. 进化检查（异步触发，不阻断响应）
    try:
        check_and_run_evolution(
            settings.db_path,
            settings=settings,
            trigger_run_id=request_id,
            force=False,
        )
    except Exception as e:
        logger.warning(_stable_json({"event": "evolution_check_failed", "error": str(e)}))

    # 13. 推送
    push_result: Optional[bool] = None
    if settings.push_enabled and settings.wxwork_webhook_url and recap is not None:
        from stock_recap.push.wechat import push_wechat_work
        try:
            push_result = push_wechat_work(
                webhook_url=settings.wxwork_webhook_url,
                recap=recap,
                fallback_text=settings.push_fallback_text,
            )
        except Exception as e:
            logger.warning(_stable_json({"event": "push_failed", "error": str(e)}))
            push_result = False

    # 14. 异步回测（非阻断）
    if req.mode == "daily" and recap is not None:
        _try_run_backtest(settings.db_path, snapshot.date)

    return GenerateResponse(
        request_id=request_id,
        created_at=_utc_now_iso(),
        prompt_version=prompt_version,
        model=model_effective(settings, req.model) if req.force_llm else None,
        provider=req.provider,
        snapshot=snapshot,
        features=features,
        recap=recap,
        rendered_markdown=rendered_md,
        rendered_wechat_text=rendered_wechat,
        eval=eval_obj,
        memory_used=[
            {"date": m.get("date"), "prompt_version": m.get("prompt_version")}
            for m in memory
        ],
        push_result=push_result,
    )


def _try_run_backtest(db_path: str, today: str) -> None:
    """检查并执行昨日策略回测（静默失败）。"""
    try:
        strategy_date = get_pending_backtest(db_path, today)
        if strategy_date is None:
            return

        # 取昨日策略 recap
        from stock_recap.db import load_recent_runs
        from stock_recap.models import RecapStrategy

        runs = load_recent_runs(db_path, today, "strategy", limit=1)
        if not runs or not runs[0].get("recap"):
            return

        recap_data = runs[0]["recap"]
        strategy_recap = RecapStrategy.model_validate(recap_data)

        # 取今日日终快照（实际行情）
        today_snapshot = collect_snapshot("live", today, skip_trading_check=True)

        result = compute_backtest(
            strategy_date=strategy_date,
            strategy_recap=strategy_recap,
            actual_date=today,
            actual_snapshot=today_snapshot,
        )

        insert_backtest(db_path, result=result, created_at=_utc_now_iso())
        logger.info(
            _stable_json(
                {
                    "event": "backtest_complete",
                    "strategy_date": strategy_date,
                    "hit_rate": result.hit_rate,
                }
            )
        )
    except Exception as e:
        logger.warning(_stable_json({"event": "backtest_failed", "error": str(e)}))


def load_recent_backtests_simple(db_path: str, limit: int = 3) -> list:
    """简化版回测历史加载（供 generate_once 内部使用）。"""
    try:
        from stock_recap.db import load_recent_backtests
        return load_recent_backtests(db_path, limit=limit)
    except Exception:
        return []
