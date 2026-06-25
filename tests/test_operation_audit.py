from pathlib import Path

from sqlalchemy import select

from media_pilot.config import AppConfig
from media_pilot.repository.audit import record_file_operation
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import AuditLog, OperationRecord
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository


def make_config(database_dir: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"),
        shows_dir=Path("/media/library/shows"),
        database_dir=database_dir,
    )


def test_records_file_operation_with_checksums_and_audit_log(tmp_path: Path) -> None:
    source = tmp_path / "workspace" / "Movie.2026.mkv"
    target = tmp_path / "staging" / "Movie.2026.mkv"
    source.parent.mkdir()
    target.parent.mkdir()
    source.write_bytes(b"source movie")
    target.write_bytes(b"target movie")
    config = make_config(tmp_path / "db")
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/Movie.2026.mkv",
                status="created",
                current_step="safe_move",
            )
        )
        record = record_file_operation(
            session,
            task_id=task.id,
            operation_type="move",
            permission_level="safe_write",
            source_path=source,
            target_path=target,
            status="succeeded",
            actor="system",
        )
        session.commit()

        operation = session.get(OperationRecord, record.id)
        audit_log = session.scalars(select(AuditLog)).one()

    assert operation is not None
    assert operation.task_id == task.id
    assert operation.operation_type == "move"
    assert operation.permission_level == "safe_write"
    assert operation.source_path == str(source)
    assert operation.target_path == str(target)
    assert operation.status == "succeeded"
    assert operation.details["source_checksum"].startswith("sha256:")
    assert operation.details["target_checksum"].startswith("sha256:")
    assert operation.details["source_size_bytes"] == 12
    assert operation.details["target_size_bytes"] == 12
    assert audit_log.task_id == task.id
    assert audit_log.actor == "system"
    assert audit_log.object_type == "operation_record"
    assert audit_log.object_id == operation.id
    assert audit_log.action == "file_operation_recorded"
    assert audit_log.context["status"] == "succeeded"
    assert audit_log.context["permission_level"] == "safe_write"


def test_records_failed_operation_when_target_missing(tmp_path: Path) -> None:
    source = tmp_path / "workspace" / "Movie.2026.mkv"
    target = tmp_path / "staging" / "Movie.2026.mkv"
    source.parent.mkdir()
    source.write_bytes(b"source movie")
    config = make_config(tmp_path / "db")
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/Movie.2026.mkv",
                status="created",
                current_step="safe_move",
            )
        )
        record = record_file_operation(
            session,
            task_id=task.id,
            operation_type="move",
            permission_level="safe_write",
            source_path=source,
            target_path=target,
            status="failed",
            actor="system",
            error_message="target_missing",
        )
        session.commit()

        operation = session.get(OperationRecord, record.id)

    assert operation is not None
    assert operation.status == "failed"
    assert operation.details["source_checksum"].startswith("sha256:")
    assert operation.details["target_checksum"] is None
    assert operation.details["error_message"] == "target_missing"
