"""记忆管理 + 进化闭环。

核心功能：
1. load_recent_memory   — 取历史 recap 注入 LLM context
2. load_feedback_summary — 聚合用户反馈
3. extract_market_patterns — 用 LLM 提炼近期市场规律
4. run_evolution_cycle  — 高级进化：LLM 分析自身历史质量并产出改进建议

PROMPT_VERSION 管理：
- 基础版本来自 resources/prompts manifest（PROMPT_BASE_VERSION）
- 进化触发后，如 LLM 建议 bump，则自动递增 v1/v2/v3...
- ★ 活跃版本以 ``prompt_state`` 表（单行）为跨进程事实源；
  各 worker 只做短 TTL 的本地缓存，避免在 multi-worker 下漂移。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from stock_recap.infrastructure.persistence.db import (
    count_runs_since_last_evolution,
    get_active_prompt_version,
    insert_evolution_note,
    load_evolution_history,
    load_feedback_summary,
    load_latest_evolution_note,
    load_recent_runs,
    load_runs_for_evolution,
    set_active_prompt_version,
)
from stock_recap.infrastructure.llm.prompts import PROMPT_BASE_VERSION, pattern_extraction_system
from stock_recap.domain.models import EvolutionNote, Features, MarketSnapshot, Mode

logger = logging.getLogger("stock_recap.memory")

# ─── 本地 TTL 缓存（减少每次 /metrics、/healthz 对 DB 的查询） ────────────────────
_PROMPT_VERSION_CACHE_TTL_S = 5.0
_cache_lock = threading.Lock()
_cached_version: Optional[str] = None
_cached_db_path: Optional[str] = None
_cached_at: float = 0.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ─── PROMPT_VERSION 管理（DB 事实源 + 短 TTL 缓存） ──────────────────────────────

def _default_initial_version() -> str:
    return f"{PROMPT_BASE_VERSION}.v1"


def _resolve_prompt_version(db_path: str) -> str:
    """直接从 DB 解析活跃版本；prompt_state 为空则回退到最新 evolution_note，最后回退到 base 版本。"""
    ver = get_active_prompt_version(db_path)
    if ver:
        return ver

    note = load_latest_evolution_note(db_path)
    if note and note.get("prompt_version_suggested"):
        recovered = note["prompt_version_suggested"]
        # 把老库的 evolution_notes 数据回填到 prompt_state，让后续访问走快路径
        try:
            set_active_prompt_version(db_path, recovered, updated_at=_utc_now_iso())
        except Exception as e:
            logger.warning(_stable_json({"event": "prompt_state_backfill_failed", "error": str(e)}))
        return recovered

    initial = _default_initial_version()
    try:
        set_active_prompt_version(db_path, initial, updated_at=_utc_now_iso())
    except Exception as e:
        logger.warning(_stable_json({"event": "prompt_state_init_failed", "error": str(e)}))
    return initial


def get_prompt_version(db_path: str) -> str:
    """获取当前活跃的 PROMPT_VERSION。

    线程安全；本地缓存 TTL=5s，过期后从 DB 重新解析；这保证：
    - 单 worker 下高频健康检查不会反复打 DB；
    - 多 worker 下 5s 内必定收敛到 DB 事实源。
    """
    global _cached_version, _cached_db_path, _cached_at

    now = time.monotonic()
    with _cache_lock:
        if (
            _cached_version is not None
            and _cached_db_path == db_path
            and (now - _cached_at) < _PROMPT_VERSION_CACHE_TTL_S
        ):
            return _cached_version

    # 出锁后查 DB（避免 DB 慢时阻塞其他读者）
    version = _resolve_prompt_version(db_path)

    with _cache_lock:
        _cached_version = version
        _cached_db_path = db_path
        _cached_at = time.monotonic()
    return version


def _bump_prompt_version(current: str) -> str:
    parts = current.rsplit(".v", 1)
    if len(parts) == 2:
        try:
            n = int(parts[1])
            return f"{parts[0]}.v{n + 1}"
        except ValueError:
            pass
    return current + ".v2"


def _set_prompt_version(db_path: str, version: str) -> None:
    """原子写入 prompt_state 并失效本地缓存。"""
    global _cached_version, _cached_db_path, _cached_at
    set_active_prompt_version(db_path, version, updated_at=_utc_now_iso())
    with _cache_lock:
        _cached_version = version
        _cached_db_path = db_path
        _cached_at = time.monotonic()
    logger.info(_stable_json({"event": "prompt_version_bumped", "new_version": version}))


def _invalidate_prompt_version_cache() -> None:
    """测试辅助：清空本地缓存，强制下次访问重新查 DB。"""
    global _cached_version, _cached_db_path, _cached_at
    with _cache_lock:
        _cached_version = None
        _cached_db_path = None
        _cached_at = 0.0


# ─── 基础记忆加载 ──────────────────────────────────────────────────────────────

def load_recent_memory(
    db_path: str,
    date: str,
    mode: Mode,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """取历史 recap 列表注入 LLM context（仅取摘要，避免 prompt 过长）。"""
    runs = load_recent_runs(db_path, date, mode, limit)
    result = []
    for run in runs:
        recap = run.get("recap") or {}
        # 只保留关键字段，减少 token 消耗
        summary: Dict[str, Any] = {
            "date": run["date"],
            "mode": run["mode"],
            "prompt_version": run["prompt_version"],
        }
        if run["mode"] == "daily" and "sections" in recap:
            summary["conclusions"] = [
                {"title": s.get("title"), "core_conclusion": s.get("core_conclusion")}
                for s in recap.get("sections", [])
            ]
        elif run["mode"] == "strategy" and "mainline_focus" in recap:
            summary["mainline_focus"] = recap.get("mainline_focus", [])

        # 附加评测结果
        if run.get("eval"):
            summary["eval_ok"] = run["eval"].get("ok")

        result.append(summary)
    return result


# ─── 市场模式提炼 ─────────────────────────────────────────────────────────────

def extract_market_patterns(
    db_path: str,
    days: int,
    settings: Any,
    model_spec: Optional[str] = None,
) -> Optional[str]:
    """
    调用 LLM（小模型）从近 N 天复盘中提炼持续性市场规律。
    返回一段文字描述，注入当天 prompt 作为背景上下文。
    若提炼失败则返回 None（不阻断主流程）。

    路由策略：仅在『有效 backend = openai 且 openai_api_key 已配置』时尝试；
    其余情况（用户选了 gemini-cli/cursor-cli/ollama）直接跳过，避免无谓的
    『Model Not Exist』报错与重试浪费。
    """
    from stock_recap.infrastructure.llm.backends import (
        call_llm,
        llm_backend_effective,
    )

    eff_backend = llm_backend_effective(model_spec, settings)
    if eff_backend != "openai" or not getattr(settings, "openai_api_key", None):
        logger.info(_stable_json({
            "event": "pattern_extraction_skipped_backend",
            "backend": eff_backend,
        }))
        return None

    runs = load_recent_runs(db_path, _today_str(), "daily", days)
    if len(runs) < 3:
        return None  # 历史数据不足，不提炼

    summaries = []
    for run in runs:
        recap = run.get("recap") or {}
        if run["mode"] == "daily" and "sections" in recap:
            for sec in recap.get("sections", []):
                summaries.append(f"{run['date']} {sec.get('title')}: {sec.get('core_conclusion')}")

    if not summaries:
        return None

    messages = [
        {
            "role": "system",
            "content": pattern_extraction_system(),
        },
        {"role": "user", "content": "\n".join(summaries[-30:])},  # 最多取30条
    ]

    try:
        recap_obj, _ = call_llm(
            settings=settings,
            mode="daily",
            messages=messages,
            model_spec=None,
        )
        # 这里我们不用 Recap schema，直接拿原始文本
        # 所以改用底层调用
        raise NotImplementedError  # 触发 except 走文本路径
    except Exception:
        pass

    # 直接用 openai/ollama 原始调用（不走 Recap schema 校验）
    try:
        from stock_recap.infrastructure.llm.backends import _stable_json as sj
        import httpx
        from openai import OpenAI

        if not settings.openai_api_key:
            return None

        client = OpenAI(api_key=settings.openai_api_key)
        resp = client.chat.completions.create(
            model=settings.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.3,
            max_tokens=400,
            timeout=30,
        )
        pattern_text = (resp.choices[0].message.content or "").strip()
        if pattern_text:
            logger.info(_stable_json({"event": "patterns_extracted", "chars": len(pattern_text)}))
            return pattern_text
    except Exception as e:
        logger.warning(_stable_json({"event": "pattern_extraction_failed", "error": str(e)}))

    return None


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ─── 进化注入（读取最新笔记注入 system prompt） ──────────────────────────────────

def load_evolution_guidance(db_path: str) -> Optional[str]:
    """读取最新进化笔记，提炼为可注入 system prompt 的指导文字。"""
    note = load_latest_evolution_note(db_path)
    if not note:
        return None
    notes_data = note.get("notes") or {}
    parts = []
    if notes_data.get("problems"):
        parts.append("【需要改进】" + "；".join(notes_data["problems"][:3]))
    if notes_data.get("prompt_suggestions"):
        parts.append("【写作建议】" + "；".join(notes_data["prompt_suggestions"][:3]))
    if notes_data.get("praised_patterns"):
        parts.append("【请保持】" + "；".join(notes_data["praised_patterns"][:2]))
    return "\n".join(parts) if parts else None


# ─── 进化循环（核心） ─────────────────────────────────────────────────────────

def check_and_run_evolution(
    db_path: str,
    settings: Any,
    trigger_run_id: Optional[str] = None,
    force: bool = False,
    model_spec: Optional[str] = None,
) -> Optional[str]:
    """
    检查是否满足进化触发条件，若满足则运行一次进化分析。

    触发条件（满足任一）：
    1. force=True（手动触发）
    2. 收到低评分（调用方判断后 force=True 传入）
    3. 自上次进化后累计新运行次数 >= evolution_min_runs

    返回：新的 prompt_version（如有版本升级），否则返回 None。
    """
    if not settings.evolution_enabled:
        return None

    # 进化目前完全依赖 OpenAI structured outputs（client.beta.chat.completions.parse），
    # 当用户主动选择非 openai backend 时直接跳过，避免 'Model Not Exist' 多次重试浪费。
    from stock_recap.infrastructure.llm.backends import llm_backend_effective

    eff_backend = llm_backend_effective(model_spec, settings)
    if eff_backend != "openai" or not getattr(settings, "openai_api_key", None):
        logger.info(_stable_json({
            "event": "evolution_skipped_backend",
            "backend": eff_backend,
        }))
        return None

    if not force:
        since_last = count_runs_since_last_evolution(db_path)
        if since_last < settings.evolution_min_runs:
            logger.debug(
                _stable_json(
                    {
                        "event": "evolution_skipped",
                        "since_last": since_last,
                        "threshold": settings.evolution_min_runs,
                    }
                )
            )
            return None

    logger.info(_stable_json({"event": "evolution_started", "trigger": trigger_run_id}))
    try:
        return _run_evolution(db_path, settings, trigger_run_id)
    except Exception as e:
        logger.warning(_stable_json({"event": "evolution_failed", "error": str(e)}))
        return None


def _run_evolution(
    db_path: str,
    settings: Any,
    trigger_run_id: Optional[str],
) -> Optional[str]:
    """实际执行进化分析：调用 LLM 分析历史质量，产出 EvolutionNote。"""
    from openai import OpenAI

    runs = load_runs_for_evolution(db_path, limit=20)
    feedback_summary = load_feedback_summary(db_path, limit=30)
    evo_history = load_evolution_history(db_path, limit=3)

    if not runs:
        logger.info(_stable_json({"event": "evolution_no_data"}))
        return None

    # 构建分析上下文
    analysis_context = {
        "recent_runs": [
            {
                "date": r["date"],
                "mode": r["mode"],
                "rating": r.get("rating"),
                "tags": r.get("tags", []),
                "comment": r.get("comment") or "",
                "eval_ok": (r.get("eval") or {}).get("ok"),
                "recap_summary": _recap_summary(r.get("recap")),
            }
            for r in runs
        ],
        "feedback_summary": feedback_summary,
        "previous_evolution_notes": [
            e.get("notes", {}).get("summary", "")
            for e in evo_history
        ],
    }

    system_prompt = (
        "你是一个专业的AI系统质量分析师，负责分析A股复盘智能体的历史输出质量并提出改进建议。\n"
        "请基于提供的历史运行记录（含用户评分和反馈）进行分析，输出严格符合 schema 的 JSON。\n"
        "分析要具体可操作，不要泛泛而谈。"
    )

    user_prompt = _stable_json(
        {
            "task": "分析以下历史复盘数据，产出质量改进建议",
            "context": analysis_context,
            "output_schema": EvolutionNote.model_json_schema(),
            "instruction": "仅返回 JSON，不包含任何解释文字或 markdown 代码块",
        }
    )

    if not settings.openai_api_key:
        logger.warning(_stable_json({"event": "evolution_no_api_key"}))
        return None

    client = OpenAI(api_key=settings.openai_api_key)
    try:
        resp = client.beta.chat.completions.parse(
            model=settings.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=EvolutionNote,  # type: ignore[arg-type]
            temperature=0.3,
            timeout=60,
        )
        note = resp.choices[0].message.parsed
    except Exception as e:
        logger.warning(_stable_json({"event": "evolution_llm_failed", "error": str(e)}))
        # 降级：普通 JSON 解析
        try:
            resp2 = client.chat.completions.create(
                model=settings.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                timeout=60,
            )
            content = resp2.choices[0].message.content or "{}"
            data = json.loads(content)
            note = EvolutionNote.model_validate(data)
        except Exception as e2:
            logger.warning(_stable_json({"event": "evolution_fallback_failed", "error": str(e2)}))
            return None

    current_version = get_prompt_version(db_path)
    new_version = _bump_prompt_version(current_version) if note.should_bump_version else None
    suggested_version = new_version or current_version

    insert_evolution_note(
        db_path,
        created_at=_utc_now_iso(),
        trigger_run_id=trigger_run_id,
        note=note,
        prompt_version_suggested=suggested_version,
    )

    if new_version:
        _set_prompt_version(db_path, new_version)
        logger.info(
            _stable_json(
                {
                    "event": "evolution_complete",
                    "version_bumped": True,
                    "new_version": new_version,
                    "summary": note.summary[:100],
                }
            )
        )
        return new_version
    else:
        logger.info(
            _stable_json(
                {
                    "event": "evolution_complete",
                    "version_bumped": False,
                    "summary": note.summary[:100],
                }
            )
        )
        return None


def _recap_summary(recap: Optional[Dict[str, Any]]) -> str:
    """从 recap dict 提取简短摘要（避免传太多 token 给进化分析）。"""
    if not recap:
        return ""
    mode = recap.get("mode", "")
    if mode == "daily":
        sections = recap.get("sections", [])
        return " | ".join(
            s.get("core_conclusion", "") for s in sections[:3]
        )
    elif mode == "strategy":
        focus = recap.get("mainline_focus", [])
        return "主线: " + ", ".join(focus[:5])
    return ""
