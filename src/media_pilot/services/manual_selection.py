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

from sqlalchemy.orm import Session

from media_pilot.config import AppConfig


@dataclass(frozen=True, kw_only=True)
class ManualSelectResult:
    status: str  # "published" | "waiting_user" | "saved" | "agent_failed"
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
    from media_pilot.services.auto_ingest import (
        check_eligibility,
        fetch_and_save_metadata_detail,
        persist_metadata_selection,
    )

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
        _write_system_message(
            session, task_id,
            f"[SystemAction] 用户手动选择了 {title} ({year}) from {provider}，"
            f"系统已自动完成发布。",
        )
        return ManualSelectResult(
            status="published",
            summary=f"已选择 {title} ({year}) 并完成快捷发布",
            candidate_id=sel_result.candidate_id,
        )
    if outcome.kind == "target_conflict":
        # 目标冲突 → 创建 AgentDecisionRequest(decision_type="target_conflict")
        # 并把任务切到 waiting_user / current_step="target_conflict"
        decision_id = _create_target_conflict_decision(
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
    session.flush()
    return _PublishOutcome(
        kind="published",
        final_target_dir=str(plan.final_target_dir),
        final_target_file=str(plan.final_target_file),
    )


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
    from media_pilot.repository.models import IngestTask
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
        AgentRunRepository,
    )
    from media_pilot.orchestration.state_machine import IngestTaskStatus

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
