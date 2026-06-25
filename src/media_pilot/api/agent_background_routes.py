"""API v1 — 后台 Agent 状态只读端点.

只暴露 BackgroundStatusService 的进程内快照; 不提供"立即跑一轮"或
重试 / 暂停后台线程等控制动作. 历史最多 10 条, 写入前已脱敏.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.api.schemas import ApiEnvelope
from media_pilot.services.agent_background_status import (
    BackgroundHistoryEntry,
    BackgroundStatusSnapshot,
    get_default_background_status_service,
)

router = APIRouter(prefix="/api/v1")


def _snapshot_to_dto(snap: BackgroundStatusSnapshot) -> dict:
    """把 snapshot 序列化成 dict; 显式列举字段防止 dataclass 内部字段泄露."""

    return {
        "enabled": snap.enabled,
        "state": snap.state.value,
        "summary": snap.summary,
        "disabled_reasons": list(snap.disabled_reasons),
        "waiting_user_count": snap.waiting_user_count,
        "agent_failed_count": snap.agent_failed_count,
        "last_run": snap.last_run.isoformat() if snap.last_run else None,
        "history": [_history_to_dto(e) for e in snap.history],
        "current_task_id": snap.current_task_id,
        "current_download_id": snap.current_download_id,
    }


def _history_to_dto(entry: BackgroundHistoryEntry) -> dict:
    return {
        "timestamp": entry.timestamp.isoformat(),
        "phase": entry.phase,
        "level": entry.level.value,
        "summary": entry.summary,
        "task_id": entry.task_id,
        "download_id": entry.download_id,
    }


@router.get("/agent-background/status")
def agent_background_status(request: Request) -> ApiEnvelope[dict]:
    """后台 Agent 状态快照 — 只读诊断入口."""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None,
    )
    worker = getattr(request.app.state, "worker", None)
    is_enabled = bool(worker and worker.is_enabled())

    snapshot = get_default_background_status_service().compute_snapshot(
        session_factory=session_factory,
        is_enabled=is_enabled,
    )
    return ApiEnvelope(
        status="success",
        data=_snapshot_to_dto(snapshot),
        messages=[],
        meta={},
    )
