"""Watch 输入快照式稳定窗口检测。

`WatchStableDetector` 维护进程内 `path → (snapshot, first_seen_at)` 映射：
- 首次观察某路径时记录快照和首次见到该快照的时间，返回 False（未稳定）。
- 同一路径的快照与上次一致且 `now - first_seen_at >= stable_window_seconds` 时返回 True（稳定）。
- 快照变化时重置 `first_seen_at = now` 并返回 False。

调用方应按 `scan_once` 周期传入 `now`（如 `time.time()`），让 detector 把"何时算稳定"
这一状态从无状态的 mtime 比较升级为有状态的快照比较。Detector 仅在内存中保存，
进程重启即清空，不进入数据库、文件或外部服务。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Hashable

Snapshot = tuple[Hashable, ...]


@dataclass
class _StableRecord:
    snapshot: Snapshot
    first_seen_at: float


class WatchStableDetector:
    """进程内 watch 路径快照稳定窗口状态机。"""

    def __init__(self) -> None:
        self._records: dict[Path, _StableRecord] = {}

    def observe(
        self,
        path: Path,
        snapshot: Snapshot,
        *,
        stable_window_seconds: int,
        now: float,
    ) -> bool:
        """记录 `path` 的当前快照并返回是否已稳定。

        - 首次观察：记录 `first_seen_at = now`，返回 False。
        - 快照与上次一致：`now - first_seen_at >= stable_window_seconds` 时返回 True。
        - 快照变化：重置 `first_seen_at = now`，返回 False。
        """
        record = self._records.get(path)
        if record is None or record.snapshot != snapshot:
            self._records[path] = _StableRecord(snapshot=snapshot, first_seen_at=now)
            return False
        return (now - record.first_seen_at) >= stable_window_seconds

    def forget(self, path: Path) -> None:
        """从缓存中删除 `path`（用于非媒体文件 / 空目录 / 异常路径）。"""
        self._records.pop(path, None)

    def cleanup_except(self, existing: set[Path]) -> None:
        """扫描结束时清理 watch 目录中已消失的路径，释放缓存。"""
        vanished = [p for p in self._records if p not in existing]
        for path in vanished:
            del self._records[path]


def compute_file_snapshot(path: Path) -> Snapshot:
    """返回单文件快照 `(st_size, st_mtime_ns)`。

    使用 `st_mtime_ns` 纳秒精度，避免秒级抖动把同一次 stat 误判为快照变化。
    """
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def compute_directory_snapshot(path: Path) -> Snapshot:
    """递归返回目录快照 `(total_size_bytes, latest_mtime_ns, file_count)`。

    仅统计 `is_file()` 的子项；非文件（子目录、socket、symlink 等）跳过。
    覆盖视频、字幕、NFO、图片等所有普通文件。
    """
    total = 0
    latest_mtime_ns = 0
    count = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        stat = child.stat()
        total += stat.st_size
        if stat.st_mtime_ns > latest_mtime_ns:
            latest_mtime_ns = stat.st_mtime_ns
        count += 1
    return (total, latest_mtime_ns, count)
