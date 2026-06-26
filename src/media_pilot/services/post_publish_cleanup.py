"""Post-publish source cleanup bridge for deterministic publish paths."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from media_pilot.config import AppConfig


@dataclass(frozen=True, kw_only=True)
class PostPublishCleanupResult:
    status: str
    summary: str
    data: dict = field(default_factory=dict)

    @property
    def decision_requested(self) -> bool:
        return bool(self.data.get("decision_requested"))


def run_post_publish_source_cleanup(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
    run_id: str | None,
) -> PostPublishCleanupResult:
    """Run the existing source cleanup policy after deterministic publish.

    Manual metadata selection and select-metadata decision replies can publish
    without returning control to the LLM. They still need the same post-publish
    source cleanup semantics as the Agent mainline, so this helper delegates to
    the existing ``handle_source_cleanup`` tool instead of duplicating policy.
    Cleanup failure must not turn a successful library publish into a publish
    failure; the tool records a warning OperationRecord and leaves the task in
    ``library_import_complete``.
    """
    from media_pilot.agent.tools.base import ToolContext
    from media_pilot.agent.tools.write import make_handle_source_cleanup

    tool = make_handle_source_cleanup()
    result = tool.handler(
        ToolContext(
            session=session,
            config=config,
            task_id=task_id,
            run_id=run_id,
        ),
        {"task_id": task_id},
    )
    return PostPublishCleanupResult(
        status=result.status,
        summary=result.summary,
        data=result.data if isinstance(result.data, dict) else {},
    )
