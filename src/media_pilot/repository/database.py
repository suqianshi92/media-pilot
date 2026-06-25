from pathlib import Path

from sqlalchemy import Engine, event, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from media_pilot.config import AppConfig

DATABASE_FILE_NAME = "media-pilot.sqlite3"

# SQLite busy_timeout (毫秒) — 后台 qB 同步每 5 秒会争写锁, Agent
# 长事务 (LLM 网络 + 工具调用) 期间 DB 写锁会排他, 设 busy_timeout
# 让 SQLAlchemy 在 DB 写锁冲突时等 5s, 不立刻抛 OperationalError.
# 配合 journal_mode=WAL 提升并发读吞吐, 减少 lock 等待.
_SQLITE_BUSY_TIMEOUT_MS = 5000


class Base(DeclarativeBase):
    pass


def sqlite_database_path(config: AppConfig) -> Path:
    return config.database_dir / DATABASE_FILE_NAME


def _set_sqlite_pragma(dbapi_connection, _connection_record):
    """为每条新打开的 sqlite 连接设置 PRAGMA.

    WAL 模式允许并发读, busy_timeout 让 SQLAlchemy 在 DB 锁竞争时
    等待 5s 而非立刻 OperationalError. Event listener 在每个新
    connection 上都执行, 不影响已存在 connection.

    注意: PRAGMA foreign_keys=ON 故意不开 — 现有测试与代码路径
    (bind_ingest_task 用占位 ID / AgentRun 缺 task_id 兜底) 依赖
    SQLite 默认的 off 行为. 强一致由 SQLAlchemy 应用层维护.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    try:
        cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    except Exception:
        pass
    cursor.close()


def create_engine_from_config(config: AppConfig) -> Engine:
    database_path = sqlite_database_path(config)
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path}",
        future=True,
        connect_args={
            # 等待写锁的兜底时长 (秒) — 与 PRAGMA busy_timeout 二选一
            # 即可, 同时设更稳: SQLAlchemy 层 30s, sqlite 层 5s 重试.
            "timeout": 30,
            "check_same_thread": False,
        },
    )
    event.listen(engine, "connect", _set_sqlite_pragma)
    return engine


def create_session_factory(config: AppConfig) -> sessionmaker[Session]:
    return sessionmaker(
        bind=create_engine_from_config(config),
        expire_on_commit=False,
        future=True,
    )


def initialize_database(config: AppConfig) -> Path:
    database_path = sqlite_database_path(config)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine_from_config(config)
    Base.metadata.create_all(engine)

    # 简单迁移：为已有数据库补充缺失列
    with engine.connect() as conn:
        _ensure_column(conn, "ingest_tasks", "source_download_task_id", "TEXT")
        _ensure_column(conn, "download_tasks", "ingest_task_id", "TEXT")
        _ensure_column(conn, "app_settings", "preferred_metadata_language", "TEXT")
        _ensure_column(conn, "app_settings", "source_cleanup_policy", "TEXT")
        _ensure_column(conn, "app_settings", "download_rate_limit_bytes_per_second", "INTEGER")
        _ensure_column(conn, "app_settings", "upload_rate_limit_bytes_per_second", "INTEGER")
        _ensure_column(
            conn, "app_settings", "synced_download_rate_limit_bytes_per_second", "INTEGER"
        )
        _ensure_column(
            conn, "app_settings", "synced_upload_rate_limit_bytes_per_second", "INTEGER"
        )
        _ensure_column(conn, "ingest_tasks", "title", "TEXT")
        _ensure_column(conn, "ingest_tasks", "year", "INTEGER")
        # DownloadTask → IngestTask 派发链上传透 preselected 元数据事实.
        # Agent 链路必须把它视为强事实, 不得向用户确认同一个元数据.
        _ensure_column(conn, "ingest_tasks", "preselected_metadata_profile", "TEXT")
        _ensure_column(conn, "ingest_tasks", "preselected_metadata_provider", "TEXT")
        _ensure_column(conn, "ingest_tasks", "preselected_metadata_external_id", "TEXT")
        _ensure_column(conn, "agent_decision_requests", "question", "TEXT")
        _ensure_column(conn, "agent_decision_requests", "free_text_allowed", "INTEGER")
        _ensure_column(conn, "agent_decision_requests", "payload", "JSON")
        _ensure_column(conn, "agent_tool_calls", "tool_call_id", "TEXT")
        _ensure_index(
            conn,
            "idx_one_active_agent_run_per_task",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_agent_run_per_task "
            "ON agent_runs (task_id) WHERE status = 'active'",
        )
        conn.commit()

    engine.dispose()
    return database_path


def _ensure_column(conn, table: str, column: str, col_type: str) -> None:
    """如果表中不存在指定列，则 ALTER TABLE ADD COLUMN。"""
    from sqlalchemy import text
    cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
    if column not in cols:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))


def _ensure_index(conn, index_name: str, ddl: str) -> None:
    """如果索引不存在，则执行 DDL 创建索引。幂等。"""
    from sqlalchemy import text
    conn.execute(text(ddl))
