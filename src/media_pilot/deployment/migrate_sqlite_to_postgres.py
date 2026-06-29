"""SQLite → PostgreSQL 数据迁移入口.

该脚本不内置任何生产路径, 只接受显式参数。默认要求目标库为空,
避免重复导入。为方便自动化测试, ``--database-url`` 接受任意
SQLAlchemy URL; 生产应传 ``postgresql+psycopg://...``。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, DateTime, create_engine, func, inspect, select, update
from sqlalchemy.engine import Engine

from media_pilot.config import AppConfig
from media_pilot.repository import models  # noqa: F401
from media_pilot.repository.database import Base, initialize_database

logger = logging.getLogger(__name__)

STALE_ACTIVE_ERROR = "stale active run during PostgreSQL migration"
DEFERRED_FK_COLUMNS = {
    "ingest_tasks": {"source_download_task_id"},
    "download_tasks": {"ingest_task_id"},
}


def migrate_sqlite_to_database(
    *,
    sqlite_path: Path,
    database_url: str,
    clean_stale_active_runs: bool = False,
) -> dict[str, int]:
    if not sqlite_path.is_file():
        raise MigrationError(f"SQLite file does not exist: {sqlite_path}")

    source_engine = create_engine(f"sqlite+pysqlite:///{sqlite_path}", future=True)
    target_engine = create_engine(database_url, future=True)
    try:
        _initialize_target_database(database_url)
        _assert_target_empty(target_engine)
        stale_task_ids = (
            _find_stale_active_task_ids(source_engine) if clean_stale_active_runs else set()
        )
        counts = _copy_all_tables(
            source_engine=source_engine,
            target_engine=target_engine,
            stale_task_ids=stale_task_ids,
        )
        _restore_deferred_foreign_keys(source_engine=source_engine, target_engine=target_engine)
        return counts
    finally:
        source_engine.dispose()
        target_engine.dispose()


class MigrationError(RuntimeError):
    """迁移失败 — 错误信息不得包含密钥或真实业务数据内容。"""


def _initialize_target_database(database_url: str) -> None:
    dummy = Path("/tmp/media-pilot-migration")
    initialize_database(
        AppConfig(
            downloads_dir=dummy / "downloads",
            watch_dir=dummy / "watch",
            workspace_dir=dummy / "workspace",
            movies_dir=dummy / "movies",
            shows_dir=dummy / "shows",
            database_dir=dummy / "db",
            database_url=database_url,
        )
    )


def _assert_target_empty(engine: Engine) -> None:
    non_empty: list[str] = []
    with engine.connect() as conn:
        for table in _migration_tables():
            count = conn.execute(select(func.count()).select_from(table)).scalar_one()
            if count:
                non_empty.append(table.name)
    if non_empty:
        names = ", ".join(sorted(non_empty))
        raise MigrationError(f"target database is not empty: {names}")


def _find_stale_active_task_ids(engine: Engine) -> set[str]:
    rows = _fetch_table_rows(engine, "agent_runs")
    task_status = {
        row["id"]: row.get("status")
        for row in _fetch_table_rows(engine, "ingest_tasks")
    }
    return {
        row["task_id"]
        for row in rows
        if row.get("status") == "active"
        and row.get("task_id")
        and task_status.get(row["task_id"]) == "agent_running"
    }


def _copy_all_tables(
    *,
    source_engine: Engine,
    target_engine: Engine,
    stale_task_ids: set[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    with target_engine.begin() as target_conn:
        for table in _migration_tables():
            rows = list(_iter_rows_for_table(source_engine, table.name, stale_task_ids))
            if rows:
                target_conn.execute(table.insert(), rows)
            counts[table.name] = len(rows)
    return counts


def _migration_tables():
    # 使用模型定义顺序, 避免 SQLAlchemy sorted_tables 因
    # ingest_tasks/download_tasks 双向可空外键产生排序 warning。
    return list(Base.metadata.tables.values())


def _iter_rows_for_table(
    source_engine: Engine,
    table_name: str,
    stale_task_ids: set[str],
) -> Iterable[dict[str, Any]]:
    if table_name not in inspect(source_engine).get_table_names():
        return
    table = Base.metadata.tables[table_name]
    source_columns = _source_column_names(source_engine, table_name)
    target_columns = {column.name for column in table.columns}
    copy_columns = source_columns & target_columns

    for raw_row in _fetch_table_rows(source_engine, table_name):
        row = {
            column.name: _coerce_value(column, raw_row.get(column.name))
            for column in table.columns
            if column.name in copy_columns
        }
        _apply_stale_active_cleanup(table_name, row, stale_task_ids)
        _defer_cycle_foreign_keys(table_name, row)
        yield row


def _source_column_names(engine: Engine, table_name: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table_name)}


def _fetch_table_rows(engine: Engine, table_name: str) -> list[dict[str, Any]]:
    table = Base.metadata.tables[table_name]
    if table_name not in inspect(engine).get_table_names():
        return []
    columns = [
        table.c[column_name]
        for column_name in _source_column_names(engine, table_name)
        if column_name in table.c
    ]
    if not columns:
        return []
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(*columns)).mappings()]


def _coerce_value(column, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, DateTime) and isinstance(value, str):
        return _parse_datetime(value)
    if isinstance(column.type, Boolean):
        return bool(value)
    if _is_json_column(column) and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")


def _is_json_column(column) -> bool:
    return column.type.__class__.__name__.upper() == "JSON"


def _apply_stale_active_cleanup(
    table_name: str, row: dict[str, Any], stale_task_ids: set[str]
) -> None:
    if not stale_task_ids:
        return
    if table_name == "ingest_tasks" and row.get("id") in stale_task_ids:
        row["status"] = "agent_failed"
        row["current_step"] = "postgres_migration_stale_active"
        row["failure_reason"] = STALE_ACTIVE_ERROR
    if table_name == "agent_runs" and row.get("task_id") in stale_task_ids:
        if row.get("status") == "active":
            row["status"] = "failed"
            row["current_step"] = "postgres_migration_stale_active"
            row["error_message"] = STALE_ACTIVE_ERROR


def _defer_cycle_foreign_keys(table_name: str, row: dict[str, Any]) -> None:
    for column_name in DEFERRED_FK_COLUMNS.get(table_name, set()):
        if column_name in row:
            row[column_name] = None


def _restore_deferred_foreign_keys(*, source_engine: Engine, target_engine: Engine) -> None:
    with target_engine.begin() as target_conn:
        for row in _fetch_table_rows(source_engine, "ingest_tasks"):
            value = row.get("source_download_task_id")
            if value:
                target_conn.execute(
                    update(Base.metadata.tables["ingest_tasks"])
                    .where(Base.metadata.tables["ingest_tasks"].c.id == row["id"])
                    .values(source_download_task_id=value)
                )
        for row in _fetch_table_rows(source_engine, "download_tasks"):
            value = row.get("ingest_task_id")
            if value:
                target_conn.execute(
                    update(Base.metadata.tables["download_tasks"])
                    .where(Base.metadata.tables["download_tasks"].c.id == row["id"])
                    .values(ingest_task_id=value)
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate Media Pilot SQLite data")
    parser.add_argument("--sqlite-path", required=True, type=Path)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--clean-stale-active-runs", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[migration] %(levelname)s: %(message)s")
    try:
        counts = migrate_sqlite_to_database(
            sqlite_path=args.sqlite_path,
            database_url=args.database_url,
            clean_stale_active_runs=args.clean_stale_active_runs,
        )
    except MigrationError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:
        logger.error("migration failed: %s", exc.__class__.__name__)
        return 1

    logger.info("migration completed; copied %d tables", len(counts))
    for table_name, count in sorted(counts.items()):
        logger.info("%s: %d", table_name, count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
