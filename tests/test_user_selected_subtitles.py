"""Tests for user-selected subtitle consumption in movie publish.

Section 6.3: 发布测试覆盖用户选择字幕被复制/重命名, 以及不安全字幕路径失败.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
from sqlalchemy import select

from media_pilot.adapters.metadata import (
    MetadataCredits,
    MetadataDetail,
    MetadataExternalIds,
    MetadataImages,
)
from media_pilot.config import AppConfig
from media_pilot.orchestration.jellyfin_movie_writer import (
    build_movie_write_plan,
    execute_movie_write,
)
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import (
    FileAsset,
    OperationRecord,
    WriteResult,
)
from media_pilot.repository.repositories import (
    IngestTaskCreate,
    IngestTaskRepository,
    MediaSourceSelectionRepository,
)


def _make_config(root: Path) -> AppConfig:
    config = AppConfig(
        downloads_dir=root / "downloads",
        watch_dir=root / "watch",
        workspace_dir=root / "workspace",
        movies_dir=root / "movies",
        shows_dir=root / "shows",
        database_dir=root / "db",
    )
    for directory in (
        config.downloads_dir,
        config.watch_dir,
        config.workspace_dir,
        config.movies_dir,
        config.shows_dir,
        config.database_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    return config


def _make_detail() -> MetadataDetail:
    return MetadataDetail(
        provider="tmdb",
        provider_id="12345",
        media_type="movie",
        title="Example Movie",
        original_title="Example Original",
        year=2026,
        plot="Fake plot",
        runtime_minutes=120,
        premiered="2026-01-01",
        rating=7.5,
        genres=["Drama"],
        countries=["CN"],
        studios=["Media Pilot Studio"],
        credits=MetadataCredits(
            directors=[],
            actors=[],
        ),
        external_ids=MetadataExternalIds(imdb_id="tt2026000"),
        images=MetadataImages(
            poster_url="https://example.invalid/poster.jpg",
            backdrop_url=None,
            logo_url=None,
        ),
    )


def _make_http_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"poster")

    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def _seed_user_selection(session, *, task_id: str, source_path: Path,
                          selected_subtitles: list[str]):
    """把用户字幕选择写进 MediaSourceSelection."""
    repo = MediaSourceSelectionRepository(session)
    repo.save(
        task_id=task_id,
        input_path=str(source_path),
        selected_path=str(source_path),
        confidence=1.0,
        reason="user_decision:select_subtitles",
        payload={
            "selection_source": "user_decision",
            "selected_subtitles": selected_subtitles,
        },
    )


# ── happy path: user selected subs are copied ──────────────────────


class TestUserSelectedSubtitlesCopied:
    def test_user_selected_subtitle_is_copied_to_staging(
        self, tmp_path: Path,
    ):
        config = _make_config(tmp_path)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")
        # 用户选了一个非同源字幕
        user_sub = config.downloads_dir / "extra_chs.srt"
        user_sub.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")

        session_factory = create_session_factory(config)
        client = _make_http_client()

        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source),
                    status="confirmed",
                    current_step="confirmed",
                    media_type="movie",
                ),
            )
            task_id = task.id
            _seed_user_selection(
                session, task_id=task_id, source_path=source,
                selected_subtitles=[str(user_sub)],
            )
            session.commit()
            plan = build_movie_write_plan(
                movies_dir=config.movies_dir,
                source_path=source,
                detail=_make_detail(),
                task_id=task_id,
            )
            execute_movie_write(
                session,
                task_id=task_id,
                source_path=source,
                detail=_make_detail(),
                plan=plan,
                client=client,
            )
            session.commit()

            # 检查字幕被复制到 staging (publish 成功可能已经移到 final,
            # 实际 FileAsset role=library_subtitle 是权威记录).
            sub_assets = session.scalars(
                select(FileAsset).where(FileAsset.role == "library_subtitle"),
            ).all()
            assert len(sub_assets) == 1
            assert sub_assets[0].path.endswith("Example Movie (2026).srt")
            # FileAsset 记录为 library_subtitle
            sub_assets = session.scalars(
                select(FileAsset).where(FileAsset.role == "library_subtitle"),
            ).all()
            assert len(sub_assets) == 1

    def test_user_selected_non_same_stem_subtitle_overrides_same_stem(
        self, tmp_path: Path,
    ):
        """用户选择存在时, 同源字幕不自动带入; 只复制用户选的非同源字幕."""
        config = _make_config(tmp_path)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")
        same_stem_sub = config.downloads_dir / "Example.Movie.2026.srt"
        same_stem_sub.write_text("ignored")
        user_sub = config.downloads_dir / "extra_chs.srt"
        user_sub.write_text("used")

        session_factory = create_session_factory(config)
        client = _make_http_client()

        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source),
                    status="confirmed",
                    current_step="confirmed",
                    media_type="movie",
                ),
            )
            task_id = task.id
            _seed_user_selection(
                session, task_id=task_id, source_path=source,
                selected_subtitles=[str(user_sub)],
            )
            session.commit()
            plan = build_movie_write_plan(
                movies_dir=config.movies_dir,
                source_path=source,
                detail=_make_detail(),
                task_id=task_id,
            )
            execute_movie_write(
                session,
                task_id=task_id,
                source_path=source,
                detail=_make_detail(),
                plan=plan,
                client=client,
            )
            session.commit()

            # 只应有 1 个字幕 (用户选的)
            sub_assets = session.scalars(
                select(FileAsset).where(FileAsset.role == "library_subtitle"),
            ).all()
            assert len(sub_assets) == 1
            assert Path(sub_assets[0].path).read_text() == "used"

    def test_user_chose_no_subtitles_copies_none(
        self, tmp_path: Path,
    ):
        """用户选 no_subtitles → 不复制任何字幕."""
        config = _make_config(tmp_path)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")
        same_stem_sub = config.downloads_dir / "Example.Movie.2026.srt"
        same_stem_sub.write_text("ignored")

        session_factory = create_session_factory(config)
        client = _make_http_client()

        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source),
                    status="confirmed",
                    current_step="confirmed",
                    media_type="movie",
                ),
            )
            task_id = task.id
            # payload.selected_subtitles = [] → 显式不带入
            _seed_user_selection(
                session, task_id=task_id, source_path=source,
                selected_subtitles=[],
            )
            session.commit()
            plan = build_movie_write_plan(
                movies_dir=config.movies_dir,
                source_path=source,
                detail=_make_detail(),
                task_id=task_id,
            )
            execute_movie_write(
                session,
                task_id=task_id,
                source_path=source,
                detail=_make_detail(),
                plan=plan,
                client=client,
            )
            session.commit()

            staged_subs = session.scalars(
                select(FileAsset).where(FileAsset.role == "library_subtitle"),
            ).all()
            assert len(staged_subs) == 0


# ── unsafe path: out of input node ────────────────────────────────


class TestUnsafeUserSelectedSubtitle:
    def test_subtitle_path_outside_input_node_fails_publish(
        self, tmp_path: Path,
    ):
        config = _make_config(tmp_path)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")

        # 假装 task_input_root 是 downloads/, 但用户选了一个 /tmp/elsewhere/evil.srt
        evil_sub = tmp_path / "elsewhere" / "evil.srt"
        evil_sub.parent.mkdir()
        evil_sub.write_text("malicious")

        session_factory = create_session_factory(config)
        client = _make_http_client()

        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source),
                    status="confirmed",
                    current_step="confirmed",
                    media_type="movie",
                ),
            )
            task_id = task.id
            _seed_user_selection(
                session, task_id=task_id, source_path=source,
                selected_subtitles=[str(evil_sub)],
            )
            session.commit()
            plan = build_movie_write_plan(
                movies_dir=config.movies_dir,
                source_path=source,
                detail=_make_detail(),
                task_id=task_id,
            )
            result = execute_movie_write(
                session,
                task_id=task_id,
                source_path=source,
                detail=_make_detail(),
                plan=plan,
                client=client,
            )
            session.commit()

            assert result.status == "failed"
            write_result = session.scalars(
                select(WriteResult).where(WriteResult.task_id == task_id),
            ).one()
            assert write_result.status == "failed"
            assert write_result.payload["failure_reason"] == "unsafe_user_selected_subtitles"
            # 字幕绝不能被复制 (FileAsset role=library_subtitle 不应存在)
            sub_assets = session.scalars(
                select(FileAsset).where(FileAsset.role == "library_subtitle"),
            ).all()
            assert len(sub_assets) == 0
            # 也不应进入 final_target_dir (publish_to_library 被短路)
            assert not plan.final_target_file.exists()

    def test_subtitle_file_missing_fails_publish(
        self, tmp_path: Path,
    ):
        """用户选的字幕文件在 disk 上不存在 → 拒绝发布."""
        config = _make_config(tmp_path)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")
        # 字幕路径不写入磁盘
        missing_sub = config.downloads_dir / "ghost.srt"

        session_factory = create_session_factory(config)
        client = _make_http_client()

        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source),
                    status="confirmed",
                    current_step="confirmed",
                    media_type="movie",
                ),
            )
            task_id = task.id
            _seed_user_selection(
                session, task_id=task_id, source_path=source,
                selected_subtitles=[str(missing_sub)],
            )
            session.commit()
            plan = build_movie_write_plan(
                movies_dir=config.movies_dir,
                source_path=source,
                detail=_make_detail(),
                task_id=task_id,
            )
            result = execute_movie_write(
                session,
                task_id=task_id,
                source_path=source,
                detail=_make_detail(),
                plan=plan,
                client=client,
            )
            session.commit()

            assert result.status == "failed"
            write_result = session.scalars(
                select(WriteResult).where(WriteResult.task_id == task_id),
            ).one()
            assert write_result.payload["failure_reason"] == "unsafe_user_selected_subtitles"

    def test_unsafe_subtitle_leaves_no_staging_residue(
        self, tmp_path: Path,
    ):
        """Issue 4: 字幕预校验必须在任何 staging 写入之前完成.
        越界字幕 → 拒绝本次发布, 不创建 .media-pilot-staging/<task_id>
        目录, 不留 NFO / 海报 / 视频 / 字幕半截文件."""
        config = _make_config(tmp_path)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")

        # 假装 task_input_root 是 downloads/, 但用户选了一个越界路径
        evil_sub = tmp_path / "elsewhere" / "evil.srt"
        evil_sub.parent.mkdir()
        evil_sub.write_text("malicious")

        session_factory = create_session_factory(config)
        client = _make_http_client()

        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source),
                    status="confirmed",
                    current_step="confirmed",
                    media_type="movie",
                ),
            )
            task_id = task.id
            _seed_user_selection(
                session, task_id=task_id, source_path=source,
                selected_subtitles=[str(evil_sub)],
            )
            session.commit()
            plan = build_movie_write_plan(
                movies_dir=config.movies_dir,
                source_path=source,
                detail=_make_detail(),
                task_id=task_id,
            )
            result = execute_movie_write(
                session,
                task_id=task_id,
                source_path=source,
                detail=_make_detail(),
                plan=plan,
                client=client,
            )
            session.commit()

            assert result.status == "failed"
            # 关键断言: 没有 staging 残留目录
            staging_root = config.movies_dir / ".media-pilot-staging" / task_id
            assert not staging_root.exists(), (
                f"staging residue left at {staging_root}: {list(staging_root.rglob('*'))}"
            )
            # final_target_file 也没动
            assert not plan.final_target_file.exists()
            assert not plan.final_target_dir.exists()


# ── fallback: no user selection → same-stem auto-include ──────────


class TestSameStemFallback:
    def test_no_user_selection_falls_back_to_same_stem(
        self, tmp_path: Path,
    ):
        config = _make_config(tmp_path)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")
        same_stem_sub = config.downloads_dir / "Example.Movie.2026.srt"
        same_stem_sub.write_text("auto-included")

        session_factory = create_session_factory(config)
        client = _make_http_client()

        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source),
                    status="confirmed",
                    current_step="confirmed",
                    media_type="movie",
                ),
            )
            task_id = task.id
            # 不写 MediaSourceSelection → fallback 到 same-stem
            session.commit()
            plan = build_movie_write_plan(
                movies_dir=config.movies_dir,
                source_path=source,
                detail=_make_detail(),
                task_id=task_id,
            )
            execute_movie_write(
                session,
                task_id=task_id,
                source_path=source,
                detail=_make_detail(),
                plan=plan,
                client=client,
            )
            session.commit()

            sub_assets = session.scalars(
                select(FileAsset).where(FileAsset.role == "library_subtitle"),
            ).all()
            assert len(sub_assets) == 1
            assert Path(sub_assets[0].path).read_text() == "auto-included"
