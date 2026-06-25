from enum import StrEnum

from media_pilot.repository.models import IngestTask
from media_pilot.repository.repositories import IngestTaskRepository


class IngestTaskStatus(StrEnum):
    DISCOVERED = "discovered"
    WAITING_STABLE = "waiting_stable"
    CREATED = "created"
    WORKSPACE_IMPORTED = "workspace_imported"
    AI_PARSED = "ai_parsed"
    CANDIDATES_READY = "candidates_ready"
    QUEUED = "queued"
    PROCESSING = "processing"
    LIBRARY_IMPORT_COMPLETE = "library_import_complete"
    COMPLETED = "completed"
    FAILED = "failed"
    AGENT_RUNNING = "agent_running"
    WAITING_USER = "waiting_user"
    AGENT_FAILED = "agent_failed"
    DELETED = "deleted"


def transition_task(
    repository: IngestTaskRepository,
    task: IngestTask,
    status: IngestTaskStatus,
    current_step: str,
    *,
    failure_reason: str | None = None,
) -> IngestTask:
    return repository.update_status(
        task,
        status=status,
        current_step=current_step,
        failure_reason=failure_reason,
    )
