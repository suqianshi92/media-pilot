"""Tests for auto-ingest eligibility, persist, and fetch services."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest



def _make_config(database_dir):

    from media_pilot.config.settings import AppConfig

    return AppConfig(
        downloads_dir=database_dir / "downloads",
        watch_dir=database_dir / "watch",
        workspace_dir=database_dir / "ws",
        movies_dir=database_dir / "movies",
        shows_dir=database_dir / "shows",
        database_dir=database_dir,
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        tmdb_api_key="test-tmdb-key",
    )


def _make_task(session, source_path="/data/test.mkv", **kwargs):
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

    title = kwargs.pop("title", "Test Movie")
    year = kwargs.pop("year", 2024)

    create_data = {
        "source_path": source_path,
        "status": "discovered",
        "current_step": "agent_start",
    }
    for key in ("media_type", "confidence", "failure_reason", "source_download_task_id",
                "source_size_bytes", "source_modified_at", "discovered_at"):
        if key in kwargs:
            create_data[key] = kwargs.pop(key)
    create_data.update(kwargs)

    task = IngestTaskRepository(session).create(IngestTaskCreate(**create_data))
    task.title = title
    task.year = year
    session.commit()
    return task


def _make_candidate(session, task_id, **kwargs):
    from media_pilot.repository.repositories import MediaCandidateRepository

    defaults = {
        "source": "tmdb",
        "media_type": "movie",
        "title": "Test Movie",
        "original_title": None,
        "year": 2024,
        "external_id": "12345",
        "confidence": 0.95,
        "reason": "keyword match",
        "payload": {"poster_url": "https://example.com/poster.jpg"},
    }
    defaults.update(kwargs)
    return MediaCandidateRepository(session).add_candidate(task_id=task_id, **defaults)


def _safe_video(tmp_path, config, name="test.mkv"):
    """Create a video file under a safe root (downloads_dir)."""
    config.downloads_dir.mkdir(parents=True, exist_ok=True)
    video_path = config.downloads_dir / name
    video_path.write_bytes(b"fake video content")
    return video_path


# ══════════════════════════════════════════════════════════════════════
# Eligibility
# ══════════════════════════════════════════════════════════════════════


class TestEligibility:
    def test_task_not_found(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id="nonexistent")
            assert result.eligible is False
            assert "task_not_found" in result.blocking_reasons

    def test_source_path_not_found(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_task(session, source_path="/nonexistent/path.mkv")
            task_id = task.id

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert result.eligible is False
            assert "source_path_not_found" in result.blocking_reasons

    def test_no_metadata_candidates(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_task(session, source_path=str(video_path))
            task_id = task.id

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert result.eligible is False
            assert "no_metadata_candidates" in result.blocking_reasons
            assert result.video_count == 1
            assert result.is_single_file is True

    def test_task_scoped_library_staging_source_is_safe(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        adult_movies_dir = tmp_path / "adult"
        config = replace(config, adult_movies_dir=adult_movies_dir)
        adult_movies_dir.mkdir(parents=True)

        with sf() as session:
            task = _make_task(session, source_path=str(config.downloads_dir / "placeholder.mkv"))
            task_id = task.id

            staged = (
                adult_movies_dir
                / ".media-pilot-staging"
                / task_id
                / "republish-source"
                / "movie.mkv"
            )
            staged.parent.mkdir(parents=True)
            staged.write_bytes(b"movie")
            task.source_path = str(staged)
            _make_candidate(session, task_id, confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert "source_path_outside_safe_roots" not in result.blocking_reasons

    def test_library_publish_dir_is_not_safe_source_root(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        published = config.movies_dir / "Published Movie (2026)"
        published.mkdir(parents=True)
        video = published / "movie.mkv"
        video.write_bytes(b"movie")

        with sf() as session:
            task = _make_task(session, source_path=str(video), media_type="movie")
            task_id = task.id
            _make_candidate(session, task_id, confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert "source_path_outside_safe_roots" in result.blocking_reasons

    def test_eligible_single_movie(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_task(session, source_path=str(video_path), media_type="movie")
            task_id = task.id
            _make_candidate(session, task_id, confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert result.eligible is True
            assert result.video_count == 1
            assert result.is_single_file is True
            assert result.has_clear_winner is True
            assert result.best_candidate is not None
            assert result.best_candidate["confidence"] == 0.95
            assert result.blocking_reasons == []

    def test_bdmv_movie_directory_is_allowed_past_source_gates(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        bdmv_root = config.downloads_dir / "Example Movie Disc"
        (bdmv_root / "BDMV" / "STREAM").mkdir(parents=True)
        (bdmv_root / "BDMV" / "index.bdmv").write_bytes(b"index")
        (bdmv_root / "BDMV" / "STREAM" / "00001.m2ts").write_bytes(b"main")

        with sf() as session:
            task = _make_task(
                session, source_path=str(bdmv_root), media_type="movie",
            )
            task_id = task.id
            _make_candidate(session, task_id, confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert result.eligible is True
            assert result.is_bdmv_movie is True
            assert result.is_iso_image is False
            assert result.task_facts["source_kind"] == "bdmv"
            assert "no_video_files_found" not in result.blocking_reasons
            assert "multiple_video_files_not_supported" not in result.blocking_reasons

    def test_iso_image_is_blocked(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        iso = config.downloads_dir / "Example Movie.iso"
        iso.write_bytes(b"iso")

        with sf() as session:
            task = _make_task(session, source_path=str(iso), media_type="movie")
            task_id = task.id
            _make_candidate(session, task_id, confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert result.eligible is False
            assert result.is_iso_image is True
            assert result.is_bdmv_movie is False
            assert "iso_image_not_supported" in result.blocking_reasons

    def test_low_confidence_no_clear_winner(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_task(session, source_path=str(video_path))
            task_id = task.id
            _make_candidate(session, task_id, confidence=0.7)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert result.has_clear_winner is False
            assert "no_clear_metadata_winner" in result.blocking_reasons
            assert result.eligible is False

    def test_multiple_close_candidates_no_winner(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_task(session, source_path=str(video_path))
            task_id = task.id
            _make_candidate(session, task_id, confidence=0.95, title="Candidate A", external_id="111")
            _make_candidate(session, task_id, confidence=0.92, title="Candidate B", external_id="222")
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert result.has_clear_winner is False
            assert "no_clear_metadata_winner" in result.blocking_reasons
            assert result.eligible is False
            assert result.candidate_count == 2

    def test_show_media_type_not_blocked_by_movie_only_gate(self, tmp_path):
        """剧集任务的 media_type=show 不再被 movie-only 阻塞.

        剧集的结构识别 (跨季 / 稀疏 / Season 0 / 单文件多集) 由
        ``prepare_show_structure`` 负责, 不是 ``check_eligibility`` 的
        职责. 这里的单集剧集应当通过 movie-only gate, 让 LLM
        继续推进到 publish 路径.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_task(session, source_path=str(video_path), media_type="show")
            task_id = task.id
            _make_candidate(session, task_id, media_type="show", confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)

        # 关键断言: 不再出现 media_type_not_movie 阻塞原因.
        assert not any(
            "media_type_not_movie" in r for r in result.blocking_reasons
        ), (
            "media_type=show 不应被 movie-only gate 阻塞; "
            f"actual blocking_reasons={result.blocking_reasons}"
        )
        # 该任务应该是有资格自动入库的: 候选 confidence 0.95 远高于阈值,
        # 单视频, 路径安全. eligible 应当是 True.
        assert result.eligible is True

    def test_show_multi_video_not_blocked_by_movie_gate(self, tmp_path):
        """剧集任务允许多视频 (单集 / 同季连续多集),
        不被 ``multiple_video_files_not_supported`` 阻塞.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        show_dir = config.downloads_dir / "Example.Show.S01"
        show_dir.mkdir()
        (show_dir / "Example.Show.S01E01.mkv").write_bytes(b"e1" * 2048)
        (show_dir / "Example.Show.S01E02.mkv").write_bytes(b"e2" * 2048)
        (show_dir / "Example.Show.S01E03.mkv").write_bytes(b"e3" * 2048)

        with sf() as session:
            task = _make_task(
                session, source_path=str(show_dir), media_type="show",
            )
            task_id = task.id
            _make_candidate(session, task_id, media_type="show", confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)

        assert "multiple_video_files_not_supported" not in result.blocking_reasons
        assert "media_type_not_movie" not in " ".join(result.blocking_reasons)
        # 关键: 视频数 > 1 但任务是有资格的.
        assert result.video_count >= 2
        assert result.eligible is True

    def test_unknown_media_type_blocked(self, tmp_path):
        """未识别的 media_type 仍走 media_type_not_supported 阻塞."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_task(
                session, source_path=str(video_path), media_type="anime",
            )
            task_id = task.id
            _make_candidate(session, task_id, confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert result.eligible is False
            assert any(
                "media_type_not_supported" in r for r in result.blocking_reasons
            )

    def test_sample_video_blocked(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config, name="sample-test.mkv")

        with sf() as session:
            task = _make_task(session, source_path=str(video_path))
            task_id = task.id
            _make_candidate(session, task_id)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert result.eligible is False
            assert "sample_or_trailer_not_supported" in result.blocking_reasons
            assert result.is_sample_or_trailer is True

    def test_low_value_size_ratio_excluded_does_not_block(self, tmp_path):
        """USBA-089 修复: 目录里 3.7 GB + 1.9 MB (low_value_size_ratio 排除) →
        check_eligibility MUST NOT 报 sample_or_trailer_not_supported.

        旧逻辑"只要 excluded_videos 非空就 is_sample=True" 把 size 启发式
        命中的小伴随视频也当 sample/trailer 阻断. 修复后 size-ratio 排除
        不得触发 sample/trailer 阻断.
        """
        import os

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source_dir = config.downloads_dir / "USBA-089"
        source_dir.mkdir(parents=True, exist_ok=True)

        # 稀疏文件: 不分配实际 GB 字节, stat 仍返回声明 size
        main_path = source_dir / "main.mp4"
        ad_path = source_dir / "ad.mp4"
        for p in (main_path, ad_path):
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
            os.close(fd)
        os.truncate(main_path, 4 * 1024 * 1024 * 1024)
        os.truncate(ad_path, int(1.9 * 1024 * 1024))

        with sf() as session:
            task = _make_task(session, source_path=str(source_dir), media_type="movie")
            task_id = task.id
            _make_candidate(session, task_id, confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(
                session=session, config=config, task_id=task_id,
            )
            # 关键回归: size-ratio 排除不得触发 sample/trailer 阻断
            assert "sample_or_trailer_not_supported" not in result.blocking_reasons, (
                f"low_value_size_ratio 排除不得触发 sample/trailer 阻断; "
                f"actual blocking_reasons={result.blocking_reasons}"
            )
            assert result.is_sample_or_trailer is False
            # 1 个主视频, 候选 confidence 0.95 → 应 eligible
            assert result.eligible is True
            assert result.video_count == 1

    def test_user_selected_main_with_low_value_size_ratio_excluded(self, tmp_path):
        """用户已选主视频 + 目录里只有 low_value_size_ratio 排除 →
        check_eligibility MUST NOT 报 sample/trailer_not_supported.
        """
        import os

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source_dir = config.downloads_dir / "USBA-089-selected"
        source_dir.mkdir(parents=True, exist_ok=True)

        main_path = source_dir / "main.mp4"
        ad_path = source_dir / "ad.mp4"
        for p in (main_path, ad_path):
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
            os.close(fd)
        os.truncate(main_path, 4 * 1024 * 1024 * 1024)
        os.truncate(ad_path, int(1.9 * 1024 * 1024))

        with sf() as session:
            from media_pilot.repository.repositories import MediaSourceSelectionRepository
            task = _make_task(
                session, source_path=str(source_dir), media_type="movie",
            )
            task_id = task.id
            # 用户已通过 select_primary_video 选过 main.mp4
            MediaSourceSelectionRepository(session).save(
                task_id=task_id,
                input_path=str(source_dir),
                selected_path=str(main_path),
                confidence=1.0,
                reason="user_decision",
                payload={"selection_source": "user_decision"},
            )
            _make_candidate(session, task_id, confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(
                session=session, config=config, task_id=task_id,
            )
            assert "sample_or_trailer_not_supported" not in result.blocking_reasons
            assert result.is_sample_or_trailer is False
            assert result.video_count == 1
            # task_facts.user_selected_video 透传
            assert result.task_facts.get("user_selected_video") == str(main_path)

    def test_user_selected_main_with_marker_excluded(self, tmp_path):
        """用户已选主视频 + 目录里有 marker 排除 → 不阻断 (旧逻辑兼容).

        旧逻辑: "excluded_videos 非空就 is_sample=True" 会阻断. 修复后:
        用户已选有效主视频时, marker 排除的 video 不得阻塞.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source_dir = config.downloads_dir / "Movie.Mixed"
        source_dir.mkdir(parents=True, exist_ok=True)
        main_path = source_dir / "main.mp4"
        sample_path = source_dir / "trailer.mp4"
        main_path.write_bytes(b"main")
        sample_path.write_bytes(b"trailer")

        with sf() as session:
            from media_pilot.repository.repositories import MediaSourceSelectionRepository
            task = _make_task(
                session, source_path=str(source_dir), media_type="movie",
            )
            task_id = task.id
            # 用户已选 main.mp4
            MediaSourceSelectionRepository(session).save(
                task_id=task_id,
                input_path=str(source_dir),
                selected_path=str(main_path),
                confidence=1.0,
                reason="user_decision",
                payload={"selection_source": "user_decision"},
            )
            _make_candidate(session, task_id, confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(
                session=session, config=config, task_id=task_id,
            )
            # 用户已选 → 不阻断, 即使 marker 排除非空
            assert "sample_or_trailer_not_supported" not in result.blocking_reasons
            assert result.is_sample_or_trailer is False
            assert result.video_count == 1

    def test_directory_with_only_marker_excluded_blocks(self, tmp_path):
        """目录里只有 marker 排除的视频, 没有用户选择 → 必须阻断.

        这是旧逻辑的合理保留: 没有任何有效主视频可发布时, sample/trailer
        阻断是必需的. 修复不得破坏这条边界.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source_dir = config.downloads_dir / "AllExcluded"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "sample1.mkv").write_bytes(b"sample1")
        (source_dir / "trailer.mp4").write_bytes(b"trailer")

        with sf() as session:
            task = _make_task(
                session, source_path=str(source_dir), media_type="movie",
            )
            task_id = task.id
            _make_candidate(session, task_id, confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(
                session=session, config=config, task_id=task_id,
            )
            # 全部 marker 排除 + 无用户选择 + video_files 为空 → 必须阻断
            assert "sample_or_trailer_not_supported" in result.blocking_reasons
            assert result.is_sample_or_trailer is True

    def test_unsafe_path_rejected(self, tmp_path):
        """Source path outside safe roots should be blocked."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        # Create a file outside all safe roots
        unsafe_dir = tmp_path / "unsafe"
        unsafe_dir.mkdir()
        unsafe_video = unsafe_dir / "movie.mkv"
        unsafe_video.write_bytes(b"fake video content")

        with sf() as session:
            task = _make_task(session, source_path=str(unsafe_video))
            task_id = task.id
            _make_candidate(session, task_id)
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert "source_path_outside_safe_roots" in result.blocking_reasons


# ══════════════════════════════════════════════════════════════════════
# C. user_decision 旁路 — publish gate 不阻塞用户已选过的元数据
# ══════════════════════════════════════════════════════════════════════


class TestUserDecisionShortCircuit:
    """``check_eligibility`` 必须把 source='user_decision' 候选视为
    强事实 winner, 跳过 has_clear_winner 的 margin 边界判定.

    背景: Issue 3 / 4 — ``task 5043c11e-...`` 在 Agent 通过
    ``select_metadata_candidate`` 决策后, 用户已显式选过元数据
    (落库 source='user_decision' 候选), 但 publish gate 仍报
    ``no_clear_metadata_winner``, 把任务卡在 agent_running.
    修复: user_decision 候选直接当 winner, 不得再走 has_clear_winner."""

    def test_user_decision_winner_skips_clear_winner_check(
        self, tmp_path,
    ):
        """用户决策 + 邻近 runner-up (老 tmdb 候选) → user_decision 仍胜出."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_task(session, source_path=str(video_path), media_type="movie")
            task_id = task.id
            # 老 tmdb 候选 (低 confidence, 与 user_decision 接近)
            _make_candidate(
                session, task_id, source="tmdb", title="Old Match",
                external_id="movie:111", confidence=0.6, year=2024,
            )
            # user_decision 候选 (用户选了)
            _make_candidate(
                session, task_id, source="user_decision",
                title="User Picked Movie", external_id="movie:222",
                confidence=0.95, year=2024,
            )
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            # user_decision 旁路生效, 不得报 no_clear_metadata_winner
            assert "no_clear_metadata_winner" not in result.blocking_reasons
            assert "no_metadata_candidates" not in result.blocking_reasons
            # 任务是有资格的 (单视频 / 路径安全)
            assert result.eligible is True
            # best_candidate 必须是 user_decision 那条
            assert result.best_candidate is not None
            assert result.best_candidate["provider"] == "user_decision"
            assert result.best_candidate["provider_id"] == "movie:222"
            assert result.best_candidate["title"] == "User Picked Movie"
            assert result.has_clear_winner is True

    def test_user_decision_with_no_other_candidates_still_eligible(
        self, tmp_path,
    ):
        """只有 user_decision 候选, 没有老 tmdb 行 → 仍然 eligible."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_task(session, source_path=str(video_path), media_type="movie")
            task_id = task.id
            _make_candidate(
                session, task_id, source="user_decision",
                title="User Picked", external_id="movie:333",
                confidence=0.7, year=2024,
            )
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            assert result.eligible is True
            assert "no_metadata_candidates" not in result.blocking_reasons
            assert result.best_candidate is not None
            assert result.best_candidate["provider"] == "user_decision"

    def test_user_decision_low_confidence_does_not_block(self, tmp_path):
        """user_decision 候选 confidence 较低 (例如 0.5) 仍当 winner,
        不得被 has_clear_winner 的 confidence_threshold 误判阻塞.
        强事实旁路与 confidence 数值解耦 — user 显式选过就是事实."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_task(session, source_path=str(video_path), media_type="movie")
            task_id = task.id
            _make_candidate(
                session, task_id, source="user_decision",
                title="Low Conf User Pick", external_id="movie:444",
                confidence=0.5, year=2024,
            )
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(session=session, config=config, task_id=task_id)
            # confidence 0.5 < default 0.7 threshold, 但 user_decision 旁路
            # 不得被阈值拦截
            assert "no_clear_metadata_winner" not in result.blocking_reasons
            assert result.eligible is True
            assert result.best_candidate["confidence"] == 0.5


# ══════════════════════════════════════════════════════════════════════
# Persist Metadata Selection
# ══════════════════════════════════════════════════════════════════════


class TestPersistMetadataSelection:
    def test_persist_creates_candidate_and_updates_task(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task(session)
            task_id = task.id

        with sf() as session:
            from media_pilot.services.auto_ingest import persist_metadata_selection
            result = persist_metadata_selection(
                session=session,
                task_id=task_id,
                provider_name="tmdb",
                provider_id="12345",
                media_type="movie",
                title="Inception",
                year=2010,
                confidence=0.95,
            )
            session.commit()

        assert result.status == "success"
        assert result.candidate_id is not None

        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskRepository,
                MediaCandidateRepository,
            )
            task = IngestTaskRepository(session).get(task_id)
            assert task.media_type == "movie"
            assert task.title == "Test Movie"  # not overwritten since already set

            candidates = MediaCandidateRepository(session).list_for_task(task_id)
            agent_candidates = [c for c in candidates if c.source == "agent"]
            assert len(agent_candidates) == 1
            assert agent_candidates[0].title == "Inception"
            assert agent_candidates[0].external_id == "12345"

    def test_persist_fills_missing_title(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task(session, title=None, media_type=None)
            task_id = task.id

        with sf() as session:
            from media_pilot.services.auto_ingest import persist_metadata_selection
            result = persist_metadata_selection(
                session=session,
                task_id=task_id,
                provider_name="tmdb",
                provider_id="12345",
                media_type="movie",
                title="New Title",
                year=2024,
                confidence=0.9,
            )
            session.commit()

        assert result.status == "success"

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.title == "New Title"
            assert task.media_type == "movie"
            assert task.year == 2024
            assert task.confidence == 0.9

    def test_persist_task_not_found(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)

        with sf() as session:
            from media_pilot.services.auto_ingest import persist_metadata_selection
            result = persist_metadata_selection(
                session=session,
                task_id="nonexistent",
                provider_name="tmdb",
                provider_id="12345",
                media_type="movie",
                title="Test",
            )
        assert result.status == "failure"
        assert "not found" in result.summary


# ══════════════════════════════════════════════════════════════════════
# Fetch and Save Metadata Detail
# ══════════════════════════════════════════════════════════════════════


class TestFetchAndSaveMetadataDetail:
    def test_fetch_metadata_detail_payload_is_pure(self, tmp_path, monkeypatch):
        from media_pilot.adapters.metadata import (
            MetadataCredits,
            MetadataDetail,
            MetadataExternalIds,
            MetadataImages,
        )
        from media_pilot.services.auto_ingest import (
            MetadataDetailPayload,
            fetch_metadata_detail_payload,
        )
        from media_pilot.services.metadata_draft import MetadataDraft

        config = _make_config(tmp_path)
        calls: list[str] = []

        def _fake_fetch_metadata_draft(
            *,
            config: object,
            provider_name: str,
            provider_id: str,
            media_type: str,
            language_priority: list[str],
        ) -> MetadataDraft:
            del config, language_priority
            calls.append("provider_fetch")
            assert provider_name == "tmdb"
            assert provider_id == "12345"
            assert media_type == "movie"

            detail = MetadataDetail(
                provider="tmdb",
                provider_id="movie:12345",
                media_type="movie",
                title="Test Movie",
                original_title="",
                year=2024,
                plot="some plot",
                runtime_minutes=120,
                premiered="2024-01-01",
                rating=7.1,
                genres=["Drama"],
                countries=["US"],
                studios=["Studio"],
                credits=MetadataCredits(),
                external_ids=MetadataExternalIds(imdb_id="tt1234"),
                images=MetadataImages(None, None, None),
                payload={"raw": {"origin": "test"}},
            )
            return MetadataDraft(
                detail=detail,
                directors=[{"name": "Director"}],
                actors=[{"name": "Actor"}],
                imdb_id="tt1234",
                poster_url="https://example.com/poster.jpg",
                backdrop_url="https://example.com/backdrop.jpg",
                logo_url="https://example.com/logo.png",
            )

        class _NoRepository:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError("fetch_metadata_detail_payload must not depend on repository")

        monkeypatch.setattr(
            "media_pilot.services.metadata_draft.fetch_metadata_draft",
            _fake_fetch_metadata_draft,
        )
        monkeypatch.setattr(
            "media_pilot.repository.repositories.MetadataDetailRepository",
            _NoRepository,
        )

        payload = fetch_metadata_detail_payload(
            config=config,
            provider_name="tmdb",
            provider_id="12345",
            media_type="movie",
        )

        assert calls == ["provider_fetch"]
        assert isinstance(payload, MetadataDetailPayload)
        assert payload.detail.title == "Test Movie"
        assert payload.payload["directors"] == [{"name": "Director"}]
        assert payload.payload["imdb_id"] == "tt1234"

    def test_unknown_provider_returns_failure(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            from media_pilot.services.auto_ingest import fetch_and_save_metadata_detail
            result = fetch_and_save_metadata_detail(
                session=session,
                config=config,
                task_id="any-task",
                provider_name="unknown_provider",
                provider_id="12345",
                media_type="movie",
            )
            assert result.status == "failure"
            assert "Invalid provider" in result.summary or "unknown" in result.summary.lower()

    def test_fetch_and_save_metadata_detail_fetches_before_db_save(self, tmp_path, monkeypatch):
        from media_pilot.adapters.metadata import (
            MetadataCredits,
            MetadataDetail,
            MetadataExternalIds,
            MetadataImages,
        )
        from media_pilot.services.auto_ingest import (
            MetadataDetailPayload,
            fetch_and_save_metadata_detail,
        )
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_task(session)
            task_id = task.id

        calls: list[str] = []
        detail = MetadataDetail(
            provider="tmdb",
            provider_id="movie:12345",
            media_type="movie",
            title="Test Movie",
            original_title="",
            year=2024,
            plot="some plot",
            runtime_minutes=120,
            premiered="2024-01-01",
            rating=7.1,
            genres=["Drama"],
            countries=["US"],
            studios=["Studio"],
            credits=MetadataCredits(),
            external_ids=MetadataExternalIds(imdb_id="tt1234"),
            images=MetadataImages(None, None, None),
            payload={"raw": {}},
        )

        fake_payload = MetadataDetailPayload(
            detail=detail,
            payload={
                "title": detail.title,
                "directors": [{"name": "Director"}],
                "actors": [{"name": "Actor"}],
                "imdb_id": "tt1234",
                "poster_url": "poster.jpg",
                "backdrop_url": "backdrop.jpg",
                "logo_url": "logo.png",
                "raw": {},
            },
        )

        def _fake_payload_fetch(
            *,
            config: object,
            provider_name: str,
            provider_id: str,
            media_type: str,
        ) -> MetadataDetailPayload:
            del config
            calls.append("fetch_payload")
            assert provider_name == "tmdb"
            assert provider_id == "12345"
            assert media_type == "movie"
            return fake_payload

        class _TrackingRepo:
            def __init__(self, *_args, **_kwargs):
                calls.append("repo_init")

            def save(self, *args: object, **_kwargs: object):
                calls.append("repo_save")

        monkeypatch.setattr(
            "media_pilot.services.auto_ingest.fetch_metadata_detail_payload",
            _fake_payload_fetch,
        )
        monkeypatch.setattr(
            "media_pilot.repository.repositories.MetadataDetailRepository",
            _TrackingRepo,
        )

        with sf() as session:
            result = fetch_and_save_metadata_detail(
                session=session,
                config=config,
                task_id=task_id,
                provider_name="tmdb",
                provider_id="12345",
                media_type="movie",
            )

        assert result.status == "success"
        assert calls == ["fetch_payload", "repo_init", "repo_save"]


# ── fetch_metadata_draft: TMDB provider_id 归一化 (Warcraft 现场) ─────


class TestFetchMetadataDraftTmdbNormalization:
    """``fetch_metadata_draft`` 接受裸数字 / 带前缀两种 provider_id
    形式. 内部统一用前缀形式派发 / 持久化. 既有 ``movie:abc`` /
    ``tv:123`` 等非法形式仍 409 ``invalid_provider_id``."""

    def _make_tmdb_provider(self, tmdb_id: int) -> "TmdbMovieProvider":
        import httpx
        from media_pilot.adapters.tmdb import TmdbMovieProvider

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path.removeprefix("/3")
            if path == f"/movie/{tmdb_id}":
                return httpx.Response(200, json={
                    "id": tmdb_id, "title": "Warcraft",
                    "original_title": "Warcraft",
                    "release_date": "2016-06-10",
                    "overview": "...",
                    "runtime": 123, "vote_average": 7.2,
                    "genres": [], "production_countries": [],
                    "production_companies": [],
                })
            if path.startswith(f"/movie/{tmdb_id}/"):
                return httpx.Response(200, json={"id": tmdb_id, "cast": [], "crew": []})
            return httpx.Response(404, json={"status_message": "not found"})

        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.themoviedb.org/3",
        )
        return TmdbMovieProvider(api_key="test-key", client=client)

    def _patch_factory(self, provider) -> object:
        from media_pilot.adapters import factory as factory_mod

        original = factory_mod.create_metadata_provider_by_name

        def _patched(c, name):
            return provider

        factory_mod.create_metadata_provider_by_name = _patched
        return original

    def _restore_factory(self, original) -> None:
        from media_pilot.adapters import factory as factory_mod

        factory_mod.create_metadata_provider_by_name = original

    def test_bare_movie_id_resolves(self, tmp_path: Path) -> None:
        from media_pilot.services.metadata_draft import fetch_metadata_draft

        config = _make_config(tmp_path)
        provider = self._make_tmdb_provider(68735)
        original = self._patch_factory(provider)
        try:
            draft = fetch_metadata_draft(
                config=config,
                provider_name="tmdb",
                provider_id="68735",
                media_type="movie",
                language_priority=["zh-CN"],
            )
        finally:
            self._restore_factory(original)

        # bare 数字被归一化为前缀形式, 内部 detail.provider_id = "movie:68735"
        assert draft.detail.provider_id == "movie:68735"
        assert draft.detail.title == "Warcraft"

    def test_prefixed_movie_id_still_resolves(self, tmp_path: Path) -> None:
        from media_pilot.services.metadata_draft import fetch_metadata_draft

        config = _make_config(tmp_path)
        provider = self._make_tmdb_provider(68735)
        original = self._patch_factory(provider)
        try:
            draft = fetch_metadata_draft(
                config=config,
                provider_name="tmdb",
                provider_id="movie:68735",
                media_type="movie",
                language_priority=["zh-CN"],
            )
        finally:
            self._restore_factory(original)

        # 前缀形式原样, 不双前缀
        assert draft.detail.provider_id == "movie:68735"
        assert draft.detail.title == "Warcraft"

    def test_non_numeric_id_still_rejected(self, tmp_path: Path) -> None:
        from media_pilot.adapters import factory as factory_mod
        from media_pilot.services.metadata_draft import ProviderError, fetch_metadata_draft

        config = _make_config(tmp_path)
        # 走真实 TMDB provider: invalid_provider_id 错误由 provider
        # 内部 _tmdb_movie_id 解析失败时抛出 (透传到 fetch_metadata_draft).
        provider = self._make_tmdb_provider(1)
        original = self._patch_factory(provider)
        try:
            with pytest.raises(ProviderError) as exc_info:
                fetch_metadata_draft(
                    config=config,
                    provider_name="tmdb",
                    provider_id="movie:abc",
                    media_type="movie",
                    language_priority=["zh-CN"],
                )
            assert exc_info.value.code == "invalid_provider_id"
        finally:
            self._restore_factory(original)

    def test_floating_point_id_still_rejected(self, tmp_path: Path) -> None:
        from media_pilot.adapters import factory as factory_mod
        from media_pilot.services.metadata_draft import ProviderError, fetch_metadata_draft

        config = _make_config(tmp_path)
        provider = self._make_tmdb_provider(1)
        original = self._patch_factory(provider)
        try:
            with pytest.raises(ProviderError) as exc_info:
                fetch_metadata_draft(
                    config=config,
                    provider_name="tmdb",
                    provider_id="687.35",
                    media_type="movie",
                    language_priority=["zh-CN"],
                )
            assert exc_info.value.code == "invalid_provider_id"
        finally:
            self._restore_factory(original)

    def test_tpdb_provider_id_unaffected(self, tmp_path: Path) -> None:
        """``tpdb`` provider 不被 TMDB 归一化触碰 — 走不同 protocol."""
        from media_pilot.adapters import factory as factory_mod
        from media_pilot.services.metadata_draft import fetch_metadata_draft

        config = _make_config(tmp_path)
        # TPDB provider 创建会要求额外 config 字段; 直接 mock 让 factory
        # 抛 ValueError 来确认 TMDB 归一化不会污染 tpdb 路径
        original = factory_mod.create_metadata_provider_by_name

        def _check_unused(c, name):
            # 如果归一化走错到 tpdb, 会调到 tpdb factory; 我们的目标是
            # 验证: 给 ``tpdb`` provider + 裸数字, TMDB 归一化不会触碰
            # 它 (factory 仍然按 tpdb 协议处理). 这里只验证 provider_name
            # 决定路由, 不需要实际执行.
            raise AssertionError(
                f"tpdb call should be routed normally, not intercepted: {name}",
            )

        factory_mod.create_metadata_provider_by_name = _check_unused
        try:
            with pytest.raises(AssertionError) as exc_info:
                fetch_metadata_draft(
                    config=config,
                    provider_name="tpdb",
                    provider_id="12345",
                    media_type="movie",
                    language_priority=["zh-CN"],
                )
            # 如果 TMDB 归一化触碰了 tpdb, 这里会是 ValueError;
            # 我们故意让 factory 抛 AssertionError 来确认是 tpdb 路径
            assert "tpdb" in str(exc_info.value)
        finally:
            factory_mod.create_metadata_provider_by_name = original


# ══════════════════════════════════════════════════════════════════════
# WRITE Agent Tools
# ══════════════════════════════════════════════════════════════════════


class TestWriteAgentTools:
    """Tests for WRITE tools as they would be called via the ToolRegistry."""

    def test_persist_metadata_selection_tool_success(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task(session, title=None, media_type=None)
            task_id = task.id

        with sf() as session:
            from media_pilot.agent.tools.base import ToolContext
            from media_pilot.agent.tools.write import _handle_persist_metadata_selection

            config = _make_config(tmp_path)
            ctx = ToolContext(session=session, config=config, task_id=task_id)
            result = _handle_persist_metadata_selection(ctx, {
                "task_id": task_id,
                "provider_name": "tmdb",
                "provider_id": "12345",
                "media_type": "movie",
                "title": "Inception",
                "year": 2010,
                "confidence": 0.95,
            })
            session.commit()

        assert result.status == "success"
        assert "Inception" in result.summary
        assert result.data["candidate_id"] is not None

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.title == "Inception"
            assert task.media_type == "movie"

    def test_persist_metadata_selection_tool_validation_failure(self, tmp_path):
        """Missing required field should fail at validation."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
        register_builtin_tools()
        registry = get_tool_registry()

        with sf() as session:
            from media_pilot.agent.tools.base import ToolContext
            ctx = ToolContext(session=session, config=config, task_id="any")

            # Missing required "title" field
            import pytest as pt
            with pt.raises(ValueError, match="missing required field"):
                registry.validate_input("persist_metadata_selection", {
                    "task_id": "any",
                    "provider_name": "tmdb",
                    "provider_id": "12345",
                    "media_type": "movie",
                })

    def test_publish_movie_tool_refuses_missing_detail(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_task(session, source_path=str(video_path), media_type="movie")
            task_id = task.id
            _make_candidate(session, task_id, confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.agent.tools.base import ToolContext
            from media_pilot.agent.tools.write import _handle_publish_movie_to_library

            ctx = ToolContext(session=session, config=config, task_id=task_id)
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})
            session.commit()

        assert result.status == "failure"
        assert "no metadata detail" in result.summary.lower()

    def test_publish_movie_tool_refuses_show_media_type(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_task(session, source_path=str(video_path), media_type="show")
            task_id = task.id
            _make_candidate(session, task_id, media_type="show", confidence=0.95)
            session.commit()

        with sf() as session:
            from media_pilot.agent.tools.base import ToolContext
            from media_pilot.agent.tools.write import _handle_publish_movie_to_library

            ctx = ToolContext(session=session, config=config, task_id=task_id)
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})
            session.commit()

        assert result.status == "failure"
        assert "not movie" in result.summary.lower() or "media_type" in result.summary.lower()

    def test_publish_movie_tool_refuses_sample(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config, name="sample-test.mkv")

        with sf() as session:
            task = _make_task(session, source_path=str(video_path), media_type="movie")
            task_id = task.id
            session.commit()

        with sf() as session:
            from media_pilot.agent.tools.base import ToolContext
            from media_pilot.agent.tools.write import _handle_publish_movie_to_library

            ctx = ToolContext(session=session, config=config, task_id=task_id)
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})
            session.commit()

        assert result.status == "failure"
        assert "sample" in result.summary.lower() or "trailer" in result.summary.lower()

    def test_publish_movie_tool_refuses_unsafe_path(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        unsafe_dir = tmp_path / "unsafe"
        unsafe_dir.mkdir()
        unsafe_video = unsafe_dir / "movie.mkv"
        unsafe_video.write_bytes(b"fake video content")

        with sf() as session:
            task = _make_task(session, source_path=str(unsafe_video), media_type="movie")
            task_id = task.id
            _make_candidate(session, task_id)
            session.commit()

        with sf() as session:
            from media_pilot.agent.tools.base import ToolContext
            from media_pilot.agent.tools.write import _handle_publish_movie_to_library

            ctx = ToolContext(session=session, config=config, task_id=task_id)
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})
            session.commit()

        assert result.status == "failure"
        assert "unsafe" in result.summary.lower() or "safe" in result.summary.lower()

    def test_publish_movie_failed_write_marks_task_and_run_failed(self, tmp_path, monkeypatch):
        """真实现场回归: execute_movie_write 返回 failed 时, movie 工具
        必须把 task/run 收到失败态, 不能让 UI 长期停在 agent_running."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            from media_pilot.repository.models import MetadataDetail
            from media_pilot.repository.repositories import (
                AgentRunCreate,
                AgentRunRepository,
            )

            task = _make_task(
                session,
                source_path=str(video_path),
                media_type="movie",
                status="agent_running",
                current_step="agent_running",
            )
            task_id = task.id
            _make_candidate(session, task_id, confidence=0.95)
            session.add(MetadataDetail(
                task_id=task_id,
                provider="tmdb",
                provider_id="movie:123",
                media_type="movie",
                title="Test Movie",
                original_title=None,
                year=2024,
                payload={"poster_url": "https://example.invalid/poster.jpg"},
            ))
            run = AgentRunRepository(session).create(
                AgentRunCreate(task_id=task_id, current_step="agent_start")
            )
            run_id = run.id
            session.commit()

        def fake_execute_movie_write(session, **kwargs):
            from media_pilot.orchestration.jellyfin_movie_writer import MovieWriteResult
            from media_pilot.repository.repositories import WriteResultRepository

            WriteResultRepository(session).save(
                kwargs["task_id"],
                status="failed",
                payload={"failure_reason": "poster_download_failed", "warnings": []},
            )
            return MovieWriteResult(status="failed", warnings=[])

        monkeypatch.setattr(
            "media_pilot.orchestration.jellyfin_movie_writer.execute_movie_write",
            fake_execute_movie_write,
        )

        with sf() as session:
            from media_pilot.agent.tools.base import ToolContext
            from media_pilot.agent.tools.write import _handle_publish_movie_to_library

            ctx = ToolContext(
                session=session,
                config=config,
                task_id=task_id,
                run_id=run_id,
            )
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})
            session.commit()

        assert result.status == "failure"
        assert result.data["failure_reason"] == "poster_download_failed"

        with sf() as session:
            from media_pilot.repository.models import AgentRun, IngestTask

            task = session.get(IngestTask, task_id)
            run = session.get(AgentRun, run_id)
            assert task.status == "agent_failed"
            assert task.current_step == "agent_failed"
            assert task.failure_reason == "poster_download_failed"
            assert run.status == "failed"
            assert run.current_step == "movie_publish_failed"
            assert run.error_message == "poster_download_failed"

    def test_publish_movie_tool_publishes_bdmv_directory(self, tmp_path, monkeypatch):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        bdmv_root = config.downloads_dir / "Example Movie Disc"
        (bdmv_root / "BDMV" / "STREAM").mkdir(parents=True)
        (bdmv_root / "BDMV" / "PLAYLIST").mkdir()
        (bdmv_root / "BDMV" / "CLIPINF").mkdir()
        (bdmv_root / "BDMV" / "index.bdmv").write_bytes(b"index")
        (bdmv_root / "BDMV" / "MovieObject.bdmv").write_bytes(b"movie-object")
        (bdmv_root / "BDMV" / "STREAM" / "00001.m2ts").write_bytes(b"main")

        with sf() as session:
            from media_pilot.repository.models import MetadataDetail

            task = _make_task(
                session,
                source_path=str(bdmv_root),
                media_type="movie",
                status="agent_running",
                current_step="agent_running",
            )
            task_id = task.id
            _make_candidate(session, task_id, confidence=0.95)
            session.add(MetadataDetail(
                task_id=task_id,
                provider="tmdb",
                provider_id="movie:123",
                media_type="movie",
                title="Example Movie",
                original_title=None,
                year=2026,
                payload={
                    "poster_url": "https://example.invalid/poster.jpg",
                    "backdrop_url": None,
                    "logo_url": None,
                },
            ))
            session.commit()

        def fake_download_image(_client, _url, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"image")
            return b"image"

        monkeypatch.setattr(
            "media_pilot.orchestration.jellyfin_movie_writer._download_image",
            fake_download_image,
        )

        with sf() as session:
            from media_pilot.agent.tools.base import ToolContext
            from media_pilot.agent.tools.write import _handle_publish_movie_to_library

            ctx = ToolContext(session=session, config=config, task_id=task_id)
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})
            session.commit()

        assert result.status == "success"
        assert result.data["source_kind"] == "bdmv"

        final_dir = config.movies_dir / "Example Movie (2026)"
        assert (final_dir / "BDMV" / "index.bdmv").exists()
        assert (final_dir / "BDMV" / "STREAM" / "00001.m2ts").read_bytes() == b"main"
        assert (final_dir / "BDMV" / "index.nfo").exists()
        assert (final_dir / "Example Movie (2026)-poster.jpg").exists()

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "library_import_complete"
            assert task.current_step == "library_import_complete"

    def test_write_tools_registered_with_correct_permission(self):
        from media_pilot.agent.tools.base import PermissionLevel
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

        register_builtin_tools()
        registry = get_tool_registry()

        persist = registry.get("persist_metadata_selection")
        assert persist.permission_level == PermissionLevel.WRITE

        fetch = registry.get("fetch_and_save_metadata_detail")
        assert fetch.permission_level == PermissionLevel.WRITE

        publish = registry.get("publish_movie_to_library")
        assert publish.permission_level == PermissionLevel.WRITE

    def test_write_tools_in_auto_ingest_whitelist(self):
        from media_pilot.agent.tool_schema import AUTO_INGEST_WRITE_TOOL_WHITELIST

        assert "persist_metadata_selection" in AUTO_INGEST_WRITE_TOOL_WHITELIST
        assert "fetch_and_save_metadata_detail" in AUTO_INGEST_WRITE_TOOL_WHITELIST
        assert "publish_movie_to_library" in AUTO_INGEST_WRITE_TOOL_WHITELIST

    def test_default_mode_excludes_write_tools_from_schemas(self):
        from media_pilot.agent.tool_schema import get_allowed_tool_schemas
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

        register_builtin_tools()
        registry = get_tool_registry()

        schemas = get_allowed_tool_schemas(registry, mode="default")
        names = {s["function"]["name"] for s in schemas}
        assert "persist_metadata_selection" not in names
        assert "fetch_and_save_metadata_detail" not in names
        assert "publish_movie_to_library" not in names

    def test_auto_ingest_mode_includes_write_tools_in_schemas(self):
        from media_pilot.agent.tool_schema import get_allowed_tool_schemas
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

        register_builtin_tools()
        registry = get_tool_registry()

        schemas = get_allowed_tool_schemas(registry, mode="auto_ingest")
        names = {s["function"]["name"] for s in schemas}
        assert "persist_metadata_selection" in names
        assert "fetch_and_save_metadata_detail" in names
        assert "publish_movie_to_library" in names


# ══════════════════════════════════════════════════════════════════════
# Subtitle Handling & Idempotency
# ══════════════════════════════════════════════════════════════════════


class TestSubtitleAndIdempotency:
    def test_publish_tool_idempotent_when_already_published(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_task(
                session,
                source_path=str(video_path),
                media_type="movie",
                status="library_import_complete",
                current_step="library_import_complete",
            )
            task_id = task.id

        with sf() as session:
            from media_pilot.agent.tools.base import ToolContext
            from media_pilot.agent.tools.write import _handle_publish_movie_to_library

            ctx = ToolContext(session=session, config=config, task_id=task_id)
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})
            session.commit()

        assert result.status == "success"
        assert "already published" in result.summary.lower() or "idempotent" in result.summary.lower()

    def test_find_same_stem_subtitles_detects_matching(self, tmp_path):
        from media_pilot.services.task_input_analysis import _find_same_stem_subtitles

        video_dir = tmp_path / "media"
        video_dir.mkdir()
        video_path = video_dir / "movie.mkv"
        video_path.write_bytes(b"fake video")

        # Same stem subtitle
        sub1 = video_dir / "movie.srt"
        sub1.write_bytes(b"subtitle content")

        # Same stem with language suffix
        sub2 = video_dir / "movie.zh.srt"
        sub2.write_bytes(b"zh subtitle")

        # Different stem — should NOT match
        sub3 = video_dir / "other.srt"
        sub3.write_bytes(b"other subtitle")

        subs = _find_same_stem_subtitles(video_path)
        sub_names = {s.name for s in subs}

        assert "movie.srt" in sub_names
        assert "movie.zh.srt" in sub_names
        assert "other.srt" not in sub_names

    def test_same_stem_subtitles_not_found_without_matches(self, tmp_path):
        from media_pilot.services.task_input_analysis import _find_same_stem_subtitles

        video_dir = tmp_path / "media"
        video_dir.mkdir()
        video_path = video_dir / "movie.mkv"
        video_path.write_bytes(b"fake video")

        # Only different-stem subtitles
        sub1 = video_dir / "other.srt"
        sub1.write_bytes(b"other subtitle")

        subs = _find_same_stem_subtitles(video_path)
        assert len(subs) == 0


# ══════════════════════════════════════════════════════════════════════
# Preselected Strong-Facts Bypass
# ══════════════════════════════════════════════════════════════════════


def _make_preselected_task(session, *, source_path, **kwargs):
    """Module-level helper: 创建带 preselected_metadata_* 三字段的
    IngestTask. source_path 必须真实存在 (e.g. _safe_video() 创建),
    否则 check_eligibility 走 source_path_not_found 提前返回."""
    from media_pilot.repository.repositories import (
        IngestTaskCreate, IngestTaskRepository,
    )
    defaults = {
        "source_path": source_path,
        "status": "discovered",
        "current_step": "agent_start",
        "preselected_metadata_provider": "tmdb",
        "preselected_metadata_external_id": "movie:597",
        "preselected_metadata_profile": None,
        "media_type": "movie",
    }
    defaults.update(kwargs)
    title = defaults.pop("title", "Titanic")
    year = defaults.pop("year", 1997)
    task = IngestTaskRepository(session).create(IngestTaskCreate(**defaults))
    task.title = title
    task.year = year
    session.commit()
    return task


# ══════════════════════════════════════════════════════════════════════


class TestPreselectedStrongFactsBypass:
    """regression: IngestTask 上挂 preselected_metadata_provider / external_id
    时, check_eligibility 必须主动生成可消费 winner, 不得落 no_metadata_
    candidates blocking 路径. 真实生产现场 1 个用户任务 (Titanic, tmdb
    movie:597) 走 search_metadata 浪费 step 后被 max_steps 收口, 修
    复后该路径必须在 eligibility 层就主动生成 winner."""

    def test_check_eligibility_preselected_uses_persisted_candidate(
        self, tmp_path,
    ):
        """preselected 命中 + 持久化候选存在 → check_eligibility 走
        纯计算路径, 复用既有 candidate 的 title / year, provider
        字段返回真 metadata provider (e.g. "tmdb") 而不是
        "preselected". 不得隐式落库或拉 provider detail."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_preselected_task(
                session, source_path=str(video_path),
            )
            task_id = task.id
            # 落库一条 source="preselected" 候选, 模拟 DRAFT 路径
            # (prepare_select_metadata_candidate_decision) 已经走过.
            # 必须显式 commit, 否则第二个 session 看不到.
            _make_candidate(
                session, task_id,
                source="preselected", external_id="movie:597",
                media_type="movie", title="Titanic", year=1997,
                confidence=0.7,
                reason="preselected from DownloadTask",
                payload={"preselected_provider": "tmdb"},
            )
            session.commit()

            # 记录调用前的 MediaCandidate 数量.
            from media_pilot.repository.repositories import (
                MediaCandidateRepository,
            )
            cand_count_before = len(
                MediaCandidateRepository(session).list_for_task(task_id)
            )

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(
                session=session, config=config, task_id=task_id,
            )
            session.commit()

            # 关键回归: check_eligibility 不得新增 MediaCandidate
            # (READ-ONLY 边界, 不落库).
            from media_pilot.repository.repositories import (
                MediaCandidateRepository,
            )
            cand_count_after = len(
                MediaCandidateRepository(session).list_for_task(task_id)
            )
            assert cand_count_after == cand_count_before, (
                "check_eligibility 不得新增 MediaCandidate; "
                f"before={cand_count_before}, after={cand_count_after}"
            )

        # 关键: 走 preselected 旁路, blocking 不含 no_metadata_candidates
        assert "source_path_not_found" not in result.blocking_reasons
        assert "no_metadata_candidates" not in result.blocking_reasons
        assert result.has_clear_winner is True
        assert result.best_candidate is not None
        # provider 字段: 真 metadata provider, 不是 "preselected"
        assert result.best_candidate["provider"] == "tmdb"
        assert result.best_candidate["provider_id"] == "movie:597"
        assert result.best_candidate["title"] == "Titanic"
        assert result.best_candidate["year"] == 1997
        assert result.best_candidate["confidence"] == 1.0
        # task_facts["preselected"] 暴露完整 fact
        assert "preselected" in result.task_facts
        psf = result.task_facts["preselected"]
        assert psf["provider"] == "tmdb"
        assert psf["candidate_source"] == "preselected"

    def test_check_eligibility_preselected_falls_back_to_task_fields(
        self, tmp_path, monkeypatch,
    ):
        """preselected 命中 + 持久化候选空 → check_eligibility 用 task
        自身字段 (title / year) 兜底, 不调 fetch_and_save_metadata_
        detail (那会拉网络, 违反 READ-ONLY 边界). monkeypatch 监控
        fetch_and_save_metadata_detail 必须从未被调."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        # monkeypatch 一个会被记录的 fetch, 检查从未被调
        from media_pilot.services import auto_ingest as auto_ingest_module

        fetch_called = []

        def _exploding_fetch(*args, **kwargs):
            fetch_called.append(kwargs)
            raise AssertionError(
                "check_eligibility must NOT call fetch_and_save_metadata_detail"
            )

        monkeypatch.setattr(
            auto_ingest_module, "fetch_and_save_metadata_detail",
            _exploding_fetch,
        )

        with sf() as session:
            task = _make_preselected_task(
                session, source_path=str(video_path),
                title="Titanic", year=1997,
            )
            task_id = task.id

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(
                session=session, config=config, task_id=task_id,
            )
            session.commit()

        # 关键: fetch_and_save_metadata_detail 不得被调
        assert fetch_called == [], (
            f"check_eligibility 隐式调了 fetch_and_save_metadata_detail; "
            f"calls={fetch_called}"
        )

        # task.title / task.year 兜底
        assert result.has_clear_winner is True
        assert result.best_candidate["title"] == "Titanic"
        assert result.best_candidate["year"] == 1997
        assert result.best_candidate["provider"] == "tmdb"
        assert "no_metadata_candidates" not in result.blocking_reasons

        # 也不得落库 source="preselected" 候选
        with sf() as session:
            from media_pilot.repository.repositories import (
                MediaCandidateRepository,
            )
            candidates = MediaCandidateRepository(session).list_for_task(task_id)
            preselected = [c for c in candidates if c.source == "preselected"]
            assert preselected == [], (
                f"check_eligibility 不得落库 source=preselected 候选; "
                f"actual={preselected}"
            )

    def test_check_eligibility_preselected_uses_persisted_detail(
        self, tmp_path,
    ):
        """preselected 命中 + 持久化候选空 + 有持久化 MetadataDetail →
        check_eligibility 用 detail.title / detail.year 兜底, 仍标
        has_clear_winner=True. 这条路径覆盖 publish_movie_to_library
        先 fetch_and_save_metadata_detail (WRITE), 再调 check_eligibility
        (READ) 的真实场景."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_preselected_task(
                session, source_path=str(video_path),
                title="Wrong Title", year=1900,  # detail 优先
            )
            task_id = task.id
            from media_pilot.repository.models import MetadataDetail
            session.add(MetadataDetail(
                task_id=task_id, provider="tmdb",
                provider_id="movie:597", media_type="movie",
                title="Titanic (Real)", original_title=None, year=1997,
                payload={},
            ))
            session.commit()

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(
                session=session, config=config, task_id=task_id,
            )
            session.commit()

        # detail 优先于 task 字段
        assert result.has_clear_winner is True
        assert result.best_candidate["title"] == "Titanic (Real)"
        assert result.best_candidate["year"] == 1997
        assert result.best_candidate["provider"] == "tmdb"
        assert "no_metadata_candidates" not in result.blocking_reasons


# ══════════════════════════════════════════════════════════════════════
# Publish Gate — preselected winner 不等于已有 detail
# ══════════════════════════════════════════════════════════════════════


class TestPublishGatePreselected:
    """regression: preselected 解决"候选 winner"问题, 不解决"已有真实
    detail"问题. publish_movie_to_library 必须独立检查 MetadataDetail,
    即便 task 携带 preselected + 已有 source='preselected' 候选 +
    check_eligibility 放行, 没有 MetadataDetail 时仍要阻塞.

    修复前的现场: d90a49b 让 check_eligibility 在 preselected 命中时
    隐式调 fetch_and_save_metadata_detail 拉 detail, 落库 MediaCandidate
    + MetadataDetail. 这条隐式行为让 publish 自动有 detail, 但语义
    不对: READ_ONLY 边界被破坏, preselected 不再是 preselection, 而
    偷偷变成了 fetch + persist."""

    def test_publish_blocks_no_metadata_detail_even_with_preselected(
        self, tmp_path,
    ):
        """preselected task + persisted source='preselected' 候选 +
        没有 MetadataDetail → publish_movie_to_library 阻塞
        no_metadata_detail, 不被 check_eligibility 的 preselected
        旁路放行."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_preselected_task(
                session, source_path=str(video_path),
            )
            task_id = task.id
            # 模拟 DRAFT 路径已落库 source='preselected' 候选
            # (prepare_select_metadata_candidate_decision 走 _resolve_
            # preselected_winner). 必须有 payload.preselected_provider,
            # 否则 publish 链路上 provider 字段会落空.
            from media_pilot.repository.models import MediaCandidate
            session.add(MediaCandidate(
                task_id=task_id, source="preselected",
                media_type="movie", title="Titanic", year=1997,
                external_id="movie:597", confidence=1.0,
                reason="preselected from DownloadTask",
                payload={"preselected_provider": "tmdb"},
            ))
            session.commit()

        # check_eligibility 应当放行 (preselected winner).
        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            elig = check_eligibility(
                session=session, config=config, task_id=task_id,
            )
            assert elig.eligible is True, (
                f"preselected + persisted candidate 应放行; "
                f"blocking={elig.blocking_reasons}"
            )

        # publish_movie_to_library 仍要阻塞 no_metadata_detail.
        with sf() as session:
            from media_pilot.agent.tools.base import ToolContext
            from media_pilot.agent.tools.write import (
                _handle_publish_movie_to_library,
            )
            ctx = ToolContext(session=session, config=config, task_id=task_id)
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})
            session.commit()

        assert result.status == "failure"
        assert "no_metadata_detail" in str(result.data.get("reason", "")).lower() \
            or "no metadata detail" in result.summary.lower(), (
            f"preselected winner 不等于已有 detail, publish 必须阻塞 "
            f"no_metadata_detail; actual summary={result.summary!r} "
            f"data={result.data!r}"
        )

    def test_publish_succeeds_when_preselected_and_detail_both_present(
        self, tmp_path,
    ):
        """preselected task + persisted source='preselected' 候选 +
        已 fetch 落库 MetadataDetail → publish 应当走正常 publish 流
        (不一定要求 status='success', 但至少不阻塞 no_metadata_detail).
        这条覆盖正路径."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        video_path = _safe_video(tmp_path, config)

        with sf() as session:
            task = _make_preselected_task(
                session, source_path=str(video_path),
            )
            task_id = task.id
            from media_pilot.repository.models import (
                MediaCandidate, MetadataDetail,
            )
            session.add(MediaCandidate(
                task_id=task_id, source="preselected",
                media_type="movie", title="Titanic", year=1997,
                external_id="movie:597", confidence=1.0,
                reason="preselected from DownloadTask",
                payload={"preselected_provider": "tmdb"},
            ))
            session.add(MetadataDetail(
                task_id=task_id, provider="tmdb",
                provider_id="movie:597", media_type="movie",
                title="Titanic", original_title=None, year=1997,
                payload={},
            ))
            session.commit()

        with sf() as session:
            from media_pilot.agent.tools.base import ToolContext
            from media_pilot.agent.tools.write import (
                _handle_publish_movie_to_library,
            )
            ctx = ToolContext(session=session, config=config, task_id=task_id)
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})

        # 关键: 不得阻塞 no_metadata_detail (可能阻塞其他 gate,
        # 例如源文件路径 / size 校验, 但 no_metadata_detail 必须不出现).
        assert "no metadata detail" not in result.summary.lower(), (
            f"preselected + detail 都有时 publish 不得卡 no_metadata_detail; "
            f"actual summary={result.summary!r}"
        )
