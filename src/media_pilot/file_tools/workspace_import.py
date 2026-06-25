from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from media_pilot.file_tools.protection import check_allowed_path


class ImportMethod(StrEnum):
    PREPARE = "prepare"


@dataclass(frozen=True)
class WorkspaceImportResult:
    target_path: Path
    method: ImportMethod | None
    reason: str | None = None


def import_download_to_workspace(
    source_path: Path,
    *,
    downloads_dir: Path,
    watch_dir: Path | None = None,
    workspace_dir: Path,
    task_id: str | None = None,
) -> WorkspaceImportResult:
    allowed_roots: list[Path] = [downloads_dir]
    if watch_dir is not None:
        allowed_roots.append(watch_dir)
    source_decision = check_allowed_path(
        source_path, allowed_roots=tuple(allowed_roots),
    )
    if not source_decision.allowed:
        return WorkspaceImportResult(
            target_path=workspace_dir / (task_id or source_path.stem),
            method=None,
            reason=source_decision.reason,
        )

    workspace_name = task_id or source_path.stem
    target_path = workspace_dir / workspace_name
    target_decision = check_allowed_path(target_path, allowed_roots=(workspace_dir,))
    if not target_decision.allowed:
        return WorkspaceImportResult(
            target_path=target_decision.resolved_path,
            method=None,
            reason=target_decision.reason,
        )

    metadata_dir = target_decision.resolved_path / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    return WorkspaceImportResult(
        target_path=target_decision.resolved_path,
        method=ImportMethod.PREPARE,
    )
