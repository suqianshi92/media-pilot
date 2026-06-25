"""复杂电影输入决策回复 handler — 写入 MediaSourceSelection 任务事实.

select_primary_video / select_subtitles / review_complex_input
全部由后端确定性处理, 写入 MediaSourceSelection 后续跑 AgentRun.
不得移动、重命名或删除下载源文件 — 复杂输入选择只表达任务事实.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from media_pilot.config import AppConfig


@dataclass(frozen=True, kw_only=True)
class ComplexInputReplyResult:
    status: str
    selection_id: str | None = None
    reason: str = ""


def _resolve_input_path(task, existing_selection) -> str:
    """任务输入节点 = MediaSourceSelection.input_path 或 IngestTask.source_path.

    优先 MediaSourceSelection.input_path (整块任务输入), 退回
    IngestTask.source_path. 选出来的视频必须在该节点内.
    """
    if existing_selection is not None and existing_selection.input_path:
        return existing_selection.input_path
    return task.source_path or ""


def _resolve_input_root(task, existing_selection) -> Path | None:
    """把任务输入节点解析为 Path, 单文件任务取其父目录.

    与 jellyfin_movie_writer._resolve_task_input_root 行为一致:
    任务源是文件时, 输入根 = 文件所在目录; 任务是目录时, 输入根 = 目录本身.
    """
    raw = _resolve_input_path(task, existing_selection)
    if not raw:
        return None
    candidate = Path(raw)
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if resolved.is_file():
        return resolved.parent
    return resolved


def _is_path_within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def handle_select_primary_video(
    *,
    session: Session,
    config: AppConfig,
    decision,
) -> ComplexInputReplyResult:
    """用户选择主视频: 写入 MediaSourceSelection, 续跑 Agent.

    payload.selected_subtitles 沿用既有选择 (如果有), 不会清空.
    """
    from media_pilot.repository.models import MediaSourceSelection
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MediaSourceSelectionRepository,
    )

    # ORM AgentDecisionRequest 的 option_id 存在 decision["option_id"]
    # (decision JSON column 已被 reply_to_decision 写入), 也兼容 shim 的 .option_id
    option_id = decision.option_id if hasattr(decision, "option_id") else None
    if option_id is None and isinstance(getattr(decision, "decision", None), dict):
        oid = decision.decision.get("option_id")
        if isinstance(oid, str):
            option_id = oid
    if not option_id:
        return ComplexInputReplyResult(
            status="failed", reason="missing_option_id",
        )

    options = decision.options if isinstance(decision.options, list) else []
    selected_path: str | None = None
    for opt in options:
        if isinstance(opt, dict) and opt.get("id") == option_id:
            payload = opt.get("payload") if isinstance(opt.get("payload"), dict) else {}
            cand = payload.get("path")
            if isinstance(cand, str) and cand:
                selected_path = cand
            break

    if not selected_path:
        return ComplexInputReplyResult(
            status="failed", reason="option_path_missing",
        )

    task = IngestTaskRepository(session).get(decision.task_id)
    if task is None:
        return ComplexInputReplyResult(
            status="failed", reason="task_not_found",
        )

    existing = MediaSourceSelectionRepository(session).get_for_task(decision.task_id)
    input_path = _resolve_input_path(task, existing)
    input_root = _resolve_input_root(task, existing)

    # 安全硬门禁: 选出来的视频必须位于任务输入节点内.
    if not input_path or input_root is None:
        return ComplexInputReplyResult(
            status="failed", reason="input_path_missing",
        )
    if not _is_path_within(Path(selected_path), input_root):
        return ComplexInputReplyResult(
            status="failed", reason="selected_path_outside_input_node",
        )

    # 沿用既有 selected_subtitles (用户已经做过的字幕选择不应被覆盖).
    payload: dict = {
        "selection_source": "user_decision",
        "decision_id": decision.id,
        "decision_type": "select_primary_video",
    }
    if existing is not None and isinstance(existing.payload, dict):
        prior_subs = existing.payload.get("selected_subtitles")
        if isinstance(prior_subs, list):
            payload["selected_subtitles"] = list(prior_subs)

    repo = MediaSourceSelectionRepository(session)
    record = repo.save(
        task_id=decision.task_id,
        input_path=input_path,
        selected_path=selected_path,
        confidence=1.0,
        reason="user_decision:select_primary_video",
        payload=payload,
    )
    session.flush()
    return ComplexInputReplyResult(
        status="recorded",
        selection_id=record.id,
    )


def handle_select_subtitles(
    *,
    session: Session,
    config: AppConfig,
    decision,
) -> ComplexInputReplyResult:
    """用户选择字幕: 写入 MediaSourceSelection.payload.selected_subtitles.

    no_subtitles 选项 → 清空 selected_subtitles. 字幕路径必须存在且
    位于任务输入节点内, 否则整次回复失败 (handler 返回 status=failed).
    """
    from media_pilot.repository.models import MediaSourceSelection
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MediaSourceSelectionRepository,
    )

    if not getattr(decision, "option_id", None):
        # ORM AgentDecisionRequest: option_id 存在 decision["option_id"]
        d = getattr(decision, "decision", None)
        if not isinstance(d, dict) or not d.get("option_id"):
            return ComplexInputReplyResult(
                status="failed", reason="missing_option_id",
            )

    task = IngestTaskRepository(session).get(decision.task_id)
    if task is None:
        return ComplexInputReplyResult(
            status="failed", reason="task_not_found",
        )

    existing = MediaSourceSelectionRepository(session).get_for_task(decision.task_id)
    input_path = _resolve_input_path(task, existing)
    input_root = _resolve_input_root(task, existing)
    if not input_path or input_root is None:
        return ComplexInputReplyResult(
            status="failed", reason="input_path_missing",
        )

    option_id = decision.option_id if hasattr(decision, "option_id") else None
    if option_id is None and isinstance(getattr(decision, "decision", None), dict):
        oid = decision.decision.get("option_id")
        if isinstance(oid, str):
            option_id = oid
    if not option_id:
        return ComplexInputReplyResult(
            status="failed", reason="missing_option_id",
        )

    # no_subtitles 选项 → 显式空列表.
    if option_id == "no_subtitles":
        selected_subtitles: list[str] = []
    else:
        selected_subtitles = []
        for opt in (decision.options if isinstance(decision.options, list) else []):
            if not isinstance(opt, dict) or opt.get("id") != option_id:
                continue
            opt_payload = opt.get("payload") if isinstance(opt.get("payload"), dict) else {}
            cand = opt_payload.get("path")
            if isinstance(cand, str) and cand:
                selected_subtitles.append(cand)
            break

    # 安全硬门禁: 所有用户选择的字幕路径必须位于任务输入节点内.
    for sub_path in selected_subtitles:
        if not _is_path_within(Path(sub_path), input_root):
            return ComplexInputReplyResult(
                status="failed", reason="subtitle_outside_input_node",
            )

    # selected_path 沿用既有选择 (字幕决策不应覆盖已选主视频).
    selected_path = (
        existing.selected_path if existing and existing.selected_path else None
    )
    payload: dict = {
        "selection_source": "user_decision",
        "decision_id": decision.id,
        "decision_type": "select_subtitles",
        "selected_subtitles": selected_subtitles,
    }

    repo = MediaSourceSelectionRepository(session)
    record = repo.save(
        task_id=decision.task_id,
        input_path=input_path,
        selected_path=selected_path,
        confidence=1.0 if selected_path else None,
        reason="user_decision:select_subtitles",
        payload=payload,
    )
    session.flush()
    return ComplexInputReplyResult(
        status="recorded",
        selection_id=record.id,
    )


def handle_review_complex_input(
    *,
    session: Session,
    config: AppConfig,
    decision,
) -> ComplexInputReplyResult:
    """用户自由文本复核: 把说明追加到 task.failure_reason / Agent 上下文.

    不直接进入发布 — 由 Agent 续跑时基于说明重新调用
    prepare_complex_input_decision 或解释失败. 这里只把
    用户说明记录到 IngestTask.failure_reason 之外的不可见上下文:
    选择写入一条 MediaSourceSelection 任务事实, 把说明放进 payload.
    """
    from media_pilot.repository.models import MediaSourceSelection
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MediaSourceSelectionRepository,
    )

    free_text = (
        decision.decision.get("free_text") if isinstance(decision.decision, dict) else None
    )
    if not isinstance(free_text, str) or not free_text.strip():
        return ComplexInputReplyResult(
            status="failed", reason="missing_free_text",
        )

    task = IngestTaskRepository(session).get(decision.task_id)
    if task is None:
        return ComplexInputReplyResult(
            status="failed", reason="task_not_found",
        )

    existing = MediaSourceSelectionRepository(session).get_for_task(decision.task_id)
    input_path = _resolve_input_path(task, existing)

    payload: dict = {
        "selection_source": "user_decision",
        "decision_id": decision.id,
        "decision_type": "review_complex_input",
        "user_note": free_text.strip(),
    }

    repo = MediaSourceSelectionRepository(session)
    record = repo.save(
        task_id=decision.task_id,
        input_path=input_path or (task.source_path or ""),
        selected_path=existing.selected_path if existing else None,
        confidence=None,
        reason="user_decision:review_complex_input",
        payload=payload,
    )
    session.flush()
    return ComplexInputReplyResult(
        status="recorded",
        selection_id=record.id,
    )
