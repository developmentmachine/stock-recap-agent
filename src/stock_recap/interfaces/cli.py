"""CLI 入口：完整命令行支持。

基础命令（与原版兼容）：
  uv run -m stock_recap --mode daily --provider live
  uv run -m stock_recap --mode strategy --provider akshare
  uv run -m stock_recap --serve --host 0.0.0.0 --port 8000
  uv run -m stock_recap --dry-run --provider mock

新增命令：
  uv run -m stock_recap --evolve            手动触发进化周期
  uv run -m stock_recap --backtest          手动回测昨日策略
  uv run -m stock_recap --push-test         测试企业微信推送
  uv run -m stock_recap --history           查看最近运行历史
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Optional

import uvicorn

from stock_recap.application.recap import generate_once, _try_run_backtest
from stock_recap.infrastructure.data.collector import collect_snapshot
from stock_recap.infrastructure.data.features import build_features
from stock_recap.infrastructure.persistence.db import init_db, load_feedback_summary, load_history
from stock_recap.infrastructure.llm.backends import llm_backend_effective, model_effective
from stock_recap.infrastructure.llm.prompts import build_messages
from stock_recap.application.memory.manager import (
    check_and_run_evolution,
    get_prompt_version,
    load_evolution_guidance,
    load_recent_memory,
)
from stock_recap.domain.models import GenerateRequest
from stock_recap.infrastructure.push.wechat import test_push
from stock_recap.interfaces.scheduler.jobs import start_scheduler
from stock_recap.config.settings import Settings, get_settings


def _setup_logger(level: str) -> logging.Logger:
    """CLI 入口走统一的 JSON 结构化日志（带 trace_id/request_id 注入）。

    保留旧函数名 + 返回 ``stock_recap`` logger 仅是为了对调用方零侵入。
    """
    from stock_recap.observability.logging_setup import setup_structured_logging

    setup_structured_logging(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
    )
    return logging.getLogger("stock_recap")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def cli_main() -> int:
    parser = argparse.ArgumentParser(
        description="企业级 A 股复盘/策略智能体",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # mock 数据快速测试（无需网络/API key）
  uv run -m stock_recap --mode daily --provider mock

  # 实盘日终复盘（需要 OPENAI_API_KEY）
  uv run -m stock_recap --mode daily --provider live

  # 次日策略
  uv run -m stock_recap --mode strategy --provider live

  # 启动 API 服务（含调度器）
  RECAP_SCHEDULER_ENABLED=true uv run -m stock_recap --serve

  # 干跑：查看将发给 LLM 的 payload
  uv run -m stock_recap --dry-run --provider mock

  # 测试企业微信推送
  RECAP_WXWORK_WEBHOOK_URL=https://... uv run -m stock_recap --push-test

  # 手动触发进化分析
  uv run -m stock_recap --evolve

  # 手动回测昨日策略
  uv run -m stock_recap --backtest

  # 查看运行历史
  uv run -m stock_recap --history
""",
    )

    # 主操作（互斥组）
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("--serve", action="store_true", help="启动 API 服务（含调度器）")
    action_group.add_argument("--dry-run", action="store_true", help="仅打印 LLM payload，不调用")
    action_group.add_argument("--evolve", action="store_true", help="手动触发进化分析")
    action_group.add_argument("--backtest", action="store_true", help="手动回测昨日策略")
    action_group.add_argument("--push-test", action="store_true", help="测试企业微信推送配置")
    action_group.add_argument("--history", action="store_true", help="查看最近运行历史")
    action_group.add_argument(
        "--mcp-tools",
        action="store_true",
        help="启动 MCP stdio 工具服务（与进程内 function calling 语义一致）",
    )

    # 生成参数
    parser.add_argument("--mode", choices=["daily", "strategy"], default="daily")
    parser.add_argument(
        "--provider",
        type=str,
        default="live",
        metavar="ID",
        help="行情采集源：mock / live / akshare，或已注册的自定义 id",
    )
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD，默认今天")
    parser.add_argument("--no-llm", action="store_true", help="不调用 LLM，仅采集+落库")
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="模型表达：openai:<m> / ollama:<m> / cursor-cli（兼容 cursor-agent）",
    )
    parser.add_argument("--skip-trading-check", action="store_true", help="跳过交易日检查")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录（默认 RECAP_OUTPUT_DIR 或当前目录）")
    parser.add_argument("--no-write-files", action="store_true", help="不写文件，仅 stdout")

    # 后端覆盖
    parser.add_argument("--ollama-base-url", type=str, default=None)
    parser.add_argument(
        "--cursor-cli-cmd",
        type=str,
        default=None,
        help="Cursor CLI 启动命令及参数前缀（官方为 agent），覆盖 RECAP_CURSOR_CLI_CMD",
    )
    parser.add_argument(
        "--cursor-agent-cmd",
        type=str,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--cursor-timeout-s", type=int, default=None)

    # 服务参数
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)

    # 历史参数
    parser.add_argument("--limit", type=int, default=10, help="历史记录数量")

    args = parser.parse_args()

    from stock_recap.infrastructure.data.collector import list_data_provider_ids

    _pid = (args.provider or "").strip().lower()
    _allowed = set(list_data_provider_ids())
    if _pid not in _allowed:
        parser.error(
            f"未知 --provider {args.provider!r}；可用: {', '.join(sorted(_allowed))}"
        )
    args.provider = _pid

    # ── 初始化 ──────────────────────────────────────────────────────────────────
    settings = get_settings()

    # CLI 覆盖 settings
    if args.ollama_base_url:
        settings.ollama_base_url = args.ollama_base_url
    if args.cursor_cli_cmd:
        settings.cursor_cli_cmd = args.cursor_cli_cmd
    elif args.cursor_agent_cmd:
        settings.cursor_cli_cmd = args.cursor_agent_cmd
    if args.cursor_timeout_s is not None:
        settings.cursor_timeout_s = int(args.cursor_timeout_s)
    if args.output_dir:
        settings.output_dir = args.output_dir

    # MCP stdio 必须独占 stdout；先于其他日志/init，避免污染 JSON-RPC 流
    if args.mcp_tools:
        from stock_recap.observability.tracing import configure_tracing
        from stock_recap.interfaces.mcp_stdio import run_mcp_stdio

        configure_tracing(settings)
        init_db(settings.db_path)
        run_mcp_stdio()
        return 0

    logger = _setup_logger(settings.log_level)

    from stock_recap.observability.tracing import configure_tracing

    configure_tracing(settings)

    init_db(settings.db_path)

    # ── 服务模式 ──────────────────────────────────────────────────────────────
    if args.serve:
        return _cmd_serve(settings, logger, args)

    # ── 推送测试 ────────────────────────────────────────────────────────────
    if args.push_test:
        return _cmd_push_test(settings, logger)

    # ── 进化触发 ────────────────────────────────────────────────────────────
    if args.evolve:
        return _cmd_evolve(settings, logger)

    # ── 手动回测 ────────────────────────────────────────────────────────────
    if args.backtest:
        return _cmd_backtest(settings, logger, args)

    # ── 历史查看 ────────────────────────────────────────────────────────────
    if args.history:
        return _cmd_history(settings, logger, args)

    # ── 主生成流程 ──────────────────────────────────────────────────────────
    return _cmd_generate(settings, logger, args)


# ─── 子命令实现 ────────────────────────────────────────────────────────────────

def _cmd_serve(settings: Any, logger: logging.Logger, args: argparse.Namespace) -> int:
    from stock_recap.interfaces.api.routes import app

    scheduler = None
    if settings.scheduler_enabled:
        scheduler = start_scheduler(settings)
        logger.info(_stable_json({"event": "scheduler_enabled"}))

    logger.info(_stable_json({"event": "server_start", "host": args.host, "port": args.port}))
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level=settings.log_level.lower())
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
    return 0


def _cmd_push_test(settings: Any, logger: logging.Logger) -> int:
    if not settings.wxwork_webhook_url:
        print("错误：未配置 RECAP_WXWORK_WEBHOOK_URL", file=sys.stderr)
        return 1
    ok = test_push(settings.wxwork_webhook_url)
    if ok:
        print("企业微信推送测试成功")
        return 0
    else:
        print("企业微信推送测试失败，请检查 Webhook URL", file=sys.stderr)
        return 1


def _cmd_evolve(settings: Any, logger: logging.Logger) -> int:
    print("正在执行进化分析...")
    new_version = check_and_run_evolution(
        settings.db_path, settings=settings, force=True
    )
    if new_version:
        print(f"进化完成，新版本：{new_version}")
    else:
        print("进化分析完成（无版本升级）")
    return 0


def _cmd_backtest(settings: Any, logger: logging.Logger, args: argparse.Namespace) -> int:
    today = args.date or _today_str()
    print(f"正在回测昨日策略（相对于 {today}）...")
    _try_run_backtest(settings.db_path, today)
    print("回测完成，查看数据库或使用 --history 查看结果")
    return 0


def _cmd_history(settings: Any, logger: logging.Logger, args: argparse.Namespace) -> int:
    items = load_history(settings.db_path, limit=args.limit)
    print(f"\n最近 {len(items)} 条运行记录：\n")
    for item in items:
        status = "✓" if item["error"] is None else "✗"
        print(
            f"  {status} [{item['date']}] {item['mode']} | {item['provider']} | "
            f"{item['latency_ms']}ms | v{item['prompt_version']} | {item['created_at']}"
        )
        if item["error"]:
            print(f"    错误：{item['error'][:80]}")
    return 0


def _cmd_generate(settings: Any, logger: logging.Logger, args: argparse.Namespace) -> int:
    req = GenerateRequest(
        mode=args.mode,
        provider=args.provider,
        date=args.date,
        force_llm=not args.no_llm,
        model=args.model,
        skip_trading_check=args.skip_trading_check,
    )

    # dry-run：仅打印 LLM payload
    if args.dry_run:
        snapshot = collect_snapshot(req.provider, req.date, skip_trading_check=req.skip_trading_check)
        features = build_features(snapshot)
        memory = load_recent_memory(settings.db_path, snapshot.date, req.mode)
        prompt_version = get_prompt_version(settings.db_path)
        evolution_guidance = load_evolution_guidance(settings.db_path)
        feedback_summary = load_feedback_summary(settings.db_path)
        messages = build_messages(
            mode=req.mode,
            snapshot=snapshot,
            features=features,
            memory=memory,
            prompt_version=prompt_version,
            evolution_guidance=evolution_guidance,
            feedback_summary=feedback_summary,
            skill_id_override=settings.skill_id_override,
        )
        print(
            _stable_json(
                {
                    "llm_backend": llm_backend_effective(req.model, settings),
                    "model": model_effective(settings, req.model),
                    "messages": messages,
                }
            )
        )
        return 0

    # 正式生成
    resp = generate_once(req, settings)

    if resp.recap is None:
        if args.no_llm:
            print(_stable_json(resp.model_dump()))
            return 0
        logger.error(_stable_json({"event": "generate_failed", "request_id": resp.request_id}))
        print(_stable_json(resp.model_dump()), file=sys.stderr)
        return 2

    # 打印 Markdown 到 stdout
    print(resp.rendered_markdown or _stable_json(resp.model_dump()))

    # 写文件
    if not args.no_write_files:
        output_dir = args.output_dir or settings.output_dir
        os.makedirs(output_dir, exist_ok=True)
        base = f"recap_{resp.snapshot.date}_{req.mode}"
        md_path = os.path.join(output_dir, base + ".md")
        wechat_path = os.path.join(output_dir, base + "_wechat.txt")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(resp.rendered_markdown or "")
        if resp.rendered_wechat_text:
            with open(wechat_path, "w", encoding="utf-8") as f:
                f.write(resp.rendered_wechat_text)

        logger.info(_stable_json({"event": "files_written", "md": md_path, "wechat": wechat_path}))

    # 推送状态
    if resp.push_result is not None:
        status = "成功" if resp.push_result else "失败"
        logger.info(_stable_json({"event": "push_result", "status": status}))

    return 0
