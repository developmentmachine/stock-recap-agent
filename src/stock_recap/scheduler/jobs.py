"""APScheduler 调度层。

调度策略：
- 15:30 — 日终复盘（daily recap）
- 15:35 — 次日策略（strategy）
- 15:40 — 昨日策略回测（backtest）

每个 job 执行前先检查当日是否为交易日，非交易日自动跳过。
调度器通过 FastAPI lifespan 集成，start_scheduler() 返回后台调度器实例。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from stock_recap.settings import Settings

logger = logging.getLogger("stock_recap.scheduler")


def _is_trading_today() -> bool:
    from stock_recap.data.calendar import is_trading_day
    today = datetime.now().strftime("%Y-%m-%d")
    return is_trading_day(today)


def _stable_json(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _run_daily_recap(settings: Settings) -> None:
    """日终复盘 Job。"""
    if not _is_trading_today():
        logger.info(_stable_json({"event": "scheduler_skip", "job": "daily_recap", "reason": "non_trading_day"}))
        return

    logger.info(_stable_json({"event": "scheduler_start", "job": "daily_recap"}))
    try:
        from stock_recap.core import generate_once
        from stock_recap.models import GenerateRequest
        from stock_recap.db import init_db

        init_db(settings.db_path)
        req = GenerateRequest(mode="daily", provider="live", force_llm=True)
        resp = generate_once(req, settings)
        logger.info(
            _stable_json(
                {
                    "event": "scheduler_done",
                    "job": "daily_recap",
                    "request_id": resp.request_id,
                    "eval_ok": resp.eval.get("ok"),
                    "push_result": resp.push_result,
                }
            )
        )
        # 写文件
        if resp.rendered_markdown:
            _write_output(settings.output_dir, resp.snapshot.date, "daily", resp.rendered_markdown, resp.rendered_wechat_text)
    except Exception as e:
        logger.error(_stable_json({"event": "scheduler_error", "job": "daily_recap", "error": str(e)}))


def _run_strategy(settings: Settings) -> None:
    """次日策略 Job。"""
    if not _is_trading_today():
        logger.info(_stable_json({"event": "scheduler_skip", "job": "strategy", "reason": "non_trading_day"}))
        return

    logger.info(_stable_json({"event": "scheduler_start", "job": "strategy"}))
    try:
        from stock_recap.core import generate_once
        from stock_recap.models import GenerateRequest
        from stock_recap.db import init_db

        init_db(settings.db_path)
        req = GenerateRequest(mode="strategy", provider="live", force_llm=True)
        resp = generate_once(req, settings)
        logger.info(
            _stable_json(
                {
                    "event": "scheduler_done",
                    "job": "strategy",
                    "request_id": resp.request_id,
                }
            )
        )
        if resp.rendered_markdown:
            _write_output(settings.output_dir, resp.snapshot.date, "strategy", resp.rendered_markdown, resp.rendered_wechat_text)
    except Exception as e:
        logger.error(_stable_json({"event": "scheduler_error", "job": "strategy", "error": str(e)}))


def _run_backtest(settings: Settings) -> None:
    """回测 Job（对昨日策略做实际命中率评估）。"""
    if not _is_trading_today():
        return

    logger.info(_stable_json({"event": "scheduler_start", "job": "backtest"}))
    try:
        from stock_recap.core import _try_run_backtest

        today = datetime.now().strftime("%Y-%m-%d")
        _try_run_backtest(settings.db_path, today)
        logger.info(_stable_json({"event": "scheduler_done", "job": "backtest"}))
    except Exception as e:
        logger.error(_stable_json({"event": "scheduler_error", "job": "backtest", "error": str(e)}))


def _write_output(
    output_dir: str,
    date: str,
    mode: str,
    markdown: str,
    wechat_text: Optional[str],
) -> None:
    import os
    os.makedirs(output_dir, exist_ok=True)
    base = f"recap_{date}_{mode}"
    md_path = os.path.join(output_dir, base + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    if wechat_text:
        wechat_path = os.path.join(output_dir, base + "_wechat.txt")
        with open(wechat_path, "w", encoding="utf-8") as f:
            f.write(wechat_text)
    logger.info(_stable_json({"event": "file_written", "md": md_path}))


def start_scheduler(settings: Settings) -> Any:
    """
    创建并启动 APScheduler BackgroundScheduler。
    返回 scheduler 实例供 FastAPI lifespan 管理生命周期。
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    scheduler.add_job(
        _run_daily_recap,
        CronTrigger(
            day_of_week="mon-fri",
            hour=settings.scheduler_daily_hour,
            minute=settings.scheduler_daily_minute,
        ),
        id="daily_recap",
        args=[settings],
        replace_existing=True,
    )

    scheduler.add_job(
        _run_strategy,
        CronTrigger(
            day_of_week="mon-fri",
            hour=settings.scheduler_daily_hour,
            minute=settings.scheduler_strategy_minute,
        ),
        id="daily_strategy",
        args=[settings],
        replace_existing=True,
    )

    scheduler.add_job(
        _run_backtest,
        CronTrigger(
            day_of_week="mon-fri",
            hour=settings.scheduler_daily_hour,
            minute=settings.scheduler_backtest_minute,
        ),
        id="daily_backtest",
        args=[settings],
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        _stable_json(
            {
                "event": "scheduler_started",
                "daily_recap": f"{settings.scheduler_daily_hour}:{settings.scheduler_daily_minute:02d}",
                "strategy": f"{settings.scheduler_daily_hour}:{settings.scheduler_strategy_minute:02d}",
                "backtest": f"{settings.scheduler_daily_hour}:{settings.scheduler_backtest_minute:02d}",
            }
        )
    )
    return scheduler
