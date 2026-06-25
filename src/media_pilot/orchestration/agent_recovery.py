"""Agent 运行时恢复：处理服务重启后的 stale AgentRun。"""

import logging

from sqlalchemy.orm import Session, sessionmaker

from media_pilot.orchestration.state_machine import IngestTaskStatus
from media_pilot.repository.repositories import AgentRunRepository, IngestTaskRepository

logger = logging.getLogger(__name__)


def recover_stale_agent_runs(session_factory: sessionmaker[Session]) -> int:
    """将启动时所有 active AgentRun 标记为 failed。

    返回被恢复的 run 数量。
    """
    count = 0
    with session_factory() as session:
        run_repo = AgentRunRepository(session)
        task_repo = IngestTaskRepository(session)
        active_runs = run_repo.list_active()

        for run in active_runs:
            run_repo.update_status(
                run,
                status="failed",
                error_message="服务重启中断了 Agent 运行。",
            )
            task = task_repo.get(run.task_id)
            if task is not None and task.status == IngestTaskStatus.AGENT_RUNNING:
                task_repo.update_status(
                    task,
                    status=IngestTaskStatus.AGENT_FAILED,
                    current_step="agent_interrupted",
                    failure_reason="Agent 运行被服务重启中断。",
                )
            count += 1
            logger.warning(
                "恢复 AgentRun %s (task %s)：标记为 failed（重启）",
                run.id,
                run.task_id,
            )

        session.commit()

    if count > 0:
        logger.info("Agent 恢复完成：标记了 %d 个 stale run 为 failed", count)
    return count
