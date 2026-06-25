import os
from pathlib import Path

import pytest

from media_pilot.orchestration.watch_stability import (
    WatchStableDetector,
    compute_directory_snapshot,
    compute_file_snapshot,
)


# ─── WatchStableDetector ──────────────────────────────────────────────


def test_detector_first_observation_is_not_stable() -> None:
    """首次观察：返回 False，不当作稳定。"""
    detector = WatchStableDetector()
    snapshot = (10, 1234)

    assert (
        detector.observe(
            Path("/a.mkv"), snapshot, stable_window_seconds=60, now=100.0
        )
        is False
    )


def test_detector_same_snapshot_within_window_is_not_stable() -> None:
    """同一快照在窗口内：仍不稳定。"""
    detector = WatchStableDetector()
    snapshot = (10, 1234)

    detector.observe(Path("/a.mkv"), snapshot, stable_window_seconds=60, now=100.0)
    assert (
        detector.observe(
            Path("/a.mkv"), snapshot, stable_window_seconds=60, now=130.0
        )
        is False
    )


def test_detector_same_snapshot_after_window_is_stable() -> None:
    """同一快照保持稳定窗口秒后：稳定。"""
    detector = WatchStableDetector()
    snapshot = (10, 1234)

    detector.observe(Path("/a.mkv"), snapshot, stable_window_seconds=60, now=100.0)
    assert (
        detector.observe(
            Path("/a.mkv"), snapshot, stable_window_seconds=60, now=160.0
        )
        is True
    )


def test_detector_snapshot_change_resets_wait() -> None:
    """快照变化重置等待时间。"""
    detector = WatchStableDetector()

    detector.observe(
        Path("/a.mkv"), (10, 1234), stable_window_seconds=60, now=100.0
    )
    # t=140: 距首次 40s < 60s 窗口, 应当仍未稳定
    assert (
        detector.observe(
            Path("/a.mkv"), (10, 1234), stable_window_seconds=60, now=140.0
        )
        is False
    )
    # 快照变化（size 增长）→ 重置 first_seen_at
    detector.observe(
        Path("/a.mkv"), (20, 1234), stable_window_seconds=60, now=150.0
    )
    # t=180: 距新快照 30s, 仍未稳定
    assert (
        detector.observe(
            Path("/a.mkv"), (20, 1234), stable_window_seconds=60, now=180.0
        )
        is False
    )
    # t=215: 距新快照 65s, 稳定
    assert (
        detector.observe(
            Path("/a.mkv"), (20, 1234), stable_window_seconds=60, now=215.0
        )
        is True
    )


def test_detector_zero_window_stable_immediately_on_second_observe() -> None:
    """窗口为 0 时，第二次观察（同一快照）立即稳定。"""
    detector = WatchStableDetector()
    snapshot = (10, 1234)

    detector.observe(
        Path("/a.mkv"), snapshot, stable_window_seconds=0, now=100.0
    )
    # 任何后续时间都满足 0s 窗口
    assert (
        detector.observe(
            Path("/a.mkv"), snapshot, stable_window_seconds=0, now=100.001
        )
        is True
    )


def test_detector_forget_removes_path() -> None:
    """forget 之后路径当作新观察处理。"""
    detector = WatchStableDetector()
    snapshot = (10, 1234)

    detector.observe(
        Path("/a.mkv"), snapshot, stable_window_seconds=60, now=100.0
    )
    detector.forget(Path("/a.mkv"))
    # 重新观察，应当当作首次
    assert (
        detector.observe(
            Path("/a.mkv"), snapshot, stable_window_seconds=60, now=200.0
        )
        is False
    )


def test_detector_cleanup_except_removes_vanished_paths() -> None:
    """cleanup_except 仅保留仍在目录中的路径。"""
    detector = WatchStableDetector()
    snapshot = (10, 1234)

    detector.observe(Path("/a.mkv"), snapshot, stable_window_seconds=60, now=100.0)
    detector.observe(Path("/b.mkv"), snapshot, stable_window_seconds=60, now=100.0)

    detector.cleanup_except({Path("/a.mkv")})

    # /a.mkv 仍被跟踪，/b.mkv 已被清理
    # 对 /b.mkv 重新观察，按首次处理
    assert (
        detector.observe(
            Path("/b.mkv"), snapshot, stable_window_seconds=60, now=200.0
        )
        is False
    )
    # 对 /a.mkv 同一快照且 now=170 > first_seen+60, 应当稳定
    assert (
        detector.observe(
            Path("/a.mkv"), snapshot, stable_window_seconds=60, now=170.0
        )
        is True
    )


def test_detector_independent_paths_have_independent_clocks() -> None:
    """不同路径的稳定计时相互独立。"""
    detector = WatchStableDetector()
    snapshot = (10, 1234)

    detector.observe(Path("/a.mkv"), snapshot, stable_window_seconds=60, now=100.0)
    detector.observe(Path("/b.mkv"), snapshot, stable_window_seconds=60, now=150.0)

    # t=170 时 a 已经过窗口, b 还未
    assert (
        detector.observe(
            Path("/a.mkv"), snapshot, stable_window_seconds=60, now=170.0
        )
        is True
    )
    assert (
        detector.observe(
            Path("/b.mkv"), snapshot, stable_window_seconds=60, now=170.0
        )
        is False
    )


# ─── compute_file_snapshot ────────────────────────────────────────────


def test_compute_file_snapshot_returns_size_and_mtime_ns(tmp_path: Path) -> None:
    """单文件快照 = (size_bytes, mtime_ns)。"""
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"x" * 7)
    stat = movie.stat()

    snapshot = compute_file_snapshot(movie)

    assert snapshot == (stat.st_size, stat.st_mtime_ns)


def test_compute_file_snapshot_detects_size_growth(tmp_path: Path) -> None:
    """文件增长时快照变化。"""
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"abc")
    s1 = compute_file_snapshot(movie)
    movie.write_bytes(b"abcdef")
    s2 = compute_file_snapshot(movie)

    assert s1 != s2
    assert s2[0] == 6


# ─── compute_directory_snapshot ───────────────────────────────────────


def test_compute_directory_snapshot_sums_all_regular_files(tmp_path: Path) -> None:
    """目录快照覆盖所有普通文件（视频 + 字幕 + NFO + 图片）。"""
    release = tmp_path / "Movie.2026"
    release.mkdir()
    (release / "movie.mkv").write_bytes(b"v" * 100)
    (release / "subs.srt").write_text("subs", encoding="utf-8")
    (release / "info.nfo").write_text("info", encoding="utf-8")
    (release / "poster.jpg").write_bytes(b"\xff\xd8" * 50)

    total, latest_mtime_ns, count = compute_directory_snapshot(release)

    assert count == 4
    assert total == 100 + 4 + 4 + 100


def test_compute_directory_snapshot_tracks_max_mtime_ns(tmp_path: Path) -> None:
    """latest_mtime_ns 取目录内所有普通文件的最大 mtime_ns。"""
    release = tmp_path / "Movie.2026"
    release.mkdir()
    (release / "a.mkv").write_bytes(b"a")
    (release / "b.srt").write_text("b", encoding="utf-8")
    a_stat = (release / "a.mkv").stat()
    b_stat = (release / "b.srt").stat()

    _, latest_mtime_ns, _ = compute_directory_snapshot(release)

    assert latest_mtime_ns == max(a_stat.st_mtime_ns, b_stat.st_mtime_ns)


def test_compute_directory_snapshot_includes_nested_files(tmp_path: Path) -> None:
    """目录快照覆盖所有 rglob 普通文件，包括子目录内的文件。"""
    release = tmp_path / "Movie.2026"
    release.mkdir()
    season = release / "Season1"
    season.mkdir()
    (season / "ep1.mkv").write_bytes(b"x" * 5)
    (season / "ep2.mkv").write_bytes(b"y" * 7)
    (release / "info.nfo").write_text("info", encoding="utf-8")

    total, _, count = compute_directory_snapshot(release)

    assert count == 3
    assert total == 5 + 7 + 4


def test_compute_directory_snapshot_changes_when_file_grows(tmp_path: Path) -> None:
    """目录内文件追加写后快照变化。"""
    release = tmp_path / "Movie.2026"
    release.mkdir()
    (release / "movie.mkv").write_bytes(b"abc")
    s1 = compute_directory_snapshot(release)
    # 追加写
    with (release / "movie.mkv").open("ab") as f:
        f.write(b"def")
    s2 = compute_directory_snapshot(release)

    assert s1 != s2
    assert s2[0] == 6
    assert s2[2] == 1


def test_compute_directory_snapshot_changes_when_file_added(tmp_path: Path) -> None:
    """目录内新增文件后快照变化。"""
    release = tmp_path / "Movie.2026"
    release.mkdir()
    (release / "movie.mkv").write_bytes(b"abc")
    s1 = compute_directory_snapshot(release)
    (release / "subs.srt").write_text("subs", encoding="utf-8")
    s2 = compute_directory_snapshot(release)

    assert s1 != s2
    assert s2[2] == 2
    assert s2[0] > s1[0]


def test_compute_directory_snapshot_changes_when_file_removed(tmp_path: Path) -> None:
    """目录内删除文件后快照变化。"""
    release = tmp_path / "Movie.2026"
    release.mkdir()
    (release / "movie.mkv").write_bytes(b"abc")
    (release / "subs.srt").write_text("subs", encoding="utf-8")
    s1 = compute_directory_snapshot(release)
    (release / "subs.srt").unlink()
    s2 = compute_directory_snapshot(release)

    assert s1 != s2
    assert s2[2] == 1


def test_compute_directory_snapshot_ignores_subdirectories(tmp_path: Path) -> None:
    """目录快照中子目录本身不计入 count / size / mtime。"""
    release = tmp_path / "Movie.2026"
    release.mkdir()
    (release / "movie.mkv").write_bytes(b"abc")
    (release / "subs").mkdir()  # 空子目录

    total, _, count = compute_directory_snapshot(release)

    assert count == 1
    assert total == 3
