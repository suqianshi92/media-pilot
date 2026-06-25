from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy.orm import Session

from media_pilot.config.settings import AppConfig


class PermissionLevel(StrEnum):
    READ_ONLY = "read_only"
    DRAFT = "draft"
    WRITE = "write"


@dataclass(frozen=True, kw_only=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    permission_level: PermissionLevel
    handler: Any  # Callable[[ToolContext, dict[str, Any]], ToolResult]


@dataclass(frozen=True, kw_only=True)
class ToolResult:
    status: Literal["success", "failure"]
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ToolContext:
    session: Session
    config: AppConfig
    task_id: str
    run_id: str | None = None
