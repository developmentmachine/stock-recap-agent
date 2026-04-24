"""SQLite 数据访问层。

生产特性：
- WAL 模式（并发读写安全）
- busy_timeout=5000（避免锁等待直接报错）
- contextmanager 统一管理连接生命周期
- 所有表结构 + 增量 ALTER TABLE 迁移
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional, Tuple

from stock_recap.domain.models import (
    BacktestResult,
    EvolutionNote,
    Features,
    LlmTokens,
    MarketSnapshot,
    MetricsSnapshot,
    Mode,
    Provider,
    Recap,
)


# ─── 连接管理 ───────────────────────────────────────────────────────────────────

# 内存数据库单例（:memory: 模式下所有调用共享同一连接）
_memory_conn: Optional[sqlite3.Connection] = None


def _get_memory_conn() -> sqlite3.Connection:
    global _memory_conn
    if _memory_conn is None:
        _memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
        _memory_conn.row_factory = sqlite3.Row
    return _memory_conn


@contextmanager
def get_conn(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    if db_path == ":memory:":
        conn = _get_memory_conn()
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        # 不关闭内存连接
        return

    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── 初始化 / 迁移 ─────────────────────────────────────────────────────────────

def init_db(db_path: str) -> None:
    """创建所有表（如不存在），并执行增量 ALTER TABLE 迁移。"""
    with get_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS recap_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              request_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              mode TEXT NOT NULL,
              provider TEXT NOT NULL,
              date TEXT NOT NULL,
              prompt_version TEXT NOT NULL,
              model TEXT,
              snapshot_json TEXT NOT NULL,
              features_json TEXT NOT NULL,
              recap_json TEXT,
              rendered_markdown TEXT,
              rendered_wechat_text TEXT,
              eval_json TEXT,
              error TEXT,
              latency_ms INTEGER,
              tokens_json TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_recap_runs_request_id
              ON recap_runs(request_id);
            CREATE INDEX IF NOT EXISTS idx_recap_runs_date_mode
              ON recap_runs(date, mode);

            CREATE TABLE IF NOT EXISTS recap_feedback (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              request_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              rating INTEGER NOT NULL,
              tags_json TEXT NOT NULL,
              comment TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_feedback_request_id
              ON recap_feedback(request_id);

            CREATE TABLE IF NOT EXISTS evolution_notes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              trigger_run_id TEXT,
              notes_json TEXT NOT NULL,
              prompt_version_suggested TEXT,
              applied INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS backtest_results (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              strategy_date TEXT NOT NULL,
              actual_date TEXT NOT NULL,
              hit_count INTEGER NOT NULL,
              hit_rate REAL NOT NULL,
              predicted_sectors_json TEXT NOT NULL,
              actual_top_sectors_json TEXT NOT NULL,
              detail TEXT,
              created_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_backtest_dates
              ON backtest_results(strategy_date, actual_date);

            /*
             * prompt_state：全局活跃 Prompt 版本（单行）。
             * 作为跨进程的「事实源」，避免 uvicorn 多 worker 下
             * 各进程活跃版本漂移。
             */
            CREATE TABLE IF NOT EXISTS prompt_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              active_version TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            /*
             * pending_actions（outbox）：副作用任务的「事务化收件箱」。
             * - 入库与业务写库共一个 SQLite 事务时，可避免「业务成功但副作用丢失」。
             * - UNIQUE(request_id, action_type) 保证幂等：同一请求同类动作只会落库一次，
             *   即使 generate 端被重试触发也不会引发多次推送/回测。
             * - status: pending | running | done | failed
             * - next_attempt_at: 指数退避后的下次可调度时间（ISO UTC，便于 ORDER BY 文本比较）
             * - last_error: 失败原因（最后一次）
             */
            CREATE TABLE IF NOT EXISTS pending_actions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              request_id TEXT NOT NULL,
              action_type TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt_at TEXT NOT NULL,
              last_error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_actions_dedup
              ON pending_actions(request_id, action_type);
            CREATE INDEX IF NOT EXISTS idx_pending_actions_due
              ON pending_actions(status, next_attempt_at);

            /*
             * push_log：单次推送的幂等账本。
             * UNIQUE(request_id, channel) 防止同一请求被多次推送 ——
             * 重试 / 调度重启 / 多 worker / outbox 兜底等任何场景下都安全。
             *
             * 状态机：
             *   sent     —— 成功推送
             *   skipped  —— 主动跳过（disabled/no-content）
             *   failed   —— 推送失败（保留 last_error，重试时仍可命中幂等）
             *
             * 注：同一 request_id 失败后想强制重发，需要先 DELETE 这一行（人工介入）。
             */
            CREATE TABLE IF NOT EXISTS push_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              request_id TEXT NOT NULL,
              channel TEXT NOT NULL,
              status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 1,
              last_error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_push_log_dedup
              ON push_log(request_id, channel);
            """
        )

    # 增量 ALTER TABLE（老库升级兼容）
    _safe_add_column(db_path, "recap_runs", "rendered_wechat_text", "TEXT")


def _safe_add_column(db_path: str, table: str, column: str, col_type: str) -> None:
    if db_path == ":memory:":
        return  # 内存库建表时已包含所有列，无需迁移
    with get_conn(db_path) as conn:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type};")
        except sqlite3.OperationalError:
            pass  # 列已存在，忽略


# ─── recap_runs CRUD ──────────────────────────────────────────────────────────

def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def insert_run(
    db_path: str,
    *,
    request_id: str,
    created_at: str,
    mode: Mode,
    provider: Provider,
    date: str,
    prompt_version: str,
    model: Optional[str],
    snapshot: MarketSnapshot,
    features: Features,
    recap: Optional[Recap],
    rendered_markdown: Optional[str],
    rendered_wechat_text: Optional[str],
    eval_obj: Dict[str, Any],
    error: Optional[str],
    latency_ms: int,
    tokens: LlmTokens,
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO recap_runs (
              request_id, created_at, mode, provider, date, prompt_version, model,
              snapshot_json, features_json, recap_json, rendered_markdown,
              rendered_wechat_text, eval_json, error, latency_ms, tokens_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                request_id,
                created_at,
                mode,
                provider,
                date,
                prompt_version,
                model,
                _stable_json(snapshot.model_dump()),
                _stable_json(features.model_dump()),
                _stable_json(recap.model_dump()) if recap else None,
                rendered_markdown,
                rendered_wechat_text,
                _stable_json(eval_obj),
                error,
                int(latency_ms),
                _stable_json(tokens.__dict__),
            ),
        )


def load_recent_runs(
    db_path: str,
    date: str,
    mode: Mode,
    limit: int,
) -> List[Dict[str, Any]]:
    """读取 date 之前的最近 N 条成功运行记录（含 recap + eval）。"""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT created_at, date, mode, provider, prompt_version, recap_json, eval_json
            FROM recap_runs
            WHERE date < ? AND mode = ? AND recap_json IS NOT NULL
            ORDER BY date DESC, created_at DESC
            LIMIT ?
            """,
            (date, mode, limit),
        ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "created_at": row["created_at"],
                "date": row["date"],
                "mode": row["mode"],
                "provider": row["provider"],
                "prompt_version": row["prompt_version"],
                "recap": json.loads(row["recap_json"]) if row["recap_json"] else None,
                "eval": json.loads(row["eval_json"]) if row["eval_json"] else None,
            }
        )
    return result


def load_runs_for_evolution(
    db_path: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """读取最近 N 条运行记录（含 recap + eval），供进化分析用。"""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT r.request_id, r.created_at, r.date, r.mode, r.recap_json, r.eval_json,
                   f.rating, f.tags_json, f.comment
            FROM recap_runs r
            LEFT JOIN recap_feedback f ON r.request_id = f.request_id
            WHERE r.recap_json IS NOT NULL
            ORDER BY r.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "request_id": row["request_id"],
                "created_at": row["created_at"],
                "date": row["date"],
                "mode": row["mode"],
                "recap": json.loads(row["recap_json"]) if row["recap_json"] else None,
                "eval": json.loads(row["eval_json"]) if row["eval_json"] else None,
                "rating": row["rating"],
                "tags": json.loads(row["tags_json"]) if row["tags_json"] else [],
                "comment": row["comment"],
            }
        )
    return result


def count_runs_since_last_evolution(db_path: str) -> int:
    """统计上次进化后的新运行次数（用于判断是否触发进化）。"""
    with get_conn(db_path) as conn:
        last_evo = conn.execute(
            "SELECT created_at FROM evolution_notes ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if last_evo is None:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM recap_runs WHERE recap_json IS NOT NULL"
            ).fetchone()
            return int(total["cnt"]) if total else 0
        last_evo_at = last_evo["created_at"]
        cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM recap_runs WHERE recap_json IS NOT NULL AND created_at > ?",
            (last_evo_at,),
        ).fetchone()
        return int(cnt["cnt"]) if cnt else 0


# ─── recap_feedback CRUD ─────────────────────────────────────────────────────

def insert_feedback(
    db_path: str,
    *,
    request_id: str,
    created_at: str,
    rating: int,
    tags: List[str],
    comment: str,
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO recap_feedback (request_id, created_at, rating, tags_json, comment)
            VALUES (?,?,?,?,?)
            """,
            (request_id, created_at, rating, _stable_json(tags), comment),
        )


def load_feedback_summary(db_path: str, limit: int = 30) -> Dict[str, Any]:
    """聚合最近反馈：平均分、高频好评/差评 tag。"""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT rating, tags_json FROM recap_feedback
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()

    if not rows:
        return {"avg_rating": None, "low_rated_tags": [], "praise_tags": []}

    ratings = [row["rating"] for row in rows]
    avg = sum(ratings) / len(ratings)

    low_tags: Dict[str, int] = {}
    high_tags: Dict[str, int] = {}
    for row in rows:
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
        bucket = low_tags if row["rating"] <= 2 else high_tags if row["rating"] >= 4 else {}
        for t in tags:
            bucket[t] = bucket.get(t, 0) + 1

    return {
        "avg_rating": round(avg, 2),
        "low_rated_tags": sorted(low_tags, key=lambda k: -low_tags[k])[:5],
        "praise_tags": sorted(high_tags, key=lambda k: -high_tags[k])[:5],
    }


# ─── evolution_notes CRUD ────────────────────────────────────────────────────

def insert_evolution_note(
    db_path: str,
    *,
    created_at: str,
    trigger_run_id: Optional[str],
    note: EvolutionNote,
    prompt_version_suggested: Optional[str],
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO evolution_notes
              (created_at, trigger_run_id, notes_json, prompt_version_suggested, applied)
            VALUES (?,?,?,?,0)
            """,
            (
                created_at,
                trigger_run_id,
                _stable_json(note.model_dump()),
                prompt_version_suggested,
            ),
        )


def load_latest_evolution_note(db_path: str) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM evolution_notes ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "notes": json.loads(row["notes_json"]),
        "prompt_version_suggested": row["prompt_version_suggested"],
    }


def load_evolution_history(db_path: str, limit: int = 10) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM evolution_notes ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "notes": json.loads(row["notes_json"]),
            "prompt_version_suggested": row["prompt_version_suggested"],
            "applied": bool(row["applied"]),
        }
        for row in rows
    ]


# ─── backtest_results CRUD ────────────────────────────────────────────────────

def insert_backtest(
    db_path: str,
    *,
    result: BacktestResult,
    created_at: str,
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO backtest_results
              (strategy_date, actual_date, hit_count, hit_rate,
               predicted_sectors_json, actual_top_sectors_json, detail, created_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                result.strategy_date,
                result.actual_date,
                result.hit_count,
                result.hit_rate,
                _stable_json(result.predicted_sectors),
                _stable_json(result.actual_top_sectors),
                result.detail,
                created_at,
            ),
        )


def load_recent_backtests(db_path: str, limit: int = 10) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT strategy_date, actual_date, hit_count, hit_rate, detail, created_at
            FROM backtest_results
            ORDER BY actual_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_pending_backtest(db_path: str, today: str) -> Optional[str]:
    """返回有次日策略但今日尚未回测的日期（即昨日）。"""
    with get_conn(db_path) as conn:
        # 找昨日有 strategy 记录
        strategy_row = conn.execute(
            """
            SELECT date FROM recap_runs
            WHERE mode = 'strategy' AND date < ? AND recap_json IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (today,),
        ).fetchone()
        if strategy_row is None:
            return None
        strategy_date = strategy_row["date"]
        # 检查该策略日期是否已有回测
        existing = conn.execute(
            "SELECT id FROM backtest_results WHERE strategy_date = ?",
            (strategy_date,),
        ).fetchone()
        if existing is not None:
            return None
        return strategy_date


# ─── prompt_state（跨进程活跃版本） ───────────────────────────────────────────

def get_active_prompt_version(db_path: str) -> Optional[str]:
    """读取 prompt_state.active_version；未写入则返回 None。"""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT active_version FROM prompt_state WHERE id = 1"
        ).fetchone()
    if row is None:
        return None
    return row["active_version"]


def set_active_prompt_version(db_path: str, version: str, *, updated_at: str) -> None:
    """UPSERT prompt_state.active_version（幂等）。"""
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO prompt_state (id, active_version, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              active_version = excluded.active_version,
              updated_at     = excluded.updated_at
            """,
            (version, updated_at),
        )


# ─── pending_actions（outbox） ────────────────────────────────────────────────

def enqueue_pending_action(
    db_path: str,
    *,
    request_id: str,
    action_type: str,
    payload_json: str,
    now_iso: str,
) -> bool:
    """幂等入队：``UNIQUE(request_id, action_type)`` 已存在则返回 False。

    返回值：``True`` 表示新插入，``False`` 表示已经存在（不视为错误）。
    """
    with get_conn(db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO pending_actions
                  (request_id, action_type, payload_json, status, attempts,
                   next_attempt_at, last_error, created_at, updated_at)
                VALUES (?,?,?, 'pending', 0, ?, NULL, ?, ?)
                """,
                (request_id, action_type, payload_json, now_iso, now_iso, now_iso),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def claim_due_pending_actions(
    db_path: str,
    *,
    now_iso: str,
    limit: int = 16,
) -> List[Dict[str, Any]]:
    """原子地把到期的 pending 行标记为 running 并返回；避免多 worker 重复消费。"""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, request_id, action_type, payload_json, attempts
            FROM pending_actions
            WHERE status = 'pending' AND next_attempt_at <= ?
            ORDER BY next_attempt_at ASC, id ASC
            LIMIT ?
            """,
            (now_iso, int(limit)),
        ).fetchall()
        if not rows:
            return []
        ids = [int(r["id"]) for r in rows]
        placeholders = ",".join("?" for _ in ids)
        # 在同一事务里把它们抢占为 running，竞争 worker 不会重复抢到。
        conn.execute(
            f"UPDATE pending_actions SET status = 'running', updated_at = ? "
            f"WHERE id IN ({placeholders}) AND status = 'pending'",
            (now_iso, *ids),
        )
    return [dict(r) for r in rows]


def mark_pending_action_done(db_path: str, *, action_id: int, now_iso: str) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE pending_actions SET status = 'done', last_error = NULL, updated_at = ? "
            "WHERE id = ?",
            (now_iso, int(action_id)),
        )


def mark_pending_action_failed(
    db_path: str,
    *,
    action_id: int,
    now_iso: str,
    next_attempt_at_iso: Optional[str],
    last_error: str,
    final: bool,
) -> None:
    """``next_attempt_at_iso=None`` 或 ``final=True`` → 状态停在 'failed'；否则回到 'pending'。"""
    new_status = "failed" if final or not next_attempt_at_iso else "pending"
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE pending_actions
            SET status = ?,
                attempts = attempts + 1,
                next_attempt_at = COALESCE(?, next_attempt_at),
                last_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (new_status, next_attempt_at_iso, last_error, now_iso, int(action_id)),
        )


def list_pending_actions(
    db_path: str,
    *,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """供运维接口/测试观察。"""
    with get_conn(db_path) as conn:
        if status is None:
            rows = conn.execute(
                "SELECT * FROM pending_actions ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pending_actions WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, int(limit)),
            ).fetchall()
    return [dict(r) for r in rows]


# ─── push_log（推送幂等账本） ────────────────────────────────────────────────


def get_push_log(
    db_path: str, *, request_id: str, channel: str
) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM push_log WHERE request_id = ? AND channel = ?",
            (request_id, channel),
        ).fetchone()
    return dict(row) if row else None


def upsert_push_log(
    db_path: str,
    *,
    request_id: str,
    channel: str,
    status: str,
    now_iso: str,
    last_error: Optional[str] = None,
) -> None:
    """``UNIQUE(request_id, channel)`` 已存在则更新 status/attempts/last_error。"""
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO push_log
              (request_id, channel, status, attempts, last_error, created_at, updated_at)
            VALUES (?,?,?, 1, ?, ?, ?)
            ON CONFLICT(request_id, channel) DO UPDATE SET
              status = excluded.status,
              attempts = push_log.attempts + 1,
              last_error = excluded.last_error,
              updated_at = excluded.updated_at
            """,
            (request_id, channel, status, last_error, now_iso, now_iso),
        )


# ─── 指标查询 ──────────────────────────────────────────────────────────────────

def load_metrics(db_path: str, today: str, prompt_version: str) -> MetricsSnapshot:
    with get_conn(db_path) as conn:
        totals = conn.execute(
            """
            SELECT
              COUNT(*) as total,
              SUM(CASE WHEN recap_json IS NOT NULL THEN 1 ELSE 0 END) as success,
              SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as failed,
              AVG(latency_ms) as avg_latency
            FROM recap_runs
            """
        ).fetchone()
        today_row = conn.execute(
            """
            SELECT
              COUNT(*) as total,
              SUM(CASE WHEN recap_json IS NOT NULL THEN 1 ELSE 0 END) as success
            FROM recap_runs WHERE date = ?
            """,
            (today,),
        ).fetchone()
        evo_cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM evolution_notes"
        ).fetchone()
        last_run = conn.execute(
            "SELECT created_at FROM recap_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        rating_row = conn.execute(
            "SELECT AVG(rating) as avg_r FROM recap_feedback"
        ).fetchone()

    return MetricsSnapshot(
        total_runs=int(totals["total"] or 0),
        success_runs=int(totals["success"] or 0),
        failed_runs=int(totals["failed"] or 0),
        avg_latency_ms=round(float(totals["avg_latency"] or 0), 1),
        today_runs=int(today_row["total"] or 0),
        today_success=int(today_row["success"] or 0),
        current_prompt_version=prompt_version,
        evolution_count=int(evo_cnt["cnt"] or 0),
        avg_rating=round(float(rating_row["avg_r"]), 2) if rating_row["avg_r"] else None,
        last_run_at=last_run["created_at"] if last_run else None,
    )


# ─── 历史记录查询（供 /v1/history） ─────────────────────────────────────────────

def load_history(db_path: str, limit: int = 20) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT request_id, created_at, mode, provider, date,
                   prompt_version, model, eval_json, error, latency_ms
            FROM recap_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(max(1, min(limit, 200))),),
        ).fetchall()
    return [
        {
            "request_id": row["request_id"],
            "created_at": row["created_at"],
            "mode": row["mode"],
            "provider": row["provider"],
            "date": row["date"],
            "prompt_version": row["prompt_version"],
            "model": row["model"],
            "eval": json.loads(row["eval_json"]) if row["eval_json"] else None,
            "error": row["error"],
            "latency_ms": row["latency_ms"],
        }
        for row in rows
    ]
