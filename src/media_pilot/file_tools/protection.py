from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class FileOperation(StrEnum):
    READ = "read"
    MOVE = "move"
    DELETE = "delete"
    OVERWRITE = "overwrite"
    RENAME = "rename"


@dataclass(frozen=True)
class FileOperationDecision:
    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class PathPolicyDecision:
    allowed: bool
    resolved_path: Path
    reason: str | None = None


MUTATING_DOWNLOAD_OPERATIONS = frozenset(
    {
        FileOperation.MOVE,
        FileOperation.DELETE,
        FileOperation.OVERWRITE,
        FileOperation.RENAME,
    }
)


def check_allowed_path(path: Path, *, allowed_roots: tuple[Path, ...]) -> PathPolicyDecision:
    resolved_path = path.resolve(strict=False)
    resolved_roots = tuple(root.resolve(strict=False) for root in allowed_roots)

    if any(_is_relative_to(resolved_path, root) for root in resolved_roots):
        return PathPolicyDecision(allowed=True, resolved_path=resolved_path)

    return PathPolicyDecision(
        allowed=False,
        resolved_path=resolved_path,
        reason="path_outside_allowed_roots",
    )


def check_download_source_operation(
    downloads_dir: Path,
    path: Path,
    operation: FileOperation,
    *,
    watch_dir: Path | None = None,
) -> FileOperationDecision:
    """校验操作是否在只读下载源上执行破坏性操作。

    下载源包括：
    - downloads_dir: 系统内管理下载目录
    - watch_dir: 外部导入目录
    两者均不可进行 MOVE/DELETE/OVERWRITE/RENAME 操作。
    """
    roots: list[Path] = [downloads_dir.resolve(strict=False)]
    if watch_dir is not None:
        roots.append(watch_dir.resolve(strict=False))
    requested_path = path.resolve(strict=False)

    is_download_source = any(
        _is_relative_to(requested_path, root) for root in roots
    )
    if is_download_source and operation in MUTATING_DOWNLOAD_OPERATIONS:
        return FileOperationDecision(
            allowed=False,
            reason="original_download_file_is_read_only",
        )

    return FileOperationDecision(allowed=True)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
