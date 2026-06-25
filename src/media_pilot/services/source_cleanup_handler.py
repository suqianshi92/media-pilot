"""源文件清理决策回复 handler — keep_input / trash_input 确定性执行.

delete_input 不在此处处理: 它复用现有 delete_input_preview → execute_delete_input
API 流程, 通过 reply_to_decision 的 run.status="delete_input_preview" 让前端
拉起删除预检.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from media_pilot.config import AppConfig


def handle_source_cleanup_keep(
    *,
    session: Session,
    config: AppConfig,
    decision,
) -> dict:
    """用户选择保留源文件: 记录 source_input_kept OperationRecord, 不移动文件.

    task.status / current_step 保持 library_import_complete, 决策已经由
    reply_to_decision 在调度前切到 completed. 这里只做事件记录.
    """
    from media_pilot.repository.models import (
        IngestTask,
        MediaSourceSelection,
        OperationRecord,
    )
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MediaSourceSelectionRepository,
    )
    from media_pilot.services.source_cleanup_preflight import (
        resolve_task_input_node,
    )

    task_id = decision.task_id

    source_path: str = ""
    try:
        task = IngestTaskRepository(session).get(task_id)
        if task is not None:
            selection: MediaSourceSelection | None = (
                MediaSourceSelectionRepository(session).get_for_task(task_id)
            )
            kept_input_node = resolve_task_input_node(
                task=task, selection=selection,
            )
            if kept_input_node is not None:
                source_path = str(kept_input_node)
            elif task.source_path:
                source_path = str(task.source_path)
    except Exception:
        # 路径解析失败不应阻塞"保留"事件的记录
        source_path = ""

    session.add(OperationRecord(
        task_id=task_id,
        operation_type="source_input_kept",
        permission_level="write",
        source_path=source_path,
        status="succeeded",
        details={"reason": "user_reply:keep_input"},
    ))

    # 显式维持 task 的 library_import_complete + 标记 current_step
    task_obj = session.get(IngestTask, task_id)
    if task_obj is not None:
        task_obj.status = "library_import_complete"
        task_obj.current_step = "source_cleanup_kept"
        session.flush()

    session.flush()
    return {"outcome": "kept", "task_id": task_id}


def handle_source_cleanup_trash(
    *,
    session: Session,
    config: AppConfig,
    decision,
) -> dict:
    """用户选择移入回收区: 复跑预检 + execute_source_cleanup.

    预检失败时仍然记录 source_input_cleanup_failed OperationRecord, 任务
    状态保持 library_import_complete (与工具失败行为一致).
    """
    from media_pilot.repository.models import (
        IngestTask,
        MediaSourceSelection,
        OperationRecord,
    )
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MediaSourceSelectionRepository,
    )
    from media_pilot.services.source_cleanup_preflight import (
        check_source_cleanup_preflight,
        execute_source_cleanup,
    )

    task_id = decision.task_id

    task_repo = IngestTaskRepository(session)
    task = task_repo.get(task_id)
    if task is None:
        session.add(OperationRecord(
            task_id=task_id,
            operation_type="source_input_cleanup_failed",
            permission_level="write",
            status="failed",
            details={"reason": "task_not_found", "via": "decision_reply:trash_input"},
        ))
        session.flush()
        return {"outcome": "trash_failed", "reason": "task_not_found", "task_id": task_id}

    selection: MediaSourceSelection | None = (
        MediaSourceSelectionRepository(session).get_for_task(task_id)
    )

    preflight = check_source_cleanup_preflight(
        config=config, task=task, selection=selection,
    )
    if not preflight.allowed:
        session.add(OperationRecord(
            task_id=task_id,
            operation_type="source_input_cleanup_failed",
            permission_level="write",
            source_path=str(preflight.source_path) if preflight.source_path else "",
            status="failed",
            details={"reason": preflight.reason, "via": "decision_reply:trash_input"},
        ))
        task_obj = session.get(IngestTask, task_id)
        if task_obj is not None:
            task_obj.status = "library_import_complete"
            task_obj.current_step = "source_cleanup_trash_refused"
            session.flush()
        return {
            "outcome": "trash_failed",
            "reason": preflight.reason,
            "task_id": task_id,
        }

    result = execute_source_cleanup(config=config, preflight=preflight)
    if not result.success:
        session.add(OperationRecord(
            task_id=task_id,
            operation_type="source_input_cleanup_failed",
            permission_level="write",
            source_path=str(result.source_path) if result.source_path else "",
            status="failed",
            details={"reason": result.reason, "via": "decision_reply:trash_input"},
        ))
        task_obj = session.get(IngestTask, task_id)
        if task_obj is not None:
            task_obj.status = "library_import_complete"
            task_obj.current_step = "source_cleanup_trash_failed"
            session.flush()
        return {
            "outcome": "trash_failed",
            "reason": result.reason,
            "task_id": task_id,
        }

    session.add(OperationRecord(
        task_id=task_id,
        operation_type="source_input_trashed",
        permission_level="write",
        source_path=str(result.source_path) if result.source_path else "",
        target_path=str(result.trash_target) if result.trash_target else "",
        status="succeeded",
        details={"via": "decision_reply:trash_input"},
    ))
    task_obj = session.get(IngestTask, task_id)
    if task_obj is not None:
        task_obj.status = "library_import_complete"
        task_obj.current_step = "source_cleanup_trashed"
        session.flush()

    return {
        "outcome": "trashed",
        "task_id": task_id,
        "trash_target": str(result.trash_target) if result.trash_target else None,
    }
