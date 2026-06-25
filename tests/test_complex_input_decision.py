"""Tests for complex movie input analysis and decision generation.

Section 6.1: 服务层测试覆盖单文件 ready、多主视频、样片排除、
字幕不明确、无视频、疑似剧集/BDMV/ISO.

The service is side-effect-free — no DB writes, no AgentDecisionRequest
creation. Boundary cases are exercised by directly constructing
test fixtures in tmp_path.
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


# ── single-file movie → ready ────────────────────────────────────────


class TestSingleFileReady:
    def test_single_video_in_directory_is_ready(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video-bytes")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=source,
        )
        assert result.status == "ready"
        assert result.decision_type is None
        assert result.analysis is not None
        assert result.reason == "single_video_ready"
        assert len(result.analysis.video_candidates) == 1
        assert result.analysis.video_candidates[0].name == "Example.Movie.2026.mkv"

    def test_single_video_with_same_stem_subtitle_still_ready(self, tmp_path: Path):
        """单文件 + 同源字幕 → ready, 同源字幕由发布链路自动带入, 不询问用户."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video-bytes")
        sub = config.downloads_dir / "Example.Movie.2026.srt"
        sub.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=source,
        )
        assert result.status == "ready"
        same_stem_subs = [
            s for s in result.analysis.subtitle_candidates
            if s.matched_by == "same_stem"
        ]
        assert len(same_stem_subs) == 1


# ── multiple videos → select_primary_video decision ────────────────


class TestMultipleVideos:
    def test_two_videos_creates_select_primary_video_decision(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "Movie.2026.mkv").write_bytes(b"a" * 1024)
        (config.downloads_dir / "Movie.2026.Extra.mkv").write_bytes(b"b" * 2048)

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        assert result.status == "decision_requested"
        assert result.decision_type == "select_primary_video"
        assert result.free_text_allowed is False
        assert len(result.options) == 2
        option_ids = {opt.id for opt in result.options}
        assert option_ids == {"video_0", "video_1"}
        # 选项 payload 包含完整路径, 全部由后端生成
        for opt in result.options:
            assert opt.payload["path"]
            assert opt.payload["name"]
            assert opt.payload["size_bytes"] > 0

    def test_sample_video_excluded_from_candidates(self, tmp_path: Path):
        """sample/trailer 视频被排除, 不进入 select_primary_video 候选."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "Movie.2026.mkv").write_bytes(b"a" * 1024)
        (config.downloads_dir / "Movie.2026.sample.mkv").write_bytes(b"b" * 1024)
        (config.downloads_dir / "trailer.mp4").write_bytes(b"c" * 1024)

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        # 主候选只剩 Movie.2026.mkv — 不是多视频场景, 应该直接 ready.
        assert result.status == "ready"
        assert len(result.analysis.video_candidates) == 1
        assert result.analysis.video_candidates[0].name == "Movie.2026.mkv"
        # 排除的视频被记录
        excluded_names = {e.name for e in result.analysis.auxiliary_videos}
        assert "Movie.2026.sample.mkv" in excluded_names
        assert "trailer.mp4" in excluded_names


# ── ambiguous subtitles → select_subtitles decision ─────────────────


class TestAmbiguousSubtitles:
    def test_single_video_with_non_same_stem_subtitle(self, tmp_path: Path):
        """单主视频 + 非同源字幕 → 询问用户字幕选择."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "Movie.2026.mkv").write_bytes(b"video")
        (config.downloads_dir / "random_chs.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        (config.downloads_dir / "another_chs.ass").write_text("[Script Info]\n")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        assert result.status == "decision_requested"
        assert result.decision_type == "select_subtitles"
        # 选项 = 2 字幕 + 1 "no_subtitles"
        assert len(result.options) == 3
        option_ids = {opt.id for opt in result.options}
        assert "no_subtitles" in option_ids
        no_subs_option = next(o for o in result.options if o.id == "no_subtitles")
        assert no_subs_option.payload.get("selected_subtitles") == []


# ── no videos → no_videos ───────────────────────────────────────────


class TestNoVideos:
    def test_directory_with_only_subtitles_returns_no_videos(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "movie.srt").write_text("...")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        assert result.status == "no_videos"
        assert result.reason == "no_video_files_found"


# ── BDMV / ISO → unsupported / review_complex_input ────────────────


class TestBdmvIso:
    def test_bdmv_directory_returns_unsupported(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        bdmv_root = config.downloads_dir / "MY_MOVIE"
        bdmv_root.mkdir()
        (bdmv_root / "BDMV").mkdir()
        (bdmv_root / "CERTIFICATE").mkdir()
        (bdmv_root / "STREAM").mkdir()
        (bdmv_root / "STREAM" / "00001.m2ts").write_bytes(b"movie")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=bdmv_root,
        )
        assert result.status == "unsupported"
        assert result.decision_type == "review_complex_input"
        assert result.free_text_allowed is True
        assert "bdmv_or_iso" in result.analysis.detected

    def test_iso_file_returns_unsupported(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        iso_path = config.downloads_dir / "movie.iso"
        iso_path.write_bytes(b"ISO")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=iso_path,
        )
        assert result.status == "unsupported"
        assert result.decision_type == "review_complex_input"
        assert "bdmv_or_iso" in result.analysis.detected


# ── show structure → unsupported ────────────────────────────────────


class TestShowStructure:
    def test_directory_with_sxxexx_pattern_returns_show_like(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        show_dir = config.downloads_dir / "Complete.Show.S01.1080p"
        show_dir.mkdir()
        (show_dir / "Show.S01E01.mkv").write_bytes(b"ep1")
        (show_dir / "Show.S01E02.mkv").write_bytes(b"ep2")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=show_dir,
        )
        # show-like 输入不再走 review_complex_input, 改返回 show_like
        # 让工具把控制权交给 prepare_show_structure.
        assert result.status == "show_like"
        assert result.decision_type is None
        assert result.reason == "show_like"
        assert "show_structure" in result.analysis.detected

    def test_directory_with_season_keyword_returns_show_like(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        season_dir = config.downloads_dir / "Some.Show.Season.1"
        season_dir.mkdir()
        (season_dir / "ep1.mkv").write_bytes(b"e1")
        (season_dir / "ep2.mkv").write_bytes(b"e2")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=season_dir,
        )
        assert result.status == "show_like"
        assert "show_structure" in result.analysis.detected

    def test_show_like_does_not_create_review_decision_in_tool(self, tmp_path: Path):
        """prepare_complex_input_decision 工具收到 show_like 时,
        把数据当 ready 透传, 不创建 review_complex_input 决策."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        show_dir = config.downloads_dir / "Complete.Show.S01.1080p"
        show_dir.mkdir()
        (show_dir / "Show.S01E01.mkv").write_bytes(b"ep1")
        (show_dir / "Show.S01E02.mkv").write_bytes(b"ep2")

        from tests.test_api_v1 import _make_session_factory
        from media_pilot.agent.tools.complex_input import (
            _handle_prepare_complex_input_decision,
        )
        from media_pilot.agent.tools.base import ToolContext
        from media_pilot.repository.repositories import (
            AgentRunCreate,
            AgentRunRepository,
            AgentDecisionRequestRepository,
            IngestTaskRepository,
            IngestTaskCreate,
        )

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path=str(show_dir), status="agent_running",
                current_step="agent_start", media_type="show",
            ))
            run = AgentRunRepository(session).create(AgentRunCreate(
                task_id=task.id, current_step="agent_start",
            ))
            session.commit()
            task_id = task.id
            run_id = run.id

            ctx = ToolContext(
                session=session, config=config,
                task_id=task_id, run_id=run_id,
            )
            result = _handle_prepare_complex_input_decision(
                context=ctx, input_data={"task_id": task_id},
            )
            session.commit()

            drs = AgentDecisionRequestRepository(session).list_pending_by_run(run_id)

        # ready=true, is_show=true, 不创建决策.
        assert result.data["ready"] is True
        assert result.data["is_show"] is True
        assert "decision_requested" not in result.data
        # 没有创建 review_complex_input / select_primary_video 等决策.
        assert drs == []

    def test_single_file_sxxexx_returns_show_like(self, tmp_path: Path):
        """单文件 Example.Show.S01E01.mkv 应当被识别为 show_like,
        让后续工具把它当 ready 透传, data.is_show=true."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Show.S01E01.mkv"
        source.write_bytes(b"v")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=source,
        )
        assert result.status == "show_like"
        assert result.decision_type is None
        assert result.reason == "show_like"
        assert "show_structure" in result.analysis.detected
        # 候选视频就是这一个文件
        assert len(result.analysis.video_candidates) == 1
        assert result.analysis.video_candidates[0].name == "Example.Show.S01E01.mkv"

    def test_single_file_sxxexx_propagates_is_show_in_tool(self, tmp_path: Path):
        """prepare_complex_input_decision 工具收到单文件 SxxExx 时,
        把数据当 ready 透传, data.is_show=true, 不创建 review 决策."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Show.S01E01.mkv"
        source.write_bytes(b"v")

        from tests.test_api_v1 import _make_session_factory
        from media_pilot.agent.tools.complex_input import (
            _handle_prepare_complex_input_decision,
        )
        from media_pilot.agent.tools.base import ToolContext
        from media_pilot.repository.repositories import (
            AgentDecisionRequestRepository,
            AgentRunCreate,
            AgentRunRepository,
            IngestTaskCreate,
            IngestTaskRepository,
        )

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path=str(source), status="agent_running",
                current_step="agent_start", media_type="show",
            ))
            run = AgentRunRepository(session).create(AgentRunCreate(
                task_id=task.id, current_step="agent_start",
            ))
            session.commit()
            task_id = task.id
            run_id = run.id

            ctx = ToolContext(
                session=session, config=config,
                task_id=task_id, run_id=run_id,
            )
            result = _handle_prepare_complex_input_decision(
                context=ctx, input_data={"task_id": task_id},
            )
            session.commit()

            drs = AgentDecisionRequestRepository(session).list_pending_by_run(run_id)

        assert result.data["ready"] is True
        assert result.data["is_show"] is True
        assert "decision_requested" not in result.data
        assert drs == []

    def test_single_file_regular_movie_returns_ready(self, tmp_path: Path):
        """单文件普通电影 (无 SxxExx) 必须返回 ready, 不能误判为 show_like."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"v")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=source,
        )
        assert result.status == "ready"
        assert "show_structure" not in result.analysis.detected
        assert result.reason == "single_video_ready"

    def test_single_file_year_only_is_not_show_like(self, tmp_path: Path):
        """文件名带 ``S2024`` (年份前缀) 但没有 E/Ex 后缀, 不能误判 SxxExx."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "My.S2024.Movie.mkv"
        source.write_bytes(b"v")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=source,
        )
        assert result.status == "ready"
        assert "show_structure" not in result.analysis.detected


# ── unsafe path / scan failed ──────────────────────────────────────


class TestPathSafety:
    def test_unsafe_path_returns_unsafe_path(self, tmp_path: Path):
        config = _make_config(tmp_path)
        unsafe = tmp_path / "unsafe"
        unsafe.mkdir()
        target = unsafe / "movie.mkv"
        target.write_bytes(b"x")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=target,
        )
        assert result.status == "unsafe_path"
        assert result.reason == "source_path_outside_safe_roots"

    def test_nonexistent_path_returns_scan_failed(self, tmp_path: Path):
        config = _make_config(tmp_path)

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=tmp_path / "missing.mkv",
        )
        assert result.status == "scan_failed"
        assert result.reason == "source_path_not_found"


# ── decision payload generation by backend (no LLM paths) ─────────


class TestDecisionPayloadBackend:
    def test_select_primary_video_options_have_paths_in_payload(self, tmp_path: Path):
        """选项 payload 必须由后端写, 验证 size / path / name 全部存在."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "A.mkv").write_bytes(b"a" * 5000)
        (config.downloads_dir / "B.mkv").write_bytes(b"b" * 3000)

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        assert result.status == "decision_requested"
        # 校验所有选项都包含完整的 path / name / size_bytes
        opt_a = next(o for o in result.options if o.id == "video_0")
        assert opt_a.payload["path"].endswith("A.mkv")
        assert opt_a.payload["name"] == "A.mkv"
        assert opt_a.payload["size_bytes"] == 5000
        opt_b = next(o for o in result.options if o.id == "video_1")
        assert opt_b.payload["size_bytes"] == 3000
        # description 含后端生成的大小字符串
        assert "5.0 KB" in opt_a.description or "4.9 KB" in opt_a.description
        assert "2.9 KB" in opt_b.description or "3.0 KB" in opt_b.description


# ── detection: detected tags list ──────────────────────────────────


class TestDetectedTags:
    def test_multiple_videos_adds_detected_tag(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "A.mkv").write_bytes(b"a")
        (config.downloads_dir / "B.mkv").write_bytes(b"b")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        assert "multiple_videos" in result.analysis.detected

    def test_single_video_does_not_add_multiple_videos(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "A.mkv").write_bytes(b"a")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        assert "multiple_videos" not in result.analysis.detected


# ── user_selection 消费: 防止回复后再次创建同样决策 (Issue 1) ─────


class TestUserSelectionConsumption:
    def test_selected_path_makes_multiple_videos_collapse_to_single(
        self, tmp_path: Path,
    ):
        """目录里两个视频, 但 user_selection.selected_path 已指明主视频
        → 不再触发 multiple_videos 决策, 直接用 selected_path 走单视频逻辑."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        a = config.downloads_dir / "A.mkv"
        b = config.downloads_dir / "B.mkv"
        a.write_bytes(b"a" * 1024)
        b.write_bytes(b"b" * 2048)

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
            user_selection={"selected_path": str(a)},
        )
        # 应该跳过 select_primary_video, 走到单视频上下文
        assert result.status == "ready"
        assert result.reason in ("single_video_ready", "user_subtitles_resolved")
        assert result.analysis.video_candidates[0].path == str(a)

    def test_selected_subtitles_key_returns_ready(self, tmp_path: Path):
        """user_selection.payload.selected_subtitles 存在 (含空数组)
        → 字幕已处理, 不再创建 select_subtitles 决策."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        video = config.downloads_dir / "Movie.2026.mkv"
        video.write_bytes(b"video")
        sub = config.downloads_dir / "random.srt"
        sub.write_text("...")
        # 文件存在但未选, 也不应是同源 → ambiguous 场景
        assert (config.downloads_dir / "random.srt").exists()

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=video,
            user_selection={"selected_subtitles": [str(sub)]},
        )
        assert result.status == "ready"
        assert result.reason == "user_subtitles_resolved"

    def test_empty_selected_subtitles_returns_ready(self, tmp_path: Path):
        """user_selection.payload.selected_subtitles = [] (用户选 no_subtitles)
        → 字幕已处理, ready, 不再询问."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        video = config.downloads_dir / "Movie.2026.mkv"
        video.write_bytes(b"video")
        sub = config.downloads_dir / "random.srt"
        sub.write_text("...")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=video,
            user_selection={"selected_subtitles": []},
        )
        assert result.status == "ready"
        assert result.reason == "user_subtitles_resolved"

    def test_user_note_returns_unsupported_no_review_loop(
        self, tmp_path: Path,
    ):
        """user_selection 含 user_note (review_complex_input 已消费)
        → 返回 unsupported 边界, 不再创建新 review 决策."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        bdmv_root = config.downloads_dir / "BDMV_MOVIE"
        bdmv_root.mkdir()
        (bdmv_root / "BDMV").mkdir()

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=bdmv_root,
            user_selection={"user_note": "请把 BDMV 文件夹当蓝光原盘处理"},
        )
        # 不应再创建 review 决策; 应返回 unsupported + 失败事实
        assert result.status == "unsupported"
        assert result.decision_type == "review_complex_input"
        assert result.free_text_allowed is False
        assert result.reason == "review_user_note_already_consumed"

    def test_selected_path_unsafe_falls_back_to_multiple_videos(
        self, tmp_path: Path,
    ):
        """user_selection.selected_path 越界或不存在
        → 退回多视频决策让用户重选, 不静默吞掉 user_selection."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        a = config.downloads_dir / "A.mkv"
        b = config.downloads_dir / "B.mkv"
        a.write_bytes(b"a")
        b.write_bytes(b"b")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
            user_selection={"selected_path": "/nonexistent/elsewhere.mkv"},
        )
        # selected_path 不在受控根 → 退回多视频决策
        assert result.status == "decision_requested"
        assert result.decision_type == "select_primary_video"


# ── Issue B: single_video_ready 持久化 MediaSourceSelection ─────────────


class TestSingleVideoReadyPersistsMediaSourceSelection:
    """prepare_complex_input_decision 的 single_video_ready 路径走完后,
    _handle_prepare_complex_input_decision 必须把唯一主视频文件路径
    写进 MediaSourceSelection, 供后续 publish / overwrite 链路使用.
    这是 Issue B 修复的核心: 缺这一步时, publish_movie_to_library 与
    target_conflict_handler.handle_overwrite_target 会回退到 task.source_path
    (目录), 触发 IsADirectoryError.
    """

    def test_single_video_dir_persists_selection(
        self, tmp_path: Path,
    ):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from media_pilot.agent.tools.base import ToolContext
        from media_pilot.agent.tools.complex_input import (
            _handle_prepare_complex_input_decision,
        )
        from media_pilot.repository.database import Base
        from media_pilot.repository.models import (
            IngestTask, MediaSourceSelection,
        )
        from media_pilot.repository.repositories import (
            IngestTaskCreate, IngestTaskRepository,
        )

        config = _make_config(tmp_path)
        config.watch_dir.mkdir(parents=True, exist_ok=True)
        source_dir = config.watch_dir / "Warcraft ... [YTS.MX]"
        source_dir.mkdir(parents=True, exist_ok=True)
        video = source_dir / "Warcraft.2016.1080p.BluRay.x264.mkv"
        video.write_bytes(b"fake video content")
        (source_dir / "info.txt").write_text("info")

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

        with SessionLocal() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path=str(source_dir),
                status="agent_running",
                current_step="agent_start",
                media_type="movie",
            ))
            session.commit()
            task_id = task.id

        with SessionLocal() as session:
            ctx = ToolContext(
                session=session, config=config, task_id=task_id, run_id=None,
            )
            result = _handle_prepare_complex_input_decision(
                ctx, {"task_id": task_id},
            )
            session.commit()

        # 决策应当 ready (单视频目录)
        assert result.status == "success"
        assert result.data["ready"] is True
        assert result.data["reason"] == "single_video_ready"

        # MediaSourceSelection 应当被持久化
        with SessionLocal() as session:
            sel_list = session.query(MediaSourceSelection).filter(
                MediaSourceSelection.task_id == task_id,
            ).all()
            assert len(sel_list) == 1
            sel = sel_list[0]
            assert sel.selected_path == str(video)
            assert sel.input_path == str(source_dir)
            assert sel.reason.startswith("auto_single_video:")
            # payload 应当保留 auxiliary / excluded / subtitle_candidates
            assert "auxiliary_videos" in sel.payload
            assert "excluded" in sel.payload
            assert "subtitle_candidates" in sel.payload


# ── dominant size heuristic → 短路 select_primary_video ──────────────


def _write_video(path: Path, size_bytes: int) -> None:
    """Create a size_bytes-byte video stub via sparse file (no actual data)."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        os.truncate(fd, size_bytes)
    finally:
        os.close(fd)


class TestDominantPrimaryVideo:
    def test_3p7gib_plus_1p9mib_returns_ready_no_decision(self, tmp_path: Path) -> None:
        """3.7 GB + 1.9 MB → dominant 命中 → ready, 不创建 select_primary_video."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        _write_video(config.downloads_dir / "main.mp4", 3 * 1024 * 1024 * 1024 + 700 * 1024 * 1024)
        _write_video(config.downloads_dir / "ad.mp4", int(1.9 * 1024 * 1024))

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        # 命中 dominant, 走 ready 路径, 不创建 select_primary_video
        assert result.status == "ready"
        assert result.decision_type is None
        assert result.reason == "single_video_ready"
        assert len(result.analysis.video_candidates) == 1
        assert result.analysis.video_candidates[0].name == "main.mp4"
        # 伴随视频进 aux 字段
        aux_names = {a.name for a in result.analysis.auxiliary_videos}
        assert "ad.mp4" in aux_names

    def test_700mib_plus_650mib_still_creates_select_primary_video(self, tmp_path: Path) -> None:
        """大小接近 → 仍创建 select_primary_video 决策."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        _write_video(config.downloads_dir / "a.mp4", 700 * 1024 * 1024)
        _write_video(config.downloads_dir / "b.mp4", 650 * 1024 * 1024)

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        assert result.status == "decision_requested"
        assert result.decision_type == "select_primary_video"
        option_names = {opt.payload["name"] for opt in result.options}
        assert option_names == {"a.mp4", "b.mp4"}

    def test_dominant_with_ambiguous_subtitles_creates_select_subtitles(self, tmp_path: Path) -> None:
        """dominant 命中 + 非同源字幕 → 仍走 select_subtitles 决策."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        _write_video(config.downloads_dir / "main.mp4", 3 * 1024 * 1024 * 1024 + 700 * 1024 * 1024)
        _write_video(config.downloads_dir / "ad.mp4", int(1.9 * 1024 * 1024))
        (config.downloads_dir / "extra.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        # dominant 主视频是 main.mp4, 非同源字幕触发 select_subtitles
        assert result.status == "decision_requested"
        assert result.decision_type == "select_subtitles"
        assert result.analysis.video_candidates[0].name == "main.mp4"

    def test_dominant_passes_aux_videos_to_analysis(self, tmp_path: Path) -> None:
        """dominant 命中后, aux_videos 字段同时透传 marker 与 size 两类排除视频."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        _write_video(config.downloads_dir / "main.mp4", 3 * 1024 * 1024 * 1024 + 700 * 1024 * 1024)
        _write_video(config.downloads_dir / "sample-clip.mp4", 200)
        _write_video(config.downloads_dir / "ad.mp4", int(1.9 * 1024 * 1024))

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        assert result.status == "ready"
        aux_names = {a.name for a in result.analysis.auxiliary_videos}
        assert {"sample-clip.mp4", "ad.mp4"}.issubset(aux_names)
        # video_candidates 仅 main.mp4
        assert len(result.analysis.video_candidates) == 1
        assert result.analysis.video_candidates[0].name == "main.mp4"

    def test_show_structure_priority_over_dominant(self, tmp_path: Path) -> None:
        """show_structure 标记优先于 dominant_primary_video, 不被 size 启发式吞掉."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        # 目录名带 season 关键词, 触发 show_structure
        season_dir = config.downloads_dir / "Show.S01.Complete"
        season_dir.mkdir(parents=True, exist_ok=True)
        _write_video(season_dir / "main.mp4", 3 * 1024 * 1024 * 1024 + 700 * 1024 * 1024)
        _write_video(season_dir / "ad.mp4", int(1.9 * 1024 * 1024))

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=season_dir,
        )
        # show_structure 分支优先 → status=show_like, 不是 ready
        assert result.status == "show_like"
        assert "show_structure" in result.analysis.detected

    def test_4gib_plus_700mib_plus_1mib_does_not_trigger_dominant(
        self, tmp_path: Path,
    ) -> None:
        """700 MiB 远超 min(50 MiB, 4 GiB * 0.02) = 50 MiB 上限, 必须不触发.

        若 1 MB 广告被误消歧, prepare_complex_input_decision 会走
        dominant 短路 → 错过 select_primary_video 决策, 用户在 4 GB 主片
        + 700 MB 第二视频 + 1 MB 广告之间没有机会选主.
        """
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        # 字典序: ad 在 main 之前, 排序关键路径都不能误选
        _write_video(config.downloads_dir / "ad.mp4", 1 * 1024 * 1024)
        _write_video(config.downloads_dir / "main.mp4", 4 * 1024 * 1024 * 1024)
        _write_video(config.downloads_dir / "second.mp4", 700 * 1024 * 1024)

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        # heuristic 不命中 → 走多视频 select_primary_video 决策
        assert result.status == "decision_requested"
        assert result.decision_type == "select_primary_video"
        # 三个视频都是候选
        option_names = {opt.payload["name"] for opt in result.options}
        assert option_names == {"ad.mp4", "main.mp4", "second.mp4"}
        # size_bytes 透传到 payload, 最大视频是 main.mp4
        main_opt = next(o for o in result.options if o.payload["name"] == "main.mp4")
        assert main_opt.payload["size_bytes"] == 4 * 1024 * 1024 * 1024

    def test_4gib_plus_700mib_plus_1mib_does_not_trigger_dominant_reversed(
        self, tmp_path: Path,
    ) -> None:
        """同场景但文件名字典序倒过来, 验证 dominant 分支不依赖字典序.

        上一测试 ``ad.mp4`` 在前, 字典序会让 ``files[0]`` 不是最大. 这次
        倒过来, ``zzz_main.mp4`` 字典序最末, 但 size 最大; 仍不应被吞.
        """
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        _write_video(config.downloads_dir / "zzz_ad.mp4", 1 * 1024 * 1024)
        _write_video(config.downloads_dir / "zzz_second.mp4", 700 * 1024 * 1024)
        _write_video(config.downloads_dir / "zzz_main.mp4", 4 * 1024 * 1024 * 1024)

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        # heuristic 仍不命中
        assert result.status == "decision_requested"
        assert result.decision_type == "select_primary_video"
        option_names = {opt.payload["name"] for opt in result.options}
        assert option_names == {"zzz_ad.mp4", "zzz_main.mp4", "zzz_second.mp4"}

    def test_4gib_plus_1mib_plus_2mib_triggers_dominant(self, tmp_path: Path) -> None:
        """1 MiB + 2 MiB 都 <= min(50 MiB, 4 GiB * 0.02) = 50 MiB → 启发式命中.

        验证 4GB + 1MB + 2MB 这种"主片 + 两个微伴随"场景, 仍按
        dominant 命中处理, 1MB / 2MB 都进 excluded, ready 路径.
        """
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        _write_video(config.downloads_dir / "main.mp4", 4 * 1024 * 1024 * 1024)
        _write_video(config.downloads_dir / "ad1.mp4", 1 * 1024 * 1024)
        _write_video(config.downloads_dir / "ad2.mp4", 2 * 1024 * 1024)

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        # heuristic 命中 → ready, video_candidates 仅 main
        assert result.status == "ready"
        assert result.decision_type is None
        assert result.reason == "single_video_ready"
        assert len(result.analysis.video_candidates) == 1
        assert result.analysis.video_candidates[0].name == "main.mp4"
        # 两个伴随视频都进 aux 字段
        aux_names = {a.name for a in result.analysis.auxiliary_videos}
        assert {"ad1.mp4", "ad2.mp4"}.issubset(aux_names)


# ── 目录模式 same-stem 字幕: 不创建 select_subtitles 决策 ───────────


class TestDirectoryModeSameStemDecision:
    """MP-Test-03 (Darkest Hour) 现场: 目录内 only same-stem 字幕 → 不应
    创建 select_subtitles 决策. 修复后, 目录模式
    ``analyze_task_input`` 标 ``matched_by="same_stem"``,
    ``_resolve_after_primary_chosen`` 用该字段收敛, 直接 ready."""

    def test_directory_with_only_same_stem_subtitle_is_ready(
        self, tmp_path: Path,
    ) -> None:
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        _write_video(
            config.downloads_dir / "Darkest Hour (2017).mp4",
            300 * 1024 * 1024,
        )
        (config.downloads_dir / "Darkest Hour (2017).zh.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhi\n",
        )

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        # 同源字幕自动带入, 不应询问用户
        assert result.status == "ready"
        assert result.decision_type is None
        assert result.reason == "single_video_ready"
        # 同源字幕应在 analysis.subtitle_candidates 且 matched_by=same_stem
        same_stem_subs = [
            s for s in result.analysis.subtitle_candidates
            if s.matched_by == "same_stem"
        ]
        assert len(same_stem_subs) == 1
        assert same_stem_subs[0].name == "Darkest Hour (2017).zh.srt"

    def test_directory_with_mixed_subs_creates_select_subtitles(
        self, tmp_path: Path,
    ) -> None:
        """目录内含同源 + 非同源字幕 → 仍建 select_subtitles 决策, 选项
        列表只含 non-same-stem (同源由 _resolve_after_primary_chosen
        收敛)."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        _write_video(
            config.downloads_dir / "Darkest Hour (2017).mp4",
            300 * 1024 * 1024,
        )
        (config.downloads_dir / "Darkest Hour (2017).zh.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhi\n",
        )
        (config.downloads_dir / "extras.zh.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhi\n",
        )

        from media_pilot.services.complex_input_decision import (
            prepare_complex_input_decision,
        )

        result = prepare_complex_input_decision(
            config=config, source_path=config.downloads_dir,
        )
        # 同源自动带入, 仅询问 ambiguous 的 extras
        assert result.status == "decision_requested"
        assert result.decision_type == "select_subtitles"
        # 选项: 1 (extras) + 1 (no_subtitles) = 2
        assert len(result.options) == 2
        option_names = {opt.payload.get("name") for opt in result.options}
        assert "extras.zh.srt" in option_names
        # 同源字幕不在 options (auto-included)
        assert "Darkest Hour (2017).zh.srt" not in option_names
