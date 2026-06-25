"""WRITE agent tools -- persist metadata, fetch detail, and publish movies.

These tools have real filesystem and database side effects. They are
only exposed in auto_ingest mode through the tool whitelist.
"""

from __future__ import annotations

from pathlib import Path

from media_pilot.agent.tools.base import (
    PermissionLevel,
    ToolContext,
    ToolDefinition,
    ToolResult,
)


# ── persist_metadata_selection ───────────────────────────────────────

_PERSIST_METADATA_SELECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "provider_name": {"type": "string"},
        "provider_id": {"type": "string"},
        "media_type": {"type": "string", "enum": ["movie", "show"]},
        "title": {"type": "string"},
        "year": {"type": "integer"},
        "confidence": {"type": "number"},
        "original_title": {"type": "string"},
    },
    "required": ["task_id", "provider_name", "provider_id", "media_type", "title"],
    "additionalProperties": False,
}


def _handle_persist_metadata_selection(context: ToolContext, input_data: dict) -> ToolResult:
    from media_pilot.services.auto_ingest import persist_metadata_selection

    result = persist_metadata_selection(
        session=context.session,
        task_id=input_data["task_id"],
        provider_name=input_data["provider_name"],
        provider_id=input_data["provider_id"],
        media_type=input_data["media_type"],
        title=input_data["title"],
        year=input_data.get("year"),
        confidence=input_data.get("confidence"),
        original_title=input_data.get("original_title"),
    )

    return ToolResult(
        status=result.status,
        summary=result.summary,
        data={
            "candidate_id": result.candidate_id,
            "provider_name": input_data["provider_name"],
            "provider_id": input_data["provider_id"],
            "title": input_data["title"],
        },
    )


def make_persist_metadata_selection() -> ToolDefinition:
    return ToolDefinition(
        name="persist_metadata_selection",
        description="Persist the selected metadata candidate for a task. Creates a MediaCandidate record with source='agent' and updates task title/year/media_type fields.",
        parameters=_PERSIST_METADATA_SELECTION_SCHEMA,
        permission_level=PermissionLevel.WRITE,
        handler=_handle_persist_metadata_selection,
    )


# ── fetch_and_save_metadata_detail ───────────────────────────────────

_FETCH_AND_SAVE_METADATA_DETAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "provider_name": {"type": "string"},
        "provider_id": {"type": "string"},
        "media_type": {"type": "string", "enum": ["movie", "show"]},
    },
    "required": ["task_id", "provider_name", "provider_id", "media_type"],
    "additionalProperties": False,
}


def _handle_fetch_and_save_metadata_detail(context: ToolContext, input_data: dict) -> ToolResult:
    from media_pilot.services.auto_ingest import fetch_and_save_metadata_detail

    result = fetch_and_save_metadata_detail(
        session=context.session,
        config=context.config,
        task_id=input_data["task_id"],
        provider_name=input_data["provider_name"],
        provider_id=input_data["provider_id"],
        media_type=input_data["media_type"],
    )

    return ToolResult(
        status=result.status,
        summary=result.summary,
        data={
            "provider": result.provider,
            "provider_id": result.provider_id,
            "title": result.title,
            "year": result.year,
        },
    )


def make_fetch_and_save_metadata_detail() -> ToolDefinition:
    return ToolDefinition(
        name="fetch_and_save_metadata_detail",
        description="Fetch full metadata detail from a provider and save it as the task's MetadataDetail. Includes images, cast, and supplementary data.",
        parameters=_FETCH_AND_SAVE_METADATA_DETAIL_SCHEMA,
        permission_level=PermissionLevel.WRITE,
        handler=_handle_fetch_and_save_metadata_detail,
    )


# ── publish_movie_to_library ─────────────────────────────────────────

_PUBLISH_MOVIE_TO_LIBRARY_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_publish_movie_to_library(context: ToolContext, input_data: dict) -> ToolResult:
    """Publish a single-file movie to the movie library.

    Safety hard gates enforced in code (not bypassable by Agent):
    - Only supports movie tasks (refuses shows, BDMV/ISO, and multi-video directories)
    - Blocks target conflicts (no overwrite)
    - Path must be within safe roots
    - At most one main video file
    """
    import httpx

    from media_pilot.orchestration.jellyfin_movie_writer import (
        build_movie_write_plan,
        execute_movie_write,
    )
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MetadataDetailRepository,
    )
    from media_pilot.services.auto_ingest import check_eligibility
    from media_pilot.services.publish_plan_draft import _orm_detail_to_adapter
    from media_pilot.services.video_source_resolver import (
        resolve_main_video_for_publish,
    )

    task_id = input_data["task_id"]

    # ── eligibility / safety check ──────────────────────────────────
    eligibility = check_eligibility(
        session=context.session,
        config=context.config,
        task_id=task_id,
    )

    if "task_not_found" in eligibility.blocking_reasons:
        return ToolResult(status="failure", summary=f"Task {task_id} not found")

    if "source_path_not_found" in eligibility.blocking_reasons:
        return ToolResult(
            status="failure",
            summary=f"Source path not found for task {task_id}",
        )

    if "bdmv_or_iso_not_supported" in eligibility.blocking_reasons:
        return ToolResult(
            status="failure",
            summary="BDMV/ISO sources are not supported by auto publish",
            data={"requires_user": True, "reason": "bdmv_or_iso"},
        )

    if "sample_or_trailer_not_supported" in eligibility.blocking_reasons:
        return ToolResult(
            status="failure",
            summary="Sample/trailer files cannot be auto published",
            data={"requires_user": True, "reason": "sample_or_trailer"},
        )

    if "source_path_outside_safe_roots" in eligibility.blocking_reasons:
        return ToolResult(
            status="failure",
            summary="Source path is outside safe roots; refusing to publish",
            data={"requires_user": True, "reason": "unsafe_path"},
        )

    # 显式拒绝剧集: movie 工具不支持剧集发布, 剧集必须走
    # ``publish_show_to_library``. eligibility 不再产生
    # ``media_type_not_movie:show`` 阻塞原因 (避免 LLM 在 LLM 走
    # auto_ingest 路径时把 show 任务误判成 not_movie 失败),
    # 所以这里直接看 task.media_type 而不是 eligibility 字符串.
    task_repo = IngestTaskRepository(context.session)
    task = task_repo.get(task_id)
    if task is not None and task.media_type != "movie":
        return ToolResult(
            status="failure",
            summary=(
                f"publish_movie_to_library does not support media_type="
                f"{task.media_type}: {task_id}. Use publish_show_to_library for shows."
            ),
            data={
                "requires_user": True,
                "reason": "not_movie",
                "media_type": task.media_type,
            },
        )

    if "multiple_video_files_not_supported" in eligibility.blocking_reasons:
        return ToolResult(
            status="failure",
            summary="Multiple video files not supported by auto publish",
            data={"requires_user": True, "reason": "multiple_videos"},
        )

    if "no_video_files_found" in eligibility.blocking_reasons:
        return ToolResult(
            status="failure",
            summary="No video files found at source path",
            data={"requires_user": True, "reason": "no_videos"},
        )

    # ── idempotency: already published ──────────────────────────────
    task_repo = IngestTaskRepository(context.session)
    task = task_repo.get(task_id)
    if task is None:
        return ToolResult(status="failure", summary=f"Task {task_id} not found")

    if task.status == "library_import_complete":
        return ToolResult(
            status="success",
            summary=f"Task {task_id} is already published (library_import_complete). Idempotent no-op.",
            data={"already_published": True, "task_id": task_id},
        )

    if "no_metadata_candidates" in eligibility.blocking_reasons:
        return ToolResult(
            status="failure",
            summary="No metadata candidates exist for this task. Search for metadata before publishing.",
            data={"requires_user": True, "reason": "no_metadata_candidates"},
        )

    if "no_clear_metadata_winner" in eligibility.blocking_reasons:
        return ToolResult(
            status="failure",
            summary="No clear metadata winner — candidates have low confidence or are too close to auto-select. Manual metadata selection required.",
            data={
                "requires_user": True,
                "reason": "no_clear_metadata_winner",
                "candidate_count": eligibility.candidate_count,
                "confidence_threshold": eligibility.confidence_threshold,
                "margin": eligibility.margin,
            },
        )

    detail_repo = MetadataDetailRepository(context.session)
    orm_detail = detail_repo.get_for_task(task_id)
    if orm_detail is None:
        return ToolResult(
            status="failure",
            summary="No metadata detail found; fetch and save detail before publishing",
            data={"requires_user": True, "reason": "no_metadata_detail"},
        )

    adapter_detail = _orm_detail_to_adapter(orm_detail)

    # ── determine source path ───────────────────────────────────────
    # 用共享解析器 (services/video_source_resolver.py) 拿到真实主视频文件.
    # watch 目录型单电影输入 (e.g. `Warcraft ... [YTS.MX]/foo.mkv`) 会在
    # 解析器里自动补写 MediaSourceSelection, 后续 publish 不会再走目录.
    resolve_result = resolve_main_video_for_publish(
        context.session, task, config=context.config,
    )
    if resolve_result.error_code is not None or resolve_result.video_path is None:
        return ToolResult(
            status="failure",
            summary=(
                f"Cannot resolve main video for task {task_id}: "
                f"{resolve_result.error_code}: {resolve_result.error_message}"
            ),
            data={
                "requires_user": True,
                "reason": resolve_result.error_code or "invalid_video_source",
                "message": resolve_result.error_message,
                "retryable": True,
            },
        )
    video_source = resolve_result.video_path

    # ── build plan and check conflicts ──────────────────────────────
    from media_pilot.services.library_root_resolver import resolve_library_root

    plan = build_movie_write_plan(
        movies_dir=resolve_library_root(
            context.config, media_type="movie", provider=orm_detail.provider,
        ),
        source_path=video_source,
        detail=adapter_detail,
        task_id=task.id,
        provider=orm_detail.provider,
    )

    from media_pilot.orchestration.jellyfin_movie_writer import detect_movie_write_conflict
    conflict = detect_movie_write_conflict(plan)
    if conflict is not None:
        # 直接创建 AgentDecisionRequest(decision_type="target_conflict")，
        # 由右侧 Agent 面板承载；不再返回无消费者的 requires_user 信号。
        from media_pilot.repository.repositories import (
            AgentDecisionRequestCreate,
            AgentDecisionRequestRepository,
        )

        dr_repo = AgentDecisionRequestRepository(context.session)
        try:
            decision = dr_repo.create(AgentDecisionRequestCreate(
                run_id=context.run_id,
                task_id=task_id,
                decision_type="target_conflict",
                question=(
                    f"目标 {plan.final_target_file} 已被占用（{conflict}）。"
                    "请选择处理方式。"
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
                    "final_target_dir": str(plan.final_target_dir),
                    "final_target_file": str(plan.final_target_file),
                    "conflict": conflict,
                },
            ))
        except ValueError as exc:
            # 已存在 pending 决策时不再重复创建
            return ToolResult(
                status="failure",
                summary=f"Target conflict detected: {conflict}. Existing decision in progress.",
                data={"conflict": conflict},
            )

        # target_conflict 决策在 AgentDecisionRequestRepository.create 副作用里
        # 已把 task.status 切到 waiting_user；这里把 current_step 也对齐到
        # "target_conflict"，让前端时间线能反映"待处理目标冲突"。
        task_repo.update_status(
            task, status="waiting_user", current_step="target_conflict",
        )
        context.session.flush()

        return ToolResult(
            status="success",
            summary=f"Target conflict detected: {conflict}. Awaiting user decision.",
            data={
                "decision_requested": True,
                "decision_id": decision.id,
                "decision_type": "target_conflict",
                "conflict": conflict,
                "final_target_dir": str(plan.final_target_dir),
                "final_target_file": str(plan.final_target_file),
            },
        )

    # ── execute publish ─────────────────────────────────────────────
    try:
        with httpx.Client(timeout=10) as client:
            write_result = execute_movie_write(
                context.session,
                task_id=task.id,
                source_path=video_source,
                detail=adapter_detail,
                plan=plan,
                client=client,
                provider=orm_detail.provider,
            )
    except Exception as exc:
        return ToolResult(
            status="failure",
            summary=f"Movie publish failed: {exc}",
            data={"error": str(exc)},
        )

    if write_result.status in ("succeeded", "warning"):
        task.status = "library_import_complete"
        task.current_step = "library_import_complete"
        context.session.flush()

        warnings_text = f" ({len(write_result.warnings)} warning(s))" if write_result.warnings else ""
        return ToolResult(
            status="success",
            summary=f"Movie published to library: {plan.final_target_dir}{warnings_text}",
            data={
                "status": write_result.status,
                "final_target_dir": str(plan.final_target_dir),
                "final_target_file": str(plan.final_target_file),
                "warnings": write_result.warnings,
            },
        )

    if write_result.status == "target_conflict":
        # execute_movie_write 在没有 force_overwrite 时遇到冲突 → 走 target_conflict 决策。
        # 该路径通常被前置 detect_movie_write_conflict 拦截，但若并发或外部修改导致
        # 在执行阶段才出现冲突，仍按 AgentDecisionRequest 形式落库。
        from media_pilot.repository.repositories import (
            AgentDecisionRequestCreate,
            AgentDecisionRequestRepository,
        )

        dr_repo = AgentDecisionRequestRepository(context.session)
        try:
            decision = dr_repo.create(AgentDecisionRequestCreate(
                run_id=context.run_id,
                task_id=task_id,
                decision_type="target_conflict",
                question=(
                    f"目标 {plan.final_target_file} 在执行阶段出现冲突。"
                    "请选择处理方式。"
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
                    "final_target_dir": str(plan.final_target_dir),
                    "final_target_file": str(plan.final_target_file),
                    "conflict": "execute_time_conflict",
                },
            ))
        except ValueError:
            return ToolResult(
                status="failure",
                summary="Target conflict detected during execute; existing decision in progress.",
            )

        task_repo.update_status(
            task, status="waiting_user", current_step="target_conflict",
        )
        context.session.flush()

        return ToolResult(
            status="success",
            summary="Target conflict detected during execute. Awaiting user decision.",
            data={
                "decision_requested": True,
                "decision_id": decision.id,
                "decision_type": "target_conflict",
            },
        )

    return ToolResult(
        status="failure",
        summary=f"Movie publish failed with status: {write_result.status}",
        data={"status": write_result.status, "warnings": write_result.warnings},
    )


def make_publish_movie_to_library() -> ToolDefinition:
    return ToolDefinition(
        name="publish_movie_to_library",
        description="Publish a single-file movie to the Jellyfin movie library. Only supports movie tasks — refuses shows, BDMV/ISO, and multi-video directories. Copies same-stem subtitles alongside the video.",
        parameters=_PUBLISH_MOVIE_TO_LIBRARY_SCHEMA,
        permission_level=PermissionLevel.WRITE,
        handler=_handle_publish_movie_to_library,
    )


# ── revoke_publish ─────────────────────────────────────────────────────

_REVOKE_PUBLISH_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "skip_post_revoke_decision": {"type": "boolean"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_revoke_publish(context: ToolContext, input_data: dict) -> ToolResult:
    """Revoke the published media library outputs for a task.

    Deletes only the media library publish directory and cleans up the
    invalid current publish context. Does NOT delete task input or source
    files.

    When ``skip_post_revoke_decision`` is false (default), a post_revoke_action
    decision is created for the user (reingest with new search, reingest with
    existing metadata, or delete task input via separate confirmation).

    When ``skip_post_revoke_decision`` is true, the revoke cleans up the
    publish context without creating a decision, so the Agent can continue
    searching and republishing in the same run (clear correction intent).
    """
    from media_pilot.orchestration.revoke_publish import (
        check_revoke_publish,
        execute_revoke_publish,
    )

    task_id = input_data["task_id"]
    skip_decision = input_data.get("skip_post_revoke_decision", False)

    check = check_revoke_publish(context.session, task_id=task_id)
    if not check.allowed:
        return ToolResult(
            status="failure",
            summary=check.outcome_description,
            data={"allowed": False},
        )

    result = execute_revoke_publish(
        context.session, task_id=task_id,
        skip_post_revoke_decision=skip_decision,
        existing_run_id=context.run_id,
    )

    return ToolResult(
        status="success",
        summary=f"Revoke completed: {result.outcome}",
        data={
            "status": result.status,
            "outcome": result.outcome,
            "decision_id": result.decision_id,
            "waiting_for_post_revoke_action": result.status == "waiting_user",
        },
    )


def make_revoke_publish() -> ToolDefinition:
    return ToolDefinition(
        name="revoke_publish",
        description="Revoke (undo) the media library publish for a task. Deletes only published library outputs, NOT task input or source files. After revoke, a post-revoke action decision is created for the user to choose: reingest with new search, reingest with existing metadata, or delete task input via separate confirmation.",
        parameters=_REVOKE_PUBLISH_SCHEMA,
        permission_level=PermissionLevel.WRITE,
        handler=_handle_revoke_publish,
    )


# ── handle_source_cleanup ────────────────────────────────────────────────

_HANDLE_SOURCE_CLEANUP_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_source_cleanup(context: ToolContext, input_data: dict) -> ToolResult:
    """入库收尾源文件清理工具 — 由 Agent 在发布成功后或通用输入中调用.

    行为由 source_cleanup_policy (keep / ask / trash) 决定:
    - keep: 记录 source_input_kept OperationRecord, 不移动任何文件.
    - ask: 创建 source_cleanup_action 决策, 选项 keep_input / trash_input / delete_input.
    - trash: trash_dir 未配置或预检不通过 → 降级为 ask; 通过则直接移动整个任务输入节点.

    任务状态门禁: 仅当 task.status == "library_import_complete" 且存在
    成功的 WriteResult 时执行; 否则拒绝. 工具执行失败 (move 失败) 不会
    把任务回退到入库失败, OperationRecord 记录 source_input_cleanup_failed.
    """
    from media_pilot.repository.models import (
        OperationRecord,
    )
    from media_pilot.repository.repositories import (
        AgentDecisionRequestRepository,
        AgentRunRepository,
        IngestTaskRepository,
        MediaSourceSelectionRepository,
        WriteResultRepository,
    )
    from media_pilot.services.app_settings import (
        SOURCE_CLEANUP_POLICY_ASK,
        SOURCE_CLEANUP_POLICY_KEEP,
        SOURCE_CLEANUP_POLICY_TRASH,
        AppSettingsService,
    )
    from media_pilot.services.source_cleanup_preflight import (
        check_source_cleanup_preflight,
        execute_source_cleanup,
        resolve_task_input_node,
    )

    task_id = input_data["task_id"]

    task_repo = IngestTaskRepository(context.session)
    task = task_repo.get(task_id)
    if task is None:
        return ToolResult(
            status="failure",
            summary=f"Task {task_id} not found",
        )

    # 任务状态门禁: 任务的"已发布"事实由 WriteResult 决定, task.status 在
    # agent 运行中会被 runner 短暂切到 "agent_running" (freeform 输入
    # library_import_complete 任务时尤其如此). 因此这里只把 task.status
    # 用作"明显未发布"的快速过滤: 排除 discovered / agent_start / agent_running
    # 且没有 WriteResult 的情况. 真正权威的判定是下面 WriteResult 的存在与状态.
    if task.status in ("discovered", "agent_start", "agent_failed", "failed", "cancelled"):
        return ToolResult(
            status="failure",
            summary=(
                f"Task {task_id} is not in a post-publish state "
                f"(status={task.status}); refuse to clean up source."
            ),
            data={"task_status": task.status, "requires_user": True},
        )

    write_result_repo = WriteResultRepository(context.session)
    write_result = write_result_repo.get_for_task(task_id)
    if write_result is None or write_result.status not in ("succeeded", "warning"):
        return ToolResult(
            status="failure",
            summary=(
                f"No successful write result for task {task_id}; "
                "source cleanup only applies after a successful publish."
            ),
            data={"write_result_status": write_result.status if write_result else None},
        )

    source_sel_repo = MediaSourceSelectionRepository(context.session)
    selection = source_sel_repo.get_for_task(task_id)

    app_settings_svc = AppSettingsService(_settings_session_factory(context.config))
    app_settings = app_settings_svc.read_using_session(context.session)
    policy = app_settings.source_cleanup_policy

    if policy == SOURCE_CLEANUP_POLICY_KEEP:
        kept_input_node = resolve_task_input_node(
            task=task, selection=selection,
        )
        context.session.add(OperationRecord(
            task_id=task_id,
            operation_type="source_input_kept",
            permission_level="write",
            source_path=str(kept_input_node) if kept_input_node else str(task.source_path or ""),
            status="succeeded",
            details={"policy": policy, "reason": "policy=keep"},
        ))
        context.session.flush()
        return ToolResult(
            status="success",
            summary="Source files retained per keep policy.",
            data={"action": "kept", "policy": policy},
        )

    if policy == SOURCE_CLEANUP_POLICY_ASK:
        decision = _create_source_cleanup_decision(
            session=context.session,
            task_id=task_id,
            run_id=context.run_id,
            ask_reason="policy=ask",
        )
        return ToolResult(
            status="success",
            summary="Source cleanup awaiting user decision.",
            data={
                "decision_requested": True,
                "decision_id": decision.id,
                "decision_type": "source_cleanup_action",
            },
        )

    if policy == SOURCE_CLEANUP_POLICY_TRASH:
        preflight = check_source_cleanup_preflight(
            config=context.config,
            task=task,
            selection=selection,
        )
        if not preflight.allowed:
            decision = _create_source_cleanup_decision(
                session=context.session,
                task_id=task_id,
                run_id=context.run_id,
                ask_reason=f"trash_unavailable:{preflight.reason}",
            )
            return ToolResult(
                status="success",
                summary=(
                    f"Trash preflight refused ({preflight.reason}); "
                    "degraded to ask."
                ),
                data={
                    "decision_requested": True,
                    "decision_id": decision.id,
                    "decision_type": "source_cleanup_action",
                    "preflight_reason": preflight.reason,
                },
            )
        result = execute_source_cleanup(
            config=context.config, preflight=preflight,
        )
        if result.success:
            context.session.add(OperationRecord(
                task_id=task_id,
                operation_type="source_input_trashed",
                permission_level="write",
                source_path=str(result.source_path) if result.source_path else "",
                target_path=str(result.trash_target) if result.trash_target else "",
                status="succeeded",
                details={"policy": policy},
            ))
            context.session.flush()
            return ToolResult(
                status="success",
                summary=(
                    f"Source input moved to trash: {result.trash_target}"
                ),
                data={
                    "action": "trashed",
                    "policy": policy,
                    "trash_target": str(result.trash_target) if result.trash_target else None,
                },
            )
        # 移动失败 — 记录失败, 任务状态保持 library_import_complete
        context.session.add(OperationRecord(
            task_id=task_id,
            operation_type="source_input_cleanup_failed",
            permission_level="write",
            source_path=str(result.source_path) if result.source_path else "",
            status="failed",
            details={"policy": policy, "reason": result.reason},
        ))
        context.session.flush()
        return ToolResult(
            status="failure",
            summary=f"Source trash move failed: {result.reason}",
            data={
                "action": "trash_failed",
                "policy": policy,
                "reason": result.reason,
                "task_status_unchanged": task.status,
            },
        )

    # 未知策略: 退回 ask, 不修改任务状态
    decision = _create_source_cleanup_decision(
        session=context.session,
        task_id=task_id,
        run_id=context.run_id,
        ask_reason=f"unknown_policy:{policy}",
    )
    return ToolResult(
        status="success",
        summary=f"Unknown policy {policy}; degraded to ask.",
        data={
            "decision_requested": True,
            "decision_id": decision.id,
            "decision_type": "source_cleanup_action",
        },
    )


# 为 AppSettingsService 提供一个轻量 session_factory — 工具调用上下文已有
# context.session, 但 AppSettingsService 仍要求 sessionmaker. 实际
# read_using_session 只读 context.session, factory 仅用于类型兼容.
def _settings_session_factory(config):
    from media_pilot.repository.database import create_session_factory
    return create_session_factory(config)


def _create_source_cleanup_decision(
    *,
    session,
    task_id: str,
    run_id: str | None,
    ask_reason: str,
):
    """创建 source_cleanup_action AgentDecisionRequest 并把 run 切到 waiting_user.

    若 run_id 为空或对应 run 不存在, 自动创建一个 system run.
    AgentDecisionRequestRepository.create 默认会把 task.status 切到 waiting_user,
    但源文件清理只是入库后的旁路清理, 不应改写核心入库结果 — 显式把
    task.status / current_step 恢复为 library_import_complete, 配合
    current_step="source_cleanup_decision" 标记 "已发决策等用户选".
    """
    from media_pilot.repository.models import IngestTask
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
        AgentRunCreate,
        AgentRunRepository,
    )

    run_repo = AgentRunRepository(session)
    if run_id:
        run = run_repo.get(run_id)
    else:
        run = None
    if run is None:
        run = run_repo.create(AgentRunCreate(
            task_id=task_id,
            current_step="source_cleanup_decision",
        ))

    dr_repo = AgentDecisionRequestRepository(session)
    decision = dr_repo.create(AgentDecisionRequestCreate(
        run_id=run.id,
        task_id=task_id,
        decision_type="source_cleanup_action",
        question=(
            "源文件清理方式？请选择下一步操作："
        ),
        free_text_allowed=False,
        options=[
            {
                "id": "keep_input",
                "label": "保留源文件",
                "description": "保留任务输入文件，不做清理。",
            },
            {
                "id": "trash_input",
                "label": "移入回收区",
                "description": "由系统后端预检后整体移入回收区。",
            },
            {
                "id": "delete_input",
                "label": "进入删除预检",
                "description": "进入现有删除任务输入预检流程，仍需二次确认。",
            },
        ],
        payload={
            "reason": ask_reason,
        },
    ))

    # 把 task.status 恢复为 library_import_complete, 不让入库后的旁路
    # 清理把任务误标为 waiting_user. current_step 改为
    # "source_cleanup_decision" 反映"已发出决策等用户选".
    task = session.get(IngestTask, task_id)
    if task is not None:
        task.status = "library_import_complete"
        task.current_step = "source_cleanup_decision"
        session.flush()

    run_repo.update_status(
        run,
        status="waiting_user",
        current_step="source_cleanup_decision",
    )
    session.flush()
    return decision


def make_handle_source_cleanup() -> ToolDefinition:
    return ToolDefinition(
        name="handle_source_cleanup",
        description=(
            "Post-publish source cleanup tool. Run after a task is "
            "library_import_complete to process source files per "
            "source_cleanup_policy (keep/ask/trash). keep records retention; "
            "trash moves the whole task input node to trash_dir when preflight "
            "passes; otherwise (or when policy=ask) creates a source_cleanup_action "
            "decision for the user. Refuses tasks that are not library_import_complete."
        ),
        parameters=_HANDLE_SOURCE_CLEANUP_SCHEMA,
        permission_level=PermissionLevel.WRITE,
        handler=_handle_source_cleanup,
    )
