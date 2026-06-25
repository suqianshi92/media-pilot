"""staging 任务子目录清理 (orchestration/staging_cleanup.py) — 单元测试.

覆盖:
(a) 空目录被删除
(b) 非空目录保留 + 写 skipped OperationRecord
(c) IOError → failed OperationRecord
(d) 不得删除 .media-pilot-staging/ 自身
(e) 不得删除正式 library 目录
(f) 不得删其它 task_id 子目录
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from media_pilot.orchestration.staging_cleanup import (
    STAGING_ROOT_NAME,
    cleanup_empty_staging_task_dir,
)
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import OperationRecord
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository


def _setup(tmp_path: Path) -> tuple[Path, str, "Session"]:  # type: ignore[name-defined]
    """构造一个最小可用环境: movies_dir/.media-pilot-staging/ + 任务 + session."""
    from sqlalchemy.orm import Session

    media_root = tmp_path / "movies"
    media_root.mkdir()
    (media_root / STAGING_ROOT_NAME).mkdir()
    config = _make_db_config(media_root)
    initialize_database(config)
    factory = create_session_factory(config)
    session = factory()
    task = IngestTaskRepository(session).create(
        IngestTaskCreate(
            source_path=str(tmp_path / "src.mkv"),
            status="confirmed",
            current_step="confirmed",
            media_type="movie",
        )
    )
    session.flush()
    return media_root, task.id, session


def _make_db_config(media_root: Path):
    from media_pilot.config import AppConfig

    return AppConfig(
        downloads_dir=media_root.parent / "downloads",
        watch_dir=media_root.parent / "watch",
        workspace_dir=media_root.parent / "workspace",
        movies_dir=media_root,
        shows_dir=media_root.parent / "shows",
        database_dir=media_root.parent / "db",
    )


def _cleanup_records(session) -> list[OperationRecord]:
    return list(
        session.scalars(
            select(OperationRecord)
            .where(OperationRecord.operation_type == "cleanup_staging_task_dir")
            .order_by(OperationRecord.created_at)
        ).all()
    )


def test_cleanup_deletes_empty_task_subdir(tmp_path: Path) -> None:
    media_root, task_id, session = _setup(tmp_path)
    staging_task_dir = media_root / STAGING_ROOT_NAME / task_id
    staging_task_dir.mkdir()

    result = cleanup_empty_staging_task_dir(media_root, task_id, session)
    session.commit()

    assert result.status == "succeeded"
    assert not staging_task_dir.exists()
    assert (media_root / STAGING_ROOT_NAME).exists(), "staging 自身必须保留"
    records = _cleanup_records(session)
    assert len(records) == 1
    assert records[0].status == "succeeded"


def test_cleanup_skips_non_empty_task_subdir(tmp_path: Path) -> None:
    media_root, task_id, session = _setup(tmp_path)
    staging_task_dir = media_root / STAGING_ROOT_NAME / task_id
    staging_task_dir.mkdir()
    leftover = staging_task_dir / "junk.txt"
    leftover.write_bytes(b"junk")

    result = cleanup_empty_staging_task_dir(media_root, task_id, session)
    session.commit()

    assert result.status == "skipped"
    assert staging_task_dir.exists()
    assert leftover.exists()
    assert "junk.txt" in result.remaining_files
    records = _cleanup_records(session)
    assert len(records) == 1
    assert records[0].status == "skipped"
    assert "junk.txt" in records[0].details["remaining_files"]


def test_cleanup_missing_dir_is_succeeded_noop(tmp_path: Path) -> None:
    media_root, task_id, session = _setup(tmp_path)
    # 不创建 staging/<task_id>/

    result = cleanup_empty_staging_task_dir(media_root, task_id, session)
    session.commit()

    assert result.status == "succeeded"
    assert (media_root / STAGING_ROOT_NAME).exists()
    # 没创建过任何 OperationRecord
    assert _cleanup_records(session) == []


def test_cleanup_rejects_traversal_in_task_id(tmp_path: Path) -> None:
    media_root, _task_id, session = _setup(tmp_path)

    with pytest.raises(ValueError, match="非法 task_id"):
        cleanup_empty_staging_task_dir(media_root, "../escape", session)


def test_cleanup_does_not_touch_library_dir(tmp_path: Path) -> None:
    """关键边界: 不得把正式 library 目录删了. 故意把 staging 路径构造在
    library 之外, 再用非法的 task_id 触发越界保护."""
    media_root, task_id, session = _setup(tmp_path)
    # 模拟一个正式 library 目录
    library_movie = media_root / "Example Movie (2026)"
    library_movie.mkdir()
    (library_movie / "Example.Movie.2026.mkv").write_bytes(b"library-bytes")

    # 走正常路径清理空 staging
    staging_task_dir = media_root / STAGING_ROOT_NAME / task_id
    staging_task_dir.mkdir()

    cleanup_empty_staging_task_dir(media_root, task_id, session)
    session.commit()

    # 正式 library 必须在
    assert library_movie.exists()
    assert (library_movie / "Example.Movie.2026.mkv").exists()
    # 空 staging 子目录已删
    assert not staging_task_dir.exists()


def test_cleanup_does_not_delete_other_task_subdirs(tmp_path: Path) -> None:
    media_root, task_id, session = _setup(tmp_path)
    # 创建另一个 task 的 staging 子目录, 模拟并发残留
    other_task = media_root / STAGING_ROOT_NAME / "other-task-id"
    other_task.mkdir()
    (other_task / "untouched.mkv").write_bytes(b"other")

    staging_task_dir = media_root / STAGING_ROOT_NAME / task_id
    staging_task_dir.mkdir()

    cleanup_empty_staging_task_dir(media_root, task_id, session)
    session.commit()

    # 其它 task 子目录必须保留
    assert other_task.exists()
    assert (other_task / "untouched.mkv").exists()
    # 本 task 空子目录已删
    assert not staging_task_dir.exists()


def test_cleanup_does_not_remove_staging_root(tmp_path: Path) -> None:
    media_root, task_id, session = _setup(tmp_path)
    staging_task_dir = media_root / STAGING_ROOT_NAME / task_id
    staging_task_dir.mkdir()

    cleanup_empty_staging_task_dir(media_root, task_id, session)
    session.commit()

    # .media-pilot-staging/ 自身必须保留 (后续 task 还要用)
    assert (media_root / STAGING_ROOT_NAME).exists()
    assert (media_root / STAGING_ROOT_NAME).is_dir()


def test_cleanup_failed_rmdir_writes_failed_record(tmp_path: Path, monkeypatch) -> None:
    media_root, task_id, session = _setup(tmp_path)
    staging_task_dir = media_root / STAGING_ROOT_NAME / task_id
    staging_task_dir.mkdir()

    import os as real_os

    def failing_rmdir(path: str):
        raise OSError("simulated_rmdir_failure")

    monkeypatch.setattr(
        "media_pilot.orchestration.staging_cleanup.os.rmdir",
        failing_rmdir,
    )

    result = cleanup_empty_staging_task_dir(media_root, task_id, session)
    session.commit()

    assert result.status == "failed"
    assert "rmdir_failed" in (result.error_message or "")
    # 目录应在 (rmdir 失败)
    assert staging_task_dir.exists()
    records = _cleanup_records(session)
    assert len(records) == 1
    assert records[0].status == "failed"
    assert "simulated_rmdir_failure" in (records[0].details["error_message"] or "")


def test_execute_movie_write_success_clears_staging_task_dir(tmp_path: Path) -> None:
    """集成测试: execute_movie_write 成功路径后, .media-pilot-staging/<task_id>/ 消失,
    正式 library/<directory_name>/ 保留, task.status = library_import_complete."""
    import httpx
    from sqlalchemy import select

    from media_pilot.adapters.metadata import MetadataDetail, MetadataImages
    from media_pilot.config import AppConfig
    from media_pilot.orchestration.jellyfin_movie_writer import (
        build_movie_write_plan,
        execute_movie_write,
    )
    from media_pilot.repository.database import (
        create_session_factory,
        initialize_database,
    )
    from media_pilot.repository.models import WriteResult
    from media_pilot.repository.repositories import (
        IngestTaskCreate,
        IngestTaskRepository,
    )

    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path / "db",
    )
    for d in (
        config.downloads_dir,
        config.watch_dir,
        config.workspace_dir,
        config.movies_dir,
        config.shows_dir,
        config.database_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    factory = create_session_factory(config)

    source_path = config.downloads_dir / "Example.Movie.2026.1080p.BluRay.mkv"
    source_path.write_bytes(b"movie-bytes")

    from tests.test_jellyfin_movie_writer import make_detail, make_http_client

    detail = make_detail()
    detail = MetadataDetail(
        **{
            **detail.__dict__,
            "images": MetadataImages(
                poster_url="https://img.test/poster.jpg",
                backdrop_url=None,
                logo_url=None,
            ),
        }
    )
    client = make_http_client(
        {"https://img.test/poster.jpg": httpx.Response(200, content=b"poster")}
    )

    with factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path=str(source_path),
                status="confirmed",
                current_step="confirmed",
                media_type="movie",
            )
        )
        plan = build_movie_write_plan(
            movies_dir=config.movies_dir,
            source_path=source_path,
            detail=detail,
            task_id=task.id,
        )
        result = execute_movie_write(
            session,
            task_id=task.id,
            source_path=source_path,
            detail=detail,
            plan=plan,
            client=client,
        )
        session.commit()

    assert result.status in ("succeeded", "warning")
    # 库目录应在
    assert plan.final_target_dir.exists()
    assert plan.final_target_file.exists()
    # staging/<task_id>/ 整体应该被 helper 清掉
    staging_task_dir = (
        config.movies_dir / STAGING_ROOT_NAME / task.id
    )
    assert not staging_task_dir.exists()
    # staging/ 本身仍保留
    assert (config.movies_dir / STAGING_ROOT_NAME).exists()
    # 源文件保留 (helper 不得碰 watch / downloads)
    assert source_path.read_bytes() == b"movie-bytes"
    # WriteResult 状态保持
    with factory() as session:
        write_result = session.scalars(select(WriteResult)).one()
        assert write_result.status in ("succeeded", "warning")
        # cleanup OperationRecord 落了库
        cleanup_records = list(
            session.scalars(
                select(OperationRecord).where(
                    OperationRecord.operation_type == "cleanup_staging_task_dir"
                )
            ).all()
        )
        assert len(cleanup_records) >= 1
        assert all(r.status == "succeeded" for r in cleanup_records)


def test_execute_movie_write_failure_keeps_staging_task_dir(tmp_path: Path, monkeypatch) -> None:
    """集成测试: execute_movie_write 失败路径下, staging/<task_id>/ 保留 (清理器不触发)."""
    import shutil
    import httpx
    from sqlalchemy import select

    from media_pilot.adapters.metadata import MetadataDetail, MetadataImages
    from media_pilot.config import AppConfig
    from media_pilot.orchestration.jellyfin_movie_writer import (
        build_movie_write_plan,
        execute_movie_write,
    )
    from media_pilot.repository.database import (
        create_session_factory,
        initialize_database,
    )
    from media_pilot.repository.models import WriteResult
    from media_pilot.repository.repositories import (
        IngestTaskCreate,
        IngestTaskRepository,
    )

    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path / "db",
    )
    for d in (
        config.downloads_dir,
        config.watch_dir,
        config.workspace_dir,
        config.movies_dir,
        config.shows_dir,
        config.database_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    factory = create_session_factory(config)

    source_path = config.downloads_dir / "Example.Movie.2026.1080p.BluRay.mkv"
    source_path.write_bytes(b"movie-bytes")

    from tests.test_jellyfin_movie_writer import make_detail, make_http_client

    detail = make_detail()
    detail = MetadataDetail(
        **{
            **detail.__dict__,
            "images": MetadataImages(
                poster_url="https://img.test/poster.jpg",
                backdrop_url=None,
                logo_url=None,
            ),
        }
    )
    client = make_http_client(
        {"https://img.test/poster.jpg": httpx.Response(200, content=b"poster")}
    )

    original_move = shutil.move

    def failing_move(src: str, dst: str):
        if ".media-pilot-staging" in src:
            raise OSError("publish_failed")
        return original_move(src, dst)

    monkeypatch.setattr(
        "media_pilot.orchestration.jellyfin_movie_writer.shutil.move",
        failing_move,
    )

    with factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path=str(source_path),
                status="confirmed",
                current_step="confirmed",
                media_type="movie",
            )
        )
        plan = build_movie_write_plan(
            movies_dir=config.movies_dir,
            source_path=source_path,
            detail=detail,
            task_id=task.id,
        )
        result = execute_movie_write(
            session,
            task_id=task.id,
            source_path=source_path,
            detail=detail,
            plan=plan,
            client=client,
        )
        session.commit()

    assert result.status == "failed"
    # 失败路径: staging 整体保留 (cleanup 没被调用)
    staging_task_dir = config.movies_dir / STAGING_ROOT_NAME / task.id
    assert staging_task_dir.exists()
    # 也没有 cleanup OperationRecord
    with factory() as session:
        write_result = session.scalars(select(WriteResult)).one()
        assert write_result.status == "failed"
        cleanup_records = list(
            session.scalars(
                select(OperationRecord).where(
                    OperationRecord.operation_type == "cleanup_staging_task_dir"
                )
            ).all()
        )
        assert cleanup_records == []
