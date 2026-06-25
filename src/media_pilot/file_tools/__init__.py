"""Controlled file operation boundary."""

from media_pilot.file_tools.protection import (
    FileOperation,
    FileOperationDecision,
    PathPolicyDecision,
    check_allowed_path,
    check_download_source_operation,
)
from media_pilot.file_tools.safe_move import MoveMethod, SafeMoveResult, safe_move
from media_pilot.file_tools.workspace_import import (
    ImportMethod,
    WorkspaceImportResult,
    import_download_to_workspace,
)

MODULE_BOUNDARY = "controlled file operations"

__all__ = [
    "FileOperation",
    "FileOperationDecision",
    "ImportMethod",
    "MODULE_BOUNDARY",
    "MoveMethod",
    "PathPolicyDecision",
    "SafeMoveResult",
    "WorkspaceImportResult",
    "check_allowed_path",
    "check_download_source_operation",
    "import_download_to_workspace",
    "safe_move",
]
