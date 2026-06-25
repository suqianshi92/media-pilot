import os
from pathlib import Path

import pytest

from media_pilot.orchestration.ingestion import scan_downloads
from media_pilot.orchestration.watch_stability import WatchStableDetector


def test_scan_downloads_returns_only_media_files(tmp_path: Path) -> None:
    movie = tmp_path / "Movie.2024.MKV"
    sample = tmp_path / "sample.mp4"
    note = tmp_path / "readme.txt"
    directory = tmp_path / "folder"
    movie.write_bytes(b"movie")
    sample.write_bytes(b"sample")
    note.write_text("ignore", encoding="utf-8")
    directory.mkdir()

    result = scan_downloads(tmp_path)

    assert [candidate.path for candidate in result.candidates] == [movie, sample]
    assert [candidate.size_bytes for candidate in result.candidates] == [5, 6]
    assert [ignored.path for ignored in result.ignored] == [directory, note]
    assert [ignored.reason for ignored in result.ignored] == [
        "empty_directory",
        "unsupported_extension",
    ]
    assert result.candidates[0].modified_at == movie.stat().st_mtime


def test_scan_downloads_does_not_modify_download_directory(tmp_path: Path) -> None:
    media_file = tmp_path / "Episode.S01E01.mkv"
    media_file.write_bytes(b"episode")
    before = media_file.stat().st_mtime_ns

    scan_downloads(tmp_path)

    assert media_file.exists()
    assert media_file.read_bytes() == b"episode"
    assert media_file.stat().st_mtime_ns == before


def test_scan_downloads_ignores_media_files_inside_stable_window(tmp_path: Path) -> None:
    stable_file = tmp_path / "Stable.2024.mkv"
    writing_file = tmp_path / "Writing.2024.mkv"
    stable_file.write_bytes(b"stable")
    writing_file.write_bytes(b"writing")
    now = 1_000.0
    os.utime(stable_file, (now - 30, now - 30))
    os.utime(writing_file, (now - 5, now - 5))

    result = scan_downloads(tmp_path, now=now, stable_window_seconds=20)

    assert [candidate.path for candidate in result.candidates] == [stable_file]
    assert [ignored.path for ignored in result.ignored] == [writing_file]
    assert result.ignored[0].reason == "unstable"


def test_scan_downloads_includes_stable_directory_inputs(tmp_path: Path) -> None:
    release_dir = tmp_path / "Movie.2026"
    release_dir.mkdir()
    (release_dir / "Movie.2026.mkv").write_bytes(b"movie")

    result = scan_downloads(tmp_path)

    assert [candidate.path for candidate in result.candidates] == [release_dir]
    assert result.candidates[0].size_bytes >= 5


def test_scan_downloads_ignores_unstable_directories(tmp_path: Path) -> None:
    release_dir = tmp_path / "Movie.2026"
    release_dir.mkdir()
    (release_dir / "Movie.2026.mkv").write_bytes(b"movie")
    now = 1_000.0
    os.utime(release_dir, (now - 5, now - 5))

    result = scan_downloads(tmp_path, now=now, stable_window_seconds=20)

    assert result.candidates == []
    assert [ignored.path for ignored in result.ignored] == [release_dir]
    assert result.ignored[0].reason == "unstable"



def test_scan_downloads_skips_occupied_file(tmp_path: Path) -> None:
    """被占用的文件被跳过，记录 reason=download_task_reserved"""
    movie = tmp_path / "Downloading.Movie.2024.mkv"
    sample = tmp_path / "External.sample.mp4"
    movie.write_bytes(b"movie")
    sample.write_bytes(b"sample")

    occupied = frozenset({movie})
    result = scan_downloads(tmp_path, occupied_paths=occupied)

    assert [c.path for c in result.candidates] == [sample]
    assert [i.path for i in result.ignored] == [movie]
    assert [i.reason for i in result.ignored] == ["download_task_reserved"]


def test_scan_downloads_skips_occupied_directory(tmp_path: Path) -> None:
    """被占用的目录被跳过，目录内子文件不单独检查"""
    release_dir = tmp_path / "Movie.2026"
    release_dir.mkdir()
    (release_dir / "Movie.2026.mkv").write_bytes(b"movie")
    external = tmp_path / "External.mkv"
    external.write_bytes(b"ext")

    occupied = frozenset({release_dir})
    result = scan_downloads(tmp_path, occupied_paths=occupied)

    assert [c.path for c in result.candidates] == [external]
    assert [i.path for i in result.ignored] == [release_dir]
    assert result.ignored[0].reason == "download_task_reserved"


def test_scan_downloads_allows_external_files(tmp_path: Path) -> None:
    """外部输入（PikPak/手动拷贝）仍被扫描"""
    external = tmp_path / "External.Movie.mkv"
    external.write_bytes(b"movie")
    occupied_dir = tmp_path / "Occupied.Series"
    occupied_dir.mkdir()
    (occupied_dir / "S01E01.mkv").write_bytes(b"ep")

    occupied = frozenset({occupied_dir})
    result = scan_downloads(tmp_path, occupied_paths=occupied)

    candidates_paths = [c.path for c in result.candidates]
    assert external in candidates_paths
    assert occupied_dir not in candidates_paths


def test_scan_downloads_still_respects_stable_window_with_occupied(
    tmp_path: Path,
) -> None:
    """路径占用 + 稳定窗口叠加：两者都生效"""
    import os
    occupied_file = tmp_path / "Occupied.Writing.2024.mkv"
    occupied_file.write_bytes(b"writing")
    stable_external = tmp_path / "Stable.External.2024.mkv"
    stable_external.write_bytes(b"stable")

    now = 1_000.0
    os.utime(occupied_file, (now - 5, now - 5))
    os.utime(stable_external, (now - 30, now - 30))

    occupied = frozenset({occupied_file})
    result = scan_downloads(
        tmp_path, occupied_paths=occupied,
        now=now, stable_window_seconds=20,
    )

    # occupied_file: 被占用 → 跳过（不论稳定窗口）
    # stable_external: 未被占用 + 稳定 → candidate
    # occupied_file 的 reason 是 download_task_reserved（优先级高于 unstable）
    assert [c.path for c in result.candidates] == [stable_external]
    assert [i.path for i in result.ignored] == [occupied_file]
    assert result.ignored[0].reason == "download_task_reserved"


def test_scan_downloads_empty_occupied_no_effect(tmp_path: Path) -> None:
    """空占用路径集不影响正常扫描"""
    movie = tmp_path / "Movie.mkv"
    movie.write_bytes(b"movie")

    result = scan_downloads(tmp_path, occupied_paths=frozenset())
    assert [c.path for c in result.candidates] == [movie]


# ═══════════════════════════════════════════════════════════════
# 回归测试 (Task 1.3)
# 目标：复现并覆盖 split-managed-downloads-from-watch-ingest 的核心场景
# ═══════════════════════════════════════════════════════════════

def test_shared_dir_causes_managed_download_to_be_scanned(tmp_path: Path) -> None:
    """当前错误行为：管理下载与外部导入共享 downloads_dir。

    在目录语义拆分前，downloads_dir 同时服务于：
    - qBittorrent 系统内下载
    - 外部导入扫描

    同一个目录中的文件会被扫描器无条件发现并创建入库任务，
    即使该文件是系统内下载在写入中的产物。

    拆分后，本测试应改为验证 downloads_dir 内容不被发现。
    """
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    (downloads_dir / "SystemDownload.2024.mkv").write_bytes(b"system download")

    # 当前行为：扫描器消费 downloads_dir，发现所有媒体文件
    result = scan_downloads(downloads_dir)

    # 错误：系统内下载的文件也被当作外部导入
    assert len(result.candidates) == 1
    assert result.candidates[0].path == downloads_dir / "SystemDownload.2024.mkv"
    assert result.ignored == []  # 没有 occupied_paths 保护时，全部进入候选


def test_external_scan_only_sees_watch_dir(tmp_path: Path) -> None:
    """预期行为（拆分后）：外部导入扫描器只消费 watch 目录。

    - downloads_dir 只服务于系统内下载
    - watch_dir 只服务于外部导入扫描
    - 两个目录职责分离，扫描器不交叉

    当前因 AppConfig 尚无 watch_dir，本测试暂时用纯函数验证结构设计。
    拆分后应增强为 Worker.scan_once 级别的端到端测试。
    """
    downloads_dir = tmp_path / "downloads"
    watch_dir = tmp_path / "watch"
    downloads_dir.mkdir()
    watch_dir.mkdir()

    # 系统内下载（不应被发现）
    (downloads_dir / "ManagedMovie.2024.mkv").write_bytes(b"managed")
    # 外部导入（应被发现）
    (watch_dir / "ExternalMovie.2024.mkv").write_bytes(b"external")

    # 扫描器只消费 watch_dir
    result = scan_downloads(watch_dir)

    # 只发现 watch 中的内容
    assert len(result.candidates) == 1
    assert result.candidates[0].path == watch_dir / "ExternalMovie.2024.mkv"
    # downloads_dir 中的内容不在候选列表中
    paths_in_candidates = {c.path for c in result.candidates}
    assert (downloads_dir / "ManagedMovie.2024.mkv") not in paths_in_candidates


# ═══════════════════════════════════════════════════════════════
# WatchStableDetector 集成 (stabilize-watch-input-before-ingest)
# ═══════════════════════════════════════════════════════════════


def test_detector_scan_first_appearance_creates_no_task(tmp_path: Path) -> None:
    """首次出现：不创建任务，记录 unstable。"""
    movie = tmp_path / "Movie.2024.mkv"
    movie.write_bytes(b"x" * 100)
    detector = WatchStableDetector()

    result = scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )

    assert result.candidates == []
    assert [i.path for i in result.ignored] == [movie]
    assert result.ignored[0].reason == "unstable"


def test_detector_scan_same_snapshot_within_window_creates_no_task(
    tmp_path: Path,
) -> None:
    """同一快照在窗口内：不创建任务。"""
    movie = tmp_path / "Movie.2024.mkv"
    movie.write_bytes(b"x" * 100)
    detector = WatchStableDetector()

    scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    result = scan_downloads(
        tmp_path, now=130.0, stable_window_seconds=60,
        stable_detector=detector,
    )

    assert result.candidates == []
    assert [i.path for i in result.ignored] == [movie]
    assert result.ignored[0].reason == "unstable"


def test_detector_scan_same_snapshot_after_window_creates_task(
    tmp_path: Path,
) -> None:
    """同一快照满窗口：创建任务，source_size_bytes 来自稳定后的 size。"""
    movie = tmp_path / "Movie.2024.mkv"
    movie.write_bytes(b"x" * 100)
    detector = WatchStableDetector()

    scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    result = scan_downloads(
        tmp_path, now=170.0, stable_window_seconds=60,
        stable_detector=detector,
    )

    assert [c.path for c in result.candidates] == [movie]
    assert result.candidates[0].size_bytes == 100
    assert result.ignored == []


def test_detector_scan_size_change_resets_wait(tmp_path: Path) -> None:
    """文件 size 在窗口内变化：重置等待时间。"""
    movie = tmp_path / "Movie.2024.mkv"
    movie.write_bytes(b"x" * 100)
    detector = WatchStableDetector()

    # 首次观察
    scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    # t=130: 距 first_seen 30s, 未满 60s, 仍未稳定
    result = scan_downloads(
        tmp_path, now=130.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert result.candidates == []

    # size 增长 → 快照变化
    movie.write_bytes(b"x" * 200)
    # t=140: 重新计时, 距新快照 0s, 仍未稳定
    result = scan_downloads(
        tmp_path, now=140.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert result.candidates == []
    # t=200: 距新快照 60s, 满足窗口
    result = scan_downloads(
        tmp_path, now=200.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert [c.path for c in result.candidates] == [movie]
    assert result.candidates[0].size_bytes == 200


def test_detector_scan_mtime_change_resets_wait(tmp_path: Path) -> None:
    """文件 mtime 在窗口内变化：重置等待时间。"""
    movie = tmp_path / "Movie.2024.mkv"
    movie.write_bytes(b"x" * 100)
    detector = WatchStableDetector()

    scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    # 推进时间但不改 size/mtime → 仍记为不稳定
    result = scan_downloads(
        tmp_path, now=130.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert result.candidates == []

    # 仅 touch 文件（更新 mtime, size 不变）
    os.utime(movie, (130.0, 130.0))
    result = scan_downloads(
        tmp_path, now=140.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert result.candidates == []
    # t=200: 距 mtime 变化 70s, 满足 60s 窗口
    result = scan_downloads(
        tmp_path, now=200.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert [c.path for c in result.candidates] == [movie]


def test_detector_scan_directory_recursive_stable_after_window(
    tmp_path: Path,
) -> None:
    """目录快照满窗口：创建任务，source_size_bytes = total_size_bytes。"""
    release = tmp_path / "Movie.2026"
    release.mkdir()
    (release / "movie.mkv").write_bytes(b"x" * 100)
    (release / "subs.srt").write_text("hi", encoding="utf-8")
    (release / "info.nfo").write_text("info", encoding="utf-8")
    detector = WatchStableDetector()

    scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    result = scan_downloads(
        tmp_path, now=170.0, stable_window_seconds=60,
        stable_detector=detector,
    )

    assert [c.path for c in result.candidates] == [release]
    assert result.candidates[0].size_bytes == 100 + 2 + 4


def test_detector_scan_directory_subtitle_added_resets_wait(
    tmp_path: Path,
) -> None:
    """目录内新增字幕：快照变化，重置等待。"""
    release = tmp_path / "Movie.2026"
    release.mkdir()
    (release / "movie.mkv").write_bytes(b"x" * 100)
    detector = WatchStableDetector()

    scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    # t=130: 30s, 仍未稳定
    assert scan_downloads(
        tmp_path, now=130.0, stable_window_seconds=60,
        stable_detector=detector,
    ).candidates == []

    # 新增字幕
    (release / "subs.srt").write_text("hi", encoding="utf-8")
    # t=140: 快照变化, 重置; 0s, 仍未稳定
    assert scan_downloads(
        tmp_path, now=140.0, stable_window_seconds=60,
        stable_detector=detector,
    ).candidates == []
    # t=200: 距新快照 60s, 满足
    result = scan_downloads(
        tmp_path, now=200.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert [c.path for c in result.candidates] == [release]


def test_detector_scan_directory_file_grows_resets_wait(tmp_path: Path) -> None:
    """目录内主视频追加写：快照变化，重置等待。"""
    release = tmp_path / "Movie.2026"
    release.mkdir()
    movie = release / "movie.mkv"
    movie.write_bytes(b"x" * 100)
    detector = WatchStableDetector()

    scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    # 追加写
    with movie.open("ab") as f:
        f.write(b"y" * 50)
    # 新快照 0s, 仍未稳定
    assert scan_downloads(
        tmp_path, now=110.0, stable_window_seconds=60,
        stable_detector=detector,
    ).candidates == []
    # 60s 后稳定
    result = scan_downloads(
        tmp_path, now=170.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert [c.path for c in result.candidates] == [release]
    assert result.candidates[0].size_bytes == 150


def test_detector_scan_empty_directory_ignored_then_becomes_candidate(
    tmp_path: Path,
) -> None:
    """空目录被忽略；后续非空时进入稳定判断。"""
    release = tmp_path / "Movie.2026"
    release.mkdir()
    detector = WatchStableDetector()

    # 第一次扫描：空目录
    result = scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert result.candidates == []
    assert [i.path for i in result.ignored] == [release]
    assert result.ignored[0].reason == "empty_directory"

    # 添加文件
    (release / "movie.mkv").write_bytes(b"x" * 50)
    # 第二次扫描：开始计时
    result = scan_downloads(
        tmp_path, now=110.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert result.candidates == []
    assert [i.path for i in result.ignored] == [release]
    assert result.ignored[0].reason == "unstable"
    # t=170: 60s 满足
    result = scan_downloads(
        tmp_path, now=170.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert [c.path for c in result.candidates] == [release]


def test_detector_scan_top_level_subtitle_is_ignored(tmp_path: Path) -> None:
    """顶层字幕文件：unsupported_extension，不进稳定缓存。"""
    srt = tmp_path / "movie.srt"
    srt.write_text("hi", encoding="utf-8")
    detector = WatchStableDetector()

    result = scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert result.candidates == []
    assert [i.path for i in result.ignored] == [srt]
    assert result.ignored[0].reason == "unsupported_extension"

    # 后续扫描不应把字幕当作稳定候选
    result = scan_downloads(
        tmp_path, now=200.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert result.candidates == []
    assert [i.path for i in result.ignored] == [srt]
    assert result.ignored[0].reason == "unsupported_extension"


def test_detector_scan_top_level_non_media_file_ignored(tmp_path: Path) -> None:
    """顶层非媒体文件（如 .nfo）被忽略。"""
    nfo = tmp_path / "movie.nfo"
    nfo.write_text("info", encoding="utf-8")
    detector = WatchStableDetector()

    result = scan_downloads(
        tmp_path, now=200.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert result.candidates == []
    assert result.ignored[0].reason == "unsupported_extension"


def test_detector_scan_stat_error_recorded_as_scan_error(
    tmp_path: Path, monkeypatch,
) -> None:
    """stat 异常：本轮跳过，记录 scan_error，不进稳定缓存。"""
    movie = tmp_path / "Movie.2024.mkv"
    movie.write_bytes(b"x" * 100)
    detector = WatchStableDetector()

    real_stat = Path.stat

    def broken_stat(self, *args, **kwargs):
        if self == movie:
            raise OSError("simulated stat failure")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", broken_stat)

    result = scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )

    assert result.candidates == []
    assert [i.path for i in result.ignored] == [movie]
    assert result.ignored[0].reason == "scan_error"

    # 下一轮 stat 恢复后, 应按新扫描重新评估
    monkeypatch.setattr(Path, "stat", real_stat)
    result = scan_downloads(
        tmp_path, now=110.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    # 首次观察, 仍未稳定
    assert result.candidates == []
    assert [i.path for i in result.ignored] == [movie]
    assert result.ignored[0].reason == "unstable"


def test_detector_scan_directory_rglob_error_recorded_as_scan_error(
    tmp_path: Path, monkeypatch,
) -> None:
    """目录 rglob 异常：scan_error，不进稳定缓存。"""
    release = tmp_path / "Movie.2026"
    release.mkdir()
    (release / "movie.mkv").write_bytes(b"x" * 50)
    detector = WatchStableDetector()

    real_rglob = Path.rglob

    def broken_rglob(self, *args, **kwargs):
        if self == release:
            raise OSError("simulated rglob failure")
        return real_rglob(self, *args, **kwargs)

    monkeypatch.setattr(Path, "rglob", broken_rglob)

    result = scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )

    assert result.candidates == []
    assert [i.path for i in result.ignored] == [release]
    assert result.ignored[0].reason == "scan_error"


def test_detector_scan_preserves_occupied_path_priority(tmp_path: Path) -> None:
    """占用路径优先级：被占用时既不进 detector 也不进候选。"""
    movie = tmp_path / "Movie.2024.mkv"
    movie.write_bytes(b"x" * 100)
    detector = WatchStableDetector()

    occupied = frozenset({movie})
    result = scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        occupied_paths=occupied, stable_detector=detector,
    )
    assert result.candidates == []
    assert [i.path for i in result.ignored] == [movie]
    assert result.ignored[0].reason == "download_task_reserved"

    # 即使在窗口外也不能让被占用路径进入稳定缓存
    result = scan_downloads(
        tmp_path, now=200.0, stable_window_seconds=60,
        occupied_paths=occupied, stable_detector=detector,
    )
    assert result.candidates == []
    assert result.ignored[0].reason == "download_task_reserved"


def test_zero_window_disables_stable_detection_first_scan_creates_task(
    tmp_path: Path,
) -> None:
    """stable_window_seconds=0 视为稳定窗口关闭: 首次扫描即创建候选, 不进
    detector 路径."""
    movie = tmp_path / "Movie.2024.mkv"
    movie.write_bytes(b"x" * 100)
    detector = WatchStableDetector()

    result = scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=0,
        stable_detector=detector,
    )

    assert [c.path for c in result.candidates] == [movie]
    assert result.candidates[0].size_bytes == 100
    # detector 未参与观察: 0s 窗口下扫描器不调用 observe
    assert detector._records == {}


def test_negative_window_disables_stable_detection_first_scan_creates_task(
    tmp_path: Path,
) -> None:
    """负数窗口同样视为关闭 (validate_startup_config 拒绝, 纯函数层兜底)."""
    movie = tmp_path / "Movie.2024.mkv"
    movie.write_bytes(b"x" * 100)
    detector = WatchStableDetector()

    result = scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=-1,
        stable_detector=detector,
    )

    assert [c.path for c in result.candidates] == [movie]
    assert detector._records == {}


def test_positive_window_first_scan_unstable_then_window_creates_task(
    tmp_path: Path,
) -> None:
    """正数窗口: 首次 scan 记 unstable, 满足窗口后创建任务.

    合并覆盖 `0 秒 → 立即创建` 与 `正数 → 等待 → 创建` 两条分支.
    """
    movie = tmp_path / "Movie.2024.mkv"
    movie.write_bytes(b"x" * 100)
    detector = WatchStableDetector()

    # 首次扫描: 窗口未满, 不创建任务
    first = scan_downloads(
        tmp_path, now=100.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert first.candidates == []
    assert [i.path for i in first.ignored] == [movie]
    assert first.ignored[0].reason == "unstable"

    # 满窗口后扫描: 创建任务
    later = scan_downloads(
        tmp_path, now=170.0, stable_window_seconds=60,
        stable_detector=detector,
    )
    assert [c.path for c in later.candidates] == [movie]
    assert later.ignored == []
