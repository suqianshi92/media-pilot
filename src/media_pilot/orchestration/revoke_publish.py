"""撤销发布能力 —— 删除媒体库中的最终发布目录，并创建 Agent 决策决定下一步。"""

import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from media_pilot.repository.models import (
    AuditLog,
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

POST_REVOKE_OPTIONS = [
    {
        "id": "reingest_with_new_search",
        "label": "重新搜索并重新入库",
        "description": "清除当前元数据候选，Agent 将从搜索阶段重新开始处理",
    },
    {
        "id": "reingest_with_existing_metadata",
        "label": "沿用现有元数据重新入库",
        "description": "保留已选择的元数据，Agent 将从发布阶段继续处理",
    },
    {
        "id": "delete_task_input",
        "label": "删除任务输入并清理",
        "description": "删除任务关联的输入文件和数据，结束入库流程",
    },
]

# ── DTOs (re-exported via task_dtos.py) ──


@dataclass(frozen=True)
class RevokePublishCheckResult:
    """撤销发布预检结果"""

    allowed: bool
    publish_dir: str | None = None
    source_file_exists: bool = False
    is_complex_structure: bool = False
    outcome_description: str = ""


@dataclass(frozen=True)
class RevokePublishResult:
    """撤销发布执行结果"""

    status: str  # "waiting_user" | "deleted" | "completed"
    outcome: str
    decision_id: str | None = None


# ── 预检 ──


def check_revoke_publish(session: Session, *, task_id: str) -> RevokePublishCheckResult:
    """预检任务是否可撤销发布，返回结构化预检结果。

    只有当前状态为 library_import_complete 的任务才允许撤销发布。
    """
    task = session.get(IngestTask, task_id)
    if task is None:
        return RevokePublishCheckResult(
            allowed=False,
            outcome_description="任务不存在",
        )

    if task.status != "library_import_complete":
        return RevokePublishCheckResult(
            allowed=False,
            outcome_description=f"任务当前状态为 {task.status}，仅已完成入库的任务可撤销发布",
        )

    # 从 WriteResult 获取发布目录（取最新记录）
    write_result = session.scalars(
        select(WriteResult)
        .where(WriteResult.task_id == task_id)
        .where(WriteResult.status.in_(["succeeded", "warning"]))
        .order_by(WriteResult.created_at.desc())
    ).first()

    publish_dir: str | None = None
    if write_result is not None:
        publish_dir = write_result.payload.get("target_dir")

    # 获取源文件信息（取最新记录）
    from media_pilot.repository.repositories import MediaSourceSelectionRepository
    source_selection = MediaSourceSelectionRepository(session).get_for_task(task_id)

    selected_path: str | None = None
    input_path: str | None = None
    is_complex_structure = False
    if source_selection is not None:
        input_path = source_selection.input_path
        selected_path = source_selection.selected_path
        payload = source_selection.payload or {}
        is_complex_structure = bool(
            payload.get("bdmv_detected") or payload.get("source_kind") == "bdmv"
        )

    source_file_exists = False
    source_probe = input_path if is_complex_structure else selected_path
    if source_probe:
        source_file_exists = Path(source_probe).exists()

    # 判别去向
    if is_complex_structure:
        outcome = (
            "BDMV / 复杂结构：撤销后将删除发布目录并删除任务关联业务数据，"
            "当前不支持回到人工确认重处理"
        )
    elif not source_file_exists:
        outcome = "主文件已缺失：撤销后将删除发布目录并删除任务关联业务数据"
    else:
        outcome = (
            "主文件仍存在：撤销后将删除发布目录，"
            "任务进入等待用户状态，可选择重新搜索、沿用元数据或删除输入"
        )

    return RevokePublishCheckResult(
        allowed=True,
        publish_dir=publish_dir,
        source_file_exists=source_file_exists,
        is_complex_structure=is_complex_structure,
        outcome_description=outcome,
    )


# ── 执行 ──


def execute_revoke_publish(
    session: Session, *, task_id: str, skip_post_revoke_decision: bool = False,
    existing_run_id: str | None = None,
) -> RevokePublishResult:
    """执行撤销发布。

    仅删除任务记录的发布目录，不操作下载输入。
    根据预检结果决定：删除任务数据 或 创建 Agent 决策等待用户选择。

    当 ``skip_post_revoke_decision=True`` 时（用于明确纠正意图的 Agent 链路），
    撤回后不创建 post_revoke_action 决策，Agent 可在同一 run 内继续处理。

    当 ``existing_run_id`` 提供且 ``skip_post_revoke_decision=False`` 时，
    post_revoke_action 决策绑定到已有 AgentRun，而非创建新 run，
    避免 active run 冲突。
    """
    check = check_revoke_publish(session, task_id=task_id)
    if not check.allowed:
        raise _RevokeNotAllowed(check.outcome_description)

    # 1. 删除发布目录（仅删除任务记录的目录，不接受客户端任意路径）
    if check.publish_dir:
        publish_path = Path(check.publish_dir)
        if publish_path.exists():
            if publish_path.is_dir():
                shutil.rmtree(publish_path)
            else:
                publish_path.unlink()

    # 2. 决定任务去向
    if check.is_complex_structure or not check.source_file_exists:
        # BDMV / 复杂结构 或 主文件缺失 → 删除任务业务数据
        _delete_task_data(session, task_id)
        session.commit()
        return RevokePublishResult(
            status="deleted",
            outcome="已删除发布目录与任务业务数据",
        )

    # 主文件仍存在 → 清理发布上下文
    _cleanup_publish_context(session, task_id)

    if skip_post_revoke_decision:
        # Clear correction intent: revoke without creating a decision,
        # so the Agent can continue searching/publishing in the same run.
        from media_pilot.orchestration.state_machine import IngestTaskStatus
        task = session.get(IngestTask, task_id)
        if task is not None:
            task.status = IngestTaskStatus.PROCESSING
            task.current_step = "post_revoke_reingest"
        session.commit()
        return RevokePublishResult(
            status="completed",
            outcome="发布目录已删除，发布上下文已清理，等待 Agent 继续处理",
        )

    # 获取或创建承载决策的 AgentRun
    from media_pilot.repository.repositories import AgentRunRepository
    run_repo = AgentRunRepository(session)

    if existing_run_id:
        run = run_repo.get(existing_run_id)
        if run is None:
            raise ValueError(f"AgentRun {existing_run_id} not found")
        # 复用当前 run，不创建新 run，避免 active conflict
    else:
        run = _ensure_agent_run_for_decision(session, task_id)

    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
    )
    dr_repo = AgentDecisionRequestRepository(session)
    decision = dr_repo.create(AgentDecisionRequestCreate(
        run_id=run.id,
        task_id=task_id,
        decision_type="post_revoke_action",
        question="撤回发布已完成，请选择下一步操作：",
        free_text_allowed=False,
        options=POST_REVOKE_OPTIONS,
    ))

    # 将承载决策的 AgentRun 设为 waiting_user
    run_repo.update_status(run, status="waiting_user", current_step="post_revoke_decision")

    # 写入系统消息
    from media_pilot.repository.repositories import (
        AgentMessageCreate,
        AgentMessageRepository,
    )
    msg_repo = AgentMessageRepository(session)
    msg_repo.create(AgentMessageCreate(
        run_id=run.id,
        role="assistant",
        content="[SystemAction] 撤回发布已完成。发布目录已删除，请选择下一步操作。",
    ))

    session.commit()
    return RevokePublishResult(
        status="waiting_user",
        outcome="发布目录已删除，任务等待用户选择后续操作",
        decision_id=decision.id,
    )


# ── 内部辅助 ──


class _RevokeNotAllowed(ValueError):
    """任务不允许撤销发布"""


def _cleanup_publish_context(session: Session, task_id: str) -> None:
    """清理上一轮成功发布的完成态数据。

    撤销发布后必须清除旧 WriteResult/WritePlan/FileAsset，
    否则后续发布判断会依赖已撤回的过期数据。
    MetadataDetail 保留以供 reingest_with_existing_metadata 使用。
    """
    tables_in_order = [
        (WriteResult, WriteResult.task_id),
        (WritePlan, WritePlan.task_id),
        (FileAsset, FileAsset.task_id),
    ]
    for model, col in tables_in_order:
        session.execute(delete(model).where(col == task_id))


def _ensure_agent_run_for_decision(session: Session, task_id: str):
    """创建系统 AgentRun 用于承载撤回后决策。

    对于 library_import_complete 任务，没有 active run，
    AgentRunRepository.create() 不会触发 active 检查冲突。
    """
    from media_pilot.repository.repositories import (
        AgentRunCreate,
        AgentRunRepository,
    )
    run_repo = AgentRunRepository(session)
    run = run_repo.create(AgentRunCreate(
        task_id=task_id,
        current_step="post_revoke_decision",
    ))
    return run


def _delete_task_data(session: Session, task_id: str) -> None:
    """删除任务关联的业务数据（保留 IngestTask 主记录由调用方决定）。"""
    # 按依赖顺序删除：先删外键引用方，再删主表
    tables_in_order = [
        (AuditLog, AuditLog.task_id),
        (OperationRecord, OperationRecord.task_id),
        (FileAsset, FileAsset.task_id),
        (WriteResult, WriteResult.task_id),
        (WritePlan, WritePlan.task_id),
        (MetadataDetail, MetadataDetail.task_id),
        (SearchKeywordRecord, SearchKeywordRecord.task_id),
        (MediaSourceSelection, MediaSourceSelection.task_id),
        (MediaCandidate, MediaCandidate.task_id),
    ]

    for model, col in tables_in_order:
        session.execute(delete(model).where(col == task_id))

    # 最后删除主任务记录
    session.execute(delete(IngestTask).where(IngestTask.id == task_id))
