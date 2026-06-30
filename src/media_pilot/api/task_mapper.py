"""IngestTask → TaskSummary 映射辅助函数"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from media_pilot.api.task_dtos import (
    AgentStatusSummary,
    AuditLogDto,
    ConfidenceLevel,
    DownloadTaskSummary,
    FileAssetDto,
    MediaSourceCandidateFile,
    MediaSourceSelectionDto,
    MetadataCandidateDto,
    MetadataDetailDto,
    MetadataPersonDto,
    OperationRecordDto,
    ProviderCallDto,
    SearchKeywordDto,
    ShowStructureSummaryDto,
    TaskDetailDto,
    TaskStatusSummary,
    TaskSummary,
    TimelineEventDto,
    EpisodeMappingDto,
    WritePlanDto,
    WriteResultDto,
)
from media_pilot.repository.models import (
    AdapterCall,
    AgentDecisionRequest,
    AgentMessage,
    AgentRun,
    AuditLog,
    DownloadTask,
    EpisodeMapping,
    FileAsset,
    IngestTask,
    MediaCandidate,
    MediaSourceSelection,
    MetadataDetail,
    OperationRecord,
    SearchKeywordRecord,
    WritePlan,
    WriteResult,
)

logger = logging.getLogger(__name__)


def map_download_task_to_summary(task: DownloadTask) -> DownloadTaskSummary:
    """将 DownloadTask ORM 映射为 DownloadTaskSummary DTO"""
    return DownloadTaskSummary(
        id=task.id,
        title=task.title,
        source=task.source,
        qb_hash=task.qb_hash,
        save_path=task.save_path,
        content_path=task.content_path,
        progress=task.progress,
        download_speed_bytes_per_second=task.download_speed_bytes_per_second,
        upload_speed_bytes_per_second=task.upload_speed_bytes_per_second,
        seeders=task.seeders,
        leechers=task.leechers,
        connections=task.connections,
        qb_state=task.qb_state,
        status=task.status,  # type: ignore[arg-type]
        error_message=task.error_message,
        ingest_task_id=task.ingest_task_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _confidence_level(confidence: float | None) -> ConfidenceLevel:
    if confidence is None:
        return "unknown"
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def _determine_file_format(
    source_selection: MediaSourceSelection | None,
    download_summary: DownloadTaskSummary | None,
) -> str | None:
    """判定实际处理格式：MKV / MP4 / BDMV / ISO / 目录 / 未知"""
    selected_path = None
    bdmv_detected = False

    if source_selection is not None:
        selected_path = source_selection.selected_path
        payload = source_selection.payload or {}
        bdmv_detected = bool(
            payload.get("bdmv_detected") or payload.get("source_kind") == "bdmv"
        )

    # 优先从主媒体选择结果判定
    if selected_path:
        return _format_from_path(selected_path)

    # 其次识别 BDMV
    if bdmv_detected:
        return "BDMV"

    # 再从下载任务内容路径判定
    if download_summary is not None and download_summary.content_path:
        return _format_from_path(download_summary.content_path)

    return None


def _format_from_path(file_path: str) -> str:
    """从路径提取文件格式标签"""
    try:
        p = Path(file_path)
    except Exception:
        return "未知"

    if p.is_dir() or p.suffix == "" or p.suffix == ".":
        return "目录"

    ext = p.suffix.lower()
    # ISO 光盘映像
    if ext == ".iso":
        return "ISO"
    # 已知视频扩展名 → MKV / MP4 / AVI / TS 等
    _KNOWN_VIDEO = frozenset({
        ".avi", ".m2ts", ".m4v", ".mkv", ".mov",
        ".mp4", ".mpeg", ".mpg", ".ts", ".webm", ".wmv",
    })
    if ext in _KNOWN_VIDEO:
        return ext.lstrip(".").upper()

    return "未知"


def _title_from_source_path(source_path: str) -> str | None:
    """从源路径提取可能的标题"""
    try:
        name = Path(source_path).name
        # 去掉扩展名
        stem = Path(name).stem if "." in name else name
        if stem:
            return stem
    except Exception:
        logger.warning("提取源路径标题失败: %s", source_path, exc_info=True)
    return None



def _format_from_path(file_path: str) -> str:
    """从路径提取文件格式标签"""
    try:
        p = Path(file_path)
    except Exception:
        return "未知"

    if p.is_dir() or p.suffix == "" or p.suffix == ".":
        return "目录"

    ext = p.suffix.lower()
    # ISO 光盘映像
    if ext == ".iso":
        return "ISO"
    # 已知视频扩展名 → MKV / MP4 / AVI / TS 等
    _KNOWN_VIDEO = frozenset({
        ".avi", ".m2ts", ".m4v", ".mkv", ".mov",
        ".mp4", ".mpeg", ".mpg", ".ts", ".webm", ".wmv",
    })
    if ext in _KNOWN_VIDEO:
        return ext.lstrip(".").upper()

    return "未知"


def _title_from_source_path(source_path: str) -> str | None:
    """从源路径提取可能的标题"""
    try:
        name = Path(source_path).name
        # 去掉扩展名
        stem = Path(name).stem if "." in name else name
        if stem:
            return stem
    except Exception:
        logger.warning("提取源路径标题失败: %s", source_path, exc_info=True)
    return None


def _build_agent_status_index(
    session: Session, task_ids: list[str]
) -> dict[str, AgentStatusSummary]:
    """批量查询每个任务的最新 AgentRun 和 pending decision count"""
    if not task_ids:
        return {}

    # 1. 每个任务的最新 AgentRun
    run_rows = list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.task_id.in_(task_ids))
            .order_by(AgentRun.created_at.desc())
        )
    )
    latest_run_index: dict[str, AgentRun] = {}
    for run in run_rows:
        if run.task_id not in latest_run_index:
            latest_run_index[run.task_id] = run

    # 2. 每个任务的 pending decision count
    pending_counts: dict[str, int] = {}
    count_rows = session.execute(
        select(
            AgentDecisionRequest.task_id,
            func.count(AgentDecisionRequest.id),
        )
        .where(
            AgentDecisionRequest.task_id.in_(task_ids),
            AgentDecisionRequest.status == "pending",
        )
        .group_by(AgentDecisionRequest.task_id)
    ).all()
    pending_counts = {row[0]: row[1] for row in count_rows}

    # 3. 每个最新 run 的最新 assistant message 摘要
    run_ids = [r.id for r in latest_run_index.values()]
    latest_messages: dict[str, str] = {}
    if run_ids:
        msg_rows = list(
            session.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.run_id.in_(run_ids),
                    AgentMessage.role == "assistant",
                    AgentMessage.content.isnot(None),
                )
                .order_by(AgentMessage.created_at.desc())
            )
        )
        for msg in msg_rows:
            if msg.run_id not in latest_messages and msg.content:
                latest_messages[msg.run_id] = msg.content

    # 4. 组装结果
    result: dict[str, AgentStatusSummary] = {}
    for task_id in task_ids:
        run = latest_run_index.get(task_id)
        if run is None:
            result[task_id] = AgentStatusSummary(run_status="none")
        else:
            summary = latest_messages.get(run.id)
            result[task_id] = AgentStatusSummary(
                run_status=run.status,  # type: ignore[arg-type]
                latest_run_id=run.id,
                pending_decision_count=pending_counts.get(task_id, 0),
                latest_message_summary=summary[:200] if summary else None,
            )

    return result


def map_to_task_summaries(
    session: Session,
    tasks: list[IngestTask],
) -> list[TaskSummary]:
    """将 IngestTask 列表映射为 TaskSummary DTO 列表"""
    if not tasks:
        return []

    task_ids = [task.id for task in tasks]
    agent_status_index = _build_agent_status_index(session, task_ids)

    # 批量加载媒体源选择记录
    source_selection_index: dict[str, MediaSourceSelection] = {}
    if task_ids:
        sel_rows = session.scalars(
            select(MediaSourceSelection)
            .where(MediaSourceSelection.task_id.in_(task_ids))
        ).all()
        for row in sel_rows:
            existing = source_selection_index.get(row.task_id)
            if existing is None or row.id > existing.id:
                source_selection_index[row.task_id] = row

    result: list[TaskSummary] = []
    for task in tasks:
        # 提取标题和年份：仅依赖 IngestTask 直写字段；
        # 旧 ConfirmationRequest.ai_candidate fallback 已随 ConfirmationRequest 一起下线。
        title = task.title
        year = task.year

        if not title:
            title = _title_from_source_path(task.source_path)

        # 生成最近消息
        latest_message = _latest_message(task)

        status_summary = TaskStatusSummary(
            status=task.status,  # type: ignore[arg-type]
            current_step=task.current_step,  # type: ignore[arg-type]
            failure_reason=task.failure_reason,
            confidence=task.confidence,
            confidence_level=_confidence_level(task.confidence),
            latest_message=latest_message,
        )

        # 下载任务信息（当入库任务来自下载时）
        download_summary = None
        if task.source_download_task_id:
            dl_task = session.get(DownloadTask, task.source_download_task_id)
            if dl_task is not None:
                download_summary = map_download_task_to_summary(dl_task)

        result.append(
            TaskSummary(
                id=task.id,
                source_path=task.source_path,
                title=title,
                year=year,
                media_type=task.media_type,  # type: ignore[arg-type]
                metadata_status=getattr(task, "metadata_status", "unknown") or "unknown",
                can_confirm=_can_confirm(agent_status_index.get(task.id)),
                flow_type=(
                    "managed_download" if download_summary is not None
                    else "external_import"
                ),
                total_status=_compute_total_status(task, download_summary),
                file_format=_determine_file_format(
                    source_selection_index.get(task.id), download_summary
                ),
                created_at=task.created_at,
                updated_at=task.updated_at,
                status_summary=status_summary,
                download_task=download_summary,
                agent_status_summary=agent_status_index.get(task.id),
            )
        )

    return result


def _compute_total_status(
    ingest_task: IngestTask,
    download_summary: DownloadTaskSummary | None,
) -> str:
    """计算统一流程卡片的 total_status（返回 raw key，前端 i18n 翻译）。

    - managed_download: downloading → completed_pending_ingest → 入库阶段状态
    - external_import: 直接使用入库任务状态 raw key
    """
    ingest_status = ingest_task.status

    if download_summary is None:
        # 外部导入：返回入库状态 raw key
        return ingest_status

    # 系统内下载：按阶段推进
    dl_status = download_summary.status
    if dl_status in ("submitted", "downloading", "submitting"):
        return "downloading"
    if dl_status in ("completed", "completed_pending_ingest"):
        if ingest_status == "discovered":
            return "completed_pending_ingest"
        return ingest_status
    if dl_status == "awaiting_sync":
        return "awaiting_sync"
    if dl_status in ("failed", "sync_failed"):
        return "sync_failed"
    # fallback
    return ingest_status


def _latest_message(task: IngestTask) -> str | None:
    """返回最新消息 key（前端 i18n 翻译）。

    - failure_reason 直接透传
    - 其他状态返回 current_step key（前端 getTaskStepLabel 翻译）

    注: ConfirmationRequest 已下线，阻塞原因不再从 options 读取；
    blocked reason 现在由 AgentDecisionRequest.payload / Agent 面板承载。
    """
    if task.failure_reason:
        return task.failure_reason

    return f"step:{task.current_step}" if task.current_step else None


def _can_confirm(agent_status: AgentStatusSummary | None) -> bool:
    """判断任务是否可由用户操作（Agent 等待用户回复且有 pending decision）。

    can_confirm 由 Agent 状态摘要 (run_status + pending_decision_count) 推导。
    """
    if agent_status is None:
        return False
    return (
        agent_status.run_status == "waiting_user"
        and agent_status.pending_decision_count > 0
    )


# ---- 任务详情映射 ----

def map_to_task_detail(
    session: Session,
    task: IngestTask,
    *,
    source_selection: MediaSourceSelection | None,
    keyword_record: SearchKeywordRecord | None,
    decided_candidate: MediaCandidate | None,
    metadata_detail: MetadataDetail | None,
    write_plan: WritePlan | None,
    write_result: WriteResult | None,
    file_assets: list[FileAsset],
    adapter_calls: list[AdapterCall],
    operation_records: list[OperationRecord],
    audit_logs: list[AuditLog],
    candidates: list[MediaCandidate],
    episode_mappings: list[EpisodeMapping] | None = None,
) -> TaskDetailDto:
    """将 IngestTask 及其关联数据映射为 TaskDetailDto"""
    task_summary = map_to_task_summaries(session, [task])[0]

    return TaskDetailDto(
        task=task_summary,
        source_selection=_map_source_selection(source_selection),
        search_keyword=_map_search_keyword(keyword_record),
        selected_candidate=_map_selected_candidate(decided_candidate),
        metadata_detail=_map_metadata_detail(task.id, metadata_detail, file_assets),
        write_plan=_map_write_plan(write_plan),
        write_result=_map_write_result(write_result),
        file_assets=[_map_file_asset(a) for a in file_assets],
        provider_calls=[_map_provider_call(c) for c in adapter_calls],
        operation_records=[_map_operation_record(r) for r in operation_records],
        audit_logs=[_map_audit_log(log) for log in audit_logs],
        timeline=_build_timeline(task, operation_records, audit_logs, adapter_calls),
        episode_mappings=[
            EpisodeMappingDto(
                file_path=m.file_path,
                season=m.season,
                episode=m.episode,
                source=m.source,
            )
            for m in (episode_mappings or [])
        ],
        show_structure=_build_show_structure_summary(
            task=task,
            episode_mappings=episode_mappings or [],
        ),
    )


def _build_show_structure_summary(
    *,
    task: IngestTask,
    episode_mappings: list[EpisodeMapping],
) -> ShowStructureSummaryDto | None:
    """构造剧集结构可读摘要.

    - 仅当 task.media_type == "show" 时返回 DTO, 否则 None (电影任务
      不需要剧集结构摘要).
    - episode_mappings 为空 + media_type=show → 任务可能还在
      prepare_show_structure 之前 / 之后被 agent_failed 拒绝. 这种情
      况下从 task.failure_reason / current_step 推断 block_reason
      (例如 cross_season / sparse_episodes / season_0), 暴露可读
      文案避免前端只看到 max_steps / agent_failed 这种内部标识.
    - mapping_mode 从 episode_mappings 的 source 字段推断: 任何
      ``source == "absolute"`` → absolute; 否则 standard_sxxexx.
      spec: task-operator-workspace / 工作台展示剧集绝对集数映射摘要.
    """
    if task.media_type != "show":
        return None

    if not episode_mappings:
        # 任务尝试过剧集结构但失败 — 用 current_step / failure_reason
        # 推断 block_reason. block_reason_label 走 i18n key, 给前端
        # 翻译, 不暴露 raw JSON.
        block_reason = None
        block_reason_label = None
        block_reason_message = None
        if task.failure_reason:
            block_reason = task.failure_reason
            block_reason_label = _show_block_reason_label_key(block_reason)
            block_reason_message = _show_block_reason_message(
                block_reason, task.current_step,
            )
        elif task.current_step in _KNOWN_SHOW_BLOCK_STEPS:
            block_reason = task.current_step
            block_reason_label = _show_block_reason_label_key(block_reason)
            block_reason_message = _show_block_reason_message(
                block_reason, task.current_step,
            )
        return ShowStructureSummaryDto(
            status="blocked" if block_reason else "unknown",
            season=None,
            episode_range=None,
            episode_count=0,
            mapping_mode="unknown",
            mapping_mode_label="unknown",
            block_reason=block_reason,
            block_reason_label=block_reason_label,
            block_reason_message=block_reason_message,
            detected_show_title=None,
        )

    # 已有 EpisodeMapping — 计算 range / mapping_mode
    seasons = sorted({m.season for m in episode_mappings})
    season = seasons[0] if seasons else None
    episodes = sorted(
        [m.episode for m in episode_mappings if m.season == season],
    ) if season is not None else []
    if season is None or not episodes:
        episode_range = None
    elif len(episodes) == 1:
        episode_range = f"S{season:02d}E{episodes[0]:02d}"
    else:
        episode_range = (
            f"S{season:02d}E{episodes[0]:02d}"
            f"-E{episodes[-1]:02d}"
        )

    mapping_mode = (
        "absolute" if any(m.source == "absolute" for m in episode_mappings)
        else "standard_sxxexx"
    )
    mapping_mode_label = (
        "absolute_episode_numbering"
        if mapping_mode == "absolute"
        else "standard_sxxexx"
    )

    return ShowStructureSummaryDto(
        status="auto_publishable",
        season=season,
        episode_range=episode_range,
        episode_count=len(episode_mappings),
        mapping_mode=mapping_mode,
        mapping_mode_label=mapping_mode_label,
        block_reason=None,
        block_reason_label=None,
        block_reason_message=None,
        detected_show_title=None,
    )


# show block_reason → i18n key 映射 (与 agent/tools/show.py 的
# _human_block_reason_label 保持一致 — 一处定义, 多处复用容易漂移,
# 此处复刻以避免 mapper 反向依赖 agent 层).
_SHOW_BLOCK_REASON_LABEL_KEYS: dict[str, str] = {
    "cross_season_not_supported": "show_block_cross_season",
    "sparse_episodes_not_supported": "show_block_sparse_episodes",
    "specials_season_0_not_supported": "show_block_season_0_specials",
    "multi_episode_in_single_file_not_supported": "show_block_multi_episode_in_single_file",
    "no_video_files_found": "show_block_no_video_files",
    "no_clear_show_structure": "show_block_no_clear_show_structure",
    "absolute_episode_requires_metadata_detail": "show_block_absolute_needs_metadata",
    "absolute_episode_out_of_provider_range": "show_block_absolute_out_of_range",
    "absolute_episode_sparse_not_supported": "show_block_absolute_sparse",
    "absolute_episode_ambiguous_not_supported": "show_block_absolute_ambiguous",
}

_KNOWN_SHOW_BLOCK_STEPS: set[str] = set(_SHOW_BLOCK_REASON_LABEL_KEYS.keys())


def _show_block_reason_label_key(block_reason: str) -> str:
    return _SHOW_BLOCK_REASON_LABEL_KEYS.get(
        block_reason, "show_block_unknown",
    )


def _show_block_reason_message(
    block_reason: str, current_step: str | None,
) -> str | None:
    """block_reason → 给最终用户看的人话.

    注意: 不暴露 raw JSON / 内部 step 名. 前端 i18n 翻译 ``label``
    key, 这里返回的是 raw message (英中混排) 作为 fallback / SSR 阶段
    一次性使用. spec: 工作台展示剧集绝对集数映射摘要 → 搜索循环收口
    结果 (Scenario: 绝对集数映射不明确).
    """
    if not block_reason:
        return None
    messages = {
        "cross_season_not_supported": (
            "跨季剧集暂不支持自动入库"
        ),
        "sparse_episodes_not_supported": (
            "集号不连续的剧集 (例如缺 E02) 暂不支持自动入库"
        ),
        "specials_season_0_not_supported": (
            "Season 0 特别篇暂不支持自动入库"
        ),
        "multi_episode_in_single_file_not_supported": (
            "单文件包含多集的剧集暂不支持自动入库"
        ),
        "no_video_files_found": (
            "未找到视频文件"
        ),
        "no_clear_show_structure": (
            "无法识别剧集结构 (缺少 SxxExx 命名)"
        ),
        "absolute_episode_requires_metadata_detail": (
            "资源看起来使用绝对集数, 需要先获取剧集元数据详情来验证季覆盖范围"
        ),
        "absolute_episode_out_of_provider_range": (
            "绝对集数超出 provider season 覆盖范围"
        ),
        "absolute_episode_sparse_not_supported": (
            "绝对集数不连续, 暂不支持自动入库"
        ),
        "absolute_episode_ambiguous_not_supported": (
            "绝对集数命名歧义, 暂不支持自动入库"
        ),
    }
    return messages.get(block_reason)


def _map_source_selection(selection: MediaSourceSelection | None) -> MediaSourceSelectionDto | None:
    if selection is None:
        return None

    payload = selection.payload or {}
    excluded_paths = payload.get("excluded_paths") or []
    candidate_paths = payload.get("candidate_paths") or []

    def _make_candidate_file(path: str) -> MediaSourceCandidateFile:
        name = Path(path).name
        return MediaSourceCandidateFile(path=path, name=name, reason="")

    return MediaSourceSelectionDto(
        input_path=selection.input_path,
        selected_path=selection.selected_path,
        confidence=selection.confidence,
        reason=selection.reason,
        bdmv_detected=bool(
            payload.get("bdmv_detected") or payload.get("source_kind") == "bdmv"
        ),
        stream_file_count=payload.get("stream_file_count"),
        candidate_files=[_make_candidate_file(p) for p in candidate_paths],
        excluded_files=[_make_candidate_file(p) for p in excluded_paths],
    )


def _map_search_keyword(record: SearchKeywordRecord | None) -> SearchKeywordDto | None:
    if record is None:
        return None

    payload = record.payload or {}
    source = record.source
    if source not in ("rule", "llm", "manual"):
        source = "rule"

    return SearchKeywordDto(
        keyword=record.keyword,
        source=source,  # type: ignore[arg-type]
        confidence=record.confidence,
        reason=record.reason,
        rule_keyword=payload.get("rule_keyword"),
        explanation=payload.get("explanation"),
        quality_tokens=list(payload.get("quality_tokens") or []),
        tokens_removed=list(payload.get("tokens_removed") or []),
    )


def _map_selected_candidate(
    candidate: MediaCandidate | None,
) -> MetadataCandidateDto | None:
    if candidate is None:
        return None

    payload = candidate.payload or {}
    return MetadataCandidateDto(
        provider=candidate.source,
        provider_id=candidate.external_id or "",
        title=candidate.title or "",
        original_title=candidate.original_title,
        year=candidate.year,
        media_type=candidate.media_type or "movie",  # type: ignore[arg-type]
        overview=payload.get("overview"),
        poster_url=payload.get("poster_url"),
        confidence=candidate.confidence,
        match_reason=candidate.reason,
    )


def _build_task_asset_url(task_id: str, asset_role: str) -> str:
    return f"/api/v1/tasks/{task_id}/assets/{asset_role}"


def _resolve_metadata_image_urls(
    task_id: str,
    payload: dict,
    file_assets: list[FileAsset],
) -> tuple[str | None, str | None, str | None]:
    asset_role_map = {
        "library_poster": "poster",
        "library_fanart": "fanart",
        "library_clearlogo": "clearlogo",
    }
    resolved_urls: dict[str, str | None] = {
        "poster": None,
        "fanart": None,
        "clearlogo": None,
    }

    for asset in file_assets:
        asset_role = asset_role_map.get(asset.role)
        if asset_role is None or resolved_urls[asset_role] is not None:
            continue
        resolved_urls[asset_role] = _build_task_asset_url(task_id, asset_role)

    images = payload.get("images") or {}
    if resolved_urls["poster"] is None:
        resolved_urls["poster"] = images.get("poster_url") or payload.get("poster_url")
    if resolved_urls["fanart"] is None:
        resolved_urls["fanart"] = images.get("backdrop_url") or payload.get("fanart_url")
    if resolved_urls["clearlogo"] is None:
        resolved_urls["clearlogo"] = images.get("logo_url") or payload.get("clearlogo_url")

    return (
        resolved_urls["poster"],
        resolved_urls["fanart"],
        resolved_urls["clearlogo"],
    )


def _map_metadata_detail(
    task_id: str,
    detail: MetadataDetail | None,
    file_assets: list[FileAsset],
) -> MetadataDetailDto | None:
    if detail is None:
        return None

    payload = detail.payload or {}
    credits = payload.get("credits") or {}
    external_ids = payload.get("external_ids") or {}
    external_payload = external_ids.get("payload") or {}
    poster_url, fanart_url, clearlogo_url = _resolve_metadata_image_urls(
        task_id, payload, file_assets
    )
    directors = [
        MetadataPersonDto(
            provider_id=d.get("provider_id"),
            name=d.get("name", ""),
            role=d.get("role"),
            profile_url=d.get("profile_url"),
            image_url=d.get("image_url"),
        )
        for d in (credits.get("directors") or payload.get("directors") or [])
    ]
    actors = [
        MetadataPersonDto(
            provider_id=a.get("provider_id"),
            name=a.get("name", ""),
            role=a.get("role"),
            profile_url=a.get("profile_url"),
            image_url=a.get("image_url"),
        )
        for a in (credits.get("actors") or payload.get("actors") or [])
    ]

    return MetadataDetailDto(
        provider=detail.provider,
        provider_id=detail.provider_id,
        media_type=detail.media_type,  # type: ignore[arg-type]
        title=detail.title,
        original_title=detail.original_title,
        year=detail.year,
        overview=payload.get("plot") or payload.get("overview"),
        release_date=payload.get("premiered") or payload.get("release_date"),
        runtime_minutes=payload.get("runtime_minutes"),
        rating=payload.get("rating"),
        tmdb_id=(
            str(payload.get("tmdb_id"))
            if payload.get("tmdb_id")
            else (
                str(external_payload.get("tmdb_id"))
                if external_payload.get("tmdb_id") is not None
                else None
            )
        ),
        imdb_id=(
            str(external_ids.get("imdb_id"))
            if external_ids.get("imdb_id") is not None
            else (
                str(payload.get("imdb_id"))
                if payload.get("imdb_id") is not None
                else None
            )
        ),
        genres=list(payload.get("genres") or []),
        countries=list(payload.get("countries") or payload.get("production_countries") or []),
        studios=list(payload.get("studios") or payload.get("production_companies") or []),
        directors=directors,
        actors=actors,
        poster_url=poster_url,
        fanart_url=fanart_url,
        clearlogo_url=clearlogo_url,
    )


def _map_write_plan(plan: WritePlan | None) -> WritePlanDto | None:
    if plan is None:
        return None
    payload = plan.payload or {}
    return WritePlanDto(
        target_dir=plan.target_dir,
        target_file=plan.target_file,
        nfo_path=plan.nfo_path,
        poster_path=payload.get("poster_path"),
        fanart_path=payload.get("fanart_path"),
        clearlogo_path=payload.get("clearlogo_path"),
        conflict_status=payload.get("conflict_status"),
        conflict_reason=payload.get("conflict_reason"),
    )


def _map_write_result(result: WriteResult | None) -> WriteResultDto | None:
    if result is None:
        return None
    payload = result.payload or {}
    status = result.status
    if status not in ("succeeded", "warning", "failed", "target_conflict"):
        status = "failed"
    return WriteResultDto(
        status=status,  # type: ignore[arg-type]
        failure_reason=payload.get("failure_reason"),
        warnings=list(payload.get("warnings") or []),
        written_paths=list(payload.get("written_paths") or []),
    )


def _map_file_asset(asset: FileAsset) -> FileAssetDto:
    return FileAssetDto(role=asset.role, path=asset.path, size_bytes=asset.size_bytes)


def _map_provider_call(call: AdapterCall) -> ProviderCallDto:
    status = call.status
    if status not in ("succeeded", "failed"):
        status = "failed"
    return ProviderCallDto(
        adapter_name=call.adapter_name,
        action=call.action,
        status=status,  # type: ignore[arg-type]
        error_message=call.error_message,
        created_at=call.created_at,
    )


def _map_operation_record(record: OperationRecord) -> OperationRecordDto:
    return OperationRecordDto(
        operation_type=record.operation_type,
        permission_level=record.permission_level,
        source_path=record.source_path,
        target_path=record.target_path,
        status=record.status,
        details=record.details or {},
        created_at=record.created_at,
    )


def _map_audit_log(log: AuditLog) -> AuditLogDto:
    return AuditLogDto(
        actor=log.actor,
        action=log.action,
        object_type=log.object_type,
        object_id=log.object_id,
        created_at=log.created_at,
        context=log.context or {},
    )




def _build_timeline(
    task: IngestTask,
    operation_records: list[OperationRecord],
    audit_logs: list[AuditLog],
    adapter_calls: list[AdapterCall],
) -> list[TimelineEventDto]:
    """从任务生命周期和适配器调用构建高价值业务事件时间线。

    白名单事件：task_created、download_requested、download_completed、
    prepare_workspace、media_source_selection、
    raw_search、llm_keyword_cleanup、metadata_candidate_selected、
    metadata_detail、write_metadata_assets、move_to_library、
    library_import_complete、target_conflict、agent_failed、failed。
    """
    events: list[TimelineEventDto] = []

    # 1. 任务创建
    events.append(
        TimelineEventDto(
            key="task_created",
            title="任务创建",
            detail=f"源文件: {task.source_path}",
            created_at=task.created_at,
            tone="default",
        )
    )

    # 1.5. 下载事件（仅 managed_download 流程）
    download_req = _find_operation(operation_records, "download_requested")
    if download_req:
        details = download_req.details or {}
        title_text = details.get("title", "")
        source_text = details.get("source", "")
        detail = f"来源: {source_text} / {title_text}" if source_text else "提交下载"
        events.append(
            TimelineEventDto(
                key="download_requested",
                title="提交下载",
                detail=detail,
                created_at=download_req.created_at,
                tone="default",
            )
        )

    download_done = _find_operation(operation_records, "download_completed")
    if download_done:
        details = download_done.details or {}
        content_path = details.get("content_path", "")
        path_name = Path(content_path).name if content_path else ""
        detail = f"文件: {path_name}" if path_name else "下载完成"
        events.append(
            TimelineEventDto(
                key="download_completed",
                title="下载完成",
                detail=detail,
                created_at=download_done.created_at,
                tone="success",
            )
        )

    # 2. 工作区准备
    import_op = _find_operation(operation_records, "prepare_workspace")
    if import_op:
        events.append(
            TimelineEventDto(
                key="prepare_workspace",
                title="准备工作区",
                detail="工作区目录: "
                f"{Path(import_op.target_path).name if import_op.target_path else '-'}",
                created_at=import_op.created_at,
                tone="default",
            )
        )

    # 3. 媒体源选择
    selection_op = _find_operation(operation_records, "media_source_selection")
    if selection_op:
        events.append(
            TimelineEventDto(
                key="media_source_selection",
                title="媒体源选择",
                detail=selection_op.reason or "已选择主媒体文件",
                created_at=selection_op.created_at,
                tone="default",
            )
        )

    # 4-5. Provider 搜索：raw 和 llm
    for call in adapter_calls:
        if call.action == "generate_search_keyword":
            resp = call.response_summary or {}
            kw = resp.get("keyword", "")
            conf = resp.get("confidence")
            events.append(
                TimelineEventDto(
                    key="llm_keyword_cleanup",
                    title="LLM 清洗关键词",
                    detail=f"关键词: {kw}" + (f" (置信度: {conf:.0%})" if conf else ""),
                    created_at=call.created_at,
                    tone="default",
                )
            )
        elif call.action == "search_movie":
            resp = call.response_summary or {}
            count = resp.get("candidate_count")
            events.append(
                TimelineEventDto(
                    key="raw_search" if "raw" in (call.request_summary or {}).get("keyword", "")
                        else "metadata_search",
                    title="检索元数据",
                    detail=f"返回 {count} 个候选"
                if count else "检索元数据",
                    created_at=call.created_at,
                    tone="default",
                )
            )

    # 6. 元数据详情
    detail_call = _find_adapter_call(adapter_calls, "get_movie_details")
    if detail_call:
        resp = detail_call.response_summary or {}
        title = resp.get("title", "")
        year = resp.get("year", "")
        detail = f"{title} ({year})" if title and year else (title or "获取详情")
        events.append(
            TimelineEventDto(
                key="metadata_detail",
                title="获取元数据详情",
                detail=detail,
                created_at=detail_call.created_at,
                tone="success" if detail_call.status == "succeeded" else "warning",
            )
        )

    # 7. 媒体资产写入（聚合）
    _WRITE_OPS = {"write_nfo", "download_poster", "download_fanart",
                  "download_clearlogo", "move_to_library"}
    write_ops = _find_operations(operation_records, _WRITE_OPS)
    if write_ops:
        paths = [op.target_path for op in write_ops if op.target_path]
        detail_text = ", ".join(Path(p).name for p in paths[:3])
        if len(paths) > 3:
            detail_text += f" 等 {len(paths)} 个文件"
        events.append(
            TimelineEventDto(
                key="write_metadata_assets",
                title="写入媒体资产",
                detail=detail_text or "写入 NFO 和图片",
                created_at=write_ops[0].created_at,
                tone="default",
            )
        )

    # 8. 入库完成或异常
    if task.status == "library_import_complete":
        events.append(
            TimelineEventDto(
                key="library_import_complete",
                title="入库完成",
                detail="影片已成功写入媒体库",
                created_at=task.updated_at,
                tone="success",
            )
        )
    elif task.status == "failed":
        events.append(
            TimelineEventDto(
                key="failed",
                title="处理失败",
                detail=task.failure_reason or "需要手动处理",
                created_at=task.updated_at,
                tone="error",
            )
        )

    # 9. 源文件清理事件 (入库后收尾).
    # 三个白名单 operation_type: source_input_kept / source_input_trashed /
    # source_input_cleanup_failed. 工具的 OperationRecord 详情 JSON 不会被
    # 直接展示, 这里抽出可读字段.
    _SOURCE_CLEANUP_OPS = {
        "source_input_kept",
        "source_input_trashed",
        "source_input_cleanup_failed",
    }
    for op in _find_operations(operation_records, _SOURCE_CLEANUP_OPS):
        details = op.details or {}
        path_name = (
            Path(op.source_path).name
            if op.source_path
            else Path(str(details.get("source_path", ""))).name
            if details.get("source_path")
            else ""
        )
        if op.operation_type == "source_input_kept":
            events.append(
                TimelineEventDto(
                    key="source_input_kept",
                    title="源文件保留",
                    detail=path_name or "源文件按策略保留",
                    created_at=op.created_at,
                    tone="default",
                )
            )
        elif op.operation_type == "source_input_trashed":
            target_name = (
                Path(str(op.target_path)).name
                if op.target_path
                else ""
            )
            detail_text = (
                f"{path_name} → 回收区/{target_name}"
                if path_name and target_name
                else (path_name or "源文件已移入回收区")
            )
            events.append(
                TimelineEventDto(
                    key="source_input_trashed",
                    title="源文件移入回收区",
                    detail=detail_text,
                    created_at=op.created_at,
                    tone="default",
                )
            )
        elif op.operation_type == "source_input_cleanup_failed":
            reason = str(details.get("reason", ""))
            events.append(
                TimelineEventDto(
                    key="source_input_cleanup_failed",
                    title="源文件清理失败",
                    detail=(
                        f"{path_name}: {reason}" if path_name and reason
                        else (reason or path_name or "源文件清理失败")
                    ),
                    created_at=op.created_at,
                    tone="warning",
                )
            )

    # 10. 当前状态事件
    # 按时间排序
    events.sort(key=lambda e: e.created_at)
    return events


def _find_operation(records, operation_type: str):
    for r in records:
        if r.operation_type == operation_type:
            return r
    return None


def _find_operations(records, operation_types: set[str]):
    return [r for r in records if r.operation_type in operation_types]


def _find_adapter_call(calls, action: str):
    for c in calls:
        if c.action == action:
            return c
    return None
