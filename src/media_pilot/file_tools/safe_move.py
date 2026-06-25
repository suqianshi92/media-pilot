import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from media_pilot.file_tools.protection import check_allowed_path


class MoveMethod(StrEnum):
    MOVE = "move"


@dataclass(frozen=True)
class SafeMoveResult:
    source_path: Path
    target_path: Path
    method: MoveMethod | None
    reason: str | None = None


def safe_move(
    source_path: Path,
    target_path: Path,
    *,
    allowed_source_roots: tuple[Path, ...],
    allowed_target_roots: tuple[Path, ...],
) -> SafeMoveResult:
    source_decision = check_allowed_path(source_path, allowed_roots=allowed_source_roots)
    target_decision = check_allowed_path(target_path, allowed_roots=allowed_target_roots)

    if not source_decision.allowed:
        return SafeMoveResult(
            source_path=source_decision.resolved_path,
            target_path=target_decision.resolved_path,
            method=None,
            reason=source_decision.reason,
        )

    if not target_decision.allowed:
        return SafeMoveResult(
            source_path=source_decision.resolved_path,
            target_path=target_decision.resolved_path,
            method=None,
            reason=target_decision.reason,
        )

    if target_decision.resolved_path.exists():
        return SafeMoveResult(
            source_path=source_decision.resolved_path,
            target_path=target_decision.resolved_path,
            method=None,
            reason="target_already_exists",
        )

    target_decision.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_decision.resolved_path), str(target_decision.resolved_path))
    return SafeMoveResult(
        source_path=source_decision.resolved_path,
        target_path=target_decision.resolved_path,
        method=MoveMethod.MOVE,
    )
