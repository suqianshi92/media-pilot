"""人工辅助元数据选择 —— 写入任务事实并复用受控工具链。

用户在工作台人工检索并选择候选后，通过此服务持久化选择、
获取元数据详情、并在条件满足时执行确定性快捷发布。

Agent 主线要求：所有可操作等待必须落到 `waiting_user + AgentDecisionRequest`，
目标冲突 / 阻塞都必须创建 decision；如任务尚无 active/waiting AgentRun，
由本服务在创建 system run 后再创建 decision，保证 reply 端点能续跑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from media_pilot.config import AppConfig


@dataclass(frozen=True, kw_only=True)
class ManualSelectResult:
    status: str  # "published" | "waiting_user" | "saved" | "agent_failed" | "rejected"
    summary: str
    candidate_id: str | None = None
    decision_id: str | None = None
    blocking_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class _PublishOutcome:
    """`_quick_publish` 结构化结果 — 区别成功/目标冲突/失败。"""
    kind: str  # "published" | "target_conflict" | "failed"
    reason: str = ""
    final_target_dir: str | None = None
    final_target_file: str | None = None
    conflict_code: str | None = None
    decision_id: str | None = None


def submit_manual_selection(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
    provider: str,
    provider_id: str,
    title: str,
    year: int | None = None,
    original_title: str | None = None,
    media_type: str = "movie",
) -> ManualSelectResult:
    """提交人工选择的元数据候选并尝试自动处理。

    1. 持久化 MediaCandidate(source="manual")
    2. 获取并保存 MetadataDetail
    3. 检查 eligibility
    4. 单文件电影 + 安全门禁全通过 → 快捷发布
    5. 安全门禁阻塞 → 创建 AgentDecisionRequest(waiting_user)
    6. 目标冲突 → 创建 AgentDecisionRequest(decision_type="target_conflict", waiting_user)
    """
    from media_pilot.repository.repositories import IngestTaskRepository
    from media_pilot.services.auto_ingest import (
        check_eligibility,
        fetch_and_save_metadata_detail,
        persist_metadata_selection,
    )

    task_repo = IngestTaskRepository(session)
    task = task_repo.get(task_id)
    if task is None:
        return ManualSelectResult(
            status="rejected",
            summary="任务不存在",
        )
    if task.status == "agent_running":
        return ManualSelectResult(
            status="rejected",
            summary="任务正在 Agent 处理中，请等待完成或先使用卡住恢复入口",
        )
    if task.status == "deleted":
        return ManualSelectResult(
            status="rejected",
            summary="任务已删除，不能重新选择元数据",
        )

    if task.status == "library_import_complete":
        revoke_result = _revoke_completed_publish_for_manual_reselect(
            session, config, task_id,
        )
        if revoke_result is not None:
            return revoke_result

    _supersede_pending_decisions(
        session, task_id, reason="manual_metadata_selection_override",
    )
    _clear_metadata_facts(session, task_id)

    # 1. 持久化用户选择的候选（source="manual" 而非 "agent"）
    sel_result = persist_metadata_selection(
        session=session,
        task_id=task_id,
        provider_name=provider,
        provider_id=provider_id,
        media_type=media_type,
        title=title,
        year=year,
        original_title=original_title,
        confidence=1.0,
    )
    if sel_result.status == "failure":
        return ManualSelectResult(
            status="saved",
            summary=sel_result.summary,
        )
    _overwrite_task_metadata_fields(
        session,
        task_id=task_id,
        title=title,
        year=year,
        media_type=media_type,
        confidence=1.0,
    )

    # 2. 获取并保存元数据详情
    detail_result = fetch_and_save_metadata_detail(
        session=session,
        config=config,
        task_id=task_id,
        provider_name=provider,
        provider_id=provider_id,
        media_type=media_type,
    )
    if detail_result.status == "failure":
        _write_system_message(
            session, task_id,
            f"[SystemAction] 用户手动选择了 {title} ({year}) from {provider}，"
            f"但获取元数据详情失败：{detail_result.summary}",
        )
        return ManualSelectResult(
            status="saved",
            summary=f"候选已保存，但获取详情失败：{detail_result.summary}",
            candidate_id=sel_result.candidate_id,
        )
    _overwrite_task_metadata_fields(
        session,
        task_id=task_id,
        title=detail_result.title or title,
        year=detail_result.year if detail_result.year is not None else year,
        media_type=media_type,
        confidence=1.0,
    )

    # 3. 检查 eligibility
    eligibility = check_eligibility(
        session=session,
        config=config,
        task_id=task_id,
    )

    # 4. 判断是否可以快捷发布
    non_metadata_blockers = [
        r for r in eligibility.blocking_reasons
        if r not in ("no_metadata_candidates", "no_clear_metadata_winner")
    ]

    if non_metadata_blockers:
        # 被安全硬门禁或其他原因阻塞 → 创建 AgentDecisionRequest(manual_selection_blocked)
        decision_id = _create_blocked_decision(
            session, task_id, eligibility, blocking_reasons=non_metadata_blockers,
        )
        _write_system_message(
            session, task_id,
            f"[SystemAction] 用户手动选择了 {title} ({year}) from {provider}，"
            f"但因 {non_metadata_blockers} 无法自动发布，等待进一步处理。",
        )
        return ManualSelectResult(
            status="waiting_user",
            summary=f"候选已保存，但因 {non_metadata_blockers} 需要用户决策",
            candidate_id=sel_result.candidate_id,
            decision_id=decision_id,
            blocking_reasons=list(eligibility.blocking_reasons),
        )

    # 5. 所有门禁通过 → 确定性快捷发布
    outcome = _quick_publish(session, config, task_id)
    if outcome.kind == "published":
        if _cleanup_published_output_reselect_source(session, task_id):
            cleanup_result = _skipped_cleanup_result(
                "Published-output reselect source was temporary and has been removed.",
            )
        else:
            cleanup_result = _run_manual_post_publish_cleanup(
                session=session, config=config, task_id=task_id,
            )
        _write_system_message(
            session, task_id,
            f"[SystemAction] 用户手动选择了 {title} ({year}) from {provider}，"
            f"系统已自动完成发布。",
        )
        if not cleanup_result.decision_requested:
            _complete_manual_selection_run(session, task_id)
        return ManualSelectResult(
            status="published",
            summary=f"已选择 {title} ({year}) 并完成快捷发布",
            candidate_id=sel_result.candidate_id,
        )
    if outcome.kind == "target_conflict":
        # 目标冲突 → 创建 AgentDecisionRequest(decision_type="target_conflict")
        # 并把任务切到 waiting_user / current_step="target_conflict"
        decision_id = outcome.decision_id or _create_target_conflict_decision(
            session, task_id, outcome, title=title, year=year, provider=provider,
        )
        _write_system_message(
            session, task_id,
            f"[SystemAction] 用户手动选择了 {title} ({year}) from {provider}，"
            f"但目标 {outcome.final_target_file} 已被占用，等待用户决策。",
        )
        return ManualSelectResult(
            status="waiting_user",
            summary=(
                f"候选已保存，目标 {outcome.final_target_file} 已被占用，"
                "等待用户决策"
            ),
            candidate_id=sel_result.candidate_id,
            decision_id=decision_id,
        )
    # write_failed / no_metadata_detail / already_published / task_not_found
    _write_system_message(
        session, task_id,
        f"[SystemAction] 用户手动选择了 {title} ({year}) from {provider}，"
        f"候选和详情已保存，但快捷发布未完成：{outcome.reason}",
    )
    return ManualSelectResult(
        status="saved",
        summary=f"候选已保存，但发布未完成：{outcome.reason}",
        candidate_id=sel_result.candidate_id,
    )


def _quick_publish(
    session: Session,
    config: AppConfig,
    task_id: str,
) -> _PublishOutcome:
    """执行确定性快捷发布，不调用 LLM。返回结构化结果。"""
    import httpx

    from media_pilot.orchestration.jellyfin_movie_writer import (
        build_movie_write_plan,
        detect_movie_write_conflict,
        execute_movie_write,
    )
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MediaSourceSelectionRepository,
        MetadataDetailRepository,
    )
    from media_pilot.services.library_root_resolver import resolve_library_root
    from media_pilot.services.publish_plan_draft import _orm_detail_to_adapter

    task_repo = IngestTaskRepository(session)
    task = task_repo.get(task_id)
    if task is None:
        return _PublishOutcome(kind="failed", reason="task_not_found")

    if task.status == "library_import_complete":
        return _PublishOutcome(kind="failed", reason="already_published")

    detail_repo = MetadataDetailRepository(session)
    orm_detail = detail_repo.get_for_task(task_id)
    if orm_detail is None:
        return _PublishOutcome(kind="failed", reason="no_metadata_detail")

    if orm_detail.media_type == "show" or task.media_type == "show":
        return _quick_publish_show(session, config, task_id)

    adapter_detail = _orm_detail_to_adapter(orm_detail)

    source_path = Path(task.source_path)
    sel_repo = MediaSourceSelectionRepository(session)
    selection = sel_repo.get_for_task(task.id)
    video_source = (
        Path(selection.selected_path)
        if selection and selection.selected_path
        else source_path
    )

    plan = build_movie_write_plan(
        movies_dir=resolve_library_root(
            config, media_type="movie", provider=orm_detail.provider,
        ),
        source_path=video_source,
        detail=adapter_detail,
        task_id=task.id,
        provider=orm_detail.provider,
    )

    conflict = detect_movie_write_conflict(plan)
    if conflict is not None:
        return _PublishOutcome(
            kind="target_conflict",
            reason=f"target conflict before execute: {conflict}",
            final_target_dir=str(plan.final_target_dir),
            final_target_file=str(plan.final_target_file),
            conflict_code=conflict,
        )

    try:
        with httpx.Client(timeout=10) as client:
            write_result = execute_movie_write(
                session,
                task_id=task.id,
                source_path=video_source,
                detail=adapter_detail,
                plan=plan,
                client=client,
            )
    except Exception as exc:
        return _PublishOutcome(kind="failed", reason=f"write_failed: {exc}")

    if write_result.status == "failed":
        return _PublishOutcome(
            kind="failed",
            reason="write_failed: MovieWrite status=failed",
            final_target_dir=str(plan.final_target_dir),
            final_target_file=str(plan.final_target_file),
        )
    if write_result.status == "target_conflict":
        return _PublishOutcome(
            kind="target_conflict",
            reason="target conflict during execute",
            final_target_dir=str(plan.final_target_dir),
            final_target_file=str(plan.final_target_file),
            conflict_code="execute_time_conflict",
        )

    task_repo.update_status(
        task, status="library_import_complete", current_step="library_import_complete",
    )
    task.metadata_status = "complete"
    session.flush()
    return _PublishOutcome(
        kind="published",
        final_target_dir=str(plan.final_target_dir),
        final_target_file=str(plan.final_target_file),
    )


def _quick_publish_show(
    session: Session,
    config: AppConfig,
    task_id: str,
) -> _PublishOutcome:
    """复用剧集发布工具执行人工选择后的确定性剧集发布。"""
    from media_pilot.agent.tools.base import ToolContext
    from media_pilot.agent.tools.show import make_publish_show_to_library

    run = _ensure_manual_select_run(session, task_id)
    tool = make_publish_show_to_library()
    result = tool.handler(
        ToolContext(session=session, config=config, task_id=task_id, run_id=run.id),
        {"task_id": task_id},
    )
    data = result.data if isinstance(result.data, dict) else {}
    if result.status == "success" and data.get("decision_requested"):
        return _PublishOutcome(
            kind="target_conflict",
            reason="target conflict from publish_show_to_library",
            final_target_dir=data.get("final_target_dir"),
            final_target_file=data.get("final_target_file") or data.get("final_target_dir"),
            conflict_code=data.get("conflict"),
            decision_id=data.get("decision_id"),
        )
    if result.status == "success":
        from media_pilot.repository.repositories import IngestTaskRepository

        task = IngestTaskRepository(session).get(task_id)
        if task is not None and task.status == "library_import_complete":
            task.failure_reason = None
            session.flush()
        return _PublishOutcome(
            kind="published",
            final_target_dir=data.get("final_target_dir"),
            final_target_file=data.get("final_target_file"),
        )
    return _PublishOutcome(kind="failed", reason=result.summary)


def _run_manual_post_publish_cleanup(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
):
    """手动发布成功后执行统一 source_cleanup_policy。"""
    from media_pilot.repository.repositories import AgentRunRepository
    from media_pilot.services.post_publish_cleanup import run_post_publish_source_cleanup

    run = AgentRunRepository(session).get_active_or_waiting_by_task(task_id)
    return run_post_publish_source_cleanup(
        session=session,
        config=config,
        task_id=task_id,
        run_id=run.id if run is not None else None,
    )


def _revoke_completed_publish_for_manual_reselect(
    session: Session,
    config: AppConfig,
    task_id: str,
) -> ManualSelectResult | None:
    """完成态手动改元数据前先撤销旧发布产物。

    返回 None 表示撤销成功，可继续写入新元数据；返回 ManualSelectResult
    表示撤销失败或任务已被撤销逻辑删除，调用方应停止。
    """
    from media_pilot.services.republish_source import prepare_republish_source

    result = prepare_republish_source(
        session=session,
        config=config,
        task_id=task_id,
    )
    if not result.ok:
        return ManualSelectResult(
            status="agent_failed",
            summary=result.summary,
        )
    return None


def _cleanup_published_output_reselect_source(session: Session, task_id: str) -> bool:
    from media_pilot.services.republish_source import cleanup_temporary_republish_source

    return cleanup_temporary_republish_source(session, task_id)


def _skipped_cleanup_result(summary: str):
    from media_pilot.services.post_publish_cleanup import PostPublishCleanupResult

    return PostPublishCleanupResult(status="success", summary=summary)


def _clear_metadata_facts(session: Session, task_id: str) -> None:
    """清理旧候选与旧详情，避免人工纠正时混用上一次错误元数据。"""
    from media_pilot.repository.models import MediaCandidate, MetadataDetail

    session.execute(delete(MetadataDetail).where(MetadataDetail.task_id == task_id))
    session.execute(delete(MediaCandidate).where(MediaCandidate.task_id == task_id))
    session.flush()


def _supersede_pending_decisions(
    session: Session,
    task_id: str,
    *,
    reason: str,
) -> None:
    """人工重选元数据时废弃旧 pending 决策，避免 stale 卡片继续可回复。"""
    from media_pilot.repository.models import utc_now
    from media_pilot.repository.repositories import AgentDecisionRequestRepository

    repo = AgentDecisionRequestRepository(session)
    for decision in repo.list_pending_by_task(task_id):
        decision.status = "superseded"
        decision.decision = {"type": "system", "reason": reason}
        decision.decided_by = "system"
        decision.decided_at = utc_now()
    session.flush()


def _overwrite_task_metadata_fields(
    session: Session,
    *,
    task_id: str,
    title: str | None,
    year: int | None,
    media_type: str,
    confidence: float | None,
) -> None:
    """人工选择是显式纠错，必须覆盖旧的错误 task 主字段。"""
    from media_pilot.repository.repositories import IngestTaskRepository

    task = IngestTaskRepository(session).get(task_id)
    if task is None:
        return
    if title:
        task.title = title
    task.year = year
    task.media_type = media_type
    if confidence is not None:
        task.confidence = confidence
    task.failure_reason = None
    session.flush()


def _complete_manual_selection_run(session: Session, task_id: str) -> None:
    """手动选择确定性发布成功后，收口旧 waiting/active AgentRun。"""
    from media_pilot.repository.repositories import AgentRunRepository

    run_repo = AgentRunRepository(session)
    run = run_repo.get_active_or_waiting_by_task(task_id)
    if run is None:
        return
    run_repo.update_status(
        run,
        status="completed",
        current_step="manual_metadata_published",
    )
    run.error_message = None
    session.flush()


def _write_system_message(session: Session, task_id: str, content: str) -> None:
    """在 Agent 对话中写入系统生成的消息。"""
    from media_pilot.repository.repositories import (
        AgentMessageCreate,
        AgentMessageRepository,
        AgentRunRepository,
    )

    run_repo = AgentRunRepository(session)
    run = run_repo.get_active_or_waiting_by_task(task_id)
    if run is None:
        return

    msg_repo = AgentMessageRepository(session)
    msg_repo.create(AgentMessageCreate(
        run_id=run.id,
        role="assistant",
        content=content,
    ))


# 用户取消人工选择继续处理时, 写入 task.failure_reason / AgentRun.error_message 的固定文案。
MANUAL_SELECTION_CANCEL_FAILURE_REASON = "用户取消人工选择后的继续处理"


def handle_manual_selection_cancel(
    session: Session,
    decision,
) -> dict:
    """取消 manual_selection_blocked 决策的继续处理。

    确定性路径, 不调用 LLM:
    - task.status / current_step = agent_failed, failure_reason 写明取消原因。
    - run.status = failed, current_step = agent_failed。
    - decision 与 user message 已经在 reply_to_decision 中先保存, 这里只切换终态。

    返回结构化结果, 便于 reply_to_decision 构造 AgentRunResult。
    """
    from media_pilot.orchestration.state_machine import IngestTaskStatus
    from media_pilot.repository.repositories import (
        AgentRunRepository,
        IngestTaskRepository,
    )

    task_repo = IngestTaskRepository(session)
    task = task_repo.get(decision.task_id)
    if task is not None:
        task_repo.update_status(
            task,
            status=IngestTaskStatus.AGENT_FAILED,
            current_step=IngestTaskStatus.AGENT_FAILED,
            failure_reason=MANUAL_SELECTION_CANCEL_FAILURE_REASON,
        )

    run_repo = AgentRunRepository(session)
    run = run_repo.get(decision.run_id)
    if run is not None:
        run_repo.update_status(
            run,
            status="failed",
            current_step=IngestTaskStatus.AGENT_FAILED,
            error_message=MANUAL_SELECTION_CANCEL_FAILURE_REASON,
        )

    session.flush()
    return {
        "outcome": "cancelled",
        "task_id": decision.task_id,
        "run_id": decision.run_id,
        "failure_reason": MANUAL_SELECTION_CANCEL_FAILURE_REASON,
    }


def handle_manual_selection_retry(
    *,
    session: Session,
    config: AppConfig,
    decision,
) -> ManualSelectResult:
    """重试 manual_selection_blocked 的确定性发布路径, 不调用 LLM。"""
    from media_pilot.repository.repositories import IngestTaskRepository
    from media_pilot.services.auto_ingest import check_eligibility

    task_repo = IngestTaskRepository(session)
    task = task_repo.get(decision.task_id)
    if task is None:
        return ManualSelectResult(status="agent_failed", summary="任务不存在")

    task_repo.update_status(
        task,
        status="processing",
        current_step="manual_selection_retry",
        failure_reason=None,
    )

    eligibility = check_eligibility(
        session=session,
        config=config,
        task_id=decision.task_id,
    )
    non_metadata_blockers = [
        r for r in eligibility.blocking_reasons
        if r not in ("no_metadata_candidates", "no_clear_metadata_winner")
    ]
    if non_metadata_blockers:
        decision_id = _create_blocked_decision(
            session,
            decision.task_id,
            eligibility,
            blocking_reasons=non_metadata_blockers,
        )
        _write_system_message(
            session,
            decision.task_id,
            "[SystemAction] 已重试手动选择后的发布，但仍被门禁阻塞："
            f"{non_metadata_blockers}",
        )
        return ManualSelectResult(
            status="waiting_user",
            summary=f"重试后仍需用户处理：{non_metadata_blockers}",
            decision_id=decision_id,
            blocking_reasons=list(eligibility.blocking_reasons),
        )

    outcome = _quick_publish(session, config, decision.task_id)
    if outcome.kind == "published":
        if _cleanup_published_output_reselect_source(session, decision.task_id):
            cleanup_result = _skipped_cleanup_result(
                "Published-output reselect source was temporary and has been removed.",
            )
        else:
            cleanup_result = _run_manual_post_publish_cleanup(
                session=session, config=config, task_id=decision.task_id,
            )
        _write_system_message(
            session,
            decision.task_id,
            "[SystemAction] 已重试手动选择后的发布，系统已自动完成入库。",
        )
        if not cleanup_result.decision_requested:
            _complete_manual_selection_run(session, decision.task_id)
        return ManualSelectResult(status="published", summary="重试发布已完成")

    if outcome.kind == "target_conflict":
        decision_id = outcome.decision_id or _create_target_conflict_decision(
            session,
            decision.task_id,
            outcome,
            title=task.title or "Unknown",
            year=task.year,
            provider="manual",
        )
        _write_system_message(
            session,
            decision.task_id,
            "[SystemAction] 已重试手动选择后的发布，但目标路径被占用，等待用户决策。",
        )
        return ManualSelectResult(
            status="waiting_user",
            summary="目标路径被占用，等待用户决策",
            decision_id=decision_id,
        )

    from media_pilot.repository.repositories import AgentRunRepository
    run = AgentRunRepository(session).get(decision.run_id)
    if run is not None:
        AgentRunRepository(session).update_status(
            run,
            status="failed",
            current_step="manual_selection_retry_failed",
            error_message=outcome.reason,
        )
    task_repo.update_status(
        task,
        status="agent_failed",
        current_step="manual_selection_retry_failed",
        failure_reason=outcome.reason,
    )
    _write_system_message(
        session,
        decision.task_id,
        f"[SystemAction] 已重试手动选择后的发布，但发布失败：{outcome.reason}",
    )
    return ManualSelectResult(
        status="agent_failed",
        summary=f"重试发布失败：{outcome.reason}",
    )


def _ensure_manual_select_run(session: Session, task_id: str):
    """为 manual select 场景确保任务有 active AgentRun。

    AgentDecisionRequest 必须挂在一个 AgentRun 上；如果任务尚无 run，
    在此创建 system run (`current_step="manual_select"`)，保证 decision
    可以被 reply 端点续跑。返回 run 对象。
    """
    from media_pilot.repository.repositories import AgentRunCreate, AgentRunRepository

    run_repo = AgentRunRepository(session)
    existing = run_repo.get_active_or_waiting_by_task(task_id)
    if existing is not None:
        return existing
    return run_repo.create(AgentRunCreate(
        task_id=task_id,
        current_step="manual_select",
    ))


def _create_blocked_decision(
    session: Session,
    task_id: str,
    eligibility,
    *,
    blocking_reasons: list[str],
) -> str:
    """当安全门禁阻塞时创建 AgentDecisionRequest(manual_selection_blocked)。

    若任务没有 active/waiting AgentRun，则先创建 system run 再创建 decision，
    保证 decision 一定挂在 run 上，reply 端点能续跑。返回 decision id（必返回）。
    """
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
        AgentRunRepository,
    )

    run = _ensure_manual_select_run(session, task_id)
    reason_text = "、".join(blocking_reasons)

    dr_repo = AgentDecisionRequestRepository(session)
    decision = dr_repo.create(AgentDecisionRequestCreate(
        run_id=run.id,
        task_id=task_id,
        decision_type="manual_selection_blocked",
        question=f"候选已选择但因以下原因无法自动发布：{reason_text}。请选择处理方式：",
        free_text_allowed=True,
        options=[
            {
                "id": "retry",
                "label": "重试",
                "description": "检查并修复问题后重试",
            },
            {
                "id": "cancel",
                "label": "取消",
                "description": "放弃本次入库",
            },
        ],
    ))

    # decision_reply 的 409 guard 要求 run.status == "waiting_user"；
    # 显式把 run 切到 waiting_user + manual_selection_blocked 步骤，
    # 保持 AgentStatusSummary.run_status 与 pending_decision_count 一致。
    # task.current_step 也对齐, 让前端时间线与 run 状态一致。
    from media_pilot.repository.models import IngestTask

    run_repo = AgentRunRepository(session)
    run_repo.update_status(
        run,
        status="waiting_user",
        current_step="manual_selection_blocked",
    )
    task = session.get(IngestTask, task_id)
    if task is not None:
        task.current_step = "manual_selection_blocked"
        session.flush()
    return decision.id


def _create_target_conflict_decision(
    session: Session,
    task_id: str,
    outcome: _PublishOutcome,
    *,
    title: str,
    year: int | None,
    provider: str,
) -> str:
    """目标冲突时创建 AgentDecisionRequest(decision_type="target_conflict")。

    若任务没有 active/waiting AgentRun，则先创建 system run 再创建 decision。
    把 task.current_step 显式设为 "target_conflict"，把 run 也切到
    waiting_user/target_conflict，让 AgentStatusSummary.run_status 与
    pending_decision_count 保持一致。
    """
    from media_pilot.orchestration.state_machine import IngestTaskStatus
    from media_pilot.repository.models import IngestTask
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
        AgentRunRepository,
    )

    run = _ensure_manual_select_run(session, task_id)

    final_file = outcome.final_target_file or ""
    conflict = outcome.conflict_code or "unknown"

    dr_repo = AgentDecisionRequestRepository(session)
    decision = dr_repo.create(AgentDecisionRequestCreate(
        run_id=run.id,
        task_id=task_id,
        decision_type="target_conflict",
        question=(
            f"目标 {final_file} 已被占用（{conflict}）。请选择处理方式。"
        ),
        free_text_allowed=False,
        options=[
            {
                "id": "overwrite_target",
                "label": "覆盖发布目标",
                "description": "由系统后端基于现有发布计划直接覆盖，不调用 LLM。",
            },
            {
                "id": "cancel_publish",
                "label": "取消本次发布",
                "description": "任务进入失败态，等待用户后续处理。",
            },
        ],
        payload={
            "final_target_dir": outcome.final_target_dir or "",
            "final_target_file": final_file,
            "conflict": conflict,
            "source": "manual_selection",
            "title": title,
            "year": year,
            "provider": provider,
        },
    ))

    # AgentDecisionRequestRepository.create 已把 task.status 切到 waiting_user；
    # 这里把 task.current_step 和 run 状态一起对齐到 "target_conflict"，
    # 让前端时间线与 AgentStatusSummary.run_status 都反映"待处理目标冲突"。
    task = session.get(IngestTask, task_id)
    if task is not None:
        task.status = IngestTaskStatus.WAITING_USER
        task.current_step = "target_conflict"
        session.flush()

    run_repo = AgentRunRepository(session)
    run_repo.update_status(
        run,
        status="waiting_user",
        current_step="target_conflict",
    )

    return decision.id
