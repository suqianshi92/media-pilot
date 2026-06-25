import stat as stat_mod
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from media_pilot.repository.models import IngestTask
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
from media_pilot.orchestration.watch_stability import (
    WatchStableDetector,
    compute_directory_snapshot,
    compute_file_snapshot,
)

MEDIA_EXTENSIONS = frozenset(
    {
        ".avi",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".ts",
        ".webm",
        ".wmv",
    }
)


@dataclass(frozen=True)
class DownloadCandidate:
    path: Path
    size_bytes: int
    modified_at: float


@dataclass(frozen=True)
class IgnoredDownload:
    path: Path
    reason: str


@dataclass(frozen=True)
class DownloadScanResult:
    candidates: list[DownloadCandidate]
    ignored: list[IgnoredDownload]


def scan_downloads(
    downloads_dir: Path,
    *,
    now: float | None = None,
    stable_window_seconds: int | None = None,
    occupied_paths: frozenset[Path] | None = None,
    stable_detector: WatchStableDetector | None = None,
) -> DownloadScanResult:
    # `stable_window_seconds <= 0` 视为稳定窗口关闭: 跳过 detector 路径与
    # mtime-now 检查, 首次扫描即可让顶层媒体文件 / 顶层非空目录成为候选.
    # 生产默认 120; 仅在显式 `MEDIA_PILOT_WATCH_STABLE_SECONDS=0` 或负数时
    # 走此分支. `Worker.scan_once` 也会在窗口 <= 0 时主动不传 detector,
    # 此处做二次防御保证单测与外部调用方行为一致.
    if stable_window_seconds is not None and stable_window_seconds <= 0:
        stable_window_seconds = None
        stable_detector = None

    candidates: list[DownloadCandidate] = []
    ignored: list[IgnoredDownload] = []

    occupied = occupied_paths or frozenset()
    seen_paths: set[Path] = set()

    for path in sorted(downloads_dir.iterdir()):
        # ── 路径占用检查（优先级最高） ──
        if occupied and _is_path_occupied(path, occupied):
            ignored.append(
                IgnoredDownload(path=path, reason="download_task_reserved")
            )
            continue

        try:
            stat = path.stat()
        except OSError:
            if stable_detector is not None:
                stable_detector.forget(path)
            ignored.append(IgnoredDownload(path=path, reason="scan_error"))
            continue

        is_directory = stat_mod.S_ISDIR(stat.st_mode)
        is_regular = stat_mod.S_ISREG(stat.st_mode)

        if is_directory:
            if stable_detector is not None:
                try:
                    snapshot = compute_directory_snapshot(path)
                except OSError:
                    stable_detector.forget(path)
                    ignored.append(IgnoredDownload(path=path, reason="scan_error"))
                    continue
                total, latest_mtime_ns, file_count = snapshot
                # 空目录不进入稳定缓存: 快照恒为 (0, 0, 0), 立即满足窗口会误判.
                if file_count == 0:
                    stable_detector.forget(path)
                    ignored.append(
                        IgnoredDownload(path=path, reason="empty_directory")
                    )
                    continue
                observation_now = time.time() if now is None else now
                is_stable = stable_detector.observe(
                    path,
                    snapshot,
                    stable_window_seconds=stable_window_seconds or 0,
                    now=observation_now,
                )
                seen_paths.add(path)
                if not is_stable:
                    ignored.append(IgnoredDownload(path=path, reason="unstable"))
                    continue
                candidates.append(
                    DownloadCandidate(
                        path=path,
                        size_bytes=total,
                        modified_at=latest_mtime_ns / 1e9,
                    )
                )
                continue

            # 无 detector 时的旧 mtime-now 路径
            if stable_window_seconds is not None:
                current_time = stat.st_mtime if now is None else now
                if current_time - stat.st_mtime < stable_window_seconds:
                    ignored.append(IgnoredDownload(path=path, reason="unstable"))
                    continue

            try:
                size_bytes = _directory_size_bytes(path)
            except OSError:
                ignored.append(IgnoredDownload(path=path, reason="scan_error"))
                continue
            if size_bytes == 0:
                ignored.append(IgnoredDownload(path=path, reason="empty_directory"))
                continue

            candidates.append(
                DownloadCandidate(
                    path=path,
                    size_bytes=size_bytes,
                    modified_at=stat.st_mtime,
                )
            )
            continue

        if not is_regular:
            continue

        if path.suffix.lower() not in MEDIA_EXTENSIONS:
            if stable_detector is not None:
                stable_detector.forget(path)
            ignored.append(
                IgnoredDownload(path=path, reason="unsupported_extension")
            )
            continue

        if stable_detector is not None:
            snapshot = compute_file_snapshot(path)
            observation_now = time.time() if now is None else now
            is_stable = stable_detector.observe(
                path,
                snapshot,
                stable_window_seconds=stable_window_seconds or 0,
                now=observation_now,
            )
            seen_paths.add(path)
            if not is_stable:
                ignored.append(IgnoredDownload(path=path, reason="unstable"))
                continue
            candidates.append(
                DownloadCandidate(
                    path=path,
                    size_bytes=stat.st_size,
                    modified_at=stat.st_mtime,
                )
            )
            continue

        if stable_window_seconds is not None:
            current_time = stat.st_mtime if now is None else now
            if current_time - stat.st_mtime < stable_window_seconds:
                ignored.append(IgnoredDownload(path=path, reason="unstable"))
                continue

        candidates.append(
            DownloadCandidate(
                path=path,
                size_bytes=stat.st_size,
                modified_at=stat.st_mtime,
            )
        )

    if stable_detector is not None:
        stable_detector.cleanup_except(seen_paths)

    return DownloadScanResult(candidates=candidates, ignored=ignored)


def _is_path_occupied(candidate: Path, occupied: frozenset[Path]) -> bool:
    """路径是否被占用（精确匹配或父目录匹配）。"""
    if candidate in occupied:
        return True
    # 逐级检查父目录：/dl/Series.S01/S01E01.mkv → 检查 /dl/Series.S01
    for parent in candidate.parents:
        if parent in occupied:
            return True
    return False


def _directory_size_bytes(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def create_ingest_task(
    repository: IngestTaskRepository,
    candidate: DownloadCandidate,
    *,
    discovered_at: datetime,
) -> IngestTask:
    return repository.create(
        IngestTaskCreate(
            source_path=str(candidate.path),
            source_size_bytes=candidate.size_bytes,
            source_modified_at=datetime.fromtimestamp(candidate.modified_at, UTC),
            discovered_at=discovered_at,
            status="discovered",
            current_step="download_scan",
        )
    )
