"""复杂电影输入分析 / 决策生成 Agent 工具.

把多视频、样片花絮、字幕归属不明确、疑似剧集/ISO
等场景转成可回复的 AgentDecisionRequest. 选项 payload
完全由后端基于任务事实生成, 不让 LLM 拼文件路径.
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


_PREPARE_COMPLEX_INPUT_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_prepare_complex_input_decision(
    context: ToolContext, input_data: dict,
) -> ToolResult:
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
        IngestTaskRepository,
        MediaSourceSelectionRepository,
    )
    from media_pilot.services.complex_input_decision import (
        prepare_complex_input_decision,
    )

    task_id = input_data["task_id"]

    task_repo = IngestTaskRepository(context.session)
    task = task_repo.get(task_id)
    if task is None:
        return ToolResult(
            status="failure",
            summary=f"Task {task_id} not found",
        )

    if not task.source_path:
        return ToolResult(
            status="failure",
            summary=f"Task {task_id} has no source_path",
        )

    # ── 读取最新 MediaSourceSelection, 避免用户在上一轮已选择后
    #    这一轮再创建同样的决策 (循环). 已选过主视频 → 用 selected_path 扫描;
    #    payload 含 selected_subtitles (含空数组) → 字幕已处理;
    #    review_complex_input 的 user_note → 上一轮复核说明, 透传给 service 避免重复.
    selection_repo = MediaSourceSelectionRepository(context.session)
    latest_selection = selection_repo.get_for_task(task_id)
    user_selection = None
    if (
        latest_selection is not None
        and isinstance(latest_selection.payload, dict)
        and latest_selection.payload.get("selection_source") == "user_decision"
    ):
        user_selection = {
            "selected_path": latest_selection.selected_path,
            "selected_subtitles": latest_selection.payload.get("selected_subtitles"),
            "decision_type": latest_selection.payload.get("decision_type"),
            "user_note": latest_selection.payload.get("user_note"),
        }

    decision = prepare_complex_input_decision(
        config=context.config,
        source_path=Path(task.source_path),
        task_id=task.id,
        user_selection=user_selection,
    )

    # 状态分布:
    # - ready: 任务可以继续走元数据搜索/发布.
    # - show_like: 检测到 SxxExx / show-like 目录; 不在电影入库路径,
    #   也不创建 review_complex_input 阻塞. 把它当 ready 透传, data
    #   携带 is_show=True 提示 LLM 走 prepare_show_structure.
    # - decision_requested: 已经在 _create_decision 内创建 AgentDecisionRequest,
    #   AgentRun 和 IngestTask 在 repository.create 副作用里切到 waiting_user.
    # - unsupported: 已经创建 review_complex_input 决策, 等待用户说明.
    # - no_videos / unsafe_path / scan_failed: 失败, 任务由调用方推进.
    if decision.status in ("ready", "show_like"):
        # single_video_ready 路径必须把唯一主视频文件路径持久化到
        # MediaSourceSelection, 让 publish_movie_to_library 与
        # target_conflict_handler.handle_overwrite_target 后续能拿到真实
        # 视频文件 (而不是 task.source_path 这个目录). 持久化失败不应
        # 阻塞 ready: services/video_source_resolver.py 会在 publish 阶段
        # 重新扫描补写 selection.
        if decision.status == "ready":
            _persist_ready_selection(
                context=context, task=task, decision=decision,
            )
        return ToolResult(
            status="success",
            summary=(
                "Task input is ready for metadata search and publish."
                if decision.status == "ready"
                else (
                    "Task input looks like a show. "
                    "Call prepare_show_structure next."
                )
            ),
            data={
                "ready": True,
                "is_show": decision.status == "show_like",
                "reason": decision.reason,
                "video_candidates": [
                    {
                        "name": f.name,
                        "path": f.path,
                        "size_bytes": f.size_bytes,
                    }
                    for f in (decision.analysis.video_candidates if decision.analysis else [])
                ],
                "subtitle_candidates": [
                    {
                        "name": f.name,
                        "path": f.path,
                        "size_bytes": f.size_bytes,
                        "matched_by": f.matched_by,
                    }
                    for f in (decision.analysis.subtitle_candidates if decision.analysis else [])
                ],
            },
        )

    if decision.status in (
        "no_videos", "unsafe_path", "scan_failed",
    ):
        # 复杂输入扫描失败 → 任务进入 agent_failed 终态, 避免停留在
        # waiting_user / agent_running 让前端时间线和 AgentRun 状态不一致.
        # 同步把 current_step 切到 agent_failed, failure_reason 写明失败
        # 原因, 方便用户从任务工作台 / 时间线看到具体阻断原因.
        task_repo.update_status(
            task,
            status="agent_failed",
            current_step="agent_failed",
            failure_reason=decision.reason,
        )
        if context.run_id:
            from media_pilot.repository.repositories import AgentRunRepository
            run_repo = AgentRunRepository(context.session)
            run = run_repo.get(context.run_id)
            if run is not None:
                run_repo.update_status(
                    run,
                    status="failed",
                    current_step="agent_failed",
                    error_message=decision.reason,
                )
        context.session.flush()
        return ToolResult(
            status="failure",
            summary=f"Complex input analysis failed: {decision.reason}",
            data={
                "ready": False,
                "reason": decision.reason,
                "status": decision.status,
            },
        )

    # decision_requested / unsupported — 都需要创建 AgentDecisionRequest.
    # 唯一例外: review_user_note_already_consumed 表示用户已经在上一轮
    # 提供了复核说明并被 handle_review_complex_input 写入
    # MediaSourceSelection. 此时再创建 review 决策会得到一个空选项 /
    # 不允许 free_text 的 pending, 用户无法回复. 改走 agent_failed,
    # failure_reason 写明状态, 让 Agent 用最终解释收口.
    if (
        decision.status == "unsupported"
        and decision.reason == "review_user_note_already_consumed"
    ):
        task_repo.update_status(
            task,
            status="agent_failed",
            current_step="agent_failed",
            failure_reason="complex_input_review_unsupported",
        )
        if context.run_id:
            from media_pilot.repository.repositories import AgentRunRepository
            run_repo = AgentRunRepository(context.session)
            run = run_repo.get(context.run_id)
            if run is not None:
                run_repo.update_status(
                    run,
                    status="failed",
                    current_step="agent_failed",
                    error_message="complex_input_review_unsupported",
                )
        context.session.flush()
        return ToolResult(
            status="failure",
            summary=(
                "Review note already consumed in previous turn. "
                "Cannot create another review decision. "
                "Task is now agent_failed; explain to the user how to "
                "proceed (e.g. retry from task workspace)."
            ),
            data={
                "ready": False,
                "reason": "complex_input_review_unsupported",
                "status": "unsupported",
            },
        )

    if not context.run_id:
        return ToolResult(
            status="failure",
            summary=(
                f"ToolContext.run_id is required to create "
                f"{decision.decision_type} decision"
            ),
        )

    dr_repo = AgentDecisionRequestRepository(context.session)
    options_for_request = [
        {
            "id": opt.id,
            "label": opt.label,
            "description": opt.description,
            "payload": opt.payload,
        }
        for opt in decision.options
    ]
    payload: dict = {
        "reason": decision.reason,
        "source_path": str(task.source_path),
    }
    if decision.analysis is not None:
        payload["video_candidates"] = [
            {"name": f.name, "path": f.path, "size_bytes": f.size_bytes}
            for f in decision.analysis.video_candidates
        ]
        payload["auxiliary_videos"] = [
            {"name": f.name, "path": f.path, "size_bytes": f.size_bytes}
            for f in decision.analysis.auxiliary_videos
        ]
        payload["subtitle_candidates"] = [
            {"name": f.name, "path": f.path, "size_bytes": f.size_bytes}
            for f in decision.analysis.subtitle_candidates
        ]
        payload["detected"] = list(decision.analysis.detected)

    try:
        dr = dr_repo.create(AgentDecisionRequestCreate(
            run_id=context.run_id,
            task_id=task_id,
            decision_type=decision.decision_type or "review_complex_input",
            question=decision.question or "请说明如何处理此复杂输入。",
            free_text_allowed=decision.free_text_allowed,
            options=options_for_request,
            payload=payload,
        ))
    except ValueError as exc:
        # 已有 pending decision 时不再重复创建.
        return ToolResult(
            status="failure",
            summary=(
                f"Cannot create {decision.decision_type} decision: {exc}"
            ),
            data={
                "ready": False,
                "reason": "existing_pending_decision",
                "decision_type": decision.decision_type,
            },
        )

    # repository.create 已把 task.status 切到 waiting_user; 同步
    # current_step, 让前端时间线反映"卡在哪一类复杂输入决策".
    task_repo.update_status(
        task, status="waiting_user", current_step=decision.decision_type or "review_complex_input",
    )
    context.session.flush()

    return ToolResult(
        status="success",
        summary=(
            f"Complex input analysis created "
            f"{decision.decision_type} decision."
        ),
        data={
            "ready": False,
            "decision_requested": True,
            "decision_id": dr.id,
            "decision_type": dr.decision_type,
            "reason": decision.reason,
        },
    )


def make_prepare_complex_input_decision() -> ToolDefinition:
    return ToolDefinition(
        name="prepare_complex_input_decision",
        description=(
            "Analyze the task input node before metadata search. "
            "Returns ready=true for single-file movies, or creates a "
            "structured AgentDecisionRequest (select_primary_video / "
            "select_subtitles / review_complex_input) and pauses the run "
            "for the user to choose. Decision options and payloads are "
            "generated server-side; do not synthesize file paths."
        ),
        parameters=_PREPARE_COMPLEX_INPUT_DECISION_SCHEMA,
        permission_level=PermissionLevel.DRAFT,
        handler=_handle_prepare_complex_input_decision,
    )


def _persist_ready_selection(
    *,
    context: ToolContext,
    task,
    decision,
) -> None:
    """single_video_ready 路径持久化 MediaSourceSelection.

    背景: watch 目录型单电影输入 (e.g. `Warcraft ... [YTS.MX]/foo.mkv`)
    在 prepare_complex_input_decision 走到 single_video_ready 路径时,
    后续 publish_movie_to_library 与 target_conflict_handler.handle_overwrite_target
    会回退到 `task.source_path` (目录), 触发 IsADirectoryError. 这里把
    唯一主视频文件路径写进 MediaSourceSelection, 让 publish / overwrite
    链路读到 selected_path 而不是目录.

    payload key 沿用 complex_input_reply 的约定 (auxiliary / excluded /
    subtitle_candidates), 供前端任务工作台展示.
    """
    from media_pilot.repository.repositories import MediaSourceSelectionRepository

    if decision.analysis is None or not decision.analysis.video_candidates:
        return

    primary = decision.analysis.video_candidates[0]
    if not primary.path:
        return

    payload: dict = {
        "selection_source": "auto_single_video",
        "reason": decision.reason,
        "auxiliary_videos": [
            {"path": f.path, "name": f.name, "size_bytes": f.size_bytes}
            for f in decision.analysis.auxiliary_videos
        ],
        "excluded": [
            {
                "path": f.path, "name": f.name,
                "size_bytes": f.size_bytes,
                "excluded_reason": f.excluded_reason or "sample/trailer/auxiliary",
            }
            for f in decision.analysis.excluded
        ],
        "subtitle_candidates": [
            {"path": f.path, "name": f.name, "size_bytes": f.size_bytes}
            for f in decision.analysis.subtitle_candidates
        ],
    }

    sel_repo = MediaSourceSelectionRepository(context.session)
    try:
        sel_repo.save(
            task_id=task.id,
            input_path=str(Path(task.source_path)),
            selected_path=primary.path,
            confidence=1.0,
            reason=f"auto_single_video:{decision.reason}",
            payload=payload,
        )
    except Exception:
        # 持久化失败不应阻塞 ready 路径, services/video_source_resolver.py
        # 会在 publish 阶段重新扫描补写 selection.
        logger.exception(
            "_persist_ready_selection 失败: task=%s, publish 时 resolver 会再补",
            task.id,
        )
