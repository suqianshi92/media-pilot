"""API v1 — JSON 端点路由分组"""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.api.auth_dependencies import (
    CurrentAuthDep,
    TaskAccessDep,
    build_stream_authorizer,
    require_authorized_agent_decision,
    require_authorized_download_task,
    require_authorized_ingest_task,
)
from media_pilot.api.schemas import ApiEnvelope, ApiMessage
from media_pilot.api.task_dtos import (
    DownloadDetailDto,
    ResearchKeywordRequest,
    RevokePublishCheckDto,
    RevokePublishResultDto,
)
from media_pilot.api.task_mapper import (
    map_download_task_to_summary,
    map_to_task_detail,
    map_to_task_summaries,
)
from media_pilot.config import AppConfig
from media_pilot.repository.models import (
    AdapterCall,
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
from media_pilot.repository.repositories import (
    DownloadTaskRepository,
    IngestTaskRepository,
)
from media_pilot.services.flow_list import VALID_FILTERS, build_flows

router = APIRouter(prefix="/api/v1")


# reply_to_agent_decision 成功状态集 — handler 完成的确定性后端路径.
# 旧实现只把 completed / waiting_user 视为 success, 导致 overwrite_target /
# cancel_publish / keep_input / trash_input / manual_selection_cancelled
# 这些确定性后端成功被 envelope.status = "error" 误标, 前端 apiPost
# 走 ApiError.onError toast, 用户看到"加载一会什么也没发生"反向.
#
# 本项目后端不做 Accept-Language / gettext: 所有用户可见文案走前端 i18n
# (agent.* keys), 后端 success message 仅作为 machine-readable status
# hint, 不暴露 run_id / 业务文案. 前端按 data.status (=== message.code)
# 自取 i18n 文案弹 toast.
_DECISION_REPLY_SUCCESS_STATUSES = frozenset({
    "completed",
    "waiting_user",
    "target_conflict_overwritten",
    "target_conflict_cancelled",
    # select_metadata_candidate publish 工具返回 target_conflict 时,
    # task 切到 waiting_user 等待用户处理新决策. 该路径不是失败, 是
    # 成功进入下一步人工确认; envelope 误标 error 会让前端红 toast
    # + 不刷新缓存. 详见 fix-decision-reply-metadata-published-ui-sync.
    "target_conflict_pending",
    "source_cleanup_kept",
    "source_cleanup_trashed",
    "manual_selection_cancelled",
    "manual_selection_published",
    # select_metadata_candidate 走确定性 fetch + publish 路径成功后,
    # task.status=library_import_complete / run.status=completed. 该
    # AgentRunResult.status 必须视为 reply success, 否则 envelope 误标
    # error, 前端 DecisionReplyCard 走 onError 弹红 toast, 详情页状态
    # 标签 / Agent 面板右上 / 列表缓存都不刷新.
    "metadata_published",
    "no_metadata_published",
    "no_metadata_published_cleanup_pending",
    "metadata_unavailable_cancelled",
})


def _collect_file_asset_safety_roots(config: AppConfig) -> tuple[Path, ...]:
    """File-asset 路径安全根集合 (route-adult-movie-library-root).

    返回允许 API 直接提供文件下载/查看的根目录 — 包括:
    - movies_dir / shows_dir / workspace_dir (常规媒体库 + 工作区临时素材)
    - adult_movies_dir (TPDB 成人影片库根, 仅在配置时加入)

    返回 tuple[Path, ...] 用 ``.resolve(strict=False)`` 规整, 避免
    路径字面量在 symlink/相对路径上失配. 调用方应使用
    ``Path.is_relative_to`` 进行包含判定.
    """
    roots: list[Path] = [
        config.movies_dir.resolve(strict=False),
        config.shows_dir.resolve(strict=False),
        config.workspace_dir.resolve(strict=False),
    ]
    if config.adult_movies_dir is not None:
        roots.append(config.adult_movies_dir.resolve(strict=False))
    return tuple(roots)


def _db_locked_response() -> JSONResponse:
    """统一生成 DB locked 409 envelope, 避免 delete 端点 500 plaintext.

    safe_commit rollback 后冒泡 → 锁竞争超过 busy_timeout=5s,
    客户端应该稍候重试 (HTTP 409 + meta.retryable=True).
    """
    return JSONResponse(
        status_code=409,
        content=ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(
                level="error",
                code="db_locked",
                text="数据库暂时被占用，请稍后重试",
            )],
            meta={"retryable": True},
        ).model_dump(),
    )

_TASK_ASSET_ROLE_MAP = {
    "poster": "library_poster",
    "fanart": "library_fanart",
    "clearlogo": "library_clearlogo",
}

# 列表状态筛选的有效值
_LIST_STATUS_FILTERS = frozenset({
    "discovered",
    "waiting_stable",
    "created",
    "workspace_imported",
    "ai_parsed",
    "candidates_ready",
    "queued",
    "processing",
    "library_import_complete",
    "completed",
    "failed",
    "agent_running",
    "waiting_user",
    "agent_failed",
    "deleted",
})


@router.get("/tasks")
def list_tasks(
    request: Request,
    access_scope: TaskAccessDep,
    status: str | None = Query(
        default=None,
        description="按状态筛选，按任务状态过滤；不传则返回全部",
    ),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=50, ge=1, le=200, alias="page_size", description="每页条数"),
) -> ApiEnvelope[dict]:
    """任务列表，支持状态筛选和分页

    返回统一 envelope，data.items 为 TaskSummary 列表，
    meta 中包含 page/page_size/total/filters 分页信息。
    """
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )

    if session_factory is None:
        return ApiEnvelope(
            status="error",
            data={"items": []},
            messages=[
                ApiMessage(
                    level="error",
                    code="database_not_configured",
                    text="未配置数据库",
                )
            ],
            meta={},
        )

    with session_factory() as session:
        repository = IngestTaskRepository(session)

        if status is not None and status in _LIST_STATUS_FILTERS:
            total = repository.count(status=status, access_scope=access_scope)
            offset = (page - 1) * page_size
            tasks = repository.list_page(
                status=status,
                limit=page_size,
                offset=offset,
                access_scope=access_scope,
            )
        else:
            if status is not None:
                return ApiEnvelope(
                    status="error",
                    data={"items": []},
                    messages=[
                        ApiMessage(
                            level="warning",
                            code="unknown_status_filter",
                            text=f"未知筛选状态: {status}",
                        )
                    ],
                    meta={},
                )
            total = repository.count(access_scope=access_scope)
            offset = (page - 1) * page_size
            tasks = repository.list_page(
                status=None,
                limit=page_size,
                offset=offset,
                access_scope=access_scope,
            )

        task_summaries = map_to_task_summaries(
            session,
            tasks,
            access_scope=access_scope,
        )

    return ApiEnvelope(
        status="success",
        data={"items": task_summaries},
        messages=[],
        meta={
            "page": page,
            "page_size": page_size,
            "total": total,
            "filters": {"status": status} if status else {},
        },
    )


@router.get("/downloads")
def list_downloads(
    request: Request,
    access_scope: TaskAccessDep,
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=50, ge=1, le=200, alias="page_size", description="每页条数"),
) -> ApiEnvelope[dict]:
    """下载任务列表 — 返回非终态 + 最近完成的下载任务"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )

    if session_factory is None:
        return ApiEnvelope(
            status="error",
            data={"items": []},
            messages=[
                ApiMessage(
                    level="error",
                    code="database_not_configured",
                    text="未配置数据库",
                )
            ],
            meta={},
        )

    with session_factory() as session:
        repo = DownloadTaskRepository(session)
        # 返回非终态 + 最近 50 个已完成任务

        non_terminal = repo.list_non_terminal(access_scope=access_scope)
        terminal = repo.list_recent_terminal(
            limit=50,
            access_scope=access_scope,
        )
        all_downloads = non_terminal + terminal
        all_downloads.sort(key=lambda t: t.updated_at, reverse=True)
        total = len(all_downloads)

        # 分页
        start = (page - 1) * page_size
        paged = all_downloads[start : start + page_size]

        items = [map_download_task_to_summary(t) for t in paged]

    return ApiEnvelope(
        status="success",
        data={"items": items},
        messages=[],
        meta={
            "page": page,
            "page_size": page_size,
            "total": total,
        },
    )


@router.get("/flows")
def list_flows(
    request: Request,
    access_scope: TaskAccessDep,
    filter: str | None = Query(default="all", description="状态筛选"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=50, ge=1, le=200, alias="page_size", description="每页条数"),
) -> ApiEnvelope[dict]:
    """媒体获取流程列表 — 统一 IngestTask + optional DownloadTask 与
    download-only DownloadTask 的只读 read-model. 后端负责 attention
    priority 排序、filter、page/page_size 与 meta.total."""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )

    if session_factory is None:
        return ApiEnvelope(
            status="error",
            data={"items": []},
            messages=[
                ApiMessage(
                    level="error",
                    code="database_not_configured",
                    text="未配置数据库",
                )
            ],
            meta={},
        )

    filter_name = filter or "all"
    if filter_name not in VALID_FILTERS:
        return ApiEnvelope(
            status="error",
            data={"items": []},
            messages=[
                ApiMessage(
                    level="warning",
                    code="unknown_filter",
                    text=f"未知筛选状态: {filter_name}",
                )
            ],
            meta={},
        )

    with session_factory() as session:
        items, total = build_flows(
            session,
            access_scope=access_scope,
            filter_name=filter_name,
            page=page,
            page_size=page_size,
        )

    return ApiEnvelope(
        status="success",
        data={"items": [item.model_dump(mode="json") for item in items]},
        messages=[],
        meta={
            "page": page,
            "page_size": page_size,
            "total": total,
            "filter": filter_name,
        },
    )


@router.post(
    "/downloads/{download_id}/delete",
    dependencies=[Depends(require_authorized_download_task)],
)
def delete_download(
    download_id: str, request: Request,
) -> ApiEnvelope[dict]:
    """彻底删除 download-only 流程。

    依次执行 qB 删除、本地文件清理、数据库级联删除。
    已关联入库任务的下载任务不允许通过此端点删除。
    """
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        raise HTTPException(status_code=500, detail="未配置数据库")

    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=500, detail="未加载配置")

    from media_pilot.orchestration.delete_unpublished import delete_download_only

    try:
        with session_factory() as session:
            dl = session.get(DownloadTask, download_id)
            if dl is None:
                raise HTTPException(status_code=404, detail="下载任务不存在")
            if dl.ingest_task_id:
                raise HTTPException(
                    status_code=409,
                    detail="该下载任务已关联入库任务，请通过入库任务入口删除",
                )

            result = delete_download_only(session, download_id, config)
    except OperationalError:
        # safe_commit rollback 后冒泡 → DB 锁占用超 5s,
        # 返回 409 + retryable=true, 让前端可以自动重试.
        return _db_locked_response()

    return ApiEnvelope(
        status="success" if result.deleted else "error",
        data={
            "task_id": result.task_id,
            "deleted": result.deleted,
            "qb_deleted": result.qb_deleted,
            "qb_error": result.qb_error,
            "files_cleaned": result.files_cleaned,
        },
        messages=[],
        meta={},
    )


@router.post(
    "/downloads/{download_id}/retry-sync",
    dependencies=[Depends(require_authorized_download_task)],
)
def retry_download_sync(
    download_id: str, request: Request,
) -> ApiEnvelope[dict]:
    """手动重试 download-only 下载任务的状态同步。

    只对没有关联 ingest_task_id 的下载任务可用。
    不重新提交 magnet/torrent，只重新与 qB 对账并更新本地状态。
    """
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        raise HTTPException(status_code=500, detail="未配置数据库")

    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=500, detail="未加载配置")

    from media_pilot.services.download_sync import DownloadSyncService

    service = DownloadSyncService(config)
    result = service.retry_sync_one(session_factory, download_id)

    return ApiEnvelope(
        status="success",
        data={
            "synced": result.synced,
            "failed": result.failed,
            "skipped": result.skipped,
        },
        messages=[],
        meta={},
    )


@router.get(
    "/downloads/{download_id}",
    dependencies=[Depends(require_authorized_download_task)],
)
def download_detail(
    download_id: str, request: Request,
) -> ApiEnvelope[dict]:
    """下载流程详情 — 返回 download-only 流程的完整信息"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        return ApiEnvelope(
            status="error", data={},
            messages=[ApiMessage(level="error", code="database_not_configured", text="未配置数据库")],
            meta={},
        )

    with session_factory() as session:
        dl = session.get(DownloadTask, download_id)
        if dl is None:
            return ApiEnvelope(
                status="error", data={},
                messages=[ApiMessage(level="error", code="not_found", text="下载任务不存在")],
                meta={},
            )
        if dl.ingest_task_id:
            raise HTTPException(
                status_code=409,
                detail="该下载任务已关联入库任务，请通过入库任务入口查看",
            )

        detail = DownloadDetailDto(
            id=dl.id,
            title=dl.title,
            source=dl.source,
            qb_hash=dl.qb_hash,
            save_path=dl.save_path,
            content_path=dl.content_path,
            progress=dl.progress,
            download_speed_bytes_per_second=dl.download_speed_bytes_per_second,
            upload_speed_bytes_per_second=dl.upload_speed_bytes_per_second,
            seeders=dl.seeders,
            leechers=dl.leechers,
            connections=dl.connections,
            qb_state=dl.qb_state,
            status=dl.status,  # type: ignore[arg-type]
            error_message=dl.error_message,
            ingest_task_id=dl.ingest_task_id,
            preselected_metadata_profile=dl.preselected_metadata_profile,
            preselected_metadata_provider=dl.preselected_metadata_provider,
            preselected_metadata_external_id=dl.preselected_metadata_external_id,
            created_at=dl.created_at,
            updated_at=dl.updated_at,
        )

    return ApiEnvelope(
        status="success",
        data=detail.model_dump(mode="json"),
        messages=[],
        meta={},
    )


@router.post(
    "/downloads/{download_id}/pause",
    dependencies=[Depends(require_authorized_download_task)],
)
def pause_download(
    download_id: str, request: Request,
) -> ApiEnvelope[dict]:
    """暂停下载"""
    return _toggle_download(download_id, request, action="pause")


@router.post(
    "/downloads/{download_id}/resume",
    dependencies=[Depends(require_authorized_download_task)],
)
def resume_download(
    download_id: str, request: Request,
) -> ApiEnvelope[dict]:
    """恢复下载"""
    return _toggle_download(download_id, request, action="resume")


def _toggle_download(
    download_id: str, request: Request, *, action: str,
) -> ApiEnvelope[dict]:
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        raise HTTPException(status_code=500, detail="未配置数据库")

    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=500, detail="未加载配置")

    from media_pilot.resource_discovery.qbittorrent_adapter import QBittorrentAdapter

    with session_factory() as session:
        dl = session.get(DownloadTask, download_id)
        if dl is None:
            raise HTTPException(status_code=404, detail="下载任务不存在")
        if dl.ingest_task_id:
            raise HTTPException(
                status_code=409,
                detail="该下载任务已关联入库任务，不支持单独控制",
            )
        if not dl.qb_hash:
            raise HTTPException(status_code=409, detail="下载任务尚未关联 qBittorrent 哈希，无法操作")

        adapter = QBittorrentAdapter(config)
        if action == "pause":
            ok = adapter.pause_torrent(dl.qb_hash)
            new_status = "paused"
        else:
            ok = adapter.resume_torrent(dl.qb_hash)
            # 恢复只是发出动作，真实状态由后续同步回填，不乐观写成 downloading
            new_status = "awaiting_sync"

        if not ok:
            raise HTTPException(status_code=502, detail=f"qBittorrent {action} 失败")

        repo = DownloadTaskRepository(session)
        repo.update_sync_status(dl, status=new_status)
        from media_pilot.orchestration.db_retry import safe_commit
        try:
            safe_commit(session)
        except OperationalError:
            return _db_locked_response()

    label = "已暂停" if action == "pause" else "已恢复"
    return ApiEnvelope(
        status="success",
        data={"download_id": download_id, "status": new_status},
        messages=[ApiMessage(level="info", code=action, text=f"下载任务{label}")],
        meta={},
    )


@router.post(
    "/downloads/{download_id}/refresh",
    dependencies=[Depends(require_authorized_download_task)],
)
def refresh_download(
    download_id: str, request: Request,
) -> ApiEnvelope[dict]:
    """刷新下载状态 — 立即与 qB 对账一次"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        raise HTTPException(status_code=500, detail="未配置数据库")

    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=500, detail="未加载配置")

    with session_factory() as session:
        dl = session.get(DownloadTask, download_id)
        if dl is None:
            raise HTTPException(status_code=404, detail="下载任务不存在")
        if dl.ingest_task_id:
            raise HTTPException(
                status_code=409,
                detail="该下载任务已关联入库任务，不支持单独刷新",
            )

    from media_pilot.services.download_sync import DownloadSyncService

    service = DownloadSyncService(config)
    result = service.retry_sync_one(session_factory, download_id)

    return ApiEnvelope(
        status="success",
        data={
            "synced": result.synced,
            "failed": result.failed,
            "skipped": result.skipped,
        },
        messages=[],
        meta={},
    )


@router.post(
    "/tasks/{task_id}/delete",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def delete_task(
    task_id: str, request: Request,
) -> ApiEnvelope[dict]:
    """彻底删除未发布入库任务。

    已发布完成的任务不允许删除。
    """
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        raise HTTPException(status_code=500, detail="未配置数据库")

    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=500, detail="未加载配置")

    from media_pilot.orchestration.delete_unpublished import delete_ingest_task

    try:
        with session_factory() as session:
            task = session.get(IngestTask, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="任务不存在")
            if task.status in ("library_import_complete", "completed"):
                raise HTTPException(
                    status_code=409,
                    detail="已发布完成的任务不允许删除，请先撤销发布",
                )

            result = delete_ingest_task(session, task_id, config)
    except OperationalError:
        return _db_locked_response()

    return ApiEnvelope(
        status="success" if result.deleted else "error",
        data={
            "task_id": result.task_id,
            "deleted": result.deleted,
            "qb_deleted": result.qb_deleted,
            "qb_error": result.qb_error,
            "files_cleaned": result.files_cleaned,
        },
        messages=[],
        meta={},
    )


@router.get(
    "/tasks/{task_id}",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def task_detail(task_id: str, request: Request) -> ApiEnvelope[dict]:
    """任务详情，返回前端详情页所需的所有结构化字段"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )

    if session_factory is None:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[
                ApiMessage(
                    level="error", code="database_not_configured",
                    text="未配置数据库",
                )
            ],
            meta={},
        )

    with session_factory() as session:
        repository = IngestTaskRepository(session)
        task = repository.get(task_id)
        if task is None:
            return ApiEnvelope(
                status="error",
                data={},
                messages=[ApiMessage(level="error", code="task_not_found", text="任务不存在")],
                meta={},
            )

        # 查询所有关联数据（与 app.py 的 task_detail HTML 页面一致）
        source_selection = session.scalars(
            select(MediaSourceSelection)
            .where(MediaSourceSelection.task_id == task_id)
            .order_by(MediaSourceSelection.created_at.desc())
        ).first()

        keyword_record = session.scalars(
            select(SearchKeywordRecord)
            .where(SearchKeywordRecord.task_id == task_id)
            .order_by(SearchKeywordRecord.created_at.desc())
        ).first()

        # 已确认候选：旧 ConfirmationRequest.decision 已下线。
        # 改为使用最新的人工/agent 候选（按 created_at 倒序的第一个非 fake_ai 候选）。
        decided_candidate = session.scalars(
            select(MediaCandidate)
            .where(MediaCandidate.task_id == task_id)
            .where(MediaCandidate.source != "fake_ai")
            .order_by(MediaCandidate.created_at.desc())
        ).first()

        candidates = list(
            session.scalars(
                select(MediaCandidate)
                .where(MediaCandidate.task_id == task_id)
                .where(MediaCandidate.source != "fake_ai")
                .order_by(MediaCandidate.created_at.asc())
            )
        )

        metadata_detail = session.scalars(
            select(MetadataDetail)
            .where(MetadataDetail.task_id == task_id)
            .order_by(MetadataDetail.created_at.desc())
        ).first()

        write_plan = session.scalars(
            select(WritePlan).where(WritePlan.task_id == task_id)
            .order_by(WritePlan.created_at.desc())
        ).first()

        write_result = session.scalars(
            select(WriteResult).where(WriteResult.task_id == task_id)
            .order_by(WriteResult.created_at.desc())
        ).first()

        file_assets = list(session.scalars(
            select(FileAsset).where(FileAsset.task_id == task_id)))
        adapter_calls = list(session.scalars(
            select(AdapterCall).where(AdapterCall.task_id == task_id)))
        operation_records = list(session.scalars(
            select(OperationRecord).where(OperationRecord.task_id == task_id)))
        audit_logs = list(session.scalars(
            select(AuditLog).where(AuditLog.task_id == task_id)))

        episode_mappings = list(session.scalars(
            select(EpisodeMapping).where(EpisodeMapping.task_id == task_id)))

        detail = map_to_task_detail(
            session, task,
            source_selection=source_selection,
            keyword_record=keyword_record,
            decided_candidate=decided_candidate,
            metadata_detail=metadata_detail,
            write_plan=write_plan,
            write_result=write_result,
            file_assets=file_assets,
            adapter_calls=adapter_calls,
            operation_records=operation_records,
            audit_logs=audit_logs,
            candidates=candidates,
            episode_mappings=episode_mappings,
        )

    return ApiEnvelope(
        status="success",
        data=detail.model_dump(mode="json"),
        messages=[],
        meta={},
    )


@router.get(
    "/tasks/{task_id}/assets/{asset_role}",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def task_asset(task_id: str, asset_role: str, request: Request) -> FileResponse:
    """返回任务已登记的受控图片资产。"""
    mapped_role = _TASK_ASSET_ROLE_MAP.get(asset_role)
    if mapped_role is None:
        raise HTTPException(status_code=404, detail="asset role not found")

    config: AppConfig | None = getattr(request.app.state, "config", None)
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if config is None or session_factory is None:
        raise HTTPException(status_code=500, detail="application not configured")

    with session_factory() as session:
        asset = session.scalars(
            select(FileAsset)
            .where(FileAsset.task_id == task_id)
            .where(FileAsset.role == mapped_role)
            .order_by(FileAsset.created_at.desc())
        ).first()

    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")

    asset_path = Path(asset.path).expanduser()
    try:
        resolved_path = asset_path.resolve(strict=False)
    except OSError as exc:
        raise HTTPException(status_code=404, detail="asset path invalid") from exc

    allowed_roots = _collect_file_asset_safety_roots(config)
    if not any(resolved_path.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="asset path outside allowed roots")

    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="asset file not found")

    media_type, _ = mimetypes.guess_type(resolved_path.name)
    return FileResponse(
        path=resolved_path,
        media_type=media_type or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get(
    "/tasks/{task_id}/status",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def task_status(task_id: str, request: Request) -> ApiEnvelope[dict]:
    """任务状态轮询 — 返回轻量 TaskStatusSummary"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        return ApiEnvelope(
            status="error", data={},
            messages=[ApiMessage(
                level="error", code="database_not_configured", text="未配置数据库"
            )],
            meta={},
        )

    with session_factory() as session:
        task = IngestTaskRepository(session).get(task_id)
        if task is None:
            return ApiEnvelope(
                status="error", data={},
                messages=[ApiMessage(
                    level="error", code="task_not_found", text="任务不存在"
                )],
                meta={},
            )

        summaries = map_to_task_summaries(session, [task])

    return ApiEnvelope(
        status="success",
        data=summaries[0].status_summary.model_dump(mode="json"),
        messages=[],
        meta={},
    )


@router.post(
    "/tasks/{task_id}/research",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def research_candidates(
    task_id: str,
    body: ResearchKeywordRequest,
    request: Request,
) -> ApiEnvelope[dict]:
    """关键词重搜 — profile-aware 手动重搜。

    新行为（ConfirmationRequest 已下线）：
    - 写入任务事实（MediaCandidate / SearchKeywordRecord）。
    - 只返回候选和 profile 搜索摘要；不会自动发布或创建决策。
    - 用户显式选择候选后，再通过 /manual-select 进入确定性写入路径。
    - 响应仅包含 candidates + search_summary，不再附带 confirmation_request。
    """
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    config: AppConfig | None = getattr(request.app.state, "config", None)

    if session_factory is None or config is None:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(
                level="error", code="not_configured", text="未配置数据库或服务"
            )],
            meta={},
        )

    keyword = body.keyword.strip()
    if not keyword:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(level="error", code="empty_keyword", text="关键词不能为空")],
            meta={},
        )

    # 2.8 未知 scope 返回错误
    scope = body.scope
    if scope not in ("all", "tmdb_movie", "tmdb_show", "tpdb_adult_movie"):
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(
                level="error", code="invalid_scope",
                text=f"未知搜索范围: {scope}。允许值为 all/tmdb_movie/tmdb_show/tpdb_adult_movie",
            )],
            meta={},
        )

    with session_factory() as session:
        task = IngestTaskRepository(session).get(task_id)
        if task is None:
            return ApiEnvelope(
                status="error",
                data={},
                messages=[ApiMessage(level="error", code="task_not_found", text="任务不存在")],
                meta={},
            )

        from media_pilot.services.manual_research import run_manual_research

        result = run_manual_research(
            session,
            task_id=task_id,
            keyword=keyword,
            scope=scope,
            config=config,
        )

        from media_pilot.api.task_dtos import (
            MetadataCandidateDto,
            ProfileSearchStatusDto,
            ResearchResponseData,
            SearchSummaryDto,
        )

        candidate_dtos = [
            MetadataCandidateDto(
                provider=c.source or "",
                provider_id=c.external_id or "",
                title=c.title or "",
                original_title=c.original_title,
                year=c.year,
                media_type=c.media_type or "movie",  # type: ignore[arg-type]
                overview=(c.payload or {}).get("overview"),
                poster_url=(c.payload or {}).get("poster_url"),
                confidence=c.confidence,
                match_reason=c.reason,
            )
            for c in result.candidates
        ]

        summary = SearchSummaryDto(
            keyword=result.summary.keyword,
            scope=result.summary.scope,
            searched_profiles=[
                ProfileSearchStatusDto(
                    profile=s.profile,
                    label=s.label,
                    provider=s.provider,
                    status=s.status,
                    candidate_count=s.candidate_count,
                    error_message=s.error_message,
                )
                for s in result.summary.searched_profiles
            ],
            total_candidates=result.summary.total_candidates,
            kept_existing_candidates=result.summary.kept_existing_candidates,
        )

        response_data = ResearchResponseData(
            candidates=candidate_dtos,
            search_summary=summary,
        )

        messages = []
        meta: dict = {}

    return ApiEnvelope(
        status="success",
        data=response_data.model_dump(mode="json"),
        messages=messages,
        meta=meta,
    )



# 手动 process 接口可处理的 Agent 起始状态。
_MANUAL_PROCESSABLE_STATUSES = frozenset({"discovered", "created", "queued"})


@router.post(
    "/tasks/{task_id}/process",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def process_task(task_id: str, request: Request) -> ApiEnvelope[dict]:
    """触发任务处理 — 对 discovered/created/queued 任务启动 Agent 运行。

    该接口是 Agent 主线手动触发入口：
    - 仅当任务处于 Agent 可推进状态时才接受请求。
    - LLM 未配置时返回 `not_configured`。
    """
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    worker = getattr(request.app.state, "worker", None)

    if session_factory is None or worker is None:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(
                level="error", code="not_configured", text="未配置数据库或 worker"
            )],
            meta={},
        )

    with session_factory() as session:
        task = IngestTaskRepository(session).get(task_id)
        if task is None:
            return ApiEnvelope(
                status="error",
                data={},
                messages=[ApiMessage(
                    level="error", code="task_not_found", text="任务不存在"
                )],
                meta={},
            )

        if task.status not in _MANUAL_PROCESSABLE_STATUSES:
            return ApiEnvelope(
                status="error",
                data={},
                messages=[ApiMessage(
                    level="error", code="invalid_task_status",
                    text=f"任务当前状态为 {task.status}，不支持处理",
                )],
                meta={},
            )

    result = worker.process_task(session_factory, task_id)

    if result.status == "not_configured":
        return ApiEnvelope(
            status="error",
            data={"task_id": task_id, "result_status": result.status},
            messages=[ApiMessage(
                level="error", code="agent_not_configured",
                text=(
                    "Agent 主线尚未配置 LLM，无法启动自动入库；"
                    "请补齐 LLM_API_KEY/LLM_BASE_URL/LLM_MODEL 后重试"
                ),
            )],
            meta={},
        )

    return ApiEnvelope(
        status="success",
        data={"task_id": task_id, "result_status": result.status},
        messages=[ApiMessage(
            level="info", code="task_processed",
            text=f"任务处理完成，最终状态: {result.status}",
        )],
        meta={},
    )


# ── 撤销发布 ──


@router.get(
    "/tasks/{task_id}/revoke-publish",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def check_revoke(task_id: str, request: Request) -> ApiEnvelope[dict]:
    """预检任务撤销发布条件"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        return ApiEnvelope(
            status="error", data={},
            messages=[ApiMessage(
                level="error", code="database_not_configured",
                text="未配置数据库",
            )],
            meta={},
        )

    from media_pilot.orchestration.revoke_publish import check_revoke_publish

    with session_factory() as session:
        result = check_revoke_publish(session, task_id=task_id)

    dto = RevokePublishCheckDto(
        allowed=result.allowed,
        publish_dir=result.publish_dir,
        source_file_exists=result.source_file_exists,
        is_complex_structure=result.is_complex_structure,
        outcome_description=result.outcome_description,
    )
    return ApiEnvelope(
        status="success" if result.allowed else "error",
        data=dto.model_dump(),
        messages=[],
        meta={},
    )


@router.post(
    "/tasks/{task_id}/revoke-publish",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def execute_revoke(task_id: str, request: Request) -> ApiEnvelope[dict]:
    """执行撤销发布"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        return ApiEnvelope(
            status="error", data={},
            messages=[ApiMessage(
                level="error", code="database_not_configured",
                text="未配置数据库",
            )],
            meta={},
        )

    from media_pilot.orchestration.revoke_publish import (
        execute_revoke_publish,
    )

    with session_factory() as session:
        try:
            result = execute_revoke_publish(session, task_id=task_id)
        except ValueError as e:
            return ApiEnvelope(
                status="error", data={},
                messages=[ApiMessage(
                    level="error", code="revoke_not_allowed",
                    text=str(e),
                )],
                meta={},
            )

    dto = RevokePublishResultDto(
        status=result.status,
        outcome=result.outcome,
        decision_id=result.decision_id,
    )
    return ApiEnvelope(
        status="success",
        data=dto.model_dump(),
        messages=[ApiMessage(
            level="info", code="revoke_completed",
            text=result.outcome,
        )],
        meta={},
    )


@router.get(
    "/tasks/{task_id}/delete-input/preview",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def preview_delete_task_input(task_id: str, request: Request) -> ApiEnvelope[dict]:
    """预检删除任务输入 —— 返回目标路径和安全性结果，不执行删除。"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    config: AppConfig | None = getattr(request.app.state, "config", None)

    if session_factory is None or config is None:
        return ApiEnvelope(status="error", data={}, messages=[
            ApiMessage(level="error", code="not_configured", text="未配置数据库或服务")
        ], meta={})

    from media_pilot.orchestration.delete_unpublished import preview_delete_input

    with session_factory() as session:
        result = preview_delete_input(session, task_id, config)

    dto = {
        "allowed": result.allowed,
        "target_path": result.target_path,
        "path_type": result.path_type,
        "outcome_description": result.outcome_description,
    }
    return ApiEnvelope(
        status="success" if result.allowed else "error",
        data=dto,
        messages=[],
        meta={},
    )


@router.post(
    "/tasks/{task_id}/delete-input",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def execute_delete_task_input(task_id: str, body: dict, request: Request) -> ApiEnvelope[dict]:
    """执行删除任务输入 —— 需前端二次确认后调用。"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    config: AppConfig | None = getattr(request.app.state, "config", None)

    if session_factory is None or config is None:
        return ApiEnvelope(status="error", data={}, messages=[
            ApiMessage(level="error", code="not_configured", text="未配置数据库或服务")
        ], meta={})

    if not body.get("confirmed"):
        return ApiEnvelope(status="error", data={}, messages=[
            ApiMessage(level="error", code="not_confirmed", text="需显式确认删除操作")
        ], meta={})

    from media_pilot.orchestration.delete_unpublished import execute_delete_input

    with session_factory() as session:
        try:
            result = execute_delete_input(session, task_id, config)
        except ValueError as exc:
            return ApiEnvelope(status="error", data={}, messages=[
                ApiMessage(level="error", code="delete_not_allowed", text=str(exc))
            ], meta={})
        except OperationalError:
            # safe_commit rollback 后冒泡 → 文件已删但 task 状态 / 业务数据
            # 清理未提交, 返回 409 让用户重试整个二次确认流程.
            return _db_locked_response()

    return ApiEnvelope(
        status="success",
        data=result,
        messages=[ApiMessage(
            level="info", code="delete_input_completed",
            text=result["outcome"],
        )],
        meta={},
    )


@router.post(
    "/tasks/{task_id}/manual-select",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def manual_select_metadata(task_id: str, body: dict, request: Request) -> ApiEnvelope[dict]:
    """人工辅助检索选择元数据候选。"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    config: AppConfig | None = getattr(request.app.state, "config", None)

    if session_factory is None or config is None:
        return ApiEnvelope(status="error", data={}, messages=[
            ApiMessage(level="error", code="not_configured", text="未配置数据库或服务")
        ], meta={})

    from media_pilot.api.task_dtos import ManualSelectRequest, ManualSelectResponse
    from media_pilot.services.manual_selection import submit_manual_selection

    req = ManualSelectRequest(**body)

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider=req.provider,
            provider_id=req.provider_id,
            title=req.title,
            year=req.year,
            original_title=req.original_title,
            media_type=req.media_type,
        )
        from media_pilot.orchestration.db_retry import safe_commit
        try:
            safe_commit(session)
        except OperationalError:
            return _db_locked_response()

    if result.status == "rejected":
        return JSONResponse(
            status_code=409,
            content=ApiEnvelope(
                status="error",
                data={},
                messages=[ApiMessage(
                    level="error",
                    code="manual_select_rejected",
                    text=result.summary,
                )],
                meta={},
            ).model_dump(),
        )
    if result.status == "agent_failed":
        return JSONResponse(
            status_code=409,
            content=ApiEnvelope(
                status="error",
                data={},
                messages=[ApiMessage(
                    level="error",
                    code="manual_select_failed",
                    text=result.summary,
                )],
                meta={},
            ).model_dump(),
        )

    dto = ManualSelectResponse(
        status=result.status,
        summary=result.summary,
        candidate_id=result.candidate_id,
        decision_id=result.decision_id,
        blocking_reasons=result.blocking_reasons,
    )
    return ApiEnvelope(
        status="success",
        data=dto.model_dump(),
        messages=[ApiMessage(
            level="info", code=f"manual_select_{result.status}",
            text=result.summary,
        )],
        meta={},
    )


@router.post(
    "/tasks/{task_id}/publish-without-metadata",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def publish_task_without_metadata(task_id: str, body: dict, request: Request) -> ApiEnvelope[dict]:
    """显式确认后执行无元数据入库。"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    config: AppConfig | None = getattr(request.app.state, "config", None)

    if session_factory is None or config is None:
        return ApiEnvelope(status="error", data={}, messages=[
            ApiMessage(level="error", code="not_configured", text="未配置数据库或服务")
        ], meta={})

    if not body.get("confirmed"):
        return ApiEnvelope(status="error", data={}, messages=[
            ApiMessage(level="error", code="not_confirmed", text="需显式确认无元数据入库")
        ], meta={})

    from media_pilot.orchestration.db_retry import safe_commit
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
        AgentRunCreate,
        AgentRunRepository,
        IngestTaskRepository,
    )
    from media_pilot.services.no_metadata_publish import publish_without_metadata
    from media_pilot.services.post_publish_cleanup import run_post_publish_source_cleanup

    with session_factory() as session:
        task = IngestTaskRepository(session).get(task_id)
        if task is None:
            return ApiEnvelope(status="error", data={}, messages=[
                ApiMessage(level="error", code="task_not_found", text="任务不存在")
            ], meta={})

        run_repo = AgentRunRepository(session)
        run = run_repo.get_active_or_waiting_by_task(task_id)
        if run is None:
            run = run_repo.create(AgentRunCreate(
                task_id=task_id,
                current_step="no_metadata_publish",
            ))

        library_target = body.get("library_target")
        result = publish_without_metadata(
            session=session,
            config=config,
            task_id=task_id,
            library_target=library_target,
        )
        if result.status == "target_conflict":
            try:
                decision = AgentDecisionRequestRepository(session).create(
                    AgentDecisionRequestCreate(
                        run_id=run.id,
                        task_id=task_id,
                        decision_type="target_conflict",
                        question=f"目标 {result.final_target_dir} 已被占用。请选择处理方式。",
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
                            "publish_mode": "no_metadata",
                            "library_target": library_target,
                            "final_target_dir": result.final_target_dir,
                            "final_target_file": result.final_target_file,
                            "conflict": "no_metadata_target_conflict",
                        },
                    )
                )
            except ValueError:
                return JSONResponse(
                    status_code=409,
                    content=ApiEnvelope(
                        status="error",
                        data={"status": "waiting_user"},
                        messages=[ApiMessage(
                            level="error",
                            code="pending_decision_exists",
                            text="已有待处理决策，请先处理当前决策",
                        )],
                        meta={"retryable": False},
                    ).model_dump(),
                )
            IngestTaskRepository(session).update_status(
                task, status="waiting_user", current_step="target_conflict",
            )
            run_repo.update_status(run, status="waiting_user", current_step="target_conflict")
            try:
                safe_commit(session)
            except OperationalError:
                return _db_locked_response()
            return ApiEnvelope(
                status="success",
                data={
                    "status": "waiting_user",
                    "decision_id": decision.id,
                    "metadata_status": "unknown",
                },
                messages=[ApiMessage(
                    level="info",
                    code="target_conflict_pending",
                    text="target_conflict_pending",
                )],
                meta={},
            )

        if result.status != "published":
            try:
                safe_commit(session)
            except OperationalError:
                return _db_locked_response()
            return JSONResponse(
                status_code=409,
                content=ApiEnvelope(
                    status="error",
                    data={
                        "status": result.status,
                        "blocking_reasons": result.blocking_reasons,
                    },
                    messages=[ApiMessage(
                        level="error",
                        code="publish_without_metadata_failed",
                        text=result.summary,
                    )],
                    meta={},
                ).model_dump(),
            )

        cleanup = run_post_publish_source_cleanup(
            session=session, config=config, task_id=task_id, run_id=run.id,
        )
        if not cleanup.decision_requested:
            run_repo.update_status(run, status="completed", current_step="no_metadata_published")
        try:
            safe_commit(session)
        except OperationalError:
            return _db_locked_response()

    return ApiEnvelope(
        status="success",
        data={
            "status": "published",
            "metadata_status": "none",
            "final_target_dir": result.final_target_dir,
            "final_target_file": result.final_target_file,
            "cleanup_decision_requested": cleanup.decision_requested,
        },
        messages=[ApiMessage(
            level="info",
            code="no_metadata_published",
            text="no_metadata_published",
        )],
        meta={},
    )


# ── Agent Run ───────────────────────────────────────────────────────


@router.post(
    "/tasks/{task_id}/agent-runs/stream",
    dependencies=[Depends(require_authorized_ingest_task)],
)
async def create_agent_run_stream(
    task_id: str,
    request: Request,
    auth: CurrentAuthDep,
):
    """为通用 Agent 输入创建流式 AgentRun，通过 SSE 返回实时事件。

    事件类型: user_message, assistant_delta, assistant_message,
    tool_call_started, tool_call_finished, decision_created, run_finished, error

    SSE 断开不会取消后端 AgentRun。
    """
    from fastapi.responses import StreamingResponse

    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    config: AppConfig | None = getattr(request.app.state, "config", None)

    if session_factory is None or config is None:
        return ApiEnvelope(
            status="error", data={},
            messages=[ApiMessage(
                level="error", code="not_configured", text="未配置数据库或服务"
            )],
            meta={},
        )

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    user_message = body.get("message")
    if not user_message:
        raise HTTPException(status_code=400, detail="message is required for streaming agent run")

    from media_pilot.agent.runner import run_agent_turn_streaming
    from media_pilot.agent.prompts import build_freeform_initial_message
    from media_pilot.services.freeform_context import build_freeform_context

    # Validate upfront
    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        from media_pilot.repository.repositories import (
            AgentDecisionRequestRepository,
            AgentRunRepository,
        )

        pending = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
        if pending:
            raise HTTPException(
                status_code=409,
                detail="Task has a pending decision that must be resolved before sending freeform input",
            )
        if task.status == "deleted":
            raise HTTPException(
                status_code=409,
                detail="Cannot send freeform input to a deleted task",
            )

        active_run = AgentRunRepository(session).get_active_or_waiting_by_task(task_id)
        if active_run is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Task {task_id} already has an active or waiting AgentRun {active_run.id}",
            )

        task_facts, recent_msgs, recent_tcs = build_freeform_context(session, task_id)

    initial_message = build_freeform_initial_message(
        task_id,
        user_message,
        task_facts=task_facts,
        recent_messages=recent_msgs,
        recent_tool_calls=recent_tcs,
    )

    emitter, _result_holder = run_agent_turn_streaming(
        session_factory=session_factory,
        config=config,
        task_id=task_id,
        initial_message=initial_message,
        user_message_text=user_message,
        mode="freeform",
    )

    def _event_generator():
        # Sync generator — Starlette wraps sync iterables with
        # iterate_in_threadpool so queue.get() doesn't block the event loop.
        try:
            for event in emitter:
                yield event.to_sse()
        except Exception:
            pass  # Client disconnected

    from media_pilot.accounts.stream_authorization import (
        stream_with_periodic_authorization,
    )

    authorized_events = stream_with_periodic_authorization(
        _event_generator(),
        authorize=build_stream_authorizer(
            session_factory,
            token=auth.token,
            task_id=task_id,
        ),
        authorization_error=(
            'event: error\ndata: {"error":"authorization_revoked"}\n\n'
        ),
    )

    return StreamingResponse(
        authorized_events,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/tasks/{task_id}/agent-runs",
    dependencies=[Depends(require_authorized_ingest_task)],
)
async def create_agent_run(task_id: str, request: Request) -> ApiEnvelope[dict]:
    """手动触发 Agent Run — 为指定任务创建并执行一次 AgentRun。

    如果任务已有 active 或 waiting_user AgentRun，返回 409。
    对于 agent_failed 任务，使用恢复消息重新运行 (ack-only, 实际 loop 在后台线程).
    如果请求带有 ``message``，则作为通用 Agent 输入创建 freeform run (同步保持现状).
    不接入下载完成自动触发。
    """
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    config: AppConfig | None = getattr(request.app.state, "config", None)

    if session_factory is None or config is None:
        return ApiEnvelope(
            status="error", data={},
            messages=[ApiMessage(
                level="error", code="not_configured", text="未配置数据库或服务"
            )],
            meta={},
        )

    from media_pilot.agent.runner import run_agent_turn, run_agent_turn_async
    from media_pilot.agent.prompts import (
        build_freeform_initial_message,
        make_retry_user_message,
    )

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    user_message = body.get("message")

    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        from media_pilot.repository.repositories import (
            AgentDecisionRequestRepository,
            AgentRunRepository,
        )

        # ── freeform input guard: pending decision must be resolved first ──
        if user_message:
            pending = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
            if pending:
                raise HTTPException(
                    status_code=409,
                    detail="Task has a pending decision that must be resolved before sending freeform input",
                )
            if task.status == "deleted":
                raise HTTPException(
                    status_code=409,
                    detail="Cannot send freeform input to a deleted task",
                )

        active_run = AgentRunRepository(session).get_active_or_waiting_by_task(task_id)
        if active_run is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Task {task_id} already has an active or waiting AgentRun {active_run.id}",
            )

        # ── agent_failed retry: ack-only (fast) + background loop ──
        # 修复 fix-agent-retry-button-ui-state-semantics: 前端 retry 按钮的
        # loading MUST 仅覆盖 ack POST 生命周期, 不应延展到 Agent 全流程.
        # 后台 loop 复用 existing run (调 continue_agent_run), 不创建第二个
        # AgentRun. freeform / default 分支保持同步, 不影响 streaming 路径.
        if not user_message and task.status == "agent_failed":
            try:
                ack = run_agent_turn_async(
                    session_factory=session_factory,
                    config=config,
                    task_id=task_id,
                    mode="auto_ingest",
                    initial_message=make_retry_user_message(task_id),
                )
            except OperationalError:
                # 同步阶段 commit 撞锁 → safe_commit rollback + 冒泡.
                # 返 409 db_locked, 与项目内其它写路径一致; 客户端稍候重试.
                return _db_locked_response()
            return ApiEnvelope(
                status="success",
                data={
                    "run_id": ack.run_id,
                    "status": ack.status,
                    "message_count": 0,
                    "tool_call_count": 0,
                    "error_message": None,
                },
                messages=[ApiMessage(
                    level="info",
                    code=f"agent_run_{ack.status}",
                    text=f"Agent run {ack.run_id}: {ack.status} (background)",
                )],
                meta={},
            )

        # ── freeform input: build context-injected initial message (同步) ──
        if user_message:
            from media_pilot.services.freeform_context import build_freeform_context
            task_facts, recent_msgs, recent_tcs = build_freeform_context(
                session, task_id,
            )
            initial_message = build_freeform_initial_message(
                task_id,
                user_message,
                task_facts=task_facts,
                recent_messages=recent_msgs,
                recent_tool_calls=recent_tcs,
            )
            result = run_agent_turn(
                session=session,
                config=config,
                task_id=task_id,
                mode="freeform",
                initial_message=initial_message,
                user_message_text=user_message,
            )
        else:
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
            )

        from media_pilot.orchestration.db_retry import safe_commit
        try:
            safe_commit(session)
        except OperationalError:
            return _db_locked_response()

    return ApiEnvelope(
        status="success" if result.status in ("completed", "waiting_user") else "error",
        data={
            "run_id": result.run_id,
            "status": result.status,
            "message_count": result.message_count,
            "tool_call_count": result.tool_call_count,
            "error_message": result.error_message,
        },
        messages=[ApiMessage(
            level="info" if result.status != "failed" else "error",
            code=f"agent_run_{result.status}",
            text=f"Agent run {result.run_id}: {result.status}",
        )],
        meta={},
    )


@router.post(
    "/tasks/{task_id}/agent-runs/recover-stuck",
    dependencies=[Depends(require_authorized_ingest_task)],
)
async def recover_stuck_agent_run_endpoint(
    task_id: str, request: Request,
) -> ApiEnvelope[dict]:
    """卡住 Agent 恢复 — 显式把旧 active run 标 failed 并启动新 ack-only run.

    与普通 ``POST /tasks/{id}/agent-runs`` 端点严格分离. 本端点只服务
    任务卡在 ``agent_running`` + 存在 active run + 无 pending decision
    的受控恢复场景, 不会触发 ``run_agent_turn`` 的自由对话路径, 也不
    复用 ``agent_failed`` 的 retry 语义.

    校验链 (任何一步失败 → 409/404 envelope):
    - 任务存在
    - task.status == "agent_running" (waiting_user / 终态都拒绝)
    - 存在 active AgentRun
    - 不存在 pending AgentDecisionRequest
    - 同步阶段数据库锁冲突 → 409 db_locked + meta.retryable=True
    """
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    config: AppConfig | None = getattr(request.app.state, "config", None)
    if session_factory is None or config is None:
        return ApiEnvelope(
            status="error", data={},
            messages=[ApiMessage(
                level="error", code="not_configured",
                text="未配置数据库或服务",
            )],
            meta={},
        )

    from media_pilot.services.recover_stuck_agent_run import recover_stuck_agent_run

    try:
        result = recover_stuck_agent_run(
            session_factory=session_factory, config=config, task_id=task_id,
        )
    except ValueError as exc:
        err = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
        status_code = err.get("status_code", 500)
        detail = err.get("detail", "recover_stuck_failed")

        # db_locked 走项目统一 envelope + 409; 其它业务错误也用 HTTP 状态码透传,
        # 不让前端只看到 200 + error envelope 还要二次解析 status_code.
        if status_code == 409 and "db_locked" in detail:
            return _db_locked_response()

        return JSONResponse(
            status_code=status_code,
            content=ApiEnvelope(
                status="error",
                data={"task_id": task_id, "status": "recover_refused"},
                messages=[ApiMessage(
                    level="error", code=f"recover_stuck_{status_code}",
                    text=detail,
                )],
                meta={},
            ).model_dump(),
        )

    return ApiEnvelope(
        status="success",
        data={
            "run_id": result["run_id"],
            "status": result["status"],
            "message_count": 0,
            "tool_call_count": 0,
            "error_message": None,
        },
        messages=[ApiMessage(
            level="info", code=f"agent_run_{result['status']}",
            text=result["status"],
        )],
        meta={},
    )


@router.get(
    "/tasks/{task_id}/agent-decisions",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def list_agent_decisions(task_id: str, request: Request) -> ApiEnvelope[list[dict]]:
    """返回指定任务的 pending decisions。"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        return ApiEnvelope(status="error", data=[], messages=[
            ApiMessage(level="error", code="not_configured", text="未配置数据库")
        ], meta={})

    from media_pilot.repository.repositories import AgentDecisionRequestRepository

    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        decisions = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
        data = [
            {
                "id": d.id,
                "run_id": d.run_id,
                "task_id": d.task_id,
                "decision_type": d.decision_type,
                "question": d.question,
                "options": d.options,
                "free_text_allowed": d.free_text_allowed,
                "payload": d.payload or {},
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in decisions
        ]

    return ApiEnvelope(status="success", data=data, messages=[], meta={})


@router.post(
    "/agent-decisions/{decision_id}/reply",
    dependencies=[Depends(require_authorized_agent_decision)],
)
def reply_to_agent_decision(
    decision_id: str,
    body: dict,
    request: Request,
    auth: CurrentAuthDep,
) -> ApiEnvelope[dict]:
    """用户回复 pending decision 并继续 AgentRun。"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    config: AppConfig | None = getattr(request.app.state, "config", None)

    if session_factory is None or config is None:
        return ApiEnvelope(status="error", data={}, messages=[
            ApiMessage(level="error", code="not_configured", text="未配置数据库或服务")
        ], meta={})

    from media_pilot.services.decision_reply import ReplyInput, reply_to_decision

    option_id = body.get("option_id")
    if option_id == "overwrite_target" and auth.user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    free_text = body.get("free_text")
    decided_by = body.get("decided_by", "user")

    reply = ReplyInput(
        decision_id=decision_id,
        option_id=option_id,
        free_text=free_text,
        decided_by=decided_by,
    )

    with session_factory() as session:
        try:
            result = reply_to_decision(
                session=session, config=config, reply=reply,
            )
            from media_pilot.orchestration.db_retry import safe_commit
            try:
                safe_commit(session)
            except OperationalError:
                # safe_commit 已 rollback 撤销决策回复 / run.status 切回等业务变更.
                # 决策保持 pending, 用户可重试或下一轮后台重试.
                return _db_locked_response()
        except ValueError as exc:
            err = exc.args[0]
            if isinstance(err, dict) and "status_code" in err:
                # 决策回复 / overwrite / 解析失败等结构化错误.
                # 把 dict 里的 code / retryable / detail 透传给前端, 让 toast
                # 区分 db_locked / invalid_video_source / movie_write_failed
                # 等场景, 而不只是裸 detail.
                status_code = int(err.get("status_code", 400))
                err_code = err.get("code") or "decision_reply_failed"
                err_detail = err.get("detail") or "决策回复失败"
                retryable = bool(err.get("retryable", False))
                return JSONResponse(
                    status_code=status_code,
                    content=ApiEnvelope(
                        status="error",
                        data={},
                        messages=[ApiMessage(
                            level="error",
                            code=err_code,
                            text=err_detail,
                        )],
                        meta={"retryable": retryable},
                    ).model_dump(),
                )
            raise HTTPException(status_code=400, detail=str(exc))

    if result.status == "delete_input_preview":
        # Look up task_id from the decision for the frontend
        with session_factory() as s:
            from media_pilot.repository.repositories import AgentDecisionRequestRepository
            d = AgentDecisionRequestRepository(s).get(decision_id)
            decision_task_id = d.task_id if d else ""
        return ApiEnvelope(
            status="success",
            data={
                "status": result.status,
                "task_id": decision_task_id,
            },
            messages=[ApiMessage(
                level="info", code="delete_input_preview",
                text="请确认删除任务输入",
            )],
            meta={},
        )

    is_success = result.status in _DECISION_REPLY_SUCCESS_STATUSES
    if is_success:
        # success path: 稳定 status token 作为 machine-readable hint
        # (== code), 不得暴露 run_id / 业务文案. 前端按 result.status
        # 自取 i18n 文案 (agent.metadataPublished 等 key) 弹 toast,
        # 不直接展示 message.text. 任何用户可见文案以后端 i18n 体系
        # (Accept-Language / gettext) 收口; 本项目暂未启用, 故此处
        # 不硬塞任何语言文案.
        message_text = result.status
        message_code = result.status
    else:
        # error path: 保留 run_id / 内部状态便于排障 (前端红色 toast
        # 不展示完整 text, 只显示 message). 用户可见错误文案仍走
        # 前端 i18n (agent.replyFailed / agent.dbLocked 等).
        message_text = f"Agent run {result.run_id}: {result.status}"
        message_code = f"agent_continue_{result.status}"
    return ApiEnvelope(
        status="success" if is_success else "error",
        data={
            "run_id": result.run_id,
            "status": result.status,
            "message_count": result.message_count,
            "tool_call_count": result.tool_call_count,
            "error_message": result.error_message,
        },
        messages=[ApiMessage(
            level="info" if is_success else "error",
            code=message_code,
            text=message_text,
        )],
        meta={},
    )


@router.get(
    "/tasks/{task_id}/agent-messages",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def list_agent_messages(task_id: str, request: Request) -> ApiEnvelope[list[dict]]:
    """返回任务的 Agent 对话消息（不含 system prompt）。"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        return ApiEnvelope(status="error", data=[], messages=[
            ApiMessage(level="error", code="not_configured", text="未配置数据库")
        ], meta={})

    from media_pilot.repository.repositories import AgentMessageRepository

    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        messages = AgentMessageRepository(session).list_by_task(task_id)
        data = [
            {
                "id": m.id,
                "run_id": m.run_id,
                "role": m.role,
                "content": m.content,
                "tool_calls": m.tool_calls,
                "tool_call_id": m.tool_call_id,
                "tool_name": m.tool_name,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
            if m.role != "system"
        ]

    return ApiEnvelope(status="success", data=data, messages=[], meta={})


@router.get(
    "/tasks/{task_id}/agent-tool-calls",
    dependencies=[Depends(require_authorized_ingest_task)],
)
def list_agent_tool_calls(task_id: str, request: Request) -> ApiEnvelope[list[dict]]:
    """返回任务的 Agent 工具调用记录（只读，用于展开调试详情）。

    status 字段在 API 响应里统一归一为 ``succeeded`` / ``failed``,
    兼容 DB 旧记录 (``succeeded``) 与 AgentRunner 写入的 ``completed``
    两种值, 前端不必再兼容多个成功 / 失败写法. ``output.status`` 仍
    保留原始 ``success`` / ``failure`` 供调试.
    """
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        return ApiEnvelope(status="error", data=[], messages=[
            ApiMessage(level="error", code="not_configured", text="未配置数据库")
        ], meta={})

    from media_pilot.repository.repositories import AgentToolCallRepository

    def _normalize_status(raw: str) -> str:
        # 兼容历史: 老 DB 写的是 "succeeded", runner 新写的是 "completed".
        if raw in ("succeeded", "completed"):
            return "succeeded"
        if raw in ("failed", "failure", "error"):
            return "failed"
        return raw  # running / pending / skipped 等中间态原样透传

    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        tool_calls = AgentToolCallRepository(session).list_by_task(task_id)
        data = [
            {
                "id": tc.id,
                "run_id": tc.run_id,
                "message_id": tc.message_id,
                "tool_call_id": tc.tool_call_id,
                "tool_name": tc.tool_name,
                "status": _normalize_status(tc.status),
                "input": tc.input,
                "output": tc.output,
                "error_message": tc.error_message,
                "duration_ms": tc.duration_ms,
                "created_at": tc.created_at.isoformat() if tc.created_at else None,
            }
            for tc in tool_calls
        ]

    return ApiEnvelope(status="success", data=data, messages=[], meta={})
