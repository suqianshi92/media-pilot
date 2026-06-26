"""Deterministic publish flow after `select_metadata_candidate` user reply.

MP-Lab-02-Matrix-1999-Dominant 现场: 用户已选候选, 旧路径完全依赖 LLM
自行调 `fetch_and_save_metadata_detail` + `publish_*_to_library`. LLM 一旦
调错工具 (e.g. 调只读的 `draft_metadata_replacement`), 任务卡在
`run.status=completed` + `task.status=agent_running` + 无 MetadataDetail
的自相矛盾状态.

这个模块在用户回复后**确定性地**完成 fetch + publish 序列, 不依赖 LLM
选择工具. 仍然保留 LLM 兜底: 当 publish 工具返回 `requires_user=True`
但 reason 不是 `target_conflict` (e.g. `multiple_videos` /
`unsafe_path`), 把控制权交回 LLM 走修复. publish 成功 / target_conflict
/ fetch 失败则全部走确定性收口, **不调** `continue_agent_run`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from media_pilot.config import AppConfig


# 复用 auto_ingest 的结果类型, 不重定义.
from media_pilot.services.auto_ingest import (
    FetchAndSaveDetailResult,
    fetch_and_save_metadata_detail,
)


# 在模块级别导入 registry 函数, 这样测试可以 monkeypatch
# `media_pilot.services.select_metadata_publish.get_tool_registry` /
# `register_builtin_tools`. 服务内部仍走局部默认调用, 测试替换
# 后上游调用拿到测试桩.
from media_pilot.agent.tools.registry import (  # noqa: E402
    get_tool_registry,
    register_builtin_tools,
)


# 公开常量给决策回复 + safety net 共用 — 避免字符串魔法.
OUTCOME_LIBRARY_IMPORT_COMPLETE = "library_import_complete"
OUTCOME_TARGET_CONFLICT = "target_conflict"
OUTCOME_AGENT_FAILED = "agent_failed"
OUTCOME_FALLBACK_TO_LLM = "fallback_to_llm"

REASON_METADATA_FETCH_FAILED = "metadata_detail_fetch_failed"
REASON_PUBLISH_OTHER_FAILURE = "publish_other_failure"


@dataclass(frozen=True, kw_only=True)
class ApplyUserChoiceResult:
    """`apply_user_metadata_choice` 的返回值.

    - outcome 决定上层 (`decision_reply` 或 `runner` safety net) 怎么
      收口 run / task.
    - failure_reason / error_message 在 outcome=AGENT_FAILED 时非空.
    - target_conflict_decision_id 在 outcome=TARGET_CONFLICT 时非空,
      上层可以 query 这个决策.
    """

    outcome: str
    failure_reason: str | None = None
    error_message: str | None = None
    target_conflict_decision_id: str | None = None
    target_conflict_question: str | None = None
    target_conflict_options: list[dict] | None = None
    target_conflict_payload: dict | None = None
    cleanup_decision_requested: bool = False


def apply_user_metadata_choice(
    *,
    session: Session,
    config: AppConfig,
    task,
    decision,
    registry: Any,
    option_id: str | None = None,
) -> ApplyUserChoiceResult:
    """把 `select_metadata_candidate` 用户选择固化为可发布的最终态.

    流程:
    1. 解析已选候选的 `provider` / `provider_id` / `media_type`
       (从 decision.options[option_id] 取对应 option, 严格匹配
       用户传入的 ``option_id``, 不得 fallback 到第一个含 payload 的
       option).
    2. 调 `fetch_and_save_metadata_detail(...)` 落 MetadataDetail.
       失败 → outcome=AGENT_FAILED.
    3. 调 `publish_movie_to_library` (movie) 或
       `publish_show_to_library` (show) 工具:
       - `status=success` + `data.decision_requested=False/未设` →
         outcome=LIBRARY_IMPORT_COMPLETE.
       - `data.decision_requested=True, decision_type="target_conflict"` →
         复用工具返回的 ``decision_id`` (或建一个新) → outcome=TARGET_CONFLICT.
       - 其它 `requires_user=True` → outcome=FALLBACK_TO_LLM.
       - 其它 `status=failure` / 抛异常 → outcome=FALLBACK_TO_LLM
         (LLM 兜底选修复工具; safety net 收尾).

    不调 LLM (continue_agent_run). 上层根据 outcome 决定 run / task 终
    态.

    ``option_id`` 必须严格等于 decision.options 中某项的 ``id`` —
    找不到或对应 option 缺 provider/provider_id/media_type → 直接
    AGENT_FAILED, **不**做"第一个含完整 payload 的 option"兜底 (旧
    行为会把用户选 B 错误落 A 的 metadata).
    """
    from media_pilot.agent.tools.base import ToolContext

    # ── 1. 解析用户选择 → provider / provider_id / media_type ──
    chosen = _resolve_chosen_candidate(session, decision, option_id=option_id)
    if chosen is None:
        return ApplyUserChoiceResult(
            outcome=OUTCOME_AGENT_FAILED,
            failure_reason=REASON_METADATA_FETCH_FAILED,
            error_message=(
                "User-decision option_id not found in decision.options or "
                "option payload missing provider/provider_id/media_type"
            ),
        )

    provider_name, provider_id, media_type = chosen

    # ── 2. fetch + save metadata detail ──
    detail_result = fetch_and_save_metadata_detail(
        session=session,
        config=config,
        task_id=task.id,
        provider_name=provider_name,
        provider_id=provider_id,
        media_type=media_type,
    )
    if detail_result.status != "success":
        return ApplyUserChoiceResult(
            outcome=OUTCOME_AGENT_FAILED,
            failure_reason=REASON_METADATA_FETCH_FAILED,
            error_message=detail_result.summary,
        )

    # ── 3. publish via tool registry ──
    tool_name = (
        "publish_movie_to_library"
        if media_type == "movie"
        else "publish_show_to_library"
    )
    tool_context = ToolContext(
        session=session, config=config,
        task_id=task.id, run_id=decision.run_id,
    )
    try:
        tool_result = registry.execute(
            tool_name, tool_context, {"task_id": task.id},
        )
    except Exception as exc:
        return ApplyUserChoiceResult(
            outcome=OUTCOME_FALLBACK_TO_LLM,
            failure_reason=REASON_PUBLISH_OTHER_FAILURE,
            error_message=f"publish tool raised: {exc}",
        )

    interpreted = _interpret_publish_result(
        session=session, decision=decision, tool_result=tool_result,
    )
    if interpreted.outcome != OUTCOME_LIBRARY_IMPORT_COMPLETE:
        return interpreted

    from media_pilot.services.post_publish_cleanup import run_post_publish_source_cleanup

    cleanup = run_post_publish_source_cleanup(
        session=session,
        config=config,
        task_id=task.id,
        run_id=decision.run_id,
    )
    return ApplyUserChoiceResult(
        outcome=interpreted.outcome,
        cleanup_decision_requested=cleanup.decision_requested,
    )


def _interpret_publish_result(
    *,
    session: Session,
    decision,
    tool_result,
) -> ApplyUserChoiceResult:
    """把 publish tool 的 `ToolResult` 翻译成 ApplyUserChoiceResult.

    判定顺序 — 必须先看 ``data.decision_requested``, 再看
    ``status``:

    1. ``data.decision_requested=True`` + ``decision_type="target_conflict"``
       → 工具内部已创建 AgentDecisionRequest (见
       ``_handle_publish_movie_to_library``), 上层**复用** ``data.
       decision_id`` 直接返回, 不再 create 一份. 旧路径先看
       ``status=="success"`` 会把这种形态误判为
       LIBRARY_IMPORT_COMPLETE, 任务被标 library_import_complete,
       用户看不到待决的 overwrite / cancel 选项.
    2. ``data.requires_user=True, reason="target_conflict"`` (旧
       requires_user 形态, 工具**没有** create decision) →
       兜底调用 ``_create_target_conflict_decision`` 创建.
    3. ``status=success`` 且 ``data`` 里没有 ``decision_requested`` →
       OUTCOME_LIBRARY_IMPORT_COMPLETE.
    4. 其它 → OUTCOME_FALLBACK_TO_LLM.
    """
    data = getattr(tool_result, "data", None) or {}

    # ── 1. publish tool 已经 create 了 target_conflict 决策 ——
    #     复用其 decision_id, 不重复创建. 任务的 waiting_user / agent
    #     _running 状态由工具侧 _handle_publish_movie_to_library 在
    #     落库时已经切到 (task_repo.update_status(waiting_user,
    #     current_step="target_conflict")), 上层不重置.
    decision_requested = bool(data.get("decision_requested"))
    if decision_requested and data.get("decision_type") == "target_conflict":
        prebuilt_decision_id = data.get("decision_id")
        if prebuilt_decision_id:
            return ApplyUserChoiceResult(
                outcome=OUTCOME_TARGET_CONFLICT,
                target_conflict_decision_id=prebuilt_decision_id,
                target_conflict_question=None,
                target_conflict_options=None,
                target_conflict_payload=None,
            )
        # 防御: 工具说 decision_requested=True 但忘了传 decision_id
        # — 退到下面的 _create_target_conflict_decision 兜底.

    # ── 2. 旧 requires_user 形态 (工具没有 create decision) —— 兜底创建. ──
    if tool_result.status != "success":
        requires_user = bool(data.get("requires_user"))
        reason = data.get("reason")
        if requires_user and reason == "target_conflict":
            target_conflict = _create_target_conflict_decision(
                session=session, decision=decision,
                final_target_dir=data.get("final_target_dir", ""),
                final_target_file=data.get("final_target_file", ""),
                conflict=reason,
            )
            return ApplyUserChoiceResult(
                outcome=OUTCOME_TARGET_CONFLICT,
                target_conflict_decision_id=target_conflict["id"],
                target_conflict_question=target_conflict["question"],
                target_conflict_options=target_conflict["options"],
                target_conflict_payload=target_conflict["payload"],
            )
        return ApplyUserChoiceResult(
            outcome=OUTCOME_FALLBACK_TO_LLM,
            failure_reason=REASON_PUBLISH_OTHER_FAILURE,
            error_message=getattr(tool_result, "summary", "publish failed"),
        )

    # ── 3. status=success 且没有 decision_requested —— 真发布成功. ──
    return ApplyUserChoiceResult(
        outcome=OUTCOME_LIBRARY_IMPORT_COMPLETE,
    )


def _resolve_chosen_candidate(
    session: Session, decision, *, option_id: str | None = None,
) -> tuple[str, str, str] | None:
    """从 decision.options[].payload 解析 user 选的 provider / id / type.

    ``decision`` 是 ORM `AgentDecisionRequest` 或 `_DecisionShim` —
    只需 ``.options`` (list[dict]).

    解析策略:
    1. 若 ``option_id`` 给出, 在 options 里找 ``id == option_id`` 的
       那一项, payload 必须含 provider / provider_id / media_type,
       否则返回 None (不 fallback).
    2. 若 ``option_id`` 缺失, 退回到 ``decision.selected_option_id``
       (历史路径). 仍不 fallback.

    payload 来源 (与 `handle_select_metadata_candidate` 写入对齐):
    - provider: 直接 `payload.provider`
    - provider_id: `payload.provider_id` 或 `payload.external_id`
      (handle_select_metadata_candidate 把 MediaCandidate.external_id
      写进 `user_decision` candidate, payload 同时也透传)
    - media_type: `payload.media_type`

    这些字段在 `prepare_select_metadata_candidate_decision` 工具
    生成选项时已经 echo 进每个 option 的 payload — 所以不需再查
    MediaCandidate 表.
    """
    options = decision.options if isinstance(decision.options, list) else []

    target_option_id = option_id or getattr(decision, "selected_option_id", None)
    if not target_option_id:
        return None

    chosen_option = next(
        (o for o in options
         if isinstance(o, dict) and o.get("id") == target_option_id),
        None,
    )
    if chosen_option is None:
        return None

    payload = chosen_option.get("payload") or {}
    provider = payload.get("provider")
    provider_id = payload.get("provider_id") or payload.get("external_id")
    media_type = payload.get("media_type")
    if not (provider and provider_id and media_type):
        return None
    return (provider, provider_id, media_type)


def _create_target_conflict_decision(
    *,
    session: Session,
    decision,
    final_target_dir: str,
    final_target_file: str,
    conflict: str,
) -> dict:
    """复用既有 `publish_movie_to_library` 创建 target_conflict
    决策的形状, 写到新 run_id 的 AgentDecisionRequest."""
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
    )

    question = (
        f"目标 {final_target_file} 已被占用（{conflict}）。"
        "请选择处理方式。"
    )
    options = [
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
    ]
    payload = {
        "final_target_dir": final_target_dir,
        "final_target_file": final_target_file,
        "conflict": conflict,
    }
    dr = AgentDecisionRequestRepository(session).create(
        AgentDecisionRequestCreate(
            run_id=decision.run_id,
            task_id=decision.task_id,
            decision_type="target_conflict",
            question=question,
            free_text_allowed=False,
            options=options,
            payload=payload,
        )
    )
    session.flush()
    return {
        "id": dr.id,
        "question": question,
        "options": options,
        "payload": payload,
    }
