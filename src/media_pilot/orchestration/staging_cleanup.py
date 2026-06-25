"""发布成功后清理空 staging 任务子目录.

边界:
- 只能删除 ``<media_root>/.media-pilot-staging/<task_id>/`` 这一个子目录.
- 不允许越过 ``.media-pilot-staging/`` 本身, 也不允许删除
  ``.media-pilot-staging/`` 之外的任何路径.
- 非空目录 → skipped (保留, 写 OperationRecord).
- 目录不存在 / 已经清理 → succeeded (no-op).
- IOError / OSError → failed, 写 OperationRecord.
- 任何路径解析时如发现越界 → 拒绝执行, 不写库, 抛 ValueError 给上层
  (调用方应捕获并 downgrade 到 warning log, 不允许吞掉).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from media_pilot.repository.audit import record_file_operation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupResult:
    status: Literal["succeeded", "skipped", "failed"]
    staging_task_dir: Path
    remaining_files: list[str] = field(default_factory=list)
    error_message: str | None = None


STAGING_ROOT_NAME = ".media-pilot-staging"


def _is_within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _staging_task_dir(media_root: Path, task_id: str) -> Path:
    """定位 ``<media_root>/.media-pilot-staging/<task_id>/`` 并校验越界.

    越界 (resolved 不在 ``<media_root>/.media-pilot-staging/`` 下) → 抛
    ValueError — 上层应捕获并降级到 warning log, 不允许吞掉.
    """
    if not task_id or "/" in task_id or "\\" in task_id or task_id in (".", ".."):
        raise ValueError(f"非法 task_id: {task_id!r}")
    candidate = (media_root / STAGING_ROOT_NAME / task_id).resolve()
    staging_root = (media_root / STAGING_ROOT_NAME).resolve()
    if not _is_within(candidate, staging_root):
        raise ValueError(
            f"解析后 staging 任务目录 {candidate} 超出 {staging_root}",
        )
    return candidate


def cleanup_empty_staging_task_dir(
    media_root: Path,
    task_id: str,
    session: Session,
) -> CleanupResult:
    """删除 ``<media_root>/.media-pilot-staging/<task_id>/`` 如果它为空.

    行为:
    - 路径越界 / 非法 task_id → 抛 ValueError (调用方必须降级到 warning,
      不得写入 OperationRecord).
    - 目录不存在 → succeeded, ``remaining_files=[]`` (no-op).
    - 目录存在且为空 → 向上 os.rmdir 逐层 (到 staging/<task_id>/ 自身停止);
      写入 OperationRecord succeeded. 注意: 只有 staging/<task_id>/ 这一层
      会被清掉, 不会往上吃掉 staging/ 本身.
    - 目录非空 → skipped, 记录 remaining_files, 写 OperationRecord skipped.
    - 其它 OSError → failed, 写 OperationRecord failed, 包含 error_message.

    必须始终写 OperationRecord (除了路径越界/非法 task_id 抛异常的路径).
    """
    try:
        task_dir = _staging_task_dir(media_root, task_id)
    except ValueError as exc:
        # 越界 — 让调用方降级到 warning log, 不得静默.
        raise

    if not task_dir.exists():
        result = CleanupResult(
            status="succeeded",
            staging_task_dir=task_dir,
            remaining_files=[],
        )
        return result

    if not task_dir.is_dir():
        result = CleanupResult(
            status="skipped",
            staging_task_dir=task_dir,
            remaining_files=[],
            error_message="staging_task_dir_exists_but_not_a_directory",
        )
        _record_cleanup(session, task_id, task_dir, result)
        return result

    try:
        entries = list(task_dir.iterdir())
    except OSError as exc:
        result = CleanupResult(
            status="failed",
            staging_task_dir=task_dir,
            remaining_files=[],
            error_message=f"listdir_failed:{exc}",
        )
        _record_cleanup(session, task_id, task_dir, result)
        return result

    if entries:
        remaining = sorted(e.name for e in entries)
        result = CleanupResult(
            status="skipped",
            staging_task_dir=task_dir,
            remaining_files=remaining,
            error_message="staging_task_dir_not_empty",
        )
        _record_cleanup(session, task_id, task_dir, result)
        return result

    # 空目录 — os.rmdir 删除 staging/<task_id>/ 本身, 不向上递归 (staging 自身保留).
    try:
        os.rmdir(task_dir)
    except OSError as exc:
        result = CleanupResult(
            status="failed",
            staging_task_dir=task_dir,
            remaining_files=[],
            error_message=f"rmdir_failed:{exc}",
        )
        _record_cleanup(session, task_id, task_dir, result)
        return result

    result = CleanupResult(
        status="succeeded",
        staging_task_dir=task_dir,
        remaining_files=[],
    )
    _record_cleanup(session, task_id, task_dir, result)
    return result


def _record_cleanup(
    session: Session,
    task_id: str,
    task_dir: Path,
    result: CleanupResult,
) -> None:
    """把 cleanup 结果写 OperationRecord; 任何写入失败只 log, 不得再抛."""
    try:
        record_file_operation(
            session,
            task_id=task_id,
            operation_type="cleanup_staging_task_dir",
            permission_level="safe_write",
            source_path=task_dir,
            target_path=task_dir,
            status=result.status,
            actor="system",
            error_message=result.error_message,
            extra_details={
                "staging_task_dir": str(task_dir),
                "remaining_files": list(result.remaining_files),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "写 cleanup_staging_task_dir OperationRecord 失败 (task_id=%s, status=%s): %s",
            task_id, result.status, exc,
        )
