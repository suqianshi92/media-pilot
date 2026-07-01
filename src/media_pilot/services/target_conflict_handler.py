"""Target conflict decision handlers.

处理 `target_conflict` 决策的两个选项：
- overwrite_target: 确定性后端基于现有发布计划执行覆盖，不调用 LLM。
- cancel_publish: 任务进入 agent_failed，failure_reason 写明"用户取消目标冲突处理"。

target_conflict 决策与 post_revoke_action 决策一样，由 services/decision_reply
在 `is_target_conflict` 分支分发到此模块；不同于 post_revoke_action 的是，
本模块的 handler 全部是确定性后端路径，不需要继续 AgentRun。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from media_pilot.config import AppConfig
from media_pilot.orchestration.state_machine import IngestTaskStatus

logger = logging.getLogger(__name__)

CANCEL_PUBLISH_FAILURE_REASON = "用户取消目标冲突处理"


def handle_overwrite_target(
    *,
    session: Session,
    config: AppConfig,
    decision,
):
    """基于 decision payload 中的 write_plan 确定性执行覆盖。

    不调用 LLM。覆盖执行成功后任务进入 `library_import_complete`。

    注意: 主视频路径不再从 decision.payload 取 (那是历史 bug, 决策创建
    时如果 MediaSourceSelection 还没写, payload 里的 final_target_file
    是用目录名后缀拼出来的, 错), 也不再回退到 `task.source_path` (目录)
    — 而是每次都用 `resolve_main_video_for_publish` 重新解析, 保证拿到
    真实主视频文件. 因此 decision.payload 只用作审计, 不再作为路径源.
    """
    import httpx

    from media_pilot.orchestration.jellyfin_movie_writer import (
        build_movie_write_plan,
        execute_movie_write,
    )
    from media_pilot.repository.repositories import (
        AgentRunRepository,
        IngestTaskRepository,
        MetadataDetailRepository,
    )
    from media_pilot.services.library_root_resolver import resolve_library_root
    from media_pilot.services.publish_plan_draft import _orm_detail_to_adapter
    from media_pilot.services.video_source_resolver import (
        resolve_main_video_for_publish,
    )

    task_id = decision.task_id

    payload = decision.payload if isinstance(getattr(decision, "payload", None), dict) else {}
    if payload.get("publish_mode") == "no_metadata":
        return _handle_overwrite_no_metadata_target(
            session=session, config=config, decision=decision,
        )

    task_repo = IngestTaskRepository(session)
    task = task_repo.get(task_id)
    if task is None:
        raise ValueError({"status_code": 404, "detail": f"Task {task_id} not found"})

    detail_repo = MetadataDetailRepository(session)
    orm_detail = detail_repo.get_for_task(task_id)
    if orm_detail is None:
        raise ValueError({
            "status_code": 409,
            "detail": "MetadataDetail not found; cannot execute overwrite",
        })

    # 剧集 overwrite 与电影 overwrite 分发 — 剧集只覆盖当前 EpisodeMapping
    # 涉及的 episode 文件 / NFO / 同源字幕, 不删除整个 show 或 season 目录.
    if orm_detail.media_type == "show":
        return _handle_overwrite_show_target(
            session=session, config=config, decision=decision,
            task=task, orm_detail=orm_detail,
        )

    adapter_detail = _orm_detail_to_adapter(orm_detail)

    # 共享解析器: 拿真实主视频文件 (单视频目录自动补写 selection).
    resolve_result = resolve_main_video_for_publish(session, task, config=config)
    if resolve_result.error_code is not None or resolve_result.video_path is None:
        # 解析失败 → 结构化 4xx, 不抛 500. task 保持 waiting_user,
        # 决策保持已 decided 状态, 用户可重试或走 cancel_publish.
        raise ValueError({
            "status_code": 422,
            "code": resolve_result.error_code or "invalid_video_source",
            "detail": (
                f"Cannot resolve main video for overwrite: "
                f"{resolve_result.error_message}"
            ),
        })
    video_source = resolve_result.video_path

    # 重建发布计划（基于当前 detail / source）；conflict 必须仍然存在。
    plan = build_movie_write_plan(
        movies_dir=resolve_library_root(
            config, media_type="movie", provider=orm_detail.provider,
        ),
        source_path=video_source,
        detail=adapter_detail,
        task_id=task.id,
        provider=orm_detail.provider,
    )

    # 注意: 历史稳定守卫 (saved_dir / saved_file 比对) 已删除 — 决策创建
    # 时 payload 里的 final_target_file 在 single_video_dir 路径上可能是
    # 错的 (用了目录名后缀), 用户回 overwrite 时的当前 plan 才是真相. 重新
    # 解析出的 plan 必然对应用户选定的最新 source, 直接执行覆盖即可.

    # 覆盖：检测到冲突时自动让 execute_movie_write 进入覆盖路径。
    try:
        with httpx.Client(timeout=10) as client:
            write_result = execute_movie_write(
                session,
                task_id=task.id,
                source_path=video_source,
                detail=adapter_detail,
                plan=plan,
                client=client,
                provider=orm_detail.provider,
                force_overwrite=True,
            )
    except Exception as exc:
        logger.exception("overwrite_target failed: %s", exc)
        # 不抛 500 → 422 结构化失败, 让前端展示并允许用户重试.
        raise ValueError({
            "status_code": 422,
            "code": "movie_write_failed",
            "detail": f"Movie overwrite failed: {exc}",
            "retryable": True,
        }) from exc

    if write_result.status in ("succeeded", "warning"):
        task.status = IngestTaskStatus.LIBRARY_IMPORT_COMPLETE
        task.current_step = IngestTaskStatus.LIBRARY_IMPORT_COMPLETE
        task.metadata_status = "complete"
        # 关联 AgentRun 标记为 completed
        run_repo = AgentRunRepository(session)
        run = run_repo.get(decision.run_id)
        if run is not None:
            run_repo.update_status(run, status="completed", current_step="library_import_complete")
        session.flush()
        return {
            "outcome": "published",
            "final_target_dir": str(plan.final_target_dir),
            "final_target_file": str(plan.final_target_file),
            "warnings": write_result.warnings,
        }

    # 实际未发布成功 → 422 让前端展示, 不抛 500.
    raise ValueError({
        "status_code": 422,
        "code": "movie_write_failed",
        "detail": f"Movie overwrite ended with status={write_result.status}",
        "retryable": True,
    })


def _handle_overwrite_no_metadata_target(
    *,
    session: Session,
    config: AppConfig,
    decision,
):
    from media_pilot.repository.repositories import AgentRunRepository, IngestTaskRepository
    from media_pilot.services.no_metadata_publish import publish_without_metadata
    from media_pilot.services.post_publish_cleanup import run_post_publish_source_cleanup

    task_id = decision.task_id
    task_repo = IngestTaskRepository(session)
    task = task_repo.get(task_id)
    if task is None:
        raise ValueError({"status_code": 404, "detail": f"Task {task_id} not found"})

    result = publish_without_metadata(
        session=session, config=config, task_id=task_id, force_overwrite=True,
        allow_agent_running=True,
        library_target=(decision.payload or {}).get("library_target"),
    )
    if result.status != "published":
        raise ValueError({
            "status_code": 422,
            "code": "no_metadata_write_failed",
            "detail": result.summary,
            "retryable": True,
        })

    cleanup = run_post_publish_source_cleanup(
        session=session, config=config, task_id=task_id, run_id=decision.run_id,
    )
    run_repo = AgentRunRepository(session)
    run = run_repo.get(decision.run_id)
    if run is not None and not cleanup.decision_requested:
        run_repo.update_status(run, status="completed", current_step="no_metadata_published")
    session.flush()
    return {
        "outcome": "published",
        "final_target_dir": result.final_target_dir,
        "final_target_file": result.final_target_file,
        "warnings": result.warnings,
    }


def handle_cancel_publish(
    *,
    session: Session,
    config: AppConfig,
    decision,
):
    """取消本次目标冲突处理：任务进入 agent_failed。

    `failure_reason` 写明"用户取消目标冲突处理"，对应 AgentRun 标记 failed，
    系统不再自动重试或继续。
    """
    from media_pilot.repository.repositories import (
        AgentRunRepository,
        IngestTaskRepository,
    )

    task_repo = IngestTaskRepository(session)
    task = task_repo.get(decision.task_id)
    if task is not None:
        task.status = IngestTaskStatus.AGENT_FAILED
        task.current_step = IngestTaskStatus.AGENT_FAILED
        task.failure_reason = CANCEL_PUBLISH_FAILURE_REASON
        task_repo.update_status(
            task,
            status=IngestTaskStatus.AGENT_FAILED,
            current_step=IngestTaskStatus.AGENT_FAILED,
            failure_reason=CANCEL_PUBLISH_FAILURE_REASON,
        )

    run_repo = AgentRunRepository(session)
    run = run_repo.get(decision.run_id)
    if run is not None:
        run_repo.update_status(run, status="failed", current_step=IngestTaskStatus.AGENT_FAILED)

    session.flush()
    return {
        "outcome": "cancelled",
        "task_id": decision.task_id,
        "failure_reason": CANCEL_PUBLISH_FAILURE_REASON,
    }


def _handle_overwrite_show_target(
    *,
    session: Session,
    config: AppConfig,
    decision,
    task,
    orm_detail,
):
    """剧集 overwrite_target: 只覆盖当前 EpisodeMapping 的 episode 产物。

    不删除整个 show 目录或 season 目录; 不修改其他无关 episode 的 NFO / 视频 /
    字幕. 与电影 overwrite 的"覆盖整个 movie 目录"语义不同, 走
    `execute_show_write(force_overwrite=True)` 路径, 后者只清理 plan 中
    当前 EpisodeTarget 对应的 file / NFO / 同源字幕.
    """
    import httpx

    from media_pilot.orchestration.jellyfin_show_writer import (
        EpisodeTarget,
        build_show_write_plan,
        execute_show_write,
    )
    from media_pilot.repository.repositories import (
        AgentRunRepository,
        EpisodeMappingRepository,
        IngestTaskRepository,
    )
    from media_pilot.services.publish_plan_draft import _orm_detail_to_adapter
    from media_pilot.services.show_structure_analysis import (
        STATUS_AUTO_PUBLISHABLE,
        get_persisted_show_structure,
    )

    task_id = task.id
    adapter_detail = _orm_detail_to_adapter(orm_detail)

    # EpisodeMapping 必须存在 — publish 时已经持久化过.
    show_struct = get_persisted_show_structure(
        session=session, task_id=task_id,
    )
    if show_struct is None or show_struct.status != STATUS_AUTO_PUBLISHABLE:
        raise ValueError({
            "status_code": 409,
            "detail": (
                f"No persisted EpisodeMapping for task {task_id}; "
                "cannot execute show overwrite."
            ),
        })

    mappings = EpisodeMappingRepository(session).get_by_task(task_id)
    if not mappings:
        raise ValueError({
            "status_code": 409,
            "detail": f"EpisodeMapping repository returned empty for {task_id}",
        })

    episodes: list[EpisodeTarget] = []
    for m in sorted(mappings, key=lambda x: (x.season, x.episode)):
        source = Path(m.file_path)
        if not source.exists() or not source.is_file():
            raise ValueError({
                "status_code": 422,
                "detail": (
                    f"Episode file no longer exists: {source}. "
                    "Task may have lost source files."
                ),
            })
        episodes.append(EpisodeTarget(
            episode=m.episode, season=m.season,
            source_file=source, target_file=Path(""),
        ))

    plan = build_show_write_plan(
        shows_dir=config.shows_dir,
        episodes=episodes,
        detail=adapter_detail,
        task_id=task.id,
        provider=orm_detail.provider,
    )

    try:
        with httpx.Client(timeout=10) as client:
            write_result = execute_show_write(
                session,
                task_id=task.id,
                detail=adapter_detail,
                plan=plan,
                client=client,
                provider=orm_detail.provider,
                force_overwrite=True,
            )
    except Exception as exc:
        logger.exception("show overwrite_target failed: %s", exc)
        raise ValueError({
            "status_code": 422,
            "code": "show_write_failed",
            "detail": f"Show overwrite failed: {exc}",
            "retryable": True,
        }) from exc

    if write_result.status in ("succeeded", "warning"):
        task.status = IngestTaskStatus.LIBRARY_IMPORT_COMPLETE
        task.current_step = IngestTaskStatus.LIBRARY_IMPORT_COMPLETE
        IngestTaskRepository(session).update_status(
            task,
            status=IngestTaskStatus.LIBRARY_IMPORT_COMPLETE,
            current_step=IngestTaskStatus.LIBRARY_IMPORT_COMPLETE,
        )
        run_repo = AgentRunRepository(session)
        run = run_repo.get(decision.run_id)
        if run is not None:
            run_repo.update_status(
                run, status="completed",
                current_step=IngestTaskStatus.LIBRARY_IMPORT_COMPLETE,
            )
        session.flush()
        return {
            "outcome": "published",
            "final_target_dir": str(plan.final_target_dir),
            "warnings": write_result.warnings,
            "media_type": "show",
        }

    raise ValueError({
        "status_code": 422,
        "code": "show_write_failed",
        "detail": (
            f"Show overwrite ended with status={write_result.status}"
        ),
        "retryable": True,
    })
