"""Current task status contract tests.

These tests keep the public status Interface aligned across the backend DTOs,
state machine, and frontend types. They intentionally avoid source scanning for
deleted historical symbols.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args


REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_TASK_TYPES = REPO_ROOT / "frontend" / "src" / "types" / "task.ts"


def test_task_status_matches_state_machine_values() -> None:
    from media_pilot.api.task_dtos import TaskStatus
    from media_pilot.orchestration.state_machine import IngestTaskStatus

    dto_values = set(get_args(TaskStatus))
    state_values = {status.value for status in IngestTaskStatus}

    assert dto_values == state_values


def test_write_result_status_contract() -> None:
    from media_pilot.api.task_dtos import WriteResultDto

    status_field = WriteResultDto.model_fields["status"]
    actual = set(get_args(status_field.annotation))

    assert actual == {"succeeded", "warning", "failed", "target_conflict"}


def test_frontend_task_status_matches_backend_dto() -> None:
    from media_pilot.api.task_dtos import TaskStatus

    frontend_source = FRONTEND_TASK_TYPES.read_text(encoding="utf-8")
    frontend_values = _extract_string_union(frontend_source, "TaskStatus")
    backend_values = set(get_args(TaskStatus))

    assert frontend_values == backend_values


def test_frontend_task_step_contains_backend_stable_steps() -> None:
    from media_pilot.api.task_dtos import TaskStep

    frontend_source = FRONTEND_TASK_TYPES.read_text(encoding="utf-8")
    frontend_values = _extract_string_union(frontend_source, "TaskStep")
    backend_values = {step.value for step in TaskStep}

    assert frontend_values == backend_values


def _extract_string_union(source: str, name: str) -> set[str]:
    match = re.search(
        rf"export type {name}\s*=\s*([\s\S]+?)(?:\n\nexport |\Z)",
        source,
    )
    assert match, f"未找到 frontend {name} union"
    return set(re.findall(r"'([^']+)'", match.group(1)))
