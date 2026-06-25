"""Build deterministic task-level context injection for freeform AgentRuns."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_pilot.repository.models import (
    AgentDecisionRequest,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    IngestTask,
    MediaCandidate,
    MediaSourceSelection,
    MetadataDetail,
    WriteResult,
)


def build_freeform_context(
    session: Session,
    task_id: str,
    *,
    max_messages: int = 20,
    max_tool_calls: int = 10,
) -> tuple[str, str, str]:
    """Return (task_facts, recent_messages, recent_tool_calls) for injection.

    task_facts: human-readable summary of current task state.
    recent_messages: last N non-system Agent messages, as Markdown.
    recent_tool_calls: last M tool call summaries, as Markdown.
    """
    task = session.get(IngestTask, task_id)
    if task is None:
        return ("", "", "")

    task_facts = _build_task_facts(session, task)
    recent_messages = _build_recent_messages(session, task_id, max_messages)
    recent_tool_calls = _build_recent_tool_calls(session, task_id, max_tool_calls)

    return task_facts, recent_messages, recent_tool_calls


def _build_task_facts(session: Session, task) -> str:
    """Build a human-readable summary of the current task state."""
    lines = [
        f"Task ID: {task.id}",
        f"Status: {task.status}",
        f"Current Step: {task.current_step or 'N/A'}",
        f"Source Path: {task.source_path}",
        f"Media Type: {task.media_type or 'unknown'}",
        f"Title: {task.title or 'N/A'}",
        f"Year: {task.year or 'N/A'}",
    ]

    selection = session.scalars(
        select(MediaSourceSelection)
        .where(MediaSourceSelection.task_id == task.id)
        .order_by(MediaSourceSelection.created_at.desc()),
    ).first()
    if selection:
        lines.append(f"Selected Path: {selection.selected_path}")

    detail = session.scalars(
        select(MetadataDetail)
        .where(MetadataDetail.task_id == task.id)
        .order_by(MetadataDetail.created_at.desc()),
    ).first()
    if detail:
        lines.append(
            f"Current Metadata: {detail.title} ({detail.year}) "
            f"from {detail.provider} ({detail.provider_id})"
        )

    candidates = list(session.scalars(
        select(MediaCandidate)
        .where(MediaCandidate.task_id == task.id)
        .order_by(MediaCandidate.confidence.desc().nullslast()),
    ).all())
    if candidates:
        cand_lines = []
        for c in candidates[:5]:
            conf_str = f"confidence={c.confidence:.2f}" if c.confidence else ""
            cand_lines.append(f"  - {c.title} ({c.year}) [{c.provider_name}] {conf_str}")
        lines.append(f"Candidates ({len(candidates)}):")
        lines.extend(cand_lines)

    wr = session.scalars(
        select(WriteResult)
        .where(WriteResult.task_id == task.id)
        .order_by(WriteResult.created_at.desc()),
    ).first()
    if wr:
        target = wr.payload.get("target_dir", "N/A") if wr.payload else "N/A"
        lines.append(f"Last Write Result: {wr.status} -> {target}")

    pending = session.scalars(
        select(AgentDecisionRequest)
        .where(AgentDecisionRequest.task_id == task.id)
        .where(AgentDecisionRequest.status == "pending"),
    ).first()
    if pending:
        lines.append(f"Pending Decision: {pending.decision_type} — {pending.question}")

    return "\n".join(lines)


def _build_recent_messages(session: Session, task_id: str, max_count: int) -> str:
    """Build a Markdown summary of recent non-system Agent messages across all runs."""
    rows = list(session.scalars(
        select(AgentMessage)
        .join(AgentRun, AgentMessage.run_id == AgentRun.id)
        .where(AgentRun.task_id == task_id)
        .where(AgentMessage.role != "system")
        .order_by(AgentMessage.created_at.desc())
        .limit(max_count),
    ).all())

    if not rows:
        return ""

    rows.reverse()
    lines = []
    for m in rows:
        role_tag = {"user": "User", "assistant": "Agent", "tool": "Tool"}.get(m.role, m.role)
        content = (m.content or "").strip()
        if not content:
            continue
        if len(content) > 500:
            content = content[:500] + "..."
        if content.startswith("[SystemAction]"):
            continue
        lines.append(f"[{role_tag}]: {content}")

    return "\n".join(lines)


def _build_recent_tool_calls(session: Session, task_id: str, max_count: int) -> str:
    """Build a Markdown summary of recent tool calls across all runs."""
    rows = list(session.scalars(
        select(AgentToolCall)
        .join(AgentRun, AgentToolCall.run_id == AgentRun.id)
        .where(AgentRun.task_id == task_id)
        .order_by(AgentToolCall.created_at.desc())
        .limit(max_count),
    ).all())

    if not rows:
        return ""

    rows.reverse()
    lines = []
    for tc in rows:
        input_summary = json.dumps(tc.input, ensure_ascii=False) if tc.input else "{}"
        if len(input_summary) > 200:
            input_summary = input_summary[:200] + "..."
        output = tc.output or {}
        output_summary = output.get("summary", "") if isinstance(output, dict) else ""
        status_tag = "OK" if tc.status == "completed" else "FAILED"
        lines.append(
            f"- [{status_tag}] {tc.tool_name}({input_summary})"
            + (f" -> {output_summary}" if output_summary else "")
        )

    return "\n".join(lines)
