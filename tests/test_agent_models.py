from sqlalchemy import inspect

from media_pilot.repository.database import Base
from media_pilot.repository.models import (
    AgentDecisionRequest,
    AgentMessage,
    AgentRun,  # noqa: F401
    AgentToolCall,
)


def test_agent_tables_are_registered() -> None:
    assert "agent_runs" in Base.metadata.tables
    assert "agent_messages" in Base.metadata.tables
    assert "agent_tool_calls" in Base.metadata.tables
    assert "agent_decision_requests" in Base.metadata.tables


def test_agent_run_has_required_columns() -> None:
    columns = inspect(AgentRun).columns

    assert columns.id.primary_key
    assert columns.id.type.python_type is str
    assert columns.task_id.type.python_type is str
    assert columns.status.type.python_type is str
    assert columns.status.default.arg == "active"
    assert columns.run_metadata.type.python_type is dict
    assert columns.created_at.type.python_type.__name__ == "datetime"
    assert columns.updated_at.type.python_type.__name__ == "datetime"


def test_agent_message_has_required_columns() -> None:
    columns = inspect(AgentMessage).columns

    assert columns.id.primary_key
    assert columns.role.type.python_type is str
    assert columns.run_id.type.python_type is str


def test_agent_tool_call_has_required_columns() -> None:
    columns = inspect(AgentToolCall).columns

    assert columns.id.primary_key
    assert columns.tool_name.type.python_type is str
    assert columns.input.type.python_type is dict
    assert columns.status.type.python_type is str
    assert columns.status.default.arg == "pending"
    assert columns.duration_ms.type.python_type is int
    # message_id is nullable (FK to agent_messages)
    assert columns.message_id.nullable is True


def test_agent_decision_request_has_required_columns() -> None:
    columns = inspect(AgentDecisionRequest).columns

    assert columns.id.primary_key
    assert columns.run_id.type.python_type is str
    assert columns.task_id.type.python_type is str
    assert columns.decision_type.type.python_type is str
    assert columns.status.type.python_type is str
    assert columns.status.default.arg == "pending"
    assert columns.question.type.python_type is str
    assert columns.free_text_allowed.type.python_type is bool
    assert columns.options.type.python_type is dict
