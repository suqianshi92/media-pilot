from sqlalchemy import inspect

from media_pilot.repository.database import Base
from media_pilot.repository.models import (
    AdapterCall,
    AuditLog,
    DownloadTask,  # noqa: F401 — 表注册需要 import
    FileAsset,
    IngestTask,
    MediaCandidate,
    MediaSourceSelection,
    MetadataDetail,
    OperationRecord,
    SearchKeywordRecord,
    WritePlan,
    WriteResult,
)


def test_core_tables_are_registered() -> None:
    # 注: confirmation_requests 表在 replace-legacy-confirmation-with-agent-decisions
    # 已从 SQLAlchemy metadata 中下线；现存数据库中可能保留旧数据，但应用层不再管理。
    assert set(Base.metadata.tables) == {
        "adapter_calls",
        "agent_decision_requests",
        "agent_messages",
        "agent_runs",
        "agent_tool_calls",
        "app_settings",
        "audit_logs",
        "download_tasks",
        "episode_mappings",
        "file_assets",
        "ingest_tasks",
        "media_candidates",
        "media_source_selections",
        "metadata_details",
        "operation_records",
        "search_keyword_records",
        "write_plans",
        "write_results",
    }


def test_ingest_task_has_portable_core_columns() -> None:
    columns = inspect(IngestTask).columns

    assert columns.id.primary_key
    assert columns.id.type.python_type is str
    assert columns.source_path.type.python_type is str
    assert columns.status.type.python_type is str
    assert columns.created_at.type.python_type.__name__ == "datetime"


def test_related_models_include_task_links_and_json_payloads() -> None:
    assert inspect(MediaCandidate).columns.task_id.type.python_type is str
    assert inspect(MediaSourceSelection).columns.task_id.type.python_type is str
    assert inspect(SearchKeywordRecord).columns.task_id.type.python_type is str
    assert inspect(MetadataDetail).columns.task_id.type.python_type is str
    assert inspect(WritePlan).columns.task_id.type.python_type is str
    assert inspect(WriteResult).columns.task_id.type.python_type is str
    assert inspect(FileAsset).columns.task_id.type.python_type is str
    assert inspect(MediaSourceSelection).columns.payload.type.python_type is dict
    assert inspect(SearchKeywordRecord).columns.payload.type.python_type is dict
    assert inspect(MetadataDetail).columns.payload.type.python_type is dict
    assert inspect(WritePlan).columns.payload.type.python_type is dict
    assert inspect(WriteResult).columns.payload.type.python_type is dict
    assert inspect(OperationRecord).columns.details.type.python_type is dict
    assert inspect(AuditLog).columns.context.type.python_type is dict
    assert inspect(AdapterCall).columns.response_summary.type.python_type is dict
