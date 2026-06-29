from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from media_pilot.config import AppConfig
from media_pilot.deployment.migrate_sqlite_to_postgres import (
    STALE_ACTIVE_ERROR,
    MigrationError,
    main,
    migrate_sqlite_to_database,
)
from media_pilot.repository.database import Base, initialize_database
from media_pilot.repository.models import AgentRun, DownloadTask, IngestTask, MediaCandidate


def _config(root: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=root / "downloads",
        watch_dir=root / "watch",
        workspace_dir=root / "workspace",
        movies_dir=root / "movies",
        shows_dir=root / "shows",
        database_dir=root / "db",
    )


def _session_for_sqlite(path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{path}", future=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine, SessionLocal()


def _init_source(root: Path) -> Path:
    cfg = _config(root)
    cfg.database_dir.mkdir(parents=True)
    initialize_database(cfg)
    return cfg.database_dir / "media-pilot.sqlite3"


def _target_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path}"


def test_migrates_rows_without_real_data(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    source_engine, session = _session_for_sqlite(source)
    try:
        task = IngestTask(
            id="task-1",
            source_path="/data/watch/example.mp4",
            status="library_import_complete",
            current_step="library_import_complete",
            title="Example",
        )
        candidate = MediaCandidate(
            id="candidate-1",
            task_id="task-1",
            source="tmdb",
            media_type="movie",
            title="Example",
            payload={"provider_id": "movie:1"},
        )
        session.add_all([task, candidate])
        session.commit()
    finally:
        session.close()
        source_engine.dispose()

    target = tmp_path / "target.sqlite3"
    counts = migrate_sqlite_to_database(
        sqlite_path=source,
        database_url=_target_url(target),
    )

    target_engine, target_session = _session_for_sqlite(target)
    try:
        assert counts["ingest_tasks"] == 1
        assert counts["media_candidates"] == 1
        migrated_task = target_session.get(IngestTask, "task-1")
        assert migrated_task is not None
        assert migrated_task.title == "Example"
        migrated_candidate = target_session.get(MediaCandidate, "candidate-1")
        assert migrated_candidate is not None
        assert migrated_candidate.payload == {"provider_id": "movie:1"}
    finally:
        target_session.close()
        target_engine.dispose()


def test_migration_restores_cyclic_download_ingest_links(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    source_engine, session = _session_for_sqlite(source)
    try:
        task = IngestTask(
            id="task-1",
            source_path="/data/downloads/example.mp4",
            status="library_import_complete",
            current_step="library_import_complete",
            source_download_task_id="download-1",
        )
        download = DownloadTask(
            id="download-1",
            title="Example",
            source="prowlarr",
            save_path="/data/downloads",
            status="completed",
            ingest_task_id="task-1",
        )
        session.add_all([task, download])
        session.commit()
    finally:
        session.close()
        source_engine.dispose()

    target = tmp_path / "target.sqlite3"
    migrate_sqlite_to_database(sqlite_path=source, database_url=_target_url(target))

    target_engine, target_session = _session_for_sqlite(target)
    try:
        migrated_task = target_session.get(IngestTask, "task-1")
        migrated_download = target_session.get(DownloadTask, "download-1")
        assert migrated_task.source_download_task_id == "download-1"
        assert migrated_download.ingest_task_id == "task-1"
    finally:
        target_session.close()
        target_engine.dispose()


def test_migration_marks_stale_active_run_when_requested(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    source_engine, session = _session_for_sqlite(source)
    try:
        task = IngestTask(
            id="task-1",
            source_path="/data/watch/stuck.mp4",
            status="agent_running",
            current_step="agent_running",
        )
        run = AgentRun(
            id="run-1",
            task_id="task-1",
            status="active",
            current_step="agent_start",
        )
        session.add_all([task, run])
        session.commit()
    finally:
        session.close()
        source_engine.dispose()

    target = tmp_path / "target.sqlite3"
    migrate_sqlite_to_database(
        sqlite_path=source,
        database_url=_target_url(target),
        clean_stale_active_runs=True,
    )

    target_engine, target_session = _session_for_sqlite(target)
    try:
        migrated_task = target_session.get(IngestTask, "task-1")
        migrated_run = target_session.get(AgentRun, "run-1")
        assert migrated_task.status == "agent_failed"
        assert migrated_task.current_step == "postgres_migration_stale_active"
        assert migrated_task.failure_reason == STALE_ACTIVE_ERROR
        assert migrated_run.status == "failed"
        assert migrated_run.error_message == STALE_ACTIVE_ERROR
    finally:
        target_session.close()
        target_engine.dispose()


def test_migration_refuses_non_empty_target(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    target = tmp_path / "target.sqlite3"
    Base.metadata.create_all(create_engine(_target_url(target), future=True))

    target_engine, target_session = _session_for_sqlite(target)
    try:
        target_session.add(
            IngestTask(
                id="existing-task",
                source_path="/data/watch/existing.mp4",
                status="created",
            )
        )
        target_session.commit()
    finally:
        target_session.close()
        target_engine.dispose()

    with pytest.raises(MigrationError, match="target database is not empty"):
        migrate_sqlite_to_database(sqlite_path=source, database_url=_target_url(target))


def test_cli_does_not_echo_raw_exception_message(monkeypatch, tmp_path: Path, caplog) -> None:
    def _raise(**_kwargs):
        raise RuntimeError("secret movie title should not be printed")

    monkeypatch.setattr(
        "media_pilot.deployment.migrate_sqlite_to_postgres.migrate_sqlite_to_database",
        _raise,
    )

    result = main([
        "--sqlite-path",
        str(tmp_path / "source.sqlite3"),
        "--database-url",
        _target_url(tmp_path / "target.sqlite3"),
    ])

    assert result == 1
    assert "RuntimeError" in caplog.text
    assert "secret movie title" not in caplog.text
