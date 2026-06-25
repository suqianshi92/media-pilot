"""video_source_resolver 共享主视频解析器测试.

覆盖 Issue B 的核心修复: watch 目录型单电影输入, 即使
MediaSourceSelection 还没写, 解析器也能拿到真实主视频文件,
不会把目录路径喂给 build_movie_write_plan / execute_movie_write.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _make_config(tmp_path: Path):
    from media_pilot.config.settings import AppConfig

    return AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "ws",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path,
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        tmdb_api_key="test-tmdb-key",
    )


@pytest.fixture
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from media_pilot.repository.database import Base

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _create_task(session, *, source_path, media_type="movie"):
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
    return IngestTaskRepository(session).create(IngestTaskCreate(
        source_path=str(source_path),
        status="discovered",
        current_step="scanned",
        media_type=media_type,
    ))


class TestSingleFileSource:
    def test_file_source_returns_file_directly(self, tmp_path, session):
        """task.source_path 是文件 → 直接用, 不需要 selection."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        video = config.downloads_dir / "Example.Movie.2026.mkv"
        video.write_bytes(b"video-bytes")

        task = _create_task(session, source_path=video)

        from media_pilot.services.video_source_resolver import (
            resolve_main_video_for_publish,
        )
        result = resolve_main_video_for_publish(
            session, task, config=config,
        )

        assert result.error_code is None
        assert result.video_path == video
        assert result.created_selection is False


class TestExistingSelection:
    def test_existing_selection_is_reused(self, tmp_path, session):
        """已有 MediaSourceSelection → 优先用 selected_path, 不重扫."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        video = config.downloads_dir / "Example.Movie.2026.mkv"
        video.write_bytes(b"video-bytes")
        task = _create_task(session, source_path=video)

        from media_pilot.repository.models import MediaSourceSelection
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(video),
            selected_path=str(video),
            confidence=1.0,
            reason="user_decision:select_primary_video",
        ))
        session.commit()

        from media_pilot.services.video_source_resolver import (
            resolve_main_video_for_publish,
        )
        result = resolve_main_video_for_publish(
            session, task, config=config,
        )

        assert result.error_code is None
        assert result.video_path == video
        assert result.created_selection is False


class TestSingleVideoDirAutoCreatesSelection:
    def test_dir_with_one_video_auto_creates_selection(
        self, tmp_path, session,
    ):
        """目录型单电影输入 (e.g. Warcraft ... [YTS.MX]/foo.mkv) —
        解析器自动补写 MediaSourceSelection 并返回 .mkv 文件路径.
        这是 Issue B 修复的核心: 后续 publish 不再回退到 task.source_path 目录.
        """
        from sqlalchemy import select
        from media_pilot.repository.models import MediaSourceSelection

        config = _make_config(tmp_path)
        config.watch_dir.mkdir(parents=True, exist_ok=True)
        source_dir = config.watch_dir / "Warcraft ... [YTS.MX]"
        source_dir.mkdir(parents=True, exist_ok=True)
        video = source_dir / "Warcraft.2016.1080p.BluRay.x264.mkv"
        video.write_bytes(b"video-bytes")
        # 噪声文件
        (source_dir / "Subs.jpg").write_bytes(b"jpg")
        (source_dir / "info.txt").write_text("info")

        task = _create_task(session, source_path=source_dir)

        from media_pilot.services.video_source_resolver import (
            resolve_main_video_for_publish,
        )
        result = resolve_main_video_for_publish(
            session, task, config=config,
        )

        # 返回的是 .mkv 文件, 不是目录
        assert result.error_code is None
        assert result.video_path == video
        assert result.video_path.is_file()
        assert result.video_path.suffix == ".mkv"
        assert result.created_selection is True

        # MediaSourceSelection 已自动写入
        sel = session.scalars(
            select(MediaSourceSelection)
            .where(MediaSourceSelection.task_id == task.id)
            .order_by(MediaSourceSelection.created_at.desc())
        ).first()
        assert sel is not None
        assert sel.selected_path == str(video)
        assert sel.input_path == str(source_dir)
        assert sel.reason == "auto_single_video_dir"
        assert "auxiliary_videos" in sel.payload
        assert "excluded" in sel.payload
        assert "subtitle_candidates" in sel.payload


class TestMultipleVideosError:
    def test_dir_with_multiple_videos_returns_multiple_videos_error(
        self, tmp_path, session,
    ):
        """目录里多个主视频 → 不再自动选, 返回 multiple_videos 让上层决策."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source_dir = config.downloads_dir / "Multi.Movie.2026"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "Movie.2026.mkv").write_bytes(b"a" * 1024)
        (source_dir / "Movie.2026.Extra.mkv").write_bytes(b"b" * 2048)

        task = _create_task(session, source_path=source_dir)

        from media_pilot.services.video_source_resolver import (
            resolve_main_video_for_publish,
        )
        result = resolve_main_video_for_publish(
            session, task, config=config,
        )

        assert result.error_code == "multiple_videos"
        assert result.video_path is None
        assert "2" in (result.error_message or "")


class TestNoVideosError:
    def test_dir_with_no_videos_returns_no_main_video_error(
        self, tmp_path, session,
    ):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source_dir = config.downloads_dir / "Empty.Movie.2026"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "Subs.jpg").write_bytes(b"jpg")
        (source_dir / "info.txt").write_text("info")

        task = _create_task(session, source_path=source_dir)

        from media_pilot.services.video_source_resolver import (
            resolve_main_video_for_publish,
        )
        result = resolve_main_video_for_publish(
            session, task, config=config,
        )

        assert result.error_code == "no_main_video"
        assert result.video_path is None


class TestSourceMissing:
    def test_missing_source_path_returns_source_missing(
        self, tmp_path, session,
    ):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        missing = config.downloads_dir / "Missing.Movie.2026.mkv"

        task = _create_task(session, source_path=missing)

        from media_pilot.services.video_source_resolver import (
            resolve_main_video_for_publish,
        )
        result = resolve_main_video_for_publish(
            session, task, config=config,
        )

        assert result.error_code == "source_missing"
        assert result.video_path is None
