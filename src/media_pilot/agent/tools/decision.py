"""Agent decision tool -- request_user_decision.

Creates a structured AgentDecisionRequest and pauses the AgentRun.
"""

from __future__ import annotations

from media_pilot.agent.tools.base import (
    PermissionLevel,
    ToolContext,
    ToolDefinition,
    ToolResult,
)

_REQUEST_USER_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision_type": {"type": "string"},
        "question": {"type": "string"},
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "payload": {"type": "object"},
                },
                "required": ["id", "label"],
            },
        },
        "free_text_allowed": {"type": "boolean"},
    },
    "required": ["decision_type", "question", "options"],
    "additionalProperties": False,
}


def _handle_request_user_decision(context: ToolContext, input_data: dict) -> ToolResult:
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
    )

    decision_type = (input_data.get("decision_type") or "").strip()
    question = (input_data.get("question") or "").strip()
    options = input_data.get("options", [])
    free_text_allowed = input_data.get("free_text_allowed", False)

    if not decision_type:
        return ToolResult(status="failure", summary="decision_type is required and must be non-empty")
    if not question:
        return ToolResult(status="failure", summary="question is required and must be non-empty")

    if not isinstance(free_text_allowed, bool):
        return ToolResult(status="failure", summary="free_text_allowed must be a boolean")

    if not isinstance(options, list):
        return ToolResult(status="failure", summary="options must be a list")

    for i, opt in enumerate(options):
        if not isinstance(opt, dict):
            return ToolResult(status="failure", summary=f"options[{i}] must be an object")
        opt_id = (opt.get("id") or "").strip()
        opt_label = (opt.get("label") or "").strip()
        if not opt_id:
            return ToolResult(status="failure", summary=f"options[{i}].id is required and must be non-empty")
        if not opt_label:
            return ToolResult(status="failure", summary=f"options[{i}].label is required and must be non-empty")

    if not free_text_allowed and len(options) == 0:
        return ToolResult(
            status="failure",
            summary="At least one option is required when free_text_allowed is false",
        )

    # run_id is required — must point to a real, active AgentRun
    if not context.run_id:
        return ToolResult(status="failure", summary="ToolContext.run_id is required for request_user_decision")

    from media_pilot.repository.repositories import AgentRunRepository
    run_repo = AgentRunRepository(context.session)
    run = run_repo.get(context.run_id)
    if run is None:
        return ToolResult(
            status="failure",
            summary=f"AgentRun {context.run_id} not found",
        )
    if run.status != "active":
        return ToolResult(
            status="failure",
            summary=f"AgentRun {context.run_id} must be active (current status: {run.status})",
        )

    # Repository enforces single pending per run
    dr_repo = AgentDecisionRequestRepository(context.session)
    try:
        dr = dr_repo.create(AgentDecisionRequestCreate(
            run_id=context.run_id,
            task_id=context.task_id,
            decision_type=decision_type,
            question=question,
            free_text_allowed=free_text_allowed,
            options=options,
        ))
    except ValueError as exc:
        return ToolResult(status="failure", summary=str(exc))

    return ToolResult(
        status="success",
        summary=f"Decision request created: {decision_type}",
        data={
            "decision_id": dr.id,
            "decision_type": dr.decision_type,
            "question": dr.question,
            "options_count": len(options),
            "free_text_allowed": free_text_allowed,
        },
    )


def make_request_user_decision() -> ToolDefinition:
    return ToolDefinition(
        name="request_user_decision",
        description="Create a structured user decision request and pause the agent run. Use this when you need the user to select from options or provide input before continuing.",
        parameters=_REQUEST_USER_DECISION_SCHEMA,
        permission_level=PermissionLevel.DRAFT,
        handler=_handle_request_user_decision,
    )


# ── prepare_select_metadata_candidate_decision ────────────────────
# 通用候选选择决策: 电影和剧集共用, 由后端基于已持久化的
# MediaCandidate 列表生成 AgentDecisionRequest 选项. 选项的
# ``payload.candidate_id`` 是稳定引用, 拒绝 LLM 拼接 provider_id /
# media_type / 路径字符串. 与 request_user_decision 不同: 本工具的
# 选项由后端生成, Agent 不需要构造 options 入参.

_PREPARE_SELECT_METADATA_CANDIDATE_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "keyword": {
            "type": "string",
            "description": (
                "Optional. Re-search keyword. When no candidates have been "
                "persisted yet and the LLM wants to re-run the same search "
                "from search_metadata to populate the candidate list, pass "
                "the same keyword/provider/media_type as the prior search."
            ),
        },
        "provider": {
            "type": "string",
            "description": "Optional. Re-search provider (default tmdb).",
        },
        "media_type": {
            "type": "string",
            "enum": ["movie", "show", "both"],
            "description": "Optional. Re-search media_type scope.",
        },
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_prepare_select_metadata_candidate_decision(
    context: ToolContext, input_data: dict,
) -> ToolResult:
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
        AgentRunRepository,
        IngestTaskRepository,
    )
    from media_pilot.services.select_metadata_candidate import (
        STATUS_AUTO_CONFIRM,
        STATUS_DECISION_REQUESTED,
        STATUS_NO_CANDIDATES,
        prepare_select_metadata_candidate_decision,
    )

    task_id = input_data["task_id"]
    task_repo = IngestTaskRepository(context.session)
    task = task_repo.get(task_id)
    if task is None:
        return ToolResult(status="failure", summary=f"Task {task_id} not found")

    # 三个可选参数都是"低置信搜索后续路径"的搜索提示. 三个都提供时
    # 服务层会重新调用 search_metadata 并把结果落库, 候选选项由
    # build_candidate_options 用稳定 candidate_id 引用生成. LLM 不
    # 需要自行拼接 provider_id / media_type / 路径 payload.
    keyword = input_data.get("keyword")
    provider = input_data.get("provider") or "tmdb"
    media_type = input_data.get("media_type") or "both"

    # MP-Test-02 (Titanic) 现场引入: LLM 漏传 keyword/provider/media_type
    # 时, 工具从同 run 最近一次成功 ``search_metadata`` 的
    # ``AgentToolCall.output`` 恢复候选.
    #
    # 真实 AgentRunner (``runner.py:614-624``) 写入 shape:
    # - ``tc.status == "completed"`` 表示 ToolResult success.
    # - ``tc.output == {"status": "success", "summary": "...",
    #   "data": <search_metadata ToolResult.data>}``,
    #   其中 ``data`` 含 ``candidates`` / ``keyword`` / ``provider`` /
    #   ``has_clear_winner`` / ``best_candidate`` / ``runner_up``
    #   (见 ``read_only.py:297-306``). ``media_type`` 不在 data echo,
    #   走 ``tc.input.media_type`` (LLM 调用时传入).
    #
    # 兼容旧测试/旧 DB: ``tc.status == "succeeded"`` + 顶层
    # ``candidates`` / ``keyword`` / ``provider`` (legacy).
    #
    # 恢复范围严格在 ``context.run_id`` 内, 不跨 run 串. 仅当
    # service 路径走 "无 candidates + 三个参数缺" 分支时才走恢复
    # (与既有 _search_and_persist_candidates 互斥). 任何记录的
    # ``data.candidates`` 缺失/格式不对/空 list → 继续向前找; 找到
    # search_metadata 但所有记录字段都坏 → 返 recovered_output_invalid.
    recovered_search_results: list[dict] | None = None
    recovered_keyword = keyword
    recovered_provider = provider
    recovered_media_type_from_history: str | None = None
    found_search_metadata = False
    if (
        not keyword
        and not input_data.get("provider")
        and not input_data.get("media_type")
        and context.run_id
    ):
        from media_pilot.repository.repositories import AgentToolCallRepository

        recent_calls = (
            AgentToolCallRepository(context.session)
            .list_by_run(context.run_id)
        )
        # 倒序找最近一条"主契约"成功的 ``search_metadata`` 调用
        for tc in reversed(recent_calls):
            if tc.tool_name != "search_metadata" or not tc.output:
                continue
            output = tc.output if isinstance(tc.output, dict) else {}
            # 主契约: tc.status=="completed" 且 output["status"]=="success".
            # 兼容: tc.status=="succeeded" (legacy).
            is_success = (
                (tc.status == "completed" and output.get("status") == "success")
                or tc.status == "succeeded"
            )
            if not is_success:
                continue
            found_search_metadata = True
            # 优先从 data 取 (real runner shape), 顶层 fallback (legacy).
            data = output.get("data") if isinstance(output.get("data"), dict) else {}
            candidates = data.get("candidates")
            if candidates is None:
                candidates = output.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                # 这条记录 candidates 缺失 / 格式不对 / 空 → 继续
                continue
            rec_keyword = data.get("keyword") or output.get("keyword") or ""
            rec_provider = data.get("provider") or output.get("provider") or "tmdb"
            # media_type 不在 read_only.py 的 data echo, 走 input
            tc_input = tc.input if isinstance(tc.input, dict) else {}
            rec_media_type = tc_input.get("media_type") or "both"
            recovered_search_results = candidates
            recovered_keyword = keyword or rec_keyword
            recovered_provider = provider or rec_provider
            recovered_media_type_from_history = rec_media_type
            break
        else:
            # 显式 noop: 循环正常结束 (没 break) 时, 不需要额外动作 —
            # ``recovered_search_results`` 仍为 None, ``found_search_metadata``
            # 反映是否找到过 search_metadata 调用. 下面的 guard 处理
            # 全坏 → recovered_output_invalid.
            pass

    # 全坏记录: 找到 search_metadata 但所有记录字段都坏 → 工具返
    # recovered_output_invalid, 不静默回退到 no_persisted_candidates.
    if (
        recovered_search_results is None
        and found_search_metadata
        and not keyword
        and not input_data.get("provider")
        and not input_data.get("media_type")
    ):
        return ToolResult(
            status="failure",
            summary=(
                f"Recovered search_metadata output for task {task_id} is "
                f"invalid: all search_metadata calls in run "
                f"{context.run_id} have empty or malformed data.candidates"
            ),
            data={
                "reason": "recovered_output_invalid",
                "detail": (
                    "all search_metadata calls have empty or malformed "
                    "data.candidates"
                ),
            },
        )

    # 工具层补到 media_type (从 ``tc.input`` 恢复), 优先覆盖 default
    if recovered_media_type_from_history:
        media_type = recovered_media_type_from_history

    decision = prepare_select_metadata_candidate_decision(
        session=context.session,
        config=context.config,
        task_id=task_id,
        keyword=recovered_keyword or keyword,
        provider_name=recovered_provider or provider,
        media_type=media_type,
        recovered_search_results=recovered_search_results,
    )

    # auto_confirm: 已有 clear winner, Agent 应直接调
    # persist_metadata_selection / fetch_and_save_metadata_detail.
    # 工具返回 best_candidate 事实, 仍允许 LLM 走常规路径或直接 publish.
    if decision.status == STATUS_AUTO_CONFIRM:
        return ToolResult(
            status="success",
            summary=(
                f"Auto-confirm candidate: "
                f"{decision.best_candidate and decision.best_candidate.get('title')}"
            ),
            data={
                "auto_confirm": True,
                "best_candidate": decision.best_candidate,
            },
        )

    if decision.status == STATUS_NO_CANDIDATES:
        return ToolResult(
            status="failure",
            summary=(
                f"No persisted metadata candidates for task {task_id}. "
                "Run search_metadata first."
            ),
            data={
                "requires_user": True,
                "reason": "no_persisted_candidates",
            },
        )

    # 恢复字段缺失 → 工具返 failure, 不写半成品
    if decision.status == "recovered_output_invalid":
        return ToolResult(
            status="failure",
            summary=(
                f"Recovered search_metadata output for task {task_id} is "
                f"invalid: {decision.reason}"
            ),
            data={
                "reason": "recovered_output_invalid",
                "detail": decision.reason,
            },
        )

    # decision_requested: 已有候选但无 clear winner, 创建
    # AgentDecisionRequest(decision_type="select_metadata_candidate").
    if not context.run_id:
        return ToolResult(
            status="failure",
            summary=(
                "ToolContext.run_id is required to create "
                "select_metadata_candidate decision"
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
        "candidate_count": len(decision.options),
    }
    try:
        dr = dr_repo.create(AgentDecisionRequestCreate(
            run_id=context.run_id,
            task_id=task_id,
            decision_type=decision.decision_type or "select_metadata_candidate",
            question=decision.question or "请选择正确的元数据候选。",
            free_text_allowed=False,
            options=options_for_request,
            payload=payload,
        ))
    except ValueError as exc:
        # 已存在 pending decision 时不再重复创建.
        return ToolResult(
            status="failure",
            summary=(
                f"Cannot create select_metadata_candidate decision: {exc}"
            ),
            data={
                "reason": "existing_pending_decision",
                "decision_type": "select_metadata_candidate",
            },
        )

    # repository.create 已把 task.status 切到 waiting_user; 同步
    # 1) task.current_step 让前端时间线反映"卡在候选选择";
    # 2) AgentRun.status 切到 waiting_user 让 decision_reply 端的
    #    run.status guard 生效. select_metadata_candidate 不再
    #    走确定性旁路, 必须 run.status == "waiting_user" 才能回复,
    #    避免 LLM 在 run 还 active 时被伪造回复触发续跑.
    task_repo.update_status(
        task, status="waiting_user", current_step="select_metadata_candidate",
    )
    run_repo = AgentRunRepository(context.session)
    run = run_repo.get(context.run_id)
    if run is not None and run.status != "waiting_user":
        run_repo.update_status(
            run, status="waiting_user",
            current_step="select_metadata_candidate",
        )
    context.session.flush()

    return ToolResult(
        status="success",
        summary=(
            f"select_metadata_candidate decision created "
            f"({len(options_for_request)} option(s))"
        ),
        data={
            "auto_confirm": False,
            "decision_requested": True,
            "decision_id": dr.id,
            "decision_type": dr.decision_type,
            "reason": decision.reason,
            "candidate_count": len(options_for_request),
        },
    )


def make_prepare_select_metadata_candidate_decision() -> ToolDefinition:
    return ToolDefinition(
        name="prepare_select_metadata_candidate_decision",
        description=(
            "Evaluate persisted metadata candidates for the task and either "
            "auto-confirm a clear winner or create a select_metadata_candidate "
            "AgentDecisionRequest for the user. Universal for movie and show: "
            "options are generated server-side from MediaCandidate records; "
            "the option payload carries a stable candidate_id reference, not "
            "paths or media_type strings. Use this when search_metadata has "
            "produced multiple candidates and a clear winner cannot be "
            "auto-confirmed by confidence threshold."
        ),
        parameters=_PREPARE_SELECT_METADATA_CANDIDATE_DECISION_SCHEMA,
        permission_level=PermissionLevel.DRAFT,
        handler=_handle_prepare_select_metadata_candidate_decision,
    )
