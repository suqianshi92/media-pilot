"""Tests for task input analysis (analyze_task_input) and dominant size heuristic.

Section X: 服务层测试覆盖 size 启发式自动消歧多视频目录.
判定纯基于 ``size_bytes`` (stat 已落), 不引入 ffprobe/mediainfo.
"""

from __future__ import annotations

import os
from pathlib import Path


def _write_video(path: Path, size_bytes: int) -> None:
    """Create a size_bytes-byte video stub via sparse file (no actual data).

    使用 ``touch`` + ``os.truncate(path, size)`` 而非
    ``write_bytes(b"\\x00" * size)`` — 后者在 GB 级会实际分配相同大小
    的内存 + 写盘, 测试套件浪费严重. 稀疏文件 ``stat().st_size`` 返回
    声明大小, 与下游 ``analyze_task_input`` 的 ``Path.stat().st_size``
    一致, 行为无差异.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        os.truncate(fd, size_bytes)
    finally:
        os.close(fd)


def _write_subtitle(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _analyze(source_path: Path):
    from media_pilot.services.task_input_analysis import analyze_task_input

    return analyze_task_input(source_path)


# ── dominant size heuristic 命中场景 ────────────────────────────────


class TestDominantSizeHeuristic:
    def test_3p7gib_plus_1p9mib_picks_3p7gib(self, tmp_path: Path) -> None:
        """USBA-089 现场: 3.7 GB 主片 + 1.9 MB 广告 → 自动挑 3.7 GB."""
        _write_video(tmp_path / "main.mp4", 3 * 1024 * 1024 * 1024 + 700 * 1024 * 1024)
        _write_video(tmp_path / "ad.mp4", int(1.9 * 1024 * 1024))

        analysis = _analyze(tmp_path)

        assert analysis.video_count == 1
        files_names = {f.name for f in analysis.files if f.type == "video"}
        assert files_names == {"main.mp4"}
        excluded_names = {e.name for e in analysis.excluded if e.type == "video"}
        assert excluded_names == {"ad.mp4"}
        ad_excluded = next(e for e in analysis.excluded if e.name == "ad.mp4")
        assert ad_excluded.excluded_reason is not None
        assert "low_value_size_ratio" in ad_excluded.excluded_reason

    def test_700mib_plus_650mib_still_multi(self, tmp_path: Path) -> None:
        """大小接近 → 不触发启发式, 双视频都进 files."""
        _write_video(tmp_path / "a.mp4", 700 * 1024 * 1024)
        _write_video(tmp_path / "b.mp4", 650 * 1024 * 1024)

        analysis = _analyze(tmp_path)

        assert analysis.video_count == 2
        files_names = {f.name for f in analysis.files if f.type == "video"}
        assert files_names == {"a.mp4", "b.mp4"}
        assert not any(e.name == "a.mp4" for e in analysis.excluded)
        assert not any(e.name == "b.mp4" for e in analysis.excluded)

    def test_3p7gib_plus_3p7gib_tie_no_pick(self, tmp_path: Path) -> None:
        """大小并列 → 不自动消歧 (拒绝平局)."""
        _write_video(tmp_path / "a.mp4", 3 * 1024 * 1024 * 1024 + 700 * 1024 * 1024)
        _write_video(tmp_path / "b.mp4", 3 * 1024 * 1024 * 1024 + 700 * 1024 * 1024)

        analysis = _analyze(tmp_path)

        assert analysis.video_count == 2
        files_names = {f.name for f in analysis.files if f.type == "video"}
        assert files_names == {"a.mp4", "b.mp4"}

    def test_dominant_below_200mib_min_no_pick(self, tmp_path: Path) -> None:
        """主片小于 200 MiB 起点 → 不自动消歧."""
        _write_video(tmp_path / "a.mp4", 150 * 1024 * 1024)
        _write_video(tmp_path / "b.mp4", 1 * 1024 * 1024)

        analysis = _analyze(tmp_path)

        assert analysis.video_count == 2
        files_names = {f.name for f in analysis.files if f.type == "video"}
        assert files_names == {"a.mp4", "b.mp4"}

    def test_companion_above_50mib_above_2pct_no_pick(self, tmp_path: Path) -> None:
        """伴随视频 60 MiB (3.7 GiB 主片) → 50 MiB 绝对上限 / 2% 比例都过线 → 不消歧."""
        _write_video(tmp_path / "a.mp4", 3 * 1024 * 1024 * 1024 + 700 * 1024 * 1024)
        _write_video(tmp_path / "b.mp4", 60 * 1024 * 1024)

        analysis = _analyze(tmp_path)

        assert analysis.video_count == 2
        files_names = {f.name for f in analysis.files if f.type == "video"}
        assert files_names == {"a.mp4", "b.mp4"}

    def test_companion_under_2pct_ratio_with_50mib_cap_picks(self, tmp_path: Path) -> None:
        """主片 200 MiB, 伴随 3 MiB (1.5% of 200 MiB = 3 MiB, < 50 MiB) → 启发式命中."""
        _write_video(tmp_path / "a.mp4", 200 * 1024 * 1024)
        _write_video(tmp_path / "b.mp4", 3 * 1024 * 1024)

        analysis = _analyze(tmp_path)

        assert analysis.video_count == 1
        b_excluded = next(e for e in analysis.excluded if e.name == "b.mp4")
        assert b_excluded.excluded_reason is not None
        assert "low_value_size_ratio" in b_excluded.excluded_reason

    def test_4gib_plus_700mib_plus_1mib_does_not_trigger_dominant(
        self, tmp_path: Path,
    ) -> None:
        """700 MiB 远超 min(50 MiB, 4 GiB * 0.02) = 50 MiB 上限 → 必须不触发.

        这是 strict guard 修复的回归测试: 任何 non_dominant > cap
        必须让 heuristic 整体失败, 不能吞掉 700 MB 候选.
        """
        # 字典序: ad 在 main 之前, dominant 选择按 size 不按字典序
        _write_video(tmp_path / "ad.mp4", 1 * 1024 * 1024)
        _write_video(tmp_path / "main.mp4", 4 * 1024 * 1024 * 1024)
        _write_video(tmp_path / "second.mp4", 700 * 1024 * 1024)

        analysis = _analyze(tmp_path)

        # 三个视频都留 files, 没有 excluded
        assert analysis.video_count == 3
        files_names = {f.name for f in analysis.files if f.type == "video"}
        assert files_names == {"ad.mp4", "main.mp4", "second.mp4"}
        video_excluded = [e for e in analysis.excluded if e.type == "video"]
        assert video_excluded == []

    def test_4gib_plus_700mib_plus_1mib_does_not_trigger_dominant_reversed(
        self, tmp_path: Path,
    ) -> None:
        """同场景但文件名字典序倒过来 → 仍不触发, 验证与字典序解耦."""
        _write_video(tmp_path / "zzz_ad.mp4", 1 * 1024 * 1024)
        _write_video(tmp_path / "zzz_second.mp4", 700 * 1024 * 1024)
        _write_video(tmp_path / "zzz_main.mp4", 4 * 1024 * 1024 * 1024)

        analysis = _analyze(tmp_path)

        assert analysis.video_count == 3
        files_names = {f.name for f in analysis.files if f.type == "video"}
        assert files_names == {"zzz_ad.mp4", "zzz_main.mp4", "zzz_second.mp4"}
        video_excluded = [e for e in analysis.excluded if e.type == "video"]
        assert video_excluded == []

    def test_4gib_plus_1mib_plus_2mib_triggers_dominant(
        self, tmp_path: Path,
    ) -> None:
        """1 MiB + 2 MiB 都 <= 50 MiB 上限 → heuristic 命中, 两个小视频都进 excluded.

        验证 strict guard 不是"过度保守": 真正的小伴随 (远低于
        50 MiB) 仍正确收敛.
        """
        _write_video(tmp_path / "main.mp4", 4 * 1024 * 1024 * 1024)
        _write_video(tmp_path / "ad1.mp4", 1 * 1024 * 1024)
        _write_video(tmp_path / "ad2.mp4", 2 * 1024 * 1024)

        analysis = _analyze(tmp_path)

        # main 留 files, ad1 / ad2 都进 excluded
        assert analysis.video_count == 1
        files_names = {f.name for f in analysis.files if f.type == "video"}
        assert files_names == {"main.mp4"}
        excluded_by_name = {e.name: e for e in analysis.excluded if e.type == "video"}
        assert set(excluded_by_name.keys()) == {"ad1.mp4", "ad2.mp4"}
        # 两条 excluded_reason 都是 low_value_size_ratio
        for c in excluded_by_name.values():
            assert c.excluded_reason is not None
            assert "low_value_size_ratio" in c.excluded_reason


# ── marker 排除与 size 启发式串联 ─────────────────────────────────────


class TestMarkerAndSizeHeuristicComposition:
    def test_marker_exclusion_then_size_heuristic(self, tmp_path: Path) -> None:
        """sample/trailer marker 排除先于 size 启发式; 两者 excluded_reason 互不冲突."""
        _write_video(tmp_path / "main.mp4", 3 * 1024 * 1024 * 1024 + 700 * 1024 * 1024)
        _write_video(tmp_path / "sample-clip.mp4", 200 * 1024)
        _write_video(tmp_path / "ad.mp4", int(1.9 * 1024 * 1024))

        analysis = _analyze(tmp_path)

        # 三个视频: main 留 files, sample-clip + ad 进 excluded
        files_names = {f.name for f in analysis.files if f.type == "video"}
        assert files_names == {"main.mp4"}
        excluded_by_name = {e.name: e for e in analysis.excluded if e.type == "video"}
        assert set(excluded_by_name.keys()) == {"sample-clip.mp4", "ad.mp4"}
        # 两条 reason 文案独立
        assert excluded_by_name["sample-clip.mp4"].excluded_reason == "sample/trailer/auxiliary"
        assert "low_value_size_ratio" in (excluded_by_name["ad.mp4"].excluded_reason or "")


# ── 字幕识别与 size 启发式互不干扰 ───────────────────────────────────


class TestSubtitleInteraction:
    def test_dominant_with_subtitle_preserved(self, tmp_path: Path) -> None:
        """size 启发式命中后, 字幕仍出现在 files (不被 size 启发式吞掉).

        ``matched_by="same_stem"`` 的同源判定在 directory 模式下由
        ``_find_same_stem_subtitles`` 不负责 (既有行为, 单文件模式才
        走那条), 目录模式所有字幕 ``matched_by=None`` 透传. 下游
        ``_resolve_after_primary_chosen`` 拿 dominant 路径再调一次
        ``_find_same_stem_subtitles`` 做同源判定, 与 size 启发式互不干扰.
        本测试只验证: 启发式不破坏 subtitle 计数 / 不被错误移出 files.
        """
        _write_video(tmp_path / "main.mp4", 3 * 1024 * 1024 * 1024 + 700 * 1024 * 1024)
        _write_video(tmp_path / "ad.mp4", int(1.9 * 1024 * 1024))
        _write_subtitle(tmp_path / "main.srt", "1\n00:00:00,000 --> 00:00:01,000\nhi\n")

        analysis = _analyze(tmp_path)

        assert analysis.subtitle_count >= 1
        sub_names = {f.name for f in analysis.files if f.type == "subtitle"}
        assert "main.srt" in sub_names


# ── 共享 same-stem helper ───────────────────────────────────────────


class TestIsSameStemSubtitle:
    """``is_same_stem_subtitle`` 公开 helper — 规则在单文件 / 目录模式
    共用, 命中条件: sub_stem == video_stem 或 sub_stem.startswith(
    video_stem + ".")."""

    def test_exact_stem_match(self) -> None:
        from media_pilot.services.task_input_analysis import is_same_stem_subtitle

        assert is_same_stem_subtitle(
            "Darkest Hour (2017)", "Darkest Hour (2017)",
        ) is True

    def test_locale_suffixed_stem_match(self) -> None:
        from media_pilot.services.task_input_analysis import is_same_stem_subtitle

        assert is_same_stem_subtitle(
            "Darkest Hour (2017).zh", "Darkest Hour (2017)",
        ) is True

    def test_different_video_is_not_same_stem(self) -> None:
        from media_pilot.services.task_input_analysis import is_same_stem_subtitle

        assert is_same_stem_subtitle(
            "Dunkirk (2017).zh", "Darkest Hour (2017)",
        ) is False

    def test_prefix_collision_without_dot_is_not_same_stem(self) -> None:
        from media_pilot.services.task_input_analysis import is_same_stem_subtitle

        assert is_same_stem_subtitle(
            "Darkest Hour (2017) Bonus", "Darkest Hour (2017)",
        ) is False


class TestDirectorySameStemSubtitle:
    """目录模式: 同源字幕必须标 ``matched_by="same_stem"`` (与单文件
    模式同源判定一致). 之前目录模式所有字幕 ``matched_by=None`` 透传,
    下游 ``_resolve_after_primary_chosen`` 拿 dominant 路径再调一次
    helper 才能识别 — 现场 (MP-Test-03 Darkest Hour) 漏判."""

    def test_directory_marks_same_stem_subtitle(self, tmp_path: Path) -> None:
        _write_video(
            tmp_path / "Darkest Hour (2017).mp4",
            300 * 1024 * 1024,
        )
        _write_subtitle(
            tmp_path / "Darkest Hour (2017).zh.srt",
            "1\n00:00:00,000 --> 00:00:01,000\nhi\n",
        )

        analysis = _analyze(tmp_path)

        sub_files = [f for f in analysis.files if f.type == "subtitle"]
        assert len(sub_files) == 1
        assert sub_files[0].name == "Darkest Hour (2017).zh.srt"
        assert sub_files[0].matched_by == "same_stem"

    def test_directory_marks_only_matching_subs(
        self, tmp_path: Path,
    ) -> None:
        """目录内含同名 + 非同名两种字幕, 只有同源的标 same_stem."""
        _write_video(
            tmp_path / "Darkest Hour (2017).mp4",
            300 * 1024 * 1024,
        )
        _write_subtitle(
            tmp_path / "Darkest Hour (2017).zh.srt",
            "1\n00:00:00,000 --> 00:00:01,000\nhi\n",
        )
        _write_subtitle(
            tmp_path / "extras.zh.srt",
            "1\n00:00:00,000 --> 00:00:01,000\nhi\n",
        )

        analysis = _analyze(tmp_path)

        subs_by_name = {
            f.name: f for f in analysis.files if f.type == "subtitle"
        }
        assert subs_by_name["Darkest Hour (2017).zh.srt"].matched_by == "same_stem"
        assert subs_by_name["extras.zh.srt"].matched_by != "same_stem"

    def test_directory_does_not_demote_existing_subtitle_to_default(
        self, tmp_path: Path,
    ) -> None:
        """目录内没有主视频同名字幕 → 字幕 ``matched_by`` 保持非 same_stem
        (为 None 或其他), 让下游能识别为 ambiguous."""
        _write_video(tmp_path / "Movie.2026.mkv", 300 * 1024 * 1024)
        _write_subtitle(
            tmp_path / "random_chs.srt",
            "1\n00:00:00,000 --> 00:00:01,000\nhi\n",
        )

        analysis = _analyze(tmp_path)

        sub_files = [f for f in analysis.files if f.type == "subtitle"]
        assert len(sub_files) == 1
        assert sub_files[0].matched_by != "same_stem"
