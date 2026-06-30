"""User reply service for pending AgentDecisionRequest.

Handles validation, persistence, and triggers continue_agent_run.
For post_revoke_action decisions, dispatches to the appropriate handler
instead of continuing the existing AgentRun.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from media_pilot.config import AppConfig


@dataclass(frozen=True, kw_only=True)
class ReplyInput:
    decision_id: str
    option_id: str | None = None
    free_text: str | None = None
    decided_by: str = "user"


def reply_to_decision(
    *,
    session: Session,
    config: AppConfig,
    reply: ReplyInput,
    mock_llm_client=None,
):
    """Validate and process a user reply to a pending decision.

    Returns the AgentRunResult from continue_agent_run (normal decisions)
    or from the post-revoke handler (post_revoke_action decisions).

    Raises ValueError with status_code context for invalid/conflict states.
    """
    from media_pilot.agent.runner import AgentRunResult
    from media_pilot.repository.repositories import (
        AgentDecisionRequestRepository,
        AgentMessageCreate,
        AgentMessageRepository,
        AgentRunRepository,
        IngestTaskRepository,
    )

    dr_repo = AgentDecisionRequestRepository(session)
    run_repo = AgentRunRepository(session)
    msg_repo = AgentMessageRepository(session)
    task_repo = IngestTaskRepository(session)

    decision = dr_repo.get(reply.decision_id)
    if decision is None:
        raise ValueError({"status_code": 404, "detail": "Decision not found"})

    if decision.status != "pending":
        raise ValueError({"status_code": 409, "detail": "Decision has already been decided"})

    run = run_repo.get(decision.run_id)
    if run is None:
        raise ValueError({"status_code": 404, "detail": "AgentRun not found"})

    # post_revoke_action decisions are created on a system run that is
    # set to "waiting_user" by the revoke flow. They're dispatched to
    # dedicated handlers rather than continuing the existing AgentRun,
    # so detect them before the generic status guard.
    is_post_revoke = decision.decision_type == "post_revoke_action"

    # target_conflict 决策由 publish_movie_to_library 工具创建；
    # 同样是确定性后端路径，不调用 LLM，不继续 AgentRun。
    is_target_conflict = decision.decision_type == "target_conflict"

    # manual_selection_blocked 由人工选择候选后安全门禁阻塞产生；
    # cancel 选项是确定性取消, 走专用 handler; retry / free_text
    # 仍走普通 Agent 续跑, 让 Agent 重新尝试处理。
    is_manual_blocked = decision.decision_type == "manual_selection_blocked"

    # source_cleanup_action 由 handle_source_cleanup 工具创建, 是入库后的
    # 旁路清理决策: keep_input / trash_input 由后端确定性执行, 不调用 LLM;
    # delete_input 进入现有删除任务输入预检 (delete_input_preview).
    is_source_cleanup_action = decision.decision_type == "source_cleanup_action"

    # complex-input 决策 (select_primary_video / select_subtitles /
    # review_complex_input) 必须在 run.status == "waiting_user" 时才能
    # 被回复 — prepare_complex_input_decision 工具创建决策时会同步把
    # run 切到 waiting_user, 此处不再作为确定性旁路放行. 试图在 run
    # 处于 active / completed 等状态时回复会得到 409, 避免循环创建.
    is_complex_input_decision = decision.decision_type in (
        "select_primary_video", "select_subtitles", "review_complex_input",
    )

    # select_metadata_candidate: 通用元数据候选选择决策 (电影/剧集共用).
    # 同样不是确定性后端路径, 必须在 run.status == "waiting_user" 时
    # 才能被回复. prepare_select_metadata_candidate_decision 工具创建
    # 决策时已把 run.status 切到 waiting_user. 此处不放行.
    is_select_metadata_candidate = decision.decision_type == "select_metadata_candidate"

    # metadata_unavailable_action: 元数据缺失/不明确后的用户确认。
    # publish_without_metadata / cancel 走确定性后端路径; continue_search
    # 切回 Agent 续跑。
    is_metadata_unavailable_action = decision.decision_type == "metadata_unavailable_action"

    if (
        not is_post_revoke
        and not is_target_conflict
        and not is_source_cleanup_action
        and not is_metadata_unavailable_action
        and run.status != "waiting_user"
    ):
        raise ValueError({
            "status_code": 409,
            "detail": f"AgentRun is not waiting for user (status={run.status})",
        })

    options = decision.options if isinstance(decision.options, list) else []

    # Validate reply content — option_id and free_text are mutually exclusive
    if reply.option_id is not None and reply.free_text is not None:
        raise ValueError({
            "status_code": 400,
            "detail": "option_id and free_text cannot both be provided",
        })

    if reply.option_id is not None:
        option_ids = {opt.get("id", "") if isinstance(opt, dict) else "" for opt in options}
        if reply.option_id not in option_ids:
            raise ValueError({
                "status_code": 400,
                "detail": f"option_id '{reply.option_id}' not found in decision options",
            })
        decision_data = {"option_id": reply.option_id, "type": "option"}
    elif reply.free_text is not None:
        if not decision.free_text_allowed:
            raise ValueError({
                "status_code": 400,
                "detail": "Free text is not allowed for this decision",
            })
        decision_data = {"free_text": reply.free_text, "type": "free_text"}
    else:
        raise ValueError({
            "status_code": 400,
            "detail": "Either option_id or free_text must be provided",
        })

    # Save decision
    dr_repo.save_decision(
        reply.decision_id,
        decision=decision_data,
        decided_by=reply.decided_by,
    )

    # Write system action message
    # 卡住 Agent 恢复 / 决策动作消息 改造: 不再写 role="user" + 内部审计
    # 文本 ("[User selected option: candidate_xxx]\nQuestion: ..."), 改为
    # [SystemAction] 系统动作摘要. 前端 MessageBubble 已经识别该前缀并
    # 渲染系统动作样式 (amber 边框 + 系统动作标签), 不再伪装成用户气泡.
    # 普通 freeform Agent 输入仍走 run_agent_turn 的同步路径, 这里只处理
    # 决策回复.
    action_content = _format_decision_action_message(reply, decision)
    msg_repo.create(AgentMessageCreate(
        run_id=decision.run_id,
        role="user",
        content=action_content,
    ))

    # ── post_revoke_action: dispatch to handler instead of continuing ──
    if is_post_revoke:
        # Mark the system AgentRun as completed — it has no LLM context
        run_repo.update_status(run, status="completed", current_step="post_revoke_decided")

        if reply.option_id == "delete_task_input":
            return AgentRunResult(
                run_id="",
                status="delete_input_preview",
                message_count=1,
                tool_call_count=0,
            )

        task_id = decision.task_id

        if reply.option_id == "reingest_with_new_search":
            from media_pilot.services.post_revoke_handler import handle_reingest_with_new_search
            return handle_reingest_with_new_search(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock_llm_client,
            )

        if reply.option_id == "reingest_with_existing_metadata":
            from media_pilot.services.post_revoke_handler import handle_reingest_with_existing_metadata
            return handle_reingest_with_existing_metadata(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock_llm_client,
            )

    # ── target_conflict: deterministic handler, no LLM ──
    if is_target_conflict:
        from media_pilot.services.target_conflict_handler import (
            handle_cancel_publish,
            handle_overwrite_target,
        )

        # 标记 AgentRun 为 completed — 它已经完成了创建决策的工作
        run_repo.update_status(run, status="completed", current_step="target_conflict_decided")

        if reply.option_id == "overwrite_target":
            handle_overwrite_target(
                session=session, config=config, decision=decision,
            )
            return AgentRunResult(
                run_id=decision.run_id,
                status="target_conflict_overwritten",
                message_count=1,
                tool_call_count=0,
            )

        if reply.option_id == "cancel_publish":
            handle_cancel_publish(
                session=session, config=config, decision=decision,
            )
            return AgentRunResult(
                run_id=decision.run_id,
                status="target_conflict_cancelled",
                message_count=1,
                tool_call_count=0,
            )

    # ── metadata_unavailable_action: user confirmed no-metadata ingest ──
    if is_metadata_unavailable_action:
        task = task_repo.get(decision.task_id)
        if task is None:
            raise ValueError({
                "status_code": 404,
                "detail": f"Task {decision.task_id} not found",
            })

        if reply.option_id == "continue_search":
            run_repo.update_status(run, status="active", current_step="user_replied")
            task_repo.update_status(
                task, status="agent_running", current_step="user_replied",
            )
            from media_pilot.agent.runner import continue_agent_run
            return continue_agent_run(
                session=session,
                config=config,
                run_id=decision.run_id,
                mock_llm_client=mock_llm_client,
            )

        if reply.option_id == "cancel":
            task_repo.update_status(
                task,
                status="agent_failed",
                current_step="agent_failed",
                failure_reason="用户取消无元数据入库",
            )
            run_repo.update_status(
                run,
                status="failed",
                current_step="metadata_unavailable_cancelled",
                error_message="用户取消无元数据入库",
            )
            return AgentRunResult(
                run_id=decision.run_id,
                status="metadata_unavailable_cancelled",
                message_count=1,
                tool_call_count=0,
            )

        if reply.option_id == "publish_without_metadata":
            from media_pilot.repository.repositories import (
                AgentDecisionRequestCreate,
            )
            from media_pilot.services.no_metadata_publish import publish_without_metadata
            from media_pilot.services.post_publish_cleanup import (
                run_post_publish_source_cleanup,
            )

            run_repo.update_status(run, status="active", current_step="no_metadata_publish")
            task_repo.update_status(
                task, status="agent_running", current_step="no_metadata_publish",
            )
            publish_result = publish_without_metadata(
                session=session, config=config, task_id=decision.task_id,
                allow_agent_running=True,
            )
            if publish_result.status == "published":
                cleanup = run_post_publish_source_cleanup(
                    session=session,
                    config=config,
                    task_id=decision.task_id,
                    run_id=decision.run_id,
                )
                if not cleanup.decision_requested:
                    run_repo.update_status(
                        run, status="completed", current_step="no_metadata_published",
                    )
                return AgentRunResult(
                    run_id=decision.run_id,
                    status=(
                        "no_metadata_published_cleanup_pending"
                        if cleanup.decision_requested else "no_metadata_published"
                    ),
                    message_count=1,
                    tool_call_count=0,
                )

            if publish_result.status == "target_conflict":
                dr_repo.create(AgentDecisionRequestCreate(
                    run_id=decision.run_id,
                    task_id=decision.task_id,
                    decision_type="target_conflict",
                    question=(
                        f"目标 {publish_result.final_target_dir} 已被占用。"
                        "请选择处理方式。"
                    ),
                    free_text_allowed=False,
                    options=[
                        {
                            "id": "overwrite_target",
                            "label": "覆盖发布目标",
                            "description": "覆盖已存在的发布目标。",
                        },
                        {
                            "id": "cancel_publish",
                            "label": "取消本次发布",
                            "description": "任务进入失败态，等待后续处理。",
                        },
                    ],
                    payload={
                        "final_target_dir": publish_result.final_target_dir,
                        "final_target_file": publish_result.final_target_file,
                        "conflict": "no_metadata_target_conflict",
                        "publish_mode": "no_metadata",
                    },
                ))
                task_repo.update_status(
                    task, status="waiting_user", current_step="target_conflict",
                )
                run_repo.update_status(
                    run, status="waiting_user", current_step="target_conflict",
                )
                return AgentRunResult(
                    run_id=decision.run_id,
                    status="target_conflict_pending",
                    message_count=1,
                    tool_call_count=0,
                )

            task_repo.update_status(
                task,
                status="agent_failed",
                current_step="agent_failed",
                failure_reason=publish_result.summary,
            )
            run_repo.update_status(
                run,
                status="failed",
                current_step="no_metadata_publish_failed",
                error_message=publish_result.summary,
            )
            return AgentRunResult(
                run_id=decision.run_id,
                status="no_metadata_publish_failed",
                message_count=1,
                tool_call_count=0,
                error_message=publish_result.summary,
            )

    # ── manual_selection_blocked: cancel / retry 都是确定性后端路径。
    # retry 重新检查门禁并执行快捷发布, 不让 LLM 猜 task_id。
    if is_manual_blocked and reply.option_id == "cancel":
        from media_pilot.services.manual_selection import (
            handle_manual_selection_cancel,
        )

        handle_manual_selection_cancel(session=session, decision=decision)
        return AgentRunResult(
            run_id=decision.run_id,
            status="manual_selection_cancelled",
            message_count=1,
            tool_call_count=0,
        )

    if is_manual_blocked and reply.option_id == "retry":
        from media_pilot.services.manual_selection import (
            handle_manual_selection_retry,
        )

        result = handle_manual_selection_retry(
            session=session, config=config, decision=decision,
        )
        return AgentRunResult(
            run_id=decision.run_id,
            status=(
                "manual_selection_published"
                if result.status == "published"
                else result.status
            ),
            message_count=1,
            tool_call_count=0,
            error_message=result.summary if result.status == "agent_failed" else None,
        )

    # ── select_metadata_candidate: 写入 user_decision MediaCandidate
    #    任务事实后, 走确定性 fetch + publish 序列. 不再纯 LLM 续跑.
    #    LLM 仅在 publish 工具返非 target_conflict 失败时介入兜底.
    if is_select_metadata_candidate:
        from media_pilot.agent.tools.registry import (
            get_tool_registry,
            register_builtin_tools,
        )
        from media_pilot.services.select_metadata_candidate import (
            handle_select_metadata_candidate,
        )
        from media_pilot.services.select_metadata_publish import (
            OUTCOME_AGENT_FAILED,
            OUTCOME_FALLBACK_TO_LLM,
            OUTCOME_LIBRARY_IMPORT_COMPLETE,
            OUTCOME_TARGET_CONFLICT,
            apply_user_metadata_choice,
        )

        handler_result = handle_select_metadata_candidate(
            session=session,
            config=config,
            decision=decision,
            option_id=reply.option_id or "",
        )
        if handler_result.status != "recorded":
            raise ValueError({
                "status_code": 400,
                "detail": (
                    f"select_metadata_candidate handler failed: "
                    f"{handler_result.reason}"
                ),
            })

        task = task_repo.get(decision.task_id)
        if task is None:
            raise ValueError({
                "status_code": 404,
                "detail": f"Task {decision.task_id} not found",
            })

        # 工具调用前: run 进入"等待服务层处理"状态, task 切到
        # agent_running 标记"已收到 user reply, 正在推进".
        run_repo.update_status(
            run, status="active", current_step="user_replied",
        )
        task_repo.update_status(
            task, status="agent_running", current_step="user_replied",
        )

        register_builtin_tools()
        registry = get_tool_registry()
        apply_result = apply_user_metadata_choice(
            session=session,
            config=config,
            task=task,
            decision=decision,
            registry=registry,
            option_id=reply.option_id,
        )

        if apply_result.outcome == OUTCOME_LIBRARY_IMPORT_COMPLETE:
            # publish 工具已经把 task.status 推到 library_import_complete
            # (工具内调了 update_status); run 收口.
            task_repo.update_status(
                task, status="library_import_complete",
                current_step=(
                    "source_cleanup_decision"
                    if apply_result.cleanup_decision_requested
                    else "library_import_complete"
                ),
            )
            if not apply_result.cleanup_decision_requested:
                run_repo.update_status(
                    run, status="completed",
                    current_step="metadata_published",
                )
            return AgentRunResult(
                run_id=decision.run_id,
                status="metadata_published",
                message_count=1,
                tool_call_count=0,
            )

        if apply_result.outcome == OUTCOME_TARGET_CONFLICT:
            # target_conflict 决策已建, task 切到 waiting_user /
            # target_conflict 等待用户选 overwrite / cancel. 工具
            # (_handle_publish_movie_to_library) 内部已经 set 过,
            # 这里防御性再 set 一次, 避免某些 publish 实现路径
            # 漏切导致 UI 显示 "Agent处理中" 但实际等用户确认.
            # 不得保留 agent_running — 与"等用户确认"语义冲突.
            task_repo.update_status(
                task, status="waiting_user",
                current_step="target_conflict",
            )
            run_repo.update_status(
                run, status="completed",
                current_step="target_conflict_pending",
            )
            return AgentRunResult(
                run_id=decision.run_id,
                status="target_conflict_pending",
                message_count=1,
                tool_call_count=0,
            )

        if apply_result.outcome == OUTCOME_AGENT_FAILED:
            failure_reason = apply_result.failure_reason or "metadata_choice_failed"
            error_message = apply_result.error_message or "metadata choice failed"
            task_repo.update_status(
                task, status="agent_failed", current_step="agent_failed",
                failure_reason=failure_reason,
            )
            run_repo.update_status(
                run, status="failed",
                current_step="agent_failed",
                error_message=error_message,
            )
            return AgentRunResult(
                run_id=decision.run_id,
                status="metadata_choice_failed",
                message_count=1,
                tool_call_count=0,
                error_message=error_message,
            )

        # OUTCOME_FALLBACK_TO_LLM: publish 失败 (非 target_conflict),
        # 把控制权交回 LLM 走修复工具. 状态已经是 active / agent_running.
        from media_pilot.agent.runner import continue_agent_run
        return continue_agent_run(
            session=session,
            config=config,
            run_id=decision.run_id,
            mock_llm_client=mock_llm_client,
        )

    # ── complex-input 决策: 写入 MediaSourceSelection 任务事实后续跑 Agent.
    #    不得绕过 Agent 直接发布, 不得移动/重命名下载源文件.
    if is_complex_input_decision:
        from media_pilot.services.complex_input_reply import (
            handle_review_complex_input,
            handle_select_primary_video,
            handle_select_subtitles,
        )

        if decision.decision_type == "select_primary_video":
            handler_result = handle_select_primary_video(
                session=session, config=config, decision=decision,
            )
        elif decision.decision_type == "select_subtitles":
            handler_result = handle_select_subtitles(
                session=session, config=config, decision=decision,
            )
        else:  # review_complex_input
            handler_result = handle_review_complex_input(
                session=session, config=config, decision=decision,
            )

        if handler_result.status != "recorded":
            raise ValueError({
                "status_code": 400,
                "detail": (
                    f"complex_input decision handler failed: "
                    f"{handler_result.reason}"
                ),
            })

        # 切回普通 Agent 续跑, 让 Agent 重新调用 prepare_complex_input_decision /
        # search_metadata / publish_movie_to_library. run 切到 active 让 LLM 续跑.
        run_repo.update_status(
            run, status="active", current_step="user_replied",
        )
        task = task_repo.get(decision.task_id)
        if task is not None:
            task_repo.update_status(
                task, status="agent_running", current_step="user_replied",
            )

        from media_pilot.agent.runner import continue_agent_run
        return continue_agent_run(
            session=session,
            config=config,
            run_id=decision.run_id,
            mock_llm_client=mock_llm_client,
        )

    # ── source_cleanup_action: keep_input / trash_input / delete_input
    #    全部走确定性后端路径, 不调用 LLM, 不继续 AgentRun.
    if is_source_cleanup_action:
        from media_pilot.services.source_cleanup_handler import (
            handle_source_cleanup_keep,
            handle_source_cleanup_trash,
        )

        # 标记承载决策的 AgentRun 为 completed — 决策已被处理
        run_repo.update_status(
            run, status="completed", current_step="source_cleanup_decided",
        )

        if reply.option_id == "keep_input":
            handle_source_cleanup_keep(
                session=session, config=config, decision=decision,
            )
            return AgentRunResult(
                run_id=decision.run_id,
                status="source_cleanup_kept",
                message_count=1,
                tool_call_count=0,
            )

        if reply.option_id == "trash_input":
            trash_result = handle_source_cleanup_trash(
                session=session, config=config, decision=decision,
            )
            # handler 预检失败 / execute 失败 → 返回 trash_failed 状态;
            # 不要在失败时仍返回 source_cleanup_trashed, 否则前端会误判成功.
            outcome = trash_result.get("outcome") if isinstance(trash_result, dict) else None
            if outcome == "trashed":
                status = "source_cleanup_trashed"
            else:
                status = "source_cleanup_failed"
            return AgentRunResult(
                run_id=decision.run_id,
                status=status,
                message_count=1,
                tool_call_count=0,
            )

        if reply.option_id == "delete_input":
            # 进入现有删除任务输入预检, 前端会触发 getDeleteInputPreview
            return AgentRunResult(
                run_id="",
                status="delete_input_preview",
                message_count=1,
                tool_call_count=0,
            )

    # ── normal decision: continue AgentRun with LLM ──
    run_repo.update_status(run, status="active", current_step="user_replied")
    task = task_repo.get(decision.task_id)
    if task is not None:
        task_repo.update_status(task, status="agent_running", current_step="user_replied")

    from media_pilot.agent.runner import continue_agent_run
    return continue_agent_run(
        session=session,
        config=config,
        run_id=decision.run_id,
        mock_llm_client=mock_llm_client,
    )


def _format_user_message(reply: ReplyInput, question: str) -> str:
    """旧的 ``role="user"`` 审计格式生成器.

    保留以兼容旧的历史 AgentMessage; 决策回复路径已切到
    ``_format_decision_action_message`` (写 ``[SystemAction] ...`` 内容).
    普通 freeform Agent 输入路径仍可继续使用本函数 (freeform 是真正的
    用户自然语言消息, 与"决策动作"语义不同). 当前生产调用点只剩下
    单元测试, 实际决策回复不再走它.
    """
    if reply.option_id is not None:
        return f"[User selected option: {reply.option_id}]\nQuestion: {question}"
    else:
        return f"[User reply]: {reply.free_text}"


def _read_field(obj, key: str):
    """兼容 dict 与 dataclass / ORM 对象的字段读取. 决策 dict 来自 repository
    序列化, ORM 对象来自 SQLAlchemy — 两者字段都在, 读取方式不同."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _format_decision_action_message(reply: ReplyInput, decision) -> str:
    """把用户对人工决策的回复格式化成 ``[SystemAction] ...`` 系统动作摘要.

    必须满足:
    - 必须以 ``[SystemAction] `` 开头 — 前端 MessageBubble 用这个前缀
      识别系统动作消息并切换样式 (amber 边框 + 系统动作标签), 不再
      渲染为用户自然语言气泡 (role=user).
    - 不得暴露 option id 作为主体. 内部 option id 是数据库 key, 暴露
      给用户是审计噪音.
    - 不得把 ``decision.question`` 原文拼到聊天消息正文. question 是给
      Agent 看的, 把它贴在用户气泡里既冗余又容易让用户怀疑自己说过
      那段话.
    - 优先使用可读 label / payload title / payload filename 作为摘要.
      拿不到可读名称时 fallback 到 "已提交选择".
    - free_text 必须以系统动作语义展示, 但保留用户原文摘要, 例如
      ``[SystemAction] 已提交补充说明: <原文前 50 字>...``.
    """
    decision_type = _read_field(decision, "decision_type") or ""
    options = _read_field(decision, "options")
    if not isinstance(options, list):
        options = []
    if reply.free_text is not None:
        snippet = (reply.free_text or "").strip()
        if len(snippet) > 50:
            snippet = snippet[:50] + "…"
        return f"[SystemAction] 已提交补充说明：{snippet}"

    # option_id 可能为 None; 但 reply_to_decision 已保证 option_id 或
    # free_text 必有其一, 不会走到这里两个都为空.
    chosen = None
    for opt in options:
        if isinstance(opt, dict) and opt.get("id") == reply.option_id:
            chosen = opt
            break
    label = _extract_option_label(chosen, decision_type) if chosen else ""

    return _summarize(decision_type, reply.option_id, label)


def _extract_option_label(opt: dict, decision_type: str) -> str:
    """从 option dict 提取可读 label.

    优先 ``opt["label"]`` (决策创建时已写入); 退到 ``opt["payload"]`` 里
    已知字段 (title / year / path); 拿不到就空字符串, 由 caller 决定
    fallback.
    """
    label = opt.get("label") if isinstance(opt, dict) else None
    if isinstance(label, str) and label.strip():
        return label.strip()
    payload = opt.get("payload") if isinstance(opt, dict) else None
    if isinstance(payload, dict):
        for key in ("title", "filename", "name", "path"):
            v = payload.get(key)
            if isinstance(v, str) and v.strip():
                # 路径类只取 basename 避免长路径污染气泡
                if key == "path":
                    return v.rsplit("/", 1)[-1] or v
                return v.strip()
    return ""


def _summarize(decision_type: str, option_id: str | None, label: str) -> str:
    """按 decision_type 生成最终系统动作摘要. label 为空时用通用 fallback."""
    if decision_type == "select_metadata_candidate":
        return f"[SystemAction] 已选择元数据候选：{label}" if label else "[SystemAction] 已选择元数据候选"
    if decision_type == "select_primary_video":
        return f"[SystemAction] 已选择主视频：{label}" if label else "[SystemAction] 已选择主视频"
    if decision_type == "select_subtitles":
        return f"[SystemAction] 已选择字幕：{label}" if label else "[SystemAction] 已选择字幕"
    if decision_type == "review_complex_input":
        return f"[SystemAction] 已确认复杂输入复核：{label}" if label else "[SystemAction] 已确认复杂输入复核"
    if decision_type == "target_conflict":
        if option_id == "overwrite_target":
            return "[SystemAction] 已选择覆盖发布目标"
        if option_id == "cancel_publish":
            return "[SystemAction] 已取消发布"
        return "[SystemAction] 已处理目标冲突决策"
    if decision_type == "manual_selection_blocked":
        if option_id == "cancel":
            return "[SystemAction] 已取消人工选择"
        return "[SystemAction] 已选择解除阻塞的处理方式"
    if decision_type == "source_cleanup_action":
        if option_id == "keep_input":
            return "[SystemAction] 已确认保留任务输入"
        if option_id == "trash_input":
            return "[SystemAction] 已确认将任务输入移入回收区"
        if option_id == "delete_input":
            return "[SystemAction] 已请求删除任务输入预检"
        return "[SystemAction] 已选择源文件清理方式"
    if decision_type == "metadata_unavailable_action":
        if option_id == "continue_search":
            return "[SystemAction] 已选择继续搜索元数据"
        if option_id == "publish_without_metadata":
            return "[SystemAction] 已确认按无元数据方式入库"
        if option_id == "cancel":
            return "[SystemAction] 已取消无元数据入库"
        return "[SystemAction] 已选择元数据不可用时的处理方式"
    if decision_type == "post_revoke_action":
        if option_id == "reingest_with_new_search":
            return "[SystemAction] 已选择重新搜索并入库"
        if option_id == "reingest_with_existing_metadata":
            return "[SystemAction] 已选择沿用现有元数据重新入库"
        if option_id == "delete_task_input":
            return "[SystemAction] 已选择撤回并删除任务输入"
        return "[SystemAction] 已选择撤回后处理方式"
    # 未知 decision_type — 仍给可读摘要, 不暴露 option_id
    if label:
        return f"[SystemAction] 已提交选择：{label}"
    return "[SystemAction] 已提交选择"
