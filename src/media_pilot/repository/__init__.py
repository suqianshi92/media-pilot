"""Database repository boundary."""

from media_pilot.repository.audit import record_file_operation
from media_pilot.repository.database import (
    Base,
    create_engine_from_config,
    create_session_factory,
    initialize_database,
)
from media_pilot.repository.models import (
    AdapterCall,
    AgentDecisionRequest,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    AuditLog,
    DownloadTask,
    FileAsset,
    IngestTask,
    MediaCandidate,
    OperationRecord,
)
from media_pilot.repository.repositories import (
    AgentDecisionRequestCreate,
    AgentDecisionRequestRepository,
    AgentMessageCreate,
    AgentMessageRepository,
    AgentRunCreate,
    AgentRunRepository,
    AgentToolCallCreate,
    AgentToolCallRepository,
    DownloadTaskCreate,
    DownloadTaskRepository,
    IngestTaskCreate,
    IngestTaskRepository,
)

MODULE_BOUNDARY = "database repositories"

__all__ = [
    "AdapterCall",
    "AgentDecisionRequest",
    "AgentDecisionRequestCreate",
    "AgentDecisionRequestRepository",
    "AgentMessage",
    "AgentMessageCreate",
    "AgentMessageRepository",
    "AgentRun",
    "AgentRunCreate",
    "AgentRunRepository",
    "AgentToolCall",
    "AgentToolCallCreate",
    "AgentToolCallRepository",
    "AuditLog",
    "Base",
    "DownloadTask",
    "DownloadTaskCreate",
    "DownloadTaskRepository",
    "FileAsset",
    "IngestTask",
    "IngestTaskCreate",
    "IngestTaskRepository",
    "MODULE_BOUNDARY",
    "MediaCandidate",
    "OperationRecord",
    "create_engine_from_config",
    "create_session_factory",
    "initialize_database",
    "record_file_operation",
]
