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
from datetime import datetime, timezone
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
              tokens_json TEXT,
              experiment_id TEXT,
              variant_id TEXT,
              tenant_id TEXT
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
              comment TEXT NOT NULL,
              tenant_id TEXT
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
              scoring_impl TEXT NOT NULL DEFAULT 'keyword_substring',
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
              updated_at TEXT NOT NULL,
              tenant_id TEXT
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

            /*
             * jobs（W5-3）：长任务原语 —— 把同步 generate_once 包成可异步轮询的作业。
             *
             * 为什么独立一张表（不复用 pending_actions）：
             * - pending_actions 是「副作用收件箱」，强调 UNIQUE(request_id, action_type)，
             *   payload 体积小，重试逻辑由 outbox 接管；
             * - jobs 是「客户端可见的 RPC 替身」，需要返回结果（result_json 可达数十 KB），
             *   语义、TTL、清理周期都和 outbox 不一样；
             * - 单独一张表也方便后期接独立 worker 进程消费。
             *
             * status 状态机：queued → running → done|failed|cancelled
             *   - queued：等待 worker 接管；
             *   - running：worker 已 claim，可能在 BackgroundTasks 中执行；
             *   - done：成功，result_json 含完整 GenerateResponse；
             *   - failed：失败，error 字段含原因；
             *   - cancelled：人为取消（保留状态，方便审计）。
             *
             * 幂等：调用方可传 idempotency_key（HTTP Header X-Idempotency-Key），
             * 同 (tenant_id, idempotency_key) 重入返回已有 job_id 而非新建。
             */
            CREATE TABLE IF NOT EXISTS jobs (
              job_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'queued',
              tenant_id TEXT,
              request_id TEXT,
              idempotency_key TEXT,
              request_json TEXT NOT NULL,
              result_json TEXT,
              error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              started_at TEXT,
              finished_at TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idem
              ON jobs(tenant_id, idempotency_key)
              WHERE idempotency_key IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_jobs_status_created
              ON jobs(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_jobs_tenant_status
              ON jobs(tenant_id, status);

            /*
             * tool_invocations：工具调用审计明细。
             * - 每次工具调用（成功 / 失败 / 拒绝）落一行；不依赖业务主表，可独立查询；
             * - 与 recap_runs 通过 request_id 关联，便于 join 出「这次复盘到底用了哪些工具」；
             * - status: ok | failed | denied | timeout
             * - principal_role 留作 Wave 5 多租户接入预留字段。
             */
            CREATE TABLE IF NOT EXISTS tool_invocations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              request_id TEXT,
              tool_name TEXT NOT NULL,
              status TEXT NOT NULL,
              read_only INTEGER NOT NULL DEFAULT 1,
              principal_role TEXT,
              arguments_json TEXT,
              latency_ms INTEGER,
              error TEXT,
              created_at TEXT NOT NULL,
              tenant_id TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_tool_inv_request_id
              ON tool_invocations(request_id);
            CREATE INDEX IF NOT EXISTS idx_tool_inv_tool_status
              ON tool_invocations(tool_name, status);

            /*
             * recap_audit：完整 LLM 输入/输出审计层，独立于 recap_runs 业务表。
             * 为什么分表（而不是把 messages_json 加到 recap_runs）：
             * 1. recap_runs 频繁被 list / join，messages_json 体积可达 100KB，会拖慢列表页；
             * 2. 审计/合规/replay 是独立查询场景，单表更适合冷数据归档；
             * 3. recap_runs schema 变更对历史数据兼容压力大，audit 表可放心迭代字段；
             * 4. tool_invocations 已经按这个范式（独立表 + request_id 关联）做了。
             *
             * 关联：通过 request_id 与 recap_runs / tool_invocations 一一对应。
             * 删除策略：归档/TTL 由调用方（cron / Wave 5 worker）按 created_at 清理。
             */
            CREATE TABLE IF NOT EXISTS recap_audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              request_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              mode TEXT NOT NULL,
              provider TEXT NOT NULL,
              prompt_version TEXT,
              model TEXT,
              trace_id TEXT,
              session_id TEXT,
              messages_json TEXT,
              recap_json TEXT,
              eval_json TEXT,
              tokens_json TEXT,
              llm_error TEXT,
              budget_error TEXT,
              critic_retries_used INTEGER NOT NULL DEFAULT 0,
              experiment_id TEXT,
              variant_id TEXT,
              tenant_id TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_recap_audit_request_id
              ON recap_audit(request_id);
            CREATE INDEX IF NOT EXISTS idx_recap_audit_created_at
              ON recap_audit(created_at);
            CREATE INDEX IF NOT EXISTS idx_recap_audit_mode_created
              ON recap_audit(mode, created_at);

            /*
             * prompt_experiments：声明一个实验（实验维度，例如 section_titles_v2_ab）。
             * 同一时间一个 mode 应只激活一个 experiment（多个 active 时按 ``starts_at`` 取最新）。
             */
            CREATE TABLE IF NOT EXISTS prompt_experiments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              experiment_id TEXT NOT NULL UNIQUE,
              mode TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              starts_at TEXT,
              ends_at TEXT,
              description TEXT,
              metadata_json TEXT,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_prompt_exp_mode_status
              ON prompt_experiments(mode, status);

            /*
             * prompt_experiment_variants：实验下属的 variant（含权重 + prompt_version 绑定）。
             * traffic_weight 单位无关；同一 experiment 下加和后做归一化分桶。
             */
            CREATE TABLE IF NOT EXISTS prompt_experiment_variants (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              experiment_id TEXT NOT NULL,
              variant_id TEXT NOT NULL,
              prompt_version TEXT NOT NULL,
              traffic_weight INTEGER NOT NULL DEFAULT 1,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              UNIQUE(experiment_id, variant_id)
            );

            CREATE INDEX IF NOT EXISTS idx_prompt_exp_var_exp
              ON prompt_experiment_variants(experiment_id);

            /*
             * tenants（W5-2）：多租户主表。
             * - api_key_hash 存 SHA-256(api_key) 的十六进制摘要，原始 key 不落库；
             * - role 是该租户的默认 RBAC 角色，可被请求级 PrincipalContext 覆盖；
             * - status: active | disabled；disabled 时 require_api_key 会拒；
             * - metadata_json 留给前端可见的描述 / SLA / 配额（不在本 wave 实现限流）。
             */
            CREATE TABLE IF NOT EXISTS tenants (
              tenant_id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              api_key_hash TEXT NOT NULL UNIQUE,
              role TEXT NOT NULL DEFAULT 'user',
              status TEXT NOT NULL DEFAULT 'active',
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tenants_status
              ON tenants(status);
            """
        )

    # 增量 ALTER TABLE（老库升级兼容）
    _safe_add_column(db_path, "recap_runs", "rendered_wechat_text", "TEXT")
    # Wave 4 / w4-4：A/B 实验落库
    _safe_add_column(db_path, "recap_runs", "experiment_id", "TEXT")
    _safe_add_column(db_path, "recap_runs", "variant_id", "TEXT")
    _safe_add_column(db_path, "recap_audit", "experiment_id", "TEXT")
    _safe_add_column(db_path, "recap_audit", "variant_id", "TEXT")
    # Wave 5 / w5-2：多租户落库
    _safe_add_column(db_path, "recap_runs", "tenant_id", "TEXT")
    _safe_add_column(db_path, "recap_audit", "tenant_id", "TEXT")
    _safe_add_column(db_path, "recap_feedback", "tenant_id", "TEXT")
    _safe_add_column(db_path, "tool_invocations", "tenant_id", "TEXT")
    _safe_add_column(db_path, "pending_actions", "tenant_id", "TEXT")
    _safe_add_column(db_path, "backtest_results", "scoring_impl", "TEXT")


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
    experiment_id: Optional[str] = None,
    variant_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO recap_runs (
              request_id, created_at, mode, provider, date, prompt_version, model,
              snapshot_json, features_json, recap_json, rendered_markdown,
              rendered_wechat_text, eval_json, error, latency_ms, tokens_json,
              experiment_id, variant_id, tenant_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                experiment_id,
                variant_id,
                tenant_id,
            ),
        )


def load_recent_runs(
    db_path: str,
    date: str,
    mode: Mode,
    limit: int,
    *,
    tenant_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """读取 date 之前的最近 N 条成功运行记录（含 recap + eval）。

    ``tenant_id`` 显式给出时强制隔离；不给（None）则保持全局视图，便于 CLI / 单租户兼容。
    """
    where = ["date < ?", "mode = ?", "recap_json IS NOT NULL"]
    params: List[Any] = [date, mode]
    if tenant_id is not None:
        where.append("tenant_id = ?")
        params.append(tenant_id)
    params.append(limit)
    sql = (
        "SELECT created_at, date, mode, provider, prompt_version, recap_json, eval_json "
        "FROM recap_runs WHERE " + " AND ".join(where) +
        " ORDER BY date DESC, created_at DESC LIMIT ?"
    )
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
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
    tenant_id: Optional[str] = None,
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO recap_feedback
              (request_id, created_at, rating, tags_json, comment, tenant_id)
            VALUES (?,?,?,?,?,?)
            """,
            (request_id, created_at, rating, _stable_json(tags), comment, tenant_id),
        )


def load_feedback_summary(
    db_path: str,
    limit: int = 30,
    *,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """聚合最近反馈：平均分、高频好评/差评 tag。``tenant_id`` 给出时按租户隔离。"""
    if tenant_id is not None:
        sql = (
            "SELECT rating, tags_json FROM recap_feedback "
            "WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?"
        )
        params: tuple = (tenant_id, limit)
    else:
        sql = "SELECT rating, tags_json FROM recap_feedback ORDER BY created_at DESC LIMIT ?"
        params = (limit,)
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

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
               predicted_sectors_json, actual_top_sectors_json, detail, scoring_impl, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                result.strategy_date,
                result.actual_date,
                result.hit_count,
                result.hit_rate,
                _stable_json(result.predicted_sectors),
                _stable_json(result.actual_top_sectors),
                result.detail,
                result.scoring_impl,
                created_at,
            ),
        )


def load_recent_backtests(db_path: str, limit: int = 10) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT strategy_date, actual_date, hit_count, hit_rate, detail, scoring_impl, created_at
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
    tenant_id: Optional[str] = None,
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
                   next_attempt_at, last_error, created_at, updated_at, tenant_id)
                VALUES (?,?,?, 'pending', 0, ?, NULL, ?, ?, ?)
                """,
                (
                    request_id,
                    action_type,
                    payload_json,
                    now_iso,
                    now_iso,
                    now_iso,
                    tenant_id,
                ),
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


# ─── tool_invocations（工具审计） ────────────────────────────────────────────


def insert_tool_invocation(
    db_path: str,
    *,
    request_id: Optional[str],
    tool_name: str,
    status: str,
    read_only: bool,
    principal_role: Optional[str],
    arguments: Optional[Dict[str, Any]],
    latency_ms: Optional[int],
    error: Optional[str],
    created_at: str,
    tenant_id: Optional[str] = None,
) -> None:
    """单次工具调用落库；任何异常向上抛由调用方决定是否吞掉。"""
    args_json = _stable_json(arguments) if arguments is not None else None
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO tool_invocations
              (request_id, tool_name, status, read_only, principal_role,
               arguments_json, latency_ms, error, created_at, tenant_id)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                request_id,
                tool_name,
                status,
                1 if read_only else 0,
                principal_role,
                args_json,
                latency_ms,
                error,
                created_at,
                tenant_id,
            ),
        )


def load_recent_tool_invocations(
    db_path: str,
    *,
    request_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """运维 / 测试用：按 request_id 或 tool_name 过滤的近期审计记录。"""
    where: List[str] = []
    params: List[Any] = []
    if request_id is not None:
        where.append("request_id = ?")
        params.append(request_id)
    if tool_name is not None:
        where.append("tool_name = ?")
        params.append(tool_name)
    sql = "SELECT * FROM tool_invocations"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


# ─── recap_audit（完整 messages + recap，replay 用） ─────────────────────────


def insert_recap_audit(
    db_path: str,
    *,
    request_id: str,
    created_at: str,
    mode: str,
    provider: str,
    prompt_version: Optional[str],
    model: Optional[str],
    trace_id: Optional[str],
    session_id: Optional[str],
    messages: Optional[List[Dict[str, Any]]],
    recap: Optional[Recap],
    eval_obj: Optional[Dict[str, Any]],
    tokens: Optional[LlmTokens],
    llm_error: Optional[str],
    budget_error: Optional[str],
    critic_retries_used: int,
    experiment_id: Optional[str] = None,
    variant_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> None:
    """落 audit；``request_id`` 唯一约束保证幂等（同次 generate 重复调用只保留首条）。"""
    messages_json = _stable_json(messages) if messages is not None else None
    recap_json = _stable_json(recap.model_dump()) if recap is not None else None
    eval_json = _stable_json(eval_obj) if eval_obj is not None else None
    tokens_json = _stable_json(tokens.__dict__) if tokens is not None else None
    with get_conn(db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO recap_audit
                  (request_id, created_at, mode, provider, prompt_version, model,
                   trace_id, session_id, messages_json, recap_json, eval_json,
                   tokens_json, llm_error, budget_error, critic_retries_used,
                   experiment_id, variant_id, tenant_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    request_id,
                    created_at,
                    mode,
                    provider,
                    prompt_version,
                    model,
                    trace_id,
                    session_id,
                    messages_json,
                    recap_json,
                    eval_json,
                    tokens_json,
                    llm_error,
                    budget_error,
                    int(critic_retries_used),
                    experiment_id,
                    variant_id,
                    tenant_id,
                ),
            )
        except sqlite3.IntegrityError:
            # 同 request_id 重入：保留最早的一份（更接近真实 LLM 输入）。
            return


def load_recap_audit(
    db_path: str,
    *,
    request_id: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = 20,
    tenant_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    where: List[str] = []
    params: List[Any] = []
    if request_id is not None:
        where.append("request_id = ?")
        params.append(request_id)
    if mode is not None:
        where.append("mode = ?")
        params.append(mode)
    if tenant_id is not None:
        where.append("tenant_id = ?")
        params.append(tenant_id)
    sql = "SELECT * FROM recap_audit"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        for jkey in ("messages_json", "recap_json", "eval_json", "tokens_json"):
            if d.get(jkey):
                try:
                    d[jkey.removesuffix("_json")] = json.loads(d[jkey])
                except Exception:
                    d[jkey.removesuffix("_json")] = None
        out.append(d)
    return out


# ─── prompt_experiments / variants ─────────────────────────────────────────


def upsert_prompt_experiment(
    db_path: str,
    *,
    experiment_id: str,
    mode: str,
    status: str = "active",
    starts_at: Optional[str] = None,
    ends_at: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> None:
    meta_json = _stable_json(metadata) if metadata is not None else None
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO prompt_experiments
              (experiment_id, mode, status, starts_at, ends_at, description, metadata_json, created_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(experiment_id) DO UPDATE SET
              mode=excluded.mode,
              status=excluded.status,
              starts_at=excluded.starts_at,
              ends_at=excluded.ends_at,
              description=excluded.description,
              metadata_json=excluded.metadata_json
            """,
            (
                experiment_id,
                mode,
                status,
                starts_at,
                ends_at,
                description,
                meta_json,
                created_at or starts_at or "",
            ),
        )


def upsert_prompt_experiment_variant(
    db_path: str,
    *,
    experiment_id: str,
    variant_id: str,
    prompt_version: str,
    traffic_weight: int = 1,
    metadata: Optional[Dict[str, Any]] = None,
    created_at: str,
) -> None:
    meta_json = _stable_json(metadata) if metadata is not None else None
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO prompt_experiment_variants
              (experiment_id, variant_id, prompt_version, traffic_weight, metadata_json, created_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(experiment_id, variant_id) DO UPDATE SET
              prompt_version=excluded.prompt_version,
              traffic_weight=excluded.traffic_weight,
              metadata_json=excluded.metadata_json
            """,
            (
                experiment_id,
                variant_id,
                prompt_version,
                int(max(0, traffic_weight)),
                meta_json,
                created_at,
            ),
        )


def load_active_experiment(
    db_path: str, *, mode: str
) -> Optional[Dict[str, Any]]:
    """对给定 mode 取一条「当前 active」实验；若有多条以 starts_at 最大为准。"""
    with get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM prompt_experiments
            WHERE mode = ? AND status = 'active'
            ORDER BY COALESCE(starts_at, '') DESC, id DESC
            LIMIT 1
            """,
            (mode,),
        ).fetchone()
    return dict(row) if row else None


def load_experiment_variants(
    db_path: str, *, experiment_id: str
) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM prompt_experiment_variants
            WHERE experiment_id = ? AND traffic_weight > 0
            ORDER BY variant_id ASC
            """,
            (experiment_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_prompt_experiments(
    db_path: str, *, mode: Optional[str] = None, status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    where: List[str] = []
    params: List[Any] = []
    if mode is not None:
        where.append("mode = ?")
        params.append(mode)
    if status is not None:
        where.append("status = ?")
        params.append(status)
    sql = "SELECT * FROM prompt_experiments"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


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

def load_history(
    db_path: str,
    limit: int = 20,
    *,
    tenant_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    capped = int(max(1, min(limit, 200)))
    if tenant_id is not None:
        sql = (
            "SELECT request_id, created_at, mode, provider, date, "
            "       prompt_version, model, eval_json, error, latency_ms "
            "FROM recap_runs WHERE tenant_id = ? "
            "ORDER BY created_at DESC LIMIT ?"
        )
        params: tuple = (tenant_id, capped)
    else:
        sql = (
            "SELECT request_id, created_at, mode, provider, date, "
            "       prompt_version, model, eval_json, error, latency_ms "
            "FROM recap_runs ORDER BY created_at DESC LIMIT ?"
        )
        params = (capped,)
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
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


# ─── tenants CRUD（W5-2） ────────────────────────────────────────────────────


def upsert_tenant(
    db_path: str,
    *,
    tenant_id: str,
    name: str,
    api_key_hash: str,
    role: str = "user",
    status: str = "active",
    metadata: Optional[Dict[str, Any]] = None,
    now_iso: Optional[str] = None,
) -> None:
    """upsert 一个租户；同 ``tenant_id`` 重入只更新可变字段。

    ``now_iso`` 不传时默认当前 UTC（CLI / 测试用例不必次次都拼时间字符串）。
    """
    if now_iso is None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta_json = _stable_json(metadata) if metadata is not None else None
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO tenants
              (tenant_id, name, api_key_hash, role, status, metadata_json,
               created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(tenant_id) DO UPDATE SET
              name=excluded.name,
              api_key_hash=excluded.api_key_hash,
              role=excluded.role,
              status=excluded.status,
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            (tenant_id, name, api_key_hash, role, status, meta_json, now_iso, now_iso),
        )


def load_tenant_by_api_key_hash(
    db_path: str, *, api_key_hash: str
) -> Optional[Dict[str, Any]]:
    """按 api_key_hash 反查 tenant；找不到 / status != active 都返回 None。"""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM tenants WHERE api_key_hash = ?",
            (api_key_hash,),
        ).fetchone()
    if row is None:
        return None
    if row["status"] != "active":
        return None
    return dict(row)


def list_tenants(
    db_path: str, *, status: Optional[str] = None, limit: int = 100
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM tenants"
    params: List[Any] = []
    if status is not None:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def count_tenants(db_path: str, *, status: Optional[str] = "active") -> int:
    sql = "SELECT COUNT(*) AS cnt FROM tenants"
    params: List[Any] = []
    if status is not None:
        sql += " WHERE status = ?"
        params.append(status)
    with get_conn(db_path) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return int(row["cnt"] or 0)


# ─── jobs CRUD（W5-3：长任务原语） ──────────────────────────────────────────


def insert_job(
    db_path: str,
    *,
    job_id: str,
    kind: str,
    request_payload: Dict[str, Any],
    tenant_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    status: str = "queued",
    created_at: Optional[str] = None,
) -> bool:
    """新建一个 job 行；同 (tenant_id, idempotency_key) 已存在时返回 False。

    返回 True = 实际新建；False = 命中幂等键，调用方应改读 ``load_job_by_idem``。
    """
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_conn(db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO jobs
                  (job_id, kind, status, tenant_id, idempotency_key,
                   request_json, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    kind,
                    status,
                    tenant_id,
                    idempotency_key,
                    _stable_json(request_payload),
                    created_at,
                    created_at,
                ),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def load_job(
    db_path: str,
    *,
    job_id: str,
    tenant_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """按 job_id 取一条 job；多租户场景下传 ``tenant_id`` 防止越权读其他租户。"""
    sql = "SELECT * FROM jobs WHERE job_id = ?"
    params: List[Any] = [job_id]
    if tenant_id is not None:
        sql += " AND tenant_id = ?"
        params.append(tenant_id)
    with get_conn(db_path) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    if row is None:
        return None
    return _decode_job_row(row)


def load_job_by_idem(
    db_path: str,
    *,
    tenant_id: Optional[str],
    idempotency_key: str,
) -> Optional[Dict[str, Any]]:
    """按幂等键查 job；传入 ``tenant_id=None`` 时匹配未绑定租户的行。"""
    if tenant_id is None:
        sql = (
            "SELECT * FROM jobs WHERE idempotency_key = ? "
            "AND tenant_id IS NULL ORDER BY created_at DESC LIMIT 1"
        )
        params: tuple = (idempotency_key,)
    else:
        sql = (
            "SELECT * FROM jobs WHERE idempotency_key = ? AND tenant_id = ? "
            "ORDER BY created_at DESC LIMIT 1"
        )
        params = (idempotency_key, tenant_id)
    with get_conn(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return _decode_job_row(row)


def list_jobs(
    db_path: str,
    *,
    tenant_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    where: List[str] = []
    params: List[Any] = []
    if tenant_id is not None:
        where.append("tenant_id = ?")
        params.append(tenant_id)
    if status is not None:
        where.append("status = ?")
        params.append(status)
    sql = "SELECT * FROM jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_decode_job_row(r) for r in rows]


def update_job_running(
    db_path: str,
    *,
    job_id: str,
    request_id: Optional[str] = None,
    started_at: Optional[str] = None,
) -> None:
    """worker claim 一个 queued job：原子地把 status 改成 running。"""
    if started_at is None:
        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
              SET status = 'running',
                  started_at = COALESCE(started_at, ?),
                  request_id = COALESCE(?, request_id),
                  updated_at = ?
              WHERE job_id = ? AND status IN ('queued', 'running')
            """,
            (started_at, request_id, started_at, job_id),
        )


def mark_job_done(
    db_path: str,
    *,
    job_id: str,
    result_payload: Dict[str, Any],
    request_id: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> None:
    if finished_at is None:
        finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
              SET status = 'done',
                  result_json = ?,
                  request_id = COALESCE(?, request_id),
                  finished_at = ?,
                  updated_at = ?
              WHERE job_id = ?
            """,
            (
                _stable_json(result_payload),
                request_id,
                finished_at,
                finished_at,
                job_id,
            ),
        )


def mark_job_failed(
    db_path: str,
    *,
    job_id: str,
    error: str,
    request_id: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> None:
    if finished_at is None:
        finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
              SET status = 'failed',
                  error = ?,
                  request_id = COALESCE(?, request_id),
                  finished_at = ?,
                  updated_at = ?
              WHERE job_id = ?
            """,
            (error[:2000], request_id, finished_at, finished_at, job_id),
        )


def claim_due_queued_jobs(
    db_path: str,
    *,
    older_than_iso: str,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    """sweeper 用：把超过阈值仍 queued 的孤儿 job 抢回 running。

    适用场景：BackgroundTasks 所在 worker 进程崩溃，job 永远停在 queued。
    """
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
              WHERE status = 'queued' AND created_at <= ?
              ORDER BY created_at ASC LIMIT ?
            """,
            (older_than_iso, int(limit)),
        ).fetchall()
        if not rows:
            return []
        ids = [r["job_id"] for r in rows]
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE jobs SET status='running', started_at=COALESCE(started_at, ?), "
            f"updated_at=? WHERE job_id IN ({placeholders})",
            (now_iso, now_iso, *ids),
        )
    return [_decode_job_row(r) for r in rows]


def _decode_job_row(row) -> Dict[str, Any]:
    d = dict(row)
    for jkey in ("request_json", "result_json"):
        raw = d.get(jkey)
        if raw:
            try:
                d[jkey.removesuffix("_json")] = json.loads(raw)
            except Exception:
                d[jkey.removesuffix("_json")] = None
    return d
