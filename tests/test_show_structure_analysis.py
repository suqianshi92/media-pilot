"""Task 1: 剧集结构分析与 EpisodeMapping 服务测试.

覆盖场景:
- 单集 (S01E01) → auto_publishable
- 同季连续多集 (S01E01-E05) → auto_publishable
- 跨季 (S01E05 + S02E01) → unsupported_cross_season
- 稀疏集 (S01E01 + S01E03) → unsupported_sparse_episodes
- Season 0 (S00E01) → unsupported_season_0_specials
- 单文件多集 (S01E01E02) → unsupported_multi_episode_in_single_file
- 无法解析 (无 SxxExx) → not_show_structure
- EpisodeMappingRepository 落库 / 覆盖语义
- 任务输入缺失 / 不存在 → 明确 block_reason
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────


def _make_session_factory(tmp_path: Path):
    from tests.test_api_v1 import _make_session_factory
    return _make_session_factory(tmp_path)


def _make_task(session, source_path: str, *, media_type: str | None = None):
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

    task = IngestTaskRepository(session).create(IngestTaskCreate(
        source_path=source_path,
        status="discovered",
        current_step="agent_start",
        media_type=media_type,
    ))
    session.commit()
    return task


# ── analyze_show_structure 单集 / 多集 ───────────────────────────


class TestSingleEpisodeAutoPublishable:
    def test_single_file_s01e01(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            STATUS_AUTO_PUBLISHABLE,
            analyze_show_structure,
        )

        source = tmp_path / "Example.Show.S01E01.mkv"
        source.write_bytes(b"v")

        result = analyze_show_structure(source)

        assert result.status == STATUS_AUTO_PUBLISHABLE
        assert result.season == 1
        assert result.episode_range == "S01E01"
        assert len(result.entries) == 1
        assert result.entries[0].file_path == str(source)
        assert result.entries[0].season == 1
        assert result.entries[0].episode == 1
        assert result.detected_show_title == "Example.Show"


class TestSameSeasonContinuousMultiEpisodeAutoPublishable:
    def test_directory_with_s01e01_to_e05(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            STATUS_AUTO_PUBLISHABLE,
            analyze_show_structure,
        )

        for i in range(1, 6):
            (tmp_path / f"Example.Show.S01E{i:02d}.mkv").write_bytes(b"v")

        result = analyze_show_structure(tmp_path)

        assert result.status == STATUS_AUTO_PUBLISHABLE
        assert result.season == 1
        assert result.episode_range == "S01E01-E05"
        assert len(result.entries) == 5
        assert sorted(e.episode for e in result.entries) == [1, 2, 3, 4, 5]
        # mapping 必须按 episode 稳定排序
        episodes = [e.episode for e in result.entries]
        assert episodes == sorted(episodes)
        # 全部同季
        assert all(e.season == 1 for e in result.entries)


# ── 跨季 / 稀疏集 ────────────────────────────────────────────────


class TestCrossSeasonUnsupported:
    def test_s01e05_with_s02e01_in_same_directory(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_CROSS_SEASON,
            STATUS_UNSUPPORTED_CROSS_SEASON,
            analyze_show_structure,
        )

        (tmp_path / "Show.S01E05.mkv").write_bytes(b"v")
        (tmp_path / "Show.S02E01.mkv").write_bytes(b"v")

        result = analyze_show_structure(tmp_path)

        assert result.status == STATUS_UNSUPPORTED_CROSS_SEASON
        assert result.block_reason == BLOCK_REASON_CROSS_SEASON


class TestSparseEpisodesUnsupported:
    def test_s01e01_and_s01e03_with_no_e02(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_SPARSE_EPISODES,
            STATUS_UNSUPPORTED_SPARSE_EPISODES,
            analyze_show_structure,
        )

        (tmp_path / "Show.S01E01.mkv").write_bytes(b"v")
        (tmp_path / "Show.S01E03.mkv").write_bytes(b"v")

        result = analyze_show_structure(tmp_path)

        assert result.status == STATUS_UNSUPPORTED_SPARSE_EPISODES
        assert result.block_reason == BLOCK_REASON_SPARSE_EPISODES
        assert result.season == 1
        # 范围反映最大跨度, 但仍是稀疏
        assert "S01E" in (result.episode_range or "")


# ── Season 0 / 单文件多集 ────────────────────────────────────────


class TestSeason0SpecialsUnsupported:
    def test_s00e01_file_refuses_to_publish(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_SEASON_0_SPECIALS,
            STATUS_UNSUPPORTED_SEASON_0_SPECIALS,
            analyze_show_structure,
        )

        source = tmp_path / "Show.S00E01.mkv"
        source.write_bytes(b"v")

        result = analyze_show_structure(source)

        assert result.status == STATUS_UNSUPPORTED_SEASON_0_SPECIALS
        assert result.block_reason == BLOCK_REASON_SEASON_0_SPECIALS


class TestMultiEpisodeInSingleFileUnsupported:
    def test_s01e01e02_file_refuses_to_publish(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_MULTI_EPISODE_IN_SINGLE_FILE,
            STATUS_UNSUPPORTED_MULTI_EPISODE_IN_SINGLE_FILE,
            analyze_show_structure,
        )

        source = tmp_path / "Show.S01E01E02.mkv"
        source.write_bytes(b"v")

        result = analyze_show_structure(source)

        assert result.status == STATUS_UNSUPPORTED_MULTI_EPISODE_IN_SINGLE_FILE
        assert result.block_reason == BLOCK_REASON_MULTI_EPISODE_IN_SINGLE_FILE


# ── 无法解析 / 任务输入缺失 ────────────────────────────────────


class TestNotShowStructure:
    def test_video_without_sxxexx_pattern(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_NOT_SHOW_STRUCTURE,
            STATUS_NOT_SHOW_STRUCTURE,
            analyze_show_structure,
        )

        source = tmp_path / "Random.Video.mkv"
        source.write_bytes(b"v")

        result = analyze_show_structure(source)

        assert result.status == STATUS_NOT_SHOW_STRUCTURE
        assert result.block_reason == BLOCK_REASON_NOT_SHOW_STRUCTURE


class TestNoVideoFiles:
    def test_directory_without_any_video(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_NO_VIDEO_FILES,
            STATUS_NO_VIDEO_FILES,
            analyze_show_structure,
        )

        # 只有字幕, 没有视频
        (tmp_path / "movie.srt").write_text("...")

        result = analyze_show_structure(tmp_path)

        assert result.status == STATUS_NO_VIDEO_FILES
        assert result.block_reason == BLOCK_REASON_NO_VIDEO_FILES

    def test_nonexistent_path(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_NO_VIDEO_FILES,
            STATUS_NO_VIDEO_FILES,
            analyze_show_structure,
        )

        missing = tmp_path / "does" / "not" / "exist.mkv"
        result = analyze_show_structure(missing)

        assert result.status == STATUS_NO_VIDEO_FILES
        assert result.block_reason == BLOCK_REASON_NO_VIDEO_FILES


# ── 辅助视频永远不进剧集候选 ────────────────────────────────────


class TestAuxiliaryVideosExcluded:
    def test_sample_video_excluded_even_with_sxxexx(self, tmp_path: Path):
        """含 SxxExx 的 sample/trailer 视频仍按辅助视频排除, 不进入剧集候选."""
        from media_pilot.services.show_structure_analysis import (
            STATUS_AUTO_PUBLISHABLE,
            analyze_show_structure,
        )

        # 真正的 episode 视频
        (tmp_path / "Show.S01E01.mkv").write_bytes(b"v")
        # 含 SxxExx 的 sample — 必须被 analyze_task_input 排除
        (tmp_path / "sample.S01E01.mkv").write_bytes(b"v")

        result = analyze_show_structure(tmp_path)

        # 真正的 E01 进候选; 排除的 sample 在 analyze_task_input 阶段被剔除,
        # per-file detect 仍能解析出 E01, 整体判定为 auto_publishable.
        # 关键: 不会把 sample 误当成 episode.
        assert result.status == STATUS_AUTO_PUBLISHABLE
        assert len(result.entries) == 1
        assert result.entries[0].file_path.endswith("Show.S01E01.mkv")

    def test_only_sample_videos_no_real_episodes(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_NO_VIDEO_FILES,
            analyze_show_structure,
        )

        # 只有 sample, 没有任何真实视频
        (tmp_path / "Show.S01E01.sample.mkv").write_bytes(b"v")

        result = analyze_show_structure(tmp_path)

        # 候选视频列表为空 (sample 已 analyze_task_input 阶段被排除),
        # 整体判定为 no_video_files.
        assert result.block_reason == BLOCK_REASON_NO_VIDEO_FILES


# ── prepare_show_episode_mapping 落库 / 任务事实持久化 ──────────


class TestPrepareShowEpisodeMappingPersistsFact:
    def test_persists_mappings_on_auto_publishable(
        self, tmp_path: Path,
    ):
        from media_pilot.services.show_structure_analysis import (
            STATUS_AUTO_PUBLISHABLE,
            prepare_show_episode_mapping,
        )

        sf = _make_session_factory(tmp_path)
        (tmp_path / "Show.S01E01.mkv").write_bytes(b"v")
        (tmp_path / "Show.S01E02.mkv").write_bytes(b"v")

        with sf() as session:
            task = _make_task(session, str(tmp_path))
            result = prepare_show_episode_mapping(
                session=session, task_id=task.id,
            )
            session.commit()
            assert result.status == STATUS_AUTO_PUBLISHABLE

        with sf() as session:
            from media_pilot.repository.repositories import EpisodeMappingRepository
            mappings = EpisodeMappingRepository(session).get_by_task(task.id)
            assert len(mappings) == 2
            assert sorted(m.episode for m in mappings) == [1, 2]
            for m in mappings:
                assert m.season == 1
                assert m.source == "filename"

    def test_clears_old_mappings_when_blocked(
        self, tmp_path: Path,
    ):
        """当 task_input 变为不支持结构时, 必须清空旧 mapping, 避免任务
        状态与 EpisodeMapping 表残留矛盾."""
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_CROSS_SEASON,
            STATUS_AUTO_PUBLISHABLE,
            prepare_show_episode_mapping,
        )

        sf = _make_session_factory(tmp_path)
        # 先构造一个能成功 publish 的输入
        (tmp_path / "Show.S01E01.mkv").write_bytes(b"v")
        (tmp_path / "Show.S01E02.mkv").write_bytes(b"v")

        with sf() as session:
            task = _make_task(session, str(tmp_path))
            first = prepare_show_episode_mapping(
                session=session, task_id=task.id,
            )
            session.commit()
            assert first.status == STATUS_AUTO_PUBLISHABLE

        # 然后让 task.source_path 变成跨季结构
        cross_dir = tmp_path / "cross"
        cross_dir.mkdir()
        (cross_dir / "Show.S01E05.mkv").write_bytes(b"v")
        (cross_dir / "Show.S02E01.mkv").write_bytes(b"v")
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            t = IngestTaskRepository(session).get(task.id)
            t.source_path = str(cross_dir)
            session.commit()

        with sf() as session:
            second = prepare_show_episode_mapping(
                session=session, task_id=task.id,
            )
            session.commit()
            assert second.block_reason == BLOCK_REASON_CROSS_SEASON

        # EpisodeMapping 表里没有残留
        with sf() as session:
            from media_pilot.repository.repositories import EpisodeMappingRepository
            assert EpisodeMappingRepository(session).get_by_task(task.id) == []

    def test_missing_task_returns_no_video_files(
        self, tmp_path: Path,
    ):
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_NO_VIDEO_FILES,
            prepare_show_episode_mapping,
        )

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            result = prepare_show_episode_mapping(
                session=session, task_id="non-existent-task",
            )
            assert result.block_reason == BLOCK_REASON_NO_VIDEO_FILES

    def test_task_without_source_path_returns_no_video_files(
        self, tmp_path: Path,
    ):
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_NO_VIDEO_FILES,
            prepare_show_episode_mapping,
        )

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, "")  # empty source_path
            result = prepare_show_episode_mapping(
                session=session, task_id=task.id,
            )
            assert result.block_reason == BLOCK_REASON_NO_VIDEO_FILES


# ── get_persisted_show_structure 读取任务事实 ──────────────────


class TestGetPersistedShowStructure:
    def test_returns_none_when_no_mappings(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            get_persisted_show_structure,
        )

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, str(tmp_path))
            result = get_persisted_show_structure(
                session=session, task_id=task.id,
            )
            assert result is None

    def test_returns_mappings_in_normalized_form(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            STATUS_AUTO_PUBLISHABLE,
            get_persisted_show_structure,
            prepare_show_episode_mapping,
        )

        sf = _make_session_factory(tmp_path)
        for i in range(1, 4):
            (tmp_path / f"Show.S01E{i:02d}.mkv").write_bytes(b"v")

        with sf() as session:
            task = _make_task(session, str(tmp_path))
            prepare_show_episode_mapping(
                session=session, task_id=task.id,
            )
            session.commit()

        with sf() as session:
            result = get_persisted_show_structure(
                session=session, task_id=task.id,
            )
            assert result is not None
            assert result.status == STATUS_AUTO_PUBLISHABLE
            assert result.season == 1
            assert result.episode_range == "S01E01-E03"
            assert len(result.entries) == 3


# ── 绝对集数映射 (fix-show-absolute-episode-ingest-and-agent-search-loop) ──


class TestAbsoluteEpisodeMappingBracketSuffix:
    """Re:Zero 风格的 [51]-[66] 数字括号 + 目录 `3rd Season` 应当解析为
    provider season 1 上的 S01E51-E66, 当 season_coverage 给出 season 1
    episode_count >= 66 时 (这是 TMDB Re:Zero season 1 的真实情况).
    """

    def test_rezero_bracket_51_to_66_maps_to_season_1(
        self, tmp_path: Path,
    ):
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_NOT_SHOW_STRUCTURE,
            MAPPING_MODE_ABSOLUTE,
            STATUS_AUTO_PUBLISHABLE,
            prepare_show_episode_mapping,
        )

        # 模拟 Re:Zero "3rd Season" 目录 + `[51]`-`[66]` 文件命名.
        rezero_dir = tmp_path / "ReZero 3rd Season"
        rezero_dir.mkdir()
        for n in range(51, 67):
            (rezero_dir / f"[{n}].mkv").write_bytes(b"v")

        # provider season coverage: season 1 episode_count = 66 (覆盖 [51]-[66]).
        season_coverage = {1: 66, 2: 25, 3: 13}

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, str(rezero_dir))
            result = prepare_show_episode_mapping(
                session=session, task_id=task.id,
                season_coverage=season_coverage,
            )
            session.commit()
            assert result.status == STATUS_AUTO_PUBLISHABLE, (
                f"expected auto_publishable, got status={result.status} "
                f"block_reason={result.block_reason}"
            )
            assert result.block_reason != BLOCK_REASON_NOT_SHOW_STRUCTURE
            assert result.season == 1
            assert result.episode_range == "S01E51-E66"
            assert len(result.entries) == 16
            # 全部 EpisodeMapping 的 source 是 absolute, 不是 filename
            from media_pilot.repository.repositories import EpisodeMappingRepository
            mappings = EpisodeMappingRepository(session).get_by_task(task.id)
            assert all(m.source == "absolute" for m in mappings)
            assert all(m.season == 1 for m in mappings)
            assert result.mapping_mode == MAPPING_MODE_ABSOLUTE

    def test_absolute_out_of_provider_range_blocks(
        self, tmp_path: Path,
    ):
        """[51]-[70] 但 provider season 1 episode_count=60: 51-60
        在范围内, 61-70 超出 → 整段必须不自动发布."""
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_ABSOLUTE_EPISODE_OUT_OF_RANGE,
            prepare_show_episode_mapping,
        )

        abs_dir = tmp_path / "ReZero"
        abs_dir.mkdir()
        for n in range(51, 71):
            (abs_dir / f"[{n}].mkv").write_bytes(b"v")

        season_coverage = {1: 60}  # 51-60 在, 61-70 超出

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, str(abs_dir))
            result = prepare_show_episode_mapping(
                session=session, task_id=task.id,
                season_coverage=season_coverage,
            )
            session.commit()
            assert result.status != "auto_publishable"
            assert result.block_reason == BLOCK_REASON_ABSOLUTE_EPISODE_OUT_OF_RANGE

    def test_absolute_sparse_blocks(self, tmp_path: Path):
        """[51], [53], [55] (缺 52/54): 稀疏绝对集数不自动发布."""
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_ABSOLUTE_EPISODE_SPARSE,
            prepare_show_episode_mapping,
        )

        abs_dir = tmp_path / "Show"
        abs_dir.mkdir()
        for n in [51, 53, 55]:
            (abs_dir / f"[{n}].mkv").write_bytes(b"v")

        season_coverage = {1: 60}

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, str(abs_dir))
            result = prepare_show_episode_mapping(
                session=session, task_id=task.id,
                season_coverage=season_coverage,
            )
            session.commit()
            assert result.block_reason == BLOCK_REASON_ABSOLUTE_EPISODE_SPARSE

    def test_absolute_ambiguous_ep_prefix_blocks(self, tmp_path: Path):
        """`EP51` / `E51` 这种字符前缀歧义不能自动映射."""
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_ABSOLUTE_EPISODE_AMBIGUOUS,
            prepare_show_episode_mapping,
        )

        abs_dir = tmp_path / "Show"
        abs_dir.mkdir()
        for n in range(51, 56):
            (abs_dir / f"EP{n}.mkv").write_bytes(b"v")

        season_coverage = {1: 60}

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, str(abs_dir))
            result = prepare_show_episode_mapping(
                session=session, task_id=task.id,
                season_coverage=season_coverage,
            )
            session.commit()
            # EP51 等字符前缀歧义 → ambiguous. 与 no_clear_show_structure
            # 不同: ambiguous 表示 "像是剧集但解析不出来".
            assert result.block_reason in (
                BLOCK_REASON_ABSOLUTE_EPISODE_AMBIGUOUS,
                "no_clear_show_structure",
            )

    def test_absolute_bracket_range_with_dash_blocks(self, tmp_path: Path):
        """`[51-55]` 这种范围写法歧义不自动映射."""
        from media_pilot.services.show_structure_analysis import (
            BLOCK_REASON_ABSOLUTE_EPISODE_AMBIGUOUS,
            BLOCK_REASON_NOT_SHOW_STRUCTURE,
            prepare_show_episode_mapping,
        )

        abs_dir = tmp_path / "Show"
        abs_dir.mkdir()
        (abs_dir / "[51-55].mkv").write_bytes(b"v")

        season_coverage = {1: 60}

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, str(abs_dir))
            result = prepare_show_episode_mapping(
                session=session, task_id=task.id,
                season_coverage=season_coverage,
            )
            session.commit()
            # 实现选择把 ambiguous 上抛为 not_show_structure; 测试接受
            # 两种 block_reason (核心是绝不自动发布).
            assert result.block_reason in (
                BLOCK_REASON_ABSOLUTE_EPISODE_AMBIGUOUS,
                BLOCK_REASON_NOT_SHOW_STRUCTURE,
            )
            assert result.status != "auto_publishable"

    def test_standard_sxxexx_wins_over_absolute(self, tmp_path: Path):
        """当目录里同时有 `Show.S01E01.mkv` 和 `[02].mkv`, 标准
        SxxExx 永远优先 → 不走绝对集数分支."""
        from media_pilot.services.show_structure_analysis import (
            MAPPING_MODE_SXXEXX,
            STATUS_AUTO_PUBLISHABLE,
            prepare_show_episode_mapping,
        )

        mixed_dir = tmp_path / "Mixed"
        mixed_dir.mkdir()
        (mixed_dir / "Show.S01E01.mkv").write_bytes(b"v")
        (mixed_dir / "Show.S01E02.mkv").write_bytes(b"v")
        (mixed_dir / "[99].mkv").write_bytes(b"v")  # 干扰项

        season_coverage = {1: 60}

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, str(mixed_dir))
            result = prepare_show_episode_mapping(
                session=session, task_id=task.id,
                season_coverage=season_coverage,
            )
            session.commit()
            assert result.status == STATUS_AUTO_PUBLISHABLE
            assert result.season == 1
            assert result.episode_range == "S01E01-E02"
            assert result.mapping_mode == MAPPING_MODE_SXXEXX
            assert len(result.entries) == 2

    def test_chinese_diji_absolute_mapping(self, tmp_path: Path):
        """`第51话` / `第51集` 中文绝对集数应当解析."""
        from media_pilot.services.show_structure_analysis import (
            STATUS_AUTO_PUBLISHABLE,
            prepare_show_episode_mapping,
        )

        cn_dir = tmp_path / "Anime"
        cn_dir.mkdir()
        for n in range(51, 54):
            (cn_dir / f"第{n}话.mkv").write_bytes(b"v")

        season_coverage = {1: 60}

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, str(cn_dir))
            result = prepare_show_episode_mapping(
                session=session, task_id=task.id,
                season_coverage=season_coverage,
            )
            session.commit()
            assert result.status == STATUS_AUTO_PUBLISHABLE
            assert result.season == 1
            assert len(result.entries) == 3


class TestDeriveSeasonCoverageFromDetail:
    """season_coverage 从 MetadataDetail.payload["raw"]["seasons"] 派生."""

    def test_derives_from_raw_seasons(self):
        from media_pilot.services.show_structure_analysis import (
            derive_season_coverage_from_detail,
        )

        payload = {
            "raw": {
                "seasons": [
                    {"season_number": 1, "episode_count": 25},
                    {"season_number": 2, "episode_count": 13},
                ],
            },
        }
        coverage = derive_season_coverage_from_detail(payload)
        assert coverage == {1: 25, 2: 13}

    def test_derives_from_top_level_seasons(self):
        from media_pilot.services.show_structure_analysis import (
            derive_season_coverage_from_detail,
        )

        payload = {
            "seasons": [
                {"season_number": 1, "episode_count": 25},
            ],
        }
        coverage = derive_season_coverage_from_detail(payload)
        assert coverage == {1: 25}

    def test_derives_from_nested_provider_payload_raw_seasons(self):
        """fetch_and_save_metadata_detail stores provider payload under payload.raw."""
        from media_pilot.services.show_structure_analysis import (
            derive_season_coverage_from_detail,
        )

        payload = {
            "provider": "tmdb",
            "provider_id": "show:65942",
            "payload": {
                "raw": {
                    "seasons": [
                        {"season_number": 1, "episode_count": 25},
                        {"season_number": 2, "episode_count": 25},
                        {"season_number": 3, "episode_count": 16},
                    ],
                },
            },
        }

        coverage = derive_season_coverage_from_detail(payload)

        assert coverage == {1: 25, 2: 25, 3: 16}

    def test_returns_empty_for_missing(self):
        from media_pilot.services.show_structure_analysis import (
            derive_season_coverage_from_detail,
        )

        assert derive_season_coverage_from_detail({}) == {}
        assert derive_season_coverage_from_detail({"raw": {}}) == {}


class TestPersistedShowStructureMappingMode:
    """`get_persisted_show_structure` 必须暴露 mapping_mode, 供 task_mapper / 前端
    推断是否走 "absolute episode numbering" 可读文案."""

    def test_persisted_struct_reflects_absolute_mode(self, tmp_path: Path):
        from media_pilot.services.show_structure_analysis import (
            MAPPING_MODE_ABSOLUTE,
            STATUS_AUTO_PUBLISHABLE,
            get_persisted_show_structure,
            prepare_show_episode_mapping,
        )

        abs_dir = tmp_path / "Show"
        abs_dir.mkdir()
        for n in range(51, 53):
            (abs_dir / f"[{n}].mkv").write_bytes(b"v")

        season_coverage = {1: 60}

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, str(abs_dir))
            prepare_show_episode_mapping(
                session=session, task_id=task.id,
                season_coverage=season_coverage,
            )
            session.commit()

        with sf() as session:
            persisted = get_persisted_show_structure(
                session=session, task_id=task.id,
            )
            assert persisted is not None
            assert persisted.status == STATUS_AUTO_PUBLISHABLE
            assert persisted.mapping_mode == MAPPING_MODE_ABSOLUTE
