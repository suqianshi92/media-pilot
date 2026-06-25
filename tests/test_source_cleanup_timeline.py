"""Section 6 backend test: source cleanup OperationRecord 事件被 _build_timeline 渲染为可读摘要."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def config_with_trash(tmp_path: Path):
    from media_pilot.config.settings import AppConfig

    downloads = tmp_path / "downloads"
    watch = tmp_path / "watch"
    workspace = tmp_path / "workspace"
    movies = tmp_path / "library" / "movies"
    shows = tmp_path / "library" / "shows"
    trash = tmp_path / "trash"
    db = tmp_path / "db"
    for d in (downloads, watch, workspace, movies, shows, db, trash):
        d.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        downloads_dir=downloads,
        watch_dir=watch,
        workspace_dir=workspace,
        movies_dir=movies,
        shows_dir=shows,
        database_dir=db,
        trash_dir=trash,
    )


@pytest.fixture
def session_factory(config_with_trash):
    from media_pilot.repository.database import (
        create_session_factory,
        initialize_database,
    )

    initialize_database(config_with_trash)
    return create_session_factory(config_with_trash)


def _make_published_task_with_cleanup_ops(
    session_factory, config_with_trash, *,
    kept_path: str = "/downloads/movie-kept.mkv",
    trashed_path: str = "/downloads/movie-trashed.mkv",
    failed_path: str = "/downloads/movie-failed.mkv",
):
    """Create a library_import_complete task + 3 source cleanup OperationRecords."""
    from media_pilot.repository.models import (
        AuditLog,
        AdapterCall,
        OperationRecord,
        WriteResult,
    )
    from media_pilot.repository.repositories import (
        IngestTaskCreate,
        IngestTaskRepository,
    )

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path="/downloads/movie-kept.mkv",
            status="library_import_complete",
            current_step="library_import_complete",
            media_type="movie",
        ))
        session.add(WriteResult(
            task_id=task.id, status="succeeded",
            payload={"final_target_dir": "/library/Movie (2026)"},
        ))
        now = datetime.now(timezone.utc)
        session.add(OperationRecord(
            task_id=task.id, operation_type="source_input_kept",
            permission_level="write", source_path=kept_path,
            status="succeeded",
            details={"reason": "user_reply:keep_input"},
            created_at=now,
        ))
        session.add(OperationRecord(
            task_id=task.id, operation_type="source_input_trashed",
            permission_level="write", source_path=trashed_path,
            target_path="/trash/movie-trashed.mkv",
            status="succeeded",
            details={"via": "decision_reply:trash_input"},
            created_at=now,
        ))
        session.add(OperationRecord(
            task_id=task.id, operation_type="source_input_cleanup_failed",
            permission_level="write", source_path=failed_path,
            status="failed",
            details={"reason": "refuse_protected_root", "via": "decision_reply:trash_input"},
            created_at=now,
        ))
        session.commit()
        return task.id


def _build_timeline(session, task_id: str):
    from media_pilot.api.task_mapper import _build_timeline
    from media_pilot.repository.models import OperationRecord
    from media_pilot.repository.repositories import IngestTaskRepository
    from sqlalchemy import select

    with session as s:
        task = IngestTaskRepository(s).get(task_id)
        ops = s.scalars(
            select(OperationRecord).where(OperationRecord.task_id == task_id),
        ).all()
        return _build_timeline(task, ops, [], [])


def test_timeline_renders_source_input_kept(session_factory, config_with_trash) -> None:
    task_id = _make_published_task_with_cleanup_ops(session_factory, config_with_trash)
    with session_factory() as session:
        events = _build_timeline(session, task_id)
    keys = {e.key for e in events}
    assert "source_input_kept" in keys
    event = next(e for e in events if e.key == "source_input_kept")
    assert event.title == "源文件保留"
    assert "movie-kept.mkv" in event.detail, (
        f"应展示文件名, 实际 detail={event.detail!r}"
    )
    assert event.tone == "default"


def test_timeline_renders_source_input_trashed(session_factory, config_with_trash) -> None:
    task_id = _make_published_task_with_cleanup_ops(session_factory, config_with_trash)
    with session_factory() as session:
        events = _build_timeline(session, task_id)
    keys = {e.key for e in events}
    assert "source_input_trashed" in keys
    event = next(e for e in events if e.key == "source_input_trashed")
    assert event.title == "源文件移入回收区"
    # 必须同时显示原文件名 + 回收区目标名
    assert "movie-trashed.mkv" in event.detail
    assert "回收区" in event.detail


def test_timeline_renders_source_input_cleanup_failed(
    session_factory, config_with_trash,
) -> None:
    task_id = _make_published_task_with_cleanup_ops(session_factory, config_with_trash)
    with session_factory() as session:
        events = _build_timeline(session, task_id)
    keys = {e.key for e in events}
    assert "source_input_cleanup_failed" in keys
    event = next(e for e in events if e.key == "source_input_cleanup_failed")
    assert event.title == "源文件清理失败"
    # 失败必须显示原因 (而非直接 dump details JSON)
    assert "refuse_protected_root" in event.detail
    # 不应暴露 details 的 JSON 字符串
    assert "{" not in event.detail
    assert event.tone == "warning"


def test_timeline_does_not_expose_raw_details_json(
    session_factory, config_with_trash,
) -> None:
    """所有 source cleanup 事件都禁止直接把 OperationRecord.details 当字符串展示."""
    task_id = _make_published_task_with_cleanup_ops(session_factory, config_with_trash)
    with session_factory() as session:
        events = _build_timeline(session, task_id)
    cleanup_events = [
        e for e in events
        if e.key in ("source_input_kept", "source_input_trashed", "source_input_cleanup_failed")
    ]
    assert cleanup_events, "应至少产生 3 个 cleanup 事件"
    for ev in cleanup_events:
        assert "{" not in ev.detail, f"事件 {ev.key} 暴露了原始 JSON: {ev.detail!r}"
        assert "}" not in ev.detail, f"事件 {ev.key} 暴露了原始 JSON: {ev.detail!r}"


def test_timeline_cleanup_failure_does_not_set_ingest_failure_tone(
    session_factory, config_with_trash,
) -> None:
    """source_input_cleanup_failed 的 tone 必须是 warning, 不能是 error (避免被读成入库失败)."""
    task_id = _make_published_task_with_cleanup_ops(session_factory, config_with_trash)
    with session_factory() as session:
        events = _build_timeline(session, task_id)
    failed_ev = next(e for e in events if e.key == "source_input_cleanup_failed")
    assert failed_ev.tone != "error", (
        f"清理失败的 tone 必须是 warning (非入库失败), 实际={failed_ev.tone}"
    )
    # 对照: 同时存在的 library_import_complete 应该是 success
    complete_ev = next(e for e in events if e.key == "library_import_complete")
    assert complete_ev.tone == "success"
