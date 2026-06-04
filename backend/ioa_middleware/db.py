"""IoA 平台 — 数据库初始化与连接管理

使用 aiosqlite 单连接模式（SQLite 不支持并发写，单连接已足够）。
"""

import aiosqlite
from pathlib import Path

_conn: aiosqlite.Connection | None = None

# ── DDL ──────────────────────────────────────────────────

CREATE_TABLES_SQL = """
-- Agent 注册表（与架构方案 v2 §3.1.1 Capability Profile 完全对齐）
-- 支持能力自描述规范 v1.0
CREATE TABLE IF NOT EXISTS agents (
    agent_id          TEXT PRIMARY KEY,
    domain            TEXT NOT NULL,
    capabilities      TEXT NOT NULL,           -- JSON 数组
    protocols         TEXT NOT NULL,           -- JSON 数组
    model             TEXT,
    load              REAL DEFAULT 0.0,
    status            TEXT DEFAULT 'active',
    endpoint          TEXT,
    last_heartbeat_ms INTEGER,
    registered_at_ms  INTEGER NOT NULL,
    metadata          TEXT                     -- JSON: 能力自描述元数据
);

-- IoAP 消息表（全量记录，用于消息流展示和链路追踪）
CREATE TABLE IF NOT EXISTS messages (
    msg_id            TEXT PRIMARY KEY,
    from_agent        TEXT NOT NULL,
    to_agent          TEXT,
    intent_type       TEXT NOT NULL,
    intent_desc       TEXT,
    priority          TEXT DEFAULT 'normal',
    payload           TEXT,
    correlation_id    TEXT,
    route_decision    TEXT,                   -- 语义路由决策日志 JSON
    status            TEXT DEFAULT 'sent',
    ts_ms             INTEGER NOT NULL,
    delivered_ms      INTEGER
);

-- DAG 任务表
CREATE TABLE IF NOT EXISTS dags (
    dag_id            TEXT PRIMARY KEY,
    correlation_id    TEXT,
    definition        TEXT NOT NULL,           -- DAG 定义 JSON
    status            TEXT DEFAULT 'pending',
    submitted_at_ms   INTEGER NOT NULL,
    finished_at_ms    INTEGER,
    result            TEXT
);

-- DAG 节点执行记录
CREATE TABLE IF NOT EXISTS dag_nodes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    dag_id            TEXT NOT NULL,
    node_id           TEXT NOT NULL,
    status            TEXT DEFAULT 'pending',
    assigned_agent    TEXT,
    started_at_ms     INTEGER,
    finished_at_ms    INTEGER,
    output            TEXT,
    retry_count       INTEGER DEFAULT 0,
    FOREIGN KEY (dag_id) REFERENCES dags(dag_id)
);

-- 审计日志（架构方案 v2 §3.4.4，完整实现）
CREATE TABLE IF NOT EXISTS audit_logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms             INTEGER NOT NULL,
    event_type        TEXT NOT NULL,
    from_agent        TEXT,
    to_agent          TEXT,
    msg_id            TEXT,
    detail            TEXT,
    auth_result       TEXT,
    correlation_id    TEXT
);

-- 闭环验证记录
CREATE TABLE IF NOT EXISTS verifications (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    dag_id            TEXT NOT NULL,
    repair_ts_ms      INTEGER NOT NULL,
    metric_before     TEXT NOT NULL,
    metric_after      TEXT NOT NULL,
    verdict           TEXT NOT NULL,
    retry_count       INTEGER DEFAULT 0,
    finished_at_ms    INTEGER NOT NULL,
    FOREIGN KEY (dag_id) REFERENCES dags(dag_id)
);

-- 量化指标汇总（供仪表盘 Tab2 读取）
CREATE TABLE IF NOT EXISTS metrics_summary (
    metric_name       TEXT PRIMARY KEY,
    current_value     REAL,
    sample_count      INTEGER,
    updated_at_ms     INTEGER NOT NULL
);
"""

CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_messages_correlation ON messages(correlation_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts         ON messages(ts_ms);
CREATE INDEX IF NOT EXISTS idx_dags_status         ON dags(status);
CREATE INDEX IF NOT EXISTS idx_audit_correlation   ON audit_logs(correlation_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts            ON audit_logs(ts_ms);
"""


# ── 公开 API ─────────────────────────────────────────────

async def init_db(db_path: str = "data/ioa.db") -> aiosqlite.Connection:
    """初始化数据库：确保目录存在、建表、建索引。返回全局连接。"""
    import logging
    logger = logging.getLogger("db")

    global _conn
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # 如果存在 WAL 残留文件，尝试删除
    wal_file = Path(db_path + "-wal")
    shm_file = Path(db_path + "-shm")
    if wal_file.exists():
        try:
            wal_file.unlink()
            shm_file.unlink(missing_ok=True)
            logger.info("Cleaned up WAL files")
        except Exception as e:
            logger.warning("Failed to clean WAL files: %s", e)

    _conn = await aiosqlite.connect(db_path)
    _conn.row_factory = aiosqlite.Row

    # WAL 模式（某些环境可能不支持，优雅降级）
    try:
        await _conn.execute("PRAGMA journal_mode=WAL")
        logger.info("SQLite WAL mode enabled")
    except Exception as e:
        logger.warning("WAL mode not supported, using default: %s", e)

    try:
        await _conn.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass

    # 确保 commit 后再建表
    await _conn.commit()

    await _conn.executescript(CREATE_TABLES_SQL)
    await _conn.executescript(CREATE_INDEXES_SQL)
    await _conn.commit()
    return _conn


def get_db() -> aiosqlite.Connection:
    """获取当前数据库连接（必须在 init_db 之后调用）。"""
    if _conn is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _conn


async def close_db() -> None:
    """关闭数据库连接。"""
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


# ── 便捷 CRUD ─────────────────────────────────────────────

async def execute(sql: str, params: tuple | list | None = None) -> aiosqlite.Cursor:
    """执行写操作 SQL，自动 commit。"""
    db = get_db()
    cursor = await db.execute(sql, params or ())
    await db.commit()
    return cursor


async def fetch_all(sql: str, params: tuple | list | None = None) -> list[dict]:
    """执行读操作 SQL，返回 list[dict]。"""
    db = get_db()
    cursor = await db.execute(sql, params or ())
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def fetch_one(sql: str, params: tuple | list | None = None) -> dict | None:
    """执行读操作 SQL，返回 dict 或 None。"""
    db = get_db()
    cursor = await db.execute(sql, params or ())
    row = await cursor.fetchone()
    return dict(row) if row else None
