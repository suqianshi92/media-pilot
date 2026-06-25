"""Post-revoke decision handlers.

处理撤回发布后的三种用户选择：
- reingest_with_new_search: 清除旧候选和元数据，Agent 从搜索阶段重新开始
- reingest_with_existing_metadata: 保留元数据，Agent 从发布阶段继续
- delete_task_input: 返回特殊状态，前端展示删除预检（实际删除在 delete_unpublished）
"""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from media_pilot.config import AppConfig


def handle_reingest_with_new_search(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
    mock_llm_client=None,
):
    """清除旧候选和元数据详情，创建新 AgentRun 从搜索开始。"""
    from media_pilot.repository.models import MediaCandidate, MetadataDetail

    session.execute(delete(MediaCandidate).where(MediaCandidate.task_id == task_id))
    session.execute(delete(MetadataDetail).where(MetadataDetail.task_id == task_id))
    session.flush()

    from media_pilot.agent.runner import run_agent_turn

    return run_agent_turn(
        session=session,
        config=config,
        task_id=task_id,
        mode="auto_ingest",
        mock_llm_client=mock_llm_client,
        initial_message=(
            f"[SystemAction] 用户选择重新搜索并入库。"
            f"请从元数据搜索开始处理任务 {task_id}。"
        ),
    )


def handle_reingest_with_existing_metadata(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
    mock_llm_client=None,
):
    """保留 MetadataDetail，清理发布上下文，创建新 AgentRun 从发布阶段继续。"""
    from sqlalchemy import select

    from media_pilot.repository.models import MetadataDetail

    detail = session.scalars(
        select(MetadataDetail).where(MetadataDetail.task_id == task_id)
    ).first()

    if detail is None:
        raise ValueError({
            "status_code": 400,
            "detail": "没有可用的元数据详情，无法沿用现有元数据重新入库",
        })

    from media_pilot.orchestration.revoke_publish import _cleanup_publish_context

    _cleanup_publish_context(session, task_id)

    from media_pilot.agent.runner import run_agent_turn

    return run_agent_turn(
        session=session,
        config=config,
        task_id=task_id,
        mode="auto_ingest",
        mock_llm_client=mock_llm_client,
        initial_message=(
            f"[SystemAction] 用户选择沿用现有元数据重新入库。"
            f"元数据详情已保留，请从发布计划阶段继续处理任务 {task_id}。"
        ),
    )
