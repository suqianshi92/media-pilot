from pathlib import Path

from sqlalchemy import inspect, text

from media_pilot.config import AppConfig
from media_pilot.repository.database import (
    Base,
    create_engine_from_config,
    database_url_from_config,
    initialize_database,
)
from media_pilot.repository.models import IngestTask


def make_config(database_dir: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"),
        shows_dir=Path("/media/library/shows"),
        database_dir=database_dir,
    )


def test_create_engine_from_config_uses_sqlite_database_file(tmp_path: Path) -> None:
    engine = create_engine_from_config(make_config(tmp_path))

    assert str(engine.url) == f"sqlite+pysqlite:///{tmp_path / 'media-pilot.sqlite3'}"


def test_create_engine_from_config_uses_database_url_when_configured(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg = AppConfig(
        downloads_dir=cfg.downloads_dir,
        watch_dir=cfg.watch_dir,
        workspace_dir=cfg.workspace_dir,
        movies_dir=cfg.movies_dir,
        shows_dir=cfg.shows_dir,
        database_dir=cfg.database_dir,
        database_url="postgresql+psycopg://media_pilot:secret@db:5432/media_pilot",
    )

    engine = create_engine_from_config(cfg)
    try:
        assert engine.dialect.name == "postgresql"
        assert str(engine.url).startswith("postgresql+psycopg://media_pilot:***@db:5432")
        assert database_url_from_config(cfg).startswith("postgresql+psycopg://")
    finally:
        engine.dispose()


def test_initialize_database_creates_sqlite_file(tmp_path: Path) -> None:
    database_file = initialize_database(make_config(tmp_path))

    assert database_file == tmp_path / "media-pilot.sqlite3"
    assert database_file.exists()
    assert IngestTask.__tablename__ in Base.metadata.tables

    engine = create_engine_from_config(make_config(tmp_path))
    try:
        assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables)
    finally:
        engine.dispose()


def test_ensure_column_adds_tool_call_id_to_existing_table(tmp_path: Path) -> None:
    """先创建缺少 tool_call_id 的 agent_tool_calls 表，再调用 initialize_database() 补齐列。"""
    config = make_config(tmp_path)
    config.database_dir.mkdir(parents=True, exist_ok=True)

    # 第一步：只通过 ORM create_all 创建完整 schema（此时 Base.metadata 已包含 tool_call_id）
    # 然后手动 DROP 该列模拟旧 schema
    initialize_database(config)
    database_file = config.database_dir / "media-pilot.sqlite3"
    assert database_file.exists()

    engine = create_engine_from_config(config)
    try:
        # 模拟旧 schema：删除 tool_call_id 列
        # SQLite 不支持 DROP COLUMN 直接方式，用重建表模拟
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS agent_tool_calls_old (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    message_id TEXT,
                    tool_name TEXT NOT NULL,
                    input JSON NOT NULL,
                    output JSON,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error_message TEXT,
                    duration_ms INTEGER,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
            """))
            # 复制数据
            conn.execute(text("INSERT INTO agent_tool_calls_old SELECT id, run_id, message_id, tool_name, input, output, status, error_message, duration_ms, created_at, updated_at FROM agent_tool_calls"))
            conn.execute(text("DROP TABLE agent_tool_calls"))
            conn.execute(text("ALTER TABLE agent_tool_calls_old RENAME TO agent_tool_calls"))
            conn.commit()

        # 确认列已缺失
        with engine.connect() as conn:
            cols_before = {row[1] for row in conn.execute(text("PRAGMA table_info(agent_tool_calls)")).fetchall()}
            assert "tool_call_id" not in cols_before

        # 第二步：调用 initialize_database() —— 应补齐 tool_call_id
        initialize_database(config)

        # 验证列已补齐
        with engine.connect() as conn:
            cols_after = {row[1] for row in conn.execute(text("PRAGMA table_info(agent_tool_calls)")).fetchall()}
            assert "tool_call_id" in cols_after
    finally:
        engine.dispose()


def test_ensure_column_adds_payload_to_agent_decision_requests(tmp_path: Path) -> None:
    """先创建缺少 payload 列的 agent_decision_requests 表，再调用 initialize_database() 补齐列。

    target_conflict 决策需要把 final_target_dir / final_target_file / conflict 写入 payload；
    旧库若未跑过新 create_all，需要 _ensure_column 把 JSON 列补齐。
    """
    config = make_config(tmp_path)
    config.database_dir.mkdir(parents=True, exist_ok=True)

    # 第一步：先建好新库（含 payload 列）
    initialize_database(config)
    database_file = config.database_dir / "media-pilot.sqlite3"
    assert database_file.exists()

    engine = create_engine_from_config(config)
    try:
        # 模拟旧 schema：删除 payload 列
        # SQLite 不支持 DROP COLUMN 直接方式，用重建表模拟旧列清单
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS agent_decision_requests_old (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    decision_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    question TEXT,
                    free_text_allowed INTEGER,
                    options JSON,
                    decision JSON,
                    decided_by TEXT,
                    decided_at TIMESTAMP,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
            """))
            # 复制数据（缺 payload 列，所以 SELECT 不带它）
            conn.execute(text("""
                INSERT INTO agent_decision_requests_old
                SELECT id, run_id, task_id, decision_type, status, question,
                       free_text_allowed, options, decision, decided_by,
                       decided_at, created_at, updated_at
                FROM agent_decision_requests
            """))
            conn.execute(text("DROP TABLE agent_decision_requests"))
            conn.execute(text("ALTER TABLE agent_decision_requests_old RENAME TO agent_decision_requests"))
            conn.commit()

        # 确认 payload 列已缺失
        with engine.connect() as conn:
            cols_before = {row[1] for row in conn.execute(text("PRAGMA table_info(agent_decision_requests)")).fetchall()}
            assert "payload" not in cols_before

        # 第二步：调用 initialize_database() —— 应补齐 payload 列
        initialize_database(config)

        # 验证列已补齐；类型应为 JSON（SQLite 在 PRAGMA 中以 "JSON" 显示）
        with engine.connect() as conn:
            cols_after = [
                (row[1], row[2])
                for row in conn.execute(text("PRAGMA table_info(agent_decision_requests)")).fetchall()
            ]
            cols_after_names = {c[0] for c in cols_after}
            assert "payload" in cols_after_names
            payload_type = next(t for n, t in cols_after if n == "payload")
            assert payload_type.upper() == "JSON"
    finally:
        engine.dispose()


def test_ensure_column_adds_source_cleanup_policy_to_app_settings(tmp_path: Path) -> None:
    """旧 app_settings 表缺 source_cleanup_policy 列时, initialize_database() 必须补齐.

    AppSettings 新增 source_cleanup_policy 后, 旧库 (无该列) 应在
    initialize_database 时被 _ensure_column 自动 ALTER TABLE 补齐.
    """
    config = make_config(tmp_path)
    config.database_dir.mkdir(parents=True, exist_ok=True)

    # 第一步: 先建好新库 (含 source_cleanup_policy 列)
    initialize_database(config)
    database_file = config.database_dir / "media-pilot.sqlite3"
    assert database_file.exists()

    engine = create_engine_from_config(config)
    try:
        # 模拟旧 schema: 删除 source_cleanup_policy 列
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS app_settings_old (
                    id TEXT PRIMARY KEY,
                    enabled_metadata_profiles JSON NOT NULL,
                    enabled_library_formats JSON NOT NULL,
                    suspicious_file_threshold_bytes INTEGER,
                    metadata_auto_confirm_confidence REAL,
                    metadata_auto_confirm_margin REAL,
                    preferred_metadata_language TEXT,
                    updated_at TIMESTAMP,
                    created_at TIMESTAMP
                )
            """))
            conn.execute(text("""
                INSERT INTO app_settings_old
                SELECT id, enabled_metadata_profiles, enabled_library_formats,
                       suspicious_file_threshold_bytes, metadata_auto_confirm_confidence,
                       metadata_auto_confirm_margin, preferred_metadata_language,
                       updated_at, created_at
                FROM app_settings
            """))
            conn.execute(text("DROP TABLE app_settings"))
            conn.execute(text("ALTER TABLE app_settings_old RENAME TO app_settings"))
            conn.commit()

        # 确认 source_cleanup_policy 列已缺失
        with engine.connect() as conn:
            cols_before = {row[1] for row in conn.execute(text("PRAGMA table_info(app_settings)")).fetchall()}
            assert "source_cleanup_policy" not in cols_before

        # 第二步: 调用 initialize_database() —— 应补齐 source_cleanup_policy
        initialize_database(config)

        # 验证列已补齐; 类型应为 TEXT
        with engine.connect() as conn:
            cols_after = [
                (row[1], row[2])
                for row in conn.execute(text("PRAGMA table_info(app_settings)")).fetchall()
            ]
            cols_after_names = {c[0] for c in cols_after}
            assert "source_cleanup_policy" in cols_after_names
            col_type = next(t for n, t in cols_after if n == "source_cleanup_policy")
            assert col_type.upper() == "TEXT"
    finally:
        engine.dispose()


def test_ensure_column_adds_rate_limit_columns_to_app_settings(tmp_path: Path) -> None:
    """旧 app_settings 表缺下载器限速列时, initialize_database() 必须补齐。"""
    config = make_config(tmp_path)
    config.database_dir.mkdir(parents=True, exist_ok=True)

    initialize_database(config)
    engine = create_engine_from_config(config)
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS app_settings_old (
                    id TEXT PRIMARY KEY,
                    enabled_metadata_profiles JSON NOT NULL,
                    enabled_library_formats JSON NOT NULL,
                    suspicious_file_threshold_bytes INTEGER,
                    metadata_auto_confirm_confidence REAL,
                    metadata_auto_confirm_margin REAL,
                    preferred_metadata_language TEXT,
                    source_cleanup_policy TEXT,
                    updated_at TIMESTAMP,
                    created_at TIMESTAMP
                )
            """))
            conn.execute(text("""
                INSERT INTO app_settings_old
                SELECT id, enabled_metadata_profiles, enabled_library_formats,
                       suspicious_file_threshold_bytes, metadata_auto_confirm_confidence,
                       metadata_auto_confirm_margin, preferred_metadata_language,
                       source_cleanup_policy, updated_at, created_at
                FROM app_settings
            """))
            conn.execute(text("DROP TABLE app_settings"))
            conn.execute(text("ALTER TABLE app_settings_old RENAME TO app_settings"))
            conn.commit()

        initialize_database(config)

        expected = {
            "download_rate_limit_bytes_per_second",
            "upload_rate_limit_bytes_per_second",
            "synced_download_rate_limit_bytes_per_second",
            "synced_upload_rate_limit_bytes_per_second",
        }
        with engine.connect() as conn:
            cols_after = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(app_settings)")).fetchall()
            }
            assert expected <= cols_after
    finally:
        engine.dispose()
