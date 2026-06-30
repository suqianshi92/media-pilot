"""Show ingest Agent 工具 — 剧集结构准备 + 剧集发布.

边界:
- ``prepare_show_structure`` 在元数据搜索和发布之间调用: 把任务输入
  节点解析为 ``EpisodeMapping`` 任务事实, 跨季 / 稀疏集 / Season 0
  / 单文件多集 / 无法解析都不自动发布.
- ``publish_show_to_library`` 是独立的 WRITE 工具, 读取
  ``MetadataDetail`` 和 ``EpisodeMapping`` 构建发布计划. 复用现有
  ``jellyfin_show_writer``, 但不恢复旧 deterministic show workflow.
- 剧集字幕只自动携带 episode same-stem 字幕; 不明确字幕不阻塞.
- 目标冲突创建 ``target_conflict`` 决策; 失败记录失败原因.
"""

from __future__ import annotations

import logging
from pathlib import Path

from media_pilot.agent.tools.base import (
    PermissionLevel,
    ToolContext,
    ToolDefinition,
    ToolResult,
)

logger = logging.getLogger(__name__)


# ── prepare_show_structure ──────────────────────────────────────

_PREPARE_SHOW_STRUCTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_prepare_show_structure(
    context: ToolContext, input_data: dict,
) -> ToolResult:
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MetadataDetailRepository,
    )
    from media_pilot.services.show_structure_analysis import (
        BLOCK_REASON_ABSOLUTE_EPISODE_NEEDS_METADATA,
        BLOCK_REASON_NOT_SHOW_STRUCTURE,
        STATUS_AUTO_PUBLISHABLE,
        STATUS_ABSOLUTE_EPISODE_NEEDS_METADATA,
        derive_season_coverage_from_detail,
        prepare_show_episode_mapping,
    )

    task_id = input_data["task_id"]
    task = IngestTaskRepository(context.session).get(task_id)
    if task is None:
        return ToolResult(status="failure", summary=f"Task {task_id} not found")
    if not task.source_path:
        return ToolResult(
            status="failure", summary=f"Task {task_id} has no source_path",
        )

    # 绝对集数映射验证: 当 task 已有 MetadataDetail (例如 retry run 期间
    # 已搜索过 TMDB show), 从 payload.raw.seasons 派生 season coverage.
    # 无 MetadataDetail → 不传 coverage, 绝对集数映射走保守路径.
    detail_repo = MetadataDetailRepository(context.session)
    orm_detail = detail_repo.get_for_task(task_id)
    season_coverage: dict[int, int] | None = None
    if orm_detail is not None and orm_detail.media_type == "show":
        season_coverage = derive_season_coverage_from_detail(orm_detail.payload)

    # media_type 已经在 prepare_complex_input_decision 阶段被识别; 这里
    # 仅做兜底, media_type 不是 "show" 时也允许 (复杂输入里看 path 模式).
    result = prepare_show_episode_mapping(
        session=context.session, task_id=task_id,
        season_coverage=season_coverage,
    )

    # 防御性旁路: LLM 误把 prepare_show_structure 用在非 show 任务上, 且
    # 输入里完全没有剧集结构 (SxxExx / season 关键词 / 多集)。这种情况下
    # 不应该把 task 切 agent_failed — 这是普通电影, 走电影主线即可。
    # 真正 show 任务碰到 no_clear_show_structure 仍按下面的失败路径处理。
    if (
        task.media_type != "show"
        and result.block_reason == BLOCK_REASON_NOT_SHOW_STRUCTURE
    ):
        return ToolResult(
            status="failure",
            summary=(
                f"Task {task_id} is not a show (media_type="
                f"{task.media_type!r}); prepare_show_structure is a no-op. "
                "Continue with the movie path."
            ),
            data={
                "auto_publishable": False,
                "block_reason": result.block_reason,
                "not_a_show": True,
                "media_type": task.media_type,
            },
        )

    if result.status == STATUS_AUTO_PUBLISHABLE:
        # 同步 task.media_type = show, 让后续 search_metadata 知道用 show.
        if task.media_type != "show":
            task.media_type = "show"
            context.session.flush()
        return ToolResult(
            status="success",
            summary=(
                f"Show structure ready: {result.episode_range} "
                f"({len(result.entries)} episode(s))"
            ),
            data={
                "auto_publishable": True,
                "season": result.season,
                "episode_range": result.episode_range,
                "episode_count": len(result.entries),
                "detected_show_title": result.detected_show_title,
                "candidate_video_count": result.candidate_video_count,
                "excluded_video_count": result.excluded_video_count,
                "mapping_mode": result.mapping_mode,
                "mapping_mode_label": (
                    "absolute_episode_numbering"
                    if result.mapping_mode == "absolute"
                    else "standard_sxxexx"
                ),
                "episode_mappings": [
                    {
                        "season": e.season,
                        "episode": e.episode,
                        "file_path": e.file_path,
                        "source": e.source,
                    }
                    for e in result.entries
                ],
            },
        )

    # show-like + 绝对集数, 但还没有 metadata detail / season coverage:
    # 这是"需要 metadata 才能继续验证"的可继续状态, 不是终态失败.
    if result.status == STATUS_ABSOLUTE_EPISODE_NEEDS_METADATA:
        if task.media_type != "show":
            task.media_type = "show"
            context.session.flush()
        return ToolResult(
            status="success",
            summary=(
                "Show structure looks like absolute episode numbering, "
                "but metadata detail is required to validate season "
                "coverage. Continue metadata search/fetch for the show."
            ),
            data={
                "auto_publishable": False,
                "requires_metadata_detail": True,
                "block_reason": BLOCK_REASON_ABSOLUTE_EPISODE_NEEDS_METADATA,
                "block_reason_label": _human_block_reason_label(
                    BLOCK_REASON_ABSOLUTE_EPISODE_NEEDS_METADATA
                ),
                "status": result.status,
                "candidate_video_count": result.candidate_video_count,
                "excluded_video_count": result.excluded_video_count,
                "mapping_mode": result.mapping_mode,
                "episode_range": result.episode_range,
                "media_type": "show",
            },
        )

    # 不支持的剧集结构 — 把 task.current_step 切到对应 block_reason,
    # 写入 failure_reason, 让后续 Agent / 前端能直接看到失败原因.
    block_reason = result.block_reason or "unsupported_show_structure"
    if block_reason != BLOCK_REASON_NOT_SHOW_STRUCTURE and task.media_type != "show":
        task.media_type = "show"
    IngestTaskRepository(context.session).update_status(
        task,
        status="agent_failed",
        current_step=block_reason,
        failure_reason=block_reason,
    )
    if context.run_id:
        from media_pilot.repository.repositories import AgentRunRepository
        run_repo = AgentRunRepository(context.session)
        run = run_repo.get(context.run_id)
        if run is not None:
            run_repo.update_status(
                run,
                status="failed",
                current_step=block_reason,
                error_message=block_reason,
            )
    context.session.flush()
    return ToolResult(
        status="failure",
        summary=f"Show structure not auto-publishable: {block_reason}",
        data={
            "auto_publishable": False,
            "block_reason": block_reason,
            "block_reason_label": _human_block_reason_label(block_reason),
            "status": result.status,
            "candidate_video_count": result.candidate_video_count,
            "excluded_video_count": result.excluded_video_count,
            "mapping_mode": result.mapping_mode,
        },
    )


def _human_block_reason_label(block_reason: str) -> str:
    """结构化 block_reason → 工作台可读文案 key.

    前端拿到后用 i18n 翻译; 不暴露 raw JSON 也不写"max_steps" / 内部
    错误.
    """
    mapping = {
        "cross_season_not_supported": "show_block_cross_season",
        "sparse_episodes_not_supported": "show_block_sparse_episodes",
        "specials_season_0_not_supported": "show_block_season_0_specials",
        "multi_episode_in_single_file_not_supported": "show_block_multi_episode_in_single_file",
        "no_video_files_found": "show_block_no_video_files",
        "no_clear_show_structure": "show_block_no_clear_show_structure",
        "absolute_episode_requires_metadata_detail": "show_block_absolute_needs_metadata",
        "absolute_episode_out_of_provider_range": "show_block_absolute_out_of_range",
        "absolute_episode_sparse_not_supported": "show_block_absolute_sparse",
        "absolute_episode_ambiguous_not_supported": "show_block_absolute_ambiguous",
    }
    return mapping.get(block_reason, "show_block_unknown")


def _human_conflict_label(conflict: str | None) -> str:
    """target_conflict 上下文的人类可读文案 key.

    解析"target_episode_file_exists:S01E05" → "show_conflict_episode_file"。
    不暴露原始字符串给前端; 由前端 i18n 翻译.
    """
    if not conflict:
        return "show_conflict_unknown"
    if conflict.startswith("target_episode_file_exists:"):
        return "show_conflict_episode_file"
    if conflict.startswith("target_episode_nfo_exists:"):
        return "show_conflict_episode_nfo"
    if conflict.startswith("target_episode_subtitle_exists:"):
        return "show_conflict_episode_subtitle"
    if conflict == "show_identity_mismatch":
        return "show_conflict_identity_mismatch"
    return "show_conflict_unknown"


def make_prepare_show_structure() -> ToolDefinition:
    return ToolDefinition(
        name="prepare_show_structure",
        description=(
            "Analyze the task input node for show structure (single episode "
            "or same-season continuous multi-episode). Persists "
            "EpisodeMapping records when auto-publishable, otherwise returns "
            "the block_reason (cross_season / sparse_episodes / "
            "season_0 / multi_episode_in_single_file / no_clear_show_structure). "
            "Call this between complex_input_decision and search_metadata for "
            "show tasks."
        ),
        parameters=_PREPARE_SHOW_STRUCTURE_SCHEMA,
        permission_level=PermissionLevel.DRAFT,
        handler=_handle_prepare_show_structure,
    )


# ── publish_show_to_library ─────────────────────────────────────

_PUBLISH_SHOW_TO_LIBRARY_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_publish_show_to_library(
    context: ToolContext, input_data: dict,
) -> ToolResult:
    import httpx

    from media_pilot.orchestration.jellyfin_show_writer import (
        EpisodeTarget,
        build_show_write_plan,
        detect_show_write_conflict,
        execute_show_write,
    )
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
        EpisodeMappingRepository,
        IngestTaskRepository,
        MetadataDetailRepository,
    )
    from media_pilot.services.publish_plan_draft import _orm_detail_to_adapter
    from media_pilot.services.show_structure_analysis import (
        STATUS_AUTO_PUBLISHABLE,
        derive_season_coverage_from_detail,
        get_persisted_show_structure,
        prepare_show_episode_mapping,
    )
    from media_pilot.services.task_input_analysis import (
        SUBTITLE_EXTENSIONS,
        _find_same_stem_subtitles,
    )

    task_id = input_data["task_id"]

    # ── 1) 任务状态门禁 ──────────────────────────────────────
    task_repo = IngestTaskRepository(context.session)
    task = task_repo.get(task_id)
    if task is None:
        return ToolResult(status="failure", summary=f"Task {task_id} not found")

    if task.status == "library_import_complete":
        return ToolResult(
            status="success",
            summary=(
                f"Task {task_id} is already published (library_import_complete). "
                "Idempotent no-op."
            ),
            data={"already_published": True, "task_id": task_id},
        )

    if task.status in ("agent_failed", "failed", "cancelled"):
        return ToolResult(
            status="failure",
            summary=(
                f"Task {task_id} is in terminal state {task.status}; "
                "refuse to publish."
            ),
            data={"task_status": task.status, "requires_user": True},
        )

    # ── 2) EpisodeMapping 必须存在 ─────────────────────────
    show_struct = get_persisted_show_structure(
        session=context.session, task_id=task_id,
    )
    detail_repo = MetadataDetailRepository(context.session)
    orm_detail = detail_repo.get_for_task(task_id)
    if show_struct is None or show_struct.status != STATUS_AUTO_PUBLISHABLE:
        if orm_detail is None:
            return ToolResult(
                status="failure",
                summary=(
                    f"No persisted EpisodeMapping for task {task_id}. "
                    "Run prepare_show_structure first."
                ),
                data={"requires_user": True, "reason": "no_episode_mapping"},
            )
        if orm_detail.media_type != "show":
            return ToolResult(
                status="failure",
                summary=(
                    f"MetadataDetail.media_type is {orm_detail.media_type!r}, "
                    "expected 'show'. publish_show_to_library only supports shows."
                ),
                data={"requires_user": True, "reason": "wrong_media_type"},
            )

        season_coverage = derive_season_coverage_from_detail(orm_detail.payload)
        recovered = prepare_show_episode_mapping(
            session=context.session,
            task_id=task_id,
            season_coverage=season_coverage,
        )
        if recovered.status == STATUS_AUTO_PUBLISHABLE:
            show_struct = recovered
        else:
            return ToolResult(
                status="failure",
                summary=(
                    f"No publishable EpisodeMapping for task {task_id}. "
                    "Show structure still needs attention after metadata detail."
                ),
                data={
                    "requires_user": True,
                    "reason": "no_episode_mapping",
                    "block_reason": recovered.block_reason,
                    "status": recovered.status,
                },
            )

    if orm_detail is None:
        return ToolResult(
            status="failure",
            summary=(
                f"No metadata detail for task {task_id}. "
                "Run fetch_and_save_metadata_detail first."
            ),
            data={"requires_user": True, "reason": "no_metadata_detail"},
        )
    if orm_detail.media_type != "show":
        return ToolResult(
            status="failure",
            summary=(
                f"MetadataDetail.media_type is {orm_detail.media_type!r}, "
                "expected 'show'. publish_show_to_library only supports shows."
            ),
            data={"requires_user": True, "reason": "wrong_media_type"},
        )

    mappings = EpisodeMappingRepository(context.session).get_by_task(task_id)
    if not mappings:
        return ToolResult(
            status="failure",
            summary=f"EpisodeMapping repository returned empty for {task_id}",
            data={"requires_user": True, "reason": "empty_episode_mapping"},
        )

    # ── 3) MetadataDetail 必须存在 ─────────────────────────
    adapter_detail = _orm_detail_to_adapter(orm_detail)

    # ── 4) 构建 EpisodeTarget 列表, 含同源字幕 ─────────────
    episodes: list[EpisodeTarget] = []
    skipped_subtitles = 0
    for m in sorted(mappings, key=lambda x: (x.season, x.episode)):
        source = Path(m.file_path)
        if not source.exists() or not source.is_file():
            return ToolResult(
                status="failure",
                summary=(
                    f"Episode file no longer exists: {source}. "
                    "Task may have lost source files."
                ),
                data={"requires_user": True, "reason": "missing_episode_file",
                      "file_path": str(source)},
            )
        same_stem_subs = _find_same_stem_subtitles(source)
        # 同源字幕: 只在 copy 视频时顺带带; 字幕文件随后由
        # execute_show_write 之后的扩展步骤复制; 当前只记录
        # skipped_subtitles 数量供摘要.
        skipped_subtitles += len([
            sub for sub in same_stem_subs
            if Path(sub.path).suffix.lower() in SUBTITLE_EXTENSIONS
            and Path(sub.path) != source
        ])
        episodes.append(EpisodeTarget(
            episode=m.episode, season=m.season,
            source_file=source, target_file=Path(""),  # 占位, 由 build_show_write_plan 覆盖
        ))

    # ── 5) 构建发布计划 + 冲突检测 ────────────────────────
    plan = build_show_write_plan(
        shows_dir=context.config.shows_dir,
        episodes=episodes,
        detail=adapter_detail,
        task_id=task.id,
        provider=orm_detail.provider,
    )
    from media_pilot.orchestration.jellyfin_show_writer import (
        detect_show_identity_conflict,
    )

    conflict = detect_show_write_conflict(plan)
    identity_conflict = detect_show_identity_conflict(plan, adapter_detail)
    effective_conflict = conflict or identity_conflict
    if effective_conflict is not None:
        # 创建 target_conflict 决策 — 与 movie publish 共享 decision_type
        if not context.run_id:
            return ToolResult(
                status="failure",
                summary="ToolContext.run_id is required to create target_conflict decision",
            )
        dr_repo = AgentDecisionRequestRepository(context.session)
        try:
            decision = dr_repo.create(AgentDecisionRequestCreate(
                run_id=context.run_id,
                task_id=task_id,
                decision_type="target_conflict",
                question=(
                    f"目标 {plan.final_target_dir} 已被占用（{effective_conflict}）。"
                    "请选择处理方式。"
                ),
                free_text_allowed=False,
                options=[
                    {
                        "id": "overwrite_target",
                        "label": "覆盖发布目标",
                        "description": (
                            "由系统后端基于现有发布计划直接覆盖本次任务涉及的"
                            " episode 文件 / NFO / 同源字幕, 不删除整个 show "
                            "或 season 目录, 不调用 LLM。"
                        ),
                    },
                    {
                        "id": "cancel_publish",
                        "label": "取消本次发布",
                        "description": "任务进入失败态，等待用户后续处理。",
                    },
                ],
                payload={
                    "final_target_dir": str(plan.final_target_dir),
                    "final_target_file": str(plan.final_target_dir),
                    "conflict": effective_conflict,
                    "media_type": "show",
                },
            ))
        except ValueError as exc:
            return ToolResult(
                status="failure",
                summary=f"Target conflict detected: {effective_conflict}. Existing decision in progress.",
                data={"conflict": effective_conflict},
            )

        task_repo.update_status(
            task, status="waiting_user", current_step="target_conflict",
        )
        context.session.flush()

        return ToolResult(
            status="success",
            summary=f"Target conflict detected: {effective_conflict}. Awaiting user decision.",
            data={
                "decision_requested": True,
                "decision_id": decision.id,
                "decision_type": "target_conflict",
                "conflict": effective_conflict,
                "conflict_label": _human_conflict_label(effective_conflict),
                "final_target_dir": str(plan.final_target_dir),
                "media_type": "show",
            },
        )

    # ── 6) 执行剧集发布 ────────────────────────────────────
    try:
        with httpx.Client(timeout=10) as client:
            write_result = execute_show_write(
                context.session,
                task_id=task.id,
                detail=adapter_detail,
                plan=plan,
                client=client,
                provider=orm_detail.provider,
            )
    except Exception as exc:
        logger.exception("Show publish failed for task %s", task_id)
        task_repo.update_status(
            task, status="agent_failed", current_step="agent_failed",
            failure_reason=f"show_publish_failed:{exc}",
        )
        return ToolResult(
            status="failure",
            summary=f"Show publish failed: {exc}",
            data={"error": str(exc), "media_type": "show"},
        )

    if write_result.status in ("succeeded", "warning"):
        # 7) 任务进入 library_import_complete
        task.status = "library_import_complete"
        task.current_step = "library_import_complete"
        task.metadata_status = "complete"
        context.session.flush()
        warnings_text = (
            f" ({len(write_result.warnings)} warning(s))"
            if write_result.warnings else ""
        )
        return ToolResult(
            status="success",
            summary=(
                f"Show published to library: {plan.final_target_dir}{warnings_text}"
            ),
            data={
                "status": write_result.status,
                "final_target_dir": str(plan.final_target_dir),
                "media_type": "show",
                "episode_count": len(plan.episodes),
                "skipped_subtitles": skipped_subtitles,
                "warnings": write_result.warnings,
            },
        )

    if write_result.status == "target_conflict":
        # 极少见: build_show_write_plan 时无冲突, execute 时出现
        # 冲突 (并发或外部修改). 与 movie 路径一致, 走 target_conflict 决策.
        if not context.run_id:
            return ToolResult(
                status="failure",
                summary="ToolContext.run_id is required to create target_conflict decision",
            )
        dr_repo = AgentDecisionRequestRepository(context.session)
        try:
            decision = dr_repo.create(AgentDecisionRequestCreate(
                run_id=context.run_id,
                task_id=task_id,
                decision_type="target_conflict",
                question=(
                    f"目标 {plan.final_target_dir} 在执行阶段出现冲突。"
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
                    "final_target_file": str(plan.final_target_dir),
                    "conflict": "execute_time_conflict",
                    "media_type": "show",
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
                "media_type": "show",
            },
        )

    # 失败: 记录失败原因, 不删除源文件, 由 agent_failed 状态表达.
    failure_reason = f"show_publish_failed:status={write_result.status}"
    task_repo.update_status(
        task, status="agent_failed", current_step="agent_failed",
        failure_reason=failure_reason,
    )
    return ToolResult(
        status="failure",
        summary=f"Show publish failed with status: {write_result.status}",
        data={
            "status": write_result.status,
            "warnings": write_result.warnings,
            "media_type": "show",
        },
    )


def make_publish_show_to_library() -> ToolDefinition:
    return ToolDefinition(
        name="publish_show_to_library",
        description=(
            "Publish a show (single episode or same-season continuous "
            "multi-episode) to the Jellyfin show library. Reads "
            "MetadataDetail and persisted EpisodeMapping records to build "
            "the write plan. Refuses tasks that lack episode mapping, "
            "metadata detail, or are not shows. Carries same-stem "
            "subtitles per episode. Creates target_conflict decision on "
            "target path collision. Does NOT delete download sources on "
            "failure."
        ),
        parameters=_PUBLISH_SHOW_TO_LIBRARY_SCHEMA,
        permission_level=PermissionLevel.WRITE,
        handler=_handle_publish_show_to_library,
    )
