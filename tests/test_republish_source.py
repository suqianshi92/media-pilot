"""Republish source preparation tests.

These cover the correction/re-ingest semantics shared by manual metadata
selection and Agent freeform revoke with skip_post_revoke_decision=true.
"""

from __future__ import annotations

from pathlib import Path

from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import (
    FileAsset,
    IngestTask,
    MediaSourceSelection,
    OperationRecord,
    WritePlan,
    WriteResult,
)
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository


def _make_config(tmp_path: Path) -> AppConfig:
    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    for path in (
        config.downloads_dir,
        config.watch_dir,
        config.workspace_dir,
        config.movies_dir,
        config.shows_dir,
        config.database_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return config


def _create_published_file_task(session_factory, *, config: AppConfig) -> str:
    missing_source = config.downloads_dir / "missing.mkv"
    publish_dir = config.movies_dir / "Published Movie (2026)"
    publish_dir.mkdir(parents=True)
    published_video = publish_dir / "Published Movie (2026).mkv"
    published_video.write_bytes(b"movie")

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(missing_source),
            status="library_import_complete",
            current_step="library_import_complete",
            media_type="movie",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(missing_source),
            selected_path=str(missing_source),
            payload={},
        ))
        session.add(WriteResult(
            task_id=task.id,
            status="succeeded",
            payload={"target_dir": str(publish_dir), "target_file": str(published_video)},
        ))
        session.add(WritePlan(
            task_id=task.id,
            target_dir=str(publish_dir),
            target_file=str(published_video),
            nfo_path=str(publish_dir / "Published Movie (2026).nfo"),
            payload={},
        ))
        session.add(FileAsset(
            task_id=task.id,
            role="library_video",
            path=str(published_video),
            size_bytes=published_video.stat().st_size,
        ))
        session.commit()
        return task.id


def _create_published_bdmv_task(session_factory, *, config: AppConfig) -> str:
    missing_source = config.downloads_dir / "Missing BDMV"
    publish_dir = config.movies_dir / "Published BDMV (2026)"
    (publish_dir / "BDMV").mkdir(parents=True)
    (publish_dir / "BDMV" / "index.bdmv").write_text("index")
    (publish_dir / "BDMV" / "STREAM").mkdir()
    (publish_dir / "BDMV" / "STREAM" / "00001.m2ts").write_bytes(b"movie")
    (publish_dir / "CERTIFICATE").mkdir()
    (publish_dir / "CERTIFICATE" / "id.bdmv").write_text("cert")

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(missing_source),
            status="library_import_complete",
            current_step="library_import_complete",
            media_type="movie",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(missing_source),
            selected_path=None,
            payload={"source_kind": "bdmv", "bdmv_dir": str(missing_source / "BDMV")},
        ))
        session.add(WriteResult(
            task_id=task.id,
            status="succeeded",
            payload={"target_dir": str(publish_dir), "source_kind": "bdmv"},
        ))
        session.add(WritePlan(
            task_id=task.id,
            target_dir=str(publish_dir),
            target_file=str(publish_dir / "BDMV" / "index.bdmv"),
            nfo_path=str(publish_dir / "BDMV" / "index.nfo"),
            payload={"source_kind": "bdmv"},
        ))
        session.add(FileAsset(
            task_id=task.id,
            role="library_bdmv",
            path=str(publish_dir / "BDMV"),
            size_bytes=None,
        ))
        session.commit()
        return task.id


def test_prepare_republish_source_uses_published_file_when_original_source_missing(
    tmp_path: Path,
) -> None:
    from sqlalchemy import select

    from media_pilot.services.republish_source import prepare_republish_source

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)
    task_id = _create_published_file_task(session_factory, config=config)

    with session_factory() as session:
        result = prepare_republish_source(session=session, config=config, task_id=task_id)
        session.commit()

    assert result.ok, result.summary
    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "processing"
        assert task.current_step == "post_revoke_reingest"
        assert task.source_path.endswith("/republish-source")

        selection = session.scalars(
            select(MediaSourceSelection)
            .where(MediaSourceSelection.task_id == task_id)
            .order_by(MediaSourceSelection.created_at.desc())
        ).first()
        assert selection is not None
        assert selection.payload["selection_source"] == "published_output_reselect"
        assert selection.payload["source_kind"] == "file"
        assert Path(selection.selected_path).read_bytes() == b"movie"

        assert session.scalars(
            select(WritePlan).where(WritePlan.task_id == task_id)
        ).first() is None
        assert session.scalars(
            select(WriteResult).where(WriteResult.task_id == task_id)
        ).first() is None


def test_prepare_republish_source_uses_published_bdmv_when_original_source_missing(
    tmp_path: Path,
) -> None:
    from sqlalchemy import select

    from media_pilot.services.republish_source import prepare_republish_source

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)
    task_id = _create_published_bdmv_task(session_factory, config=config)

    with session_factory() as session:
        result = prepare_republish_source(session=session, config=config, task_id=task_id)
        session.commit()

    assert result.ok, result.summary
    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "processing"
        assert task.source_path.endswith("/republish-source")

        selection = session.scalars(
            select(MediaSourceSelection)
            .where(MediaSourceSelection.task_id == task_id)
            .order_by(MediaSourceSelection.created_at.desc())
        ).first()
        assert selection is not None
        assert selection.selected_path is None
        assert selection.payload["source_kind"] == "bdmv"
        assert Path(selection.payload["bdmv_dir"]).joinpath("index.bdmv").exists()
        assert Path(selection.payload["certificate_dir"]).joinpath("id.bdmv").exists()


def test_prepare_republish_source_recovers_from_trash_when_publish_dir_was_already_removed(
    tmp_path: Path,
) -> None:
    """旧 bug 可能先删发布目录再因 FK 失败; 此时应回退使用回收区源文件。"""
    from sqlalchemy import select

    from media_pilot.services.republish_source import prepare_republish_source

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)
    task_id = _create_published_file_task(session_factory, config=config)

    publish_dir = config.movies_dir / "Published Movie (2026)"
    trashed = tmp_path / "trash" / "Published Movie.mkv"
    trashed.parent.mkdir(parents=True)
    trashed.write_bytes(b"from trash")
    with session_factory() as session:
        session.add(OperationRecord(
            task_id=task_id,
            operation_type="source_input_trashed",
            permission_level="write",
            source_path=str(config.downloads_dir / "missing.mkv"),
            target_path=str(trashed),
            status="succeeded",
            details={"policy": "trash"},
        ))
        session.commit()
    import shutil

    shutil.rmtree(publish_dir)

    with session_factory() as session:
        result = prepare_republish_source(session=session, config=config, task_id=task_id)
        session.commit()

    assert result.ok, result.summary
    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.source_path.endswith("/republish-source")
        selection = session.scalars(
            select(MediaSourceSelection)
            .where(MediaSourceSelection.task_id == task_id)
            .order_by(MediaSourceSelection.created_at.desc())
        ).first()
        assert selection is not None
        assert selection.payload["recovery_source"] == "trashed_input_recovery"
        assert Path(selection.selected_path).read_bytes() == b"from trash"


def test_revoke_publish_tool_skip_prepares_republish_source_instead_of_deleting_task(
    tmp_path: Path,
) -> None:
    from sqlalchemy import select

    from media_pilot.agent.tools.base import ToolContext
    from media_pilot.agent.tools.write import make_revoke_publish

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)
    task_id = _create_published_file_task(session_factory, config=config)

    with session_factory() as session:
        tool = make_revoke_publish()
        result = tool.handler(
            ToolContext(session=session, config=config, task_id=task_id, run_id=None),
            {"task_id": task_id, "skip_post_revoke_decision": True},
        )
        session.commit()

    assert result.status == "success"
    assert result.data["republish_source_prepared"] is True
    assert result.data["waiting_for_post_revoke_action"] is False

    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "processing"
        assert session.scalars(
            select(WritePlan).where(WritePlan.task_id == task_id)
        ).first() is None
