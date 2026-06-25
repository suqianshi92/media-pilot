"""删除未发布流程 —— 彻底清理下载/入库任务、源文件和数据库记录。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from media_pilot.config.settings import AppConfig
from media_pilot.orchestration.db_retry import safe_commit
from media_pilot.repository.models import (
    AdapterCall,
    AuditLog,
    DownloadTask,
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


@dataclass(frozen=True)
class DeleteResult:
    task_id: str
    flow_type: str  # "download_only" | "ingest"
    deleted: bool
    qb_deleted: bool | None = None  # None = 无需 qB 删除
    qb_error: str | None = None
    files_cleaned: list[str] = ()


# ── 路径守卫 ──


def _is_safe_to_delete(target: Path, allowed_roots: list[Path]) -> bool:
    """验证删除目标在允许根目录内且不等于根目录本身。"""
    try:
        resolved = target.resolve(strict=False)
    except Exception:
        return False
    for root in allowed_roots:
        try:
            root_resolved = root.resolve(strict=False)
        except Exception:
            continue
        if resolved == root_resolved:
            return False  # 禁止删除根目录本身
        if resolved.is_relative_to(root_resolved):
            return True
    return False


def _delete_path(target: Path) -> bool:
    """安全删除文件或目录。"""
    try:
        path = Path(target)
        if not path.exists():
            return False
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except Exception:
        return False


# ── 删除 download-only 流程 ──


def delete_download_only(
    session: Session,
    download_id: str,
    config: AppConfig,
) -> DeleteResult:
    """彻底删除一个 download-only 流程。

    1. 若有关联 qb_hash，先请求 qB 删除 torrent
    2. 删除本地源文件/目录（路径守卫保护）
    3. 级联删除数据库记录
    """
    dl = session.get(DownloadTask, download_id)
    if dl is None:
        return DeleteResult(
            task_id=download_id, flow_type="download_only",
            deleted=False,
        )

    from media_pilot.resource_discovery.qbittorrent_adapter import QBittorrentAdapter

    adapter = QBittorrentAdapter(config)
    qb_deleted = None
    qb_error = None
    files_cleaned: list[str] = []

    # 1. qB 删除
    if dl.qb_hash:
        try:
            qb_state = adapter.delete_torrent(dl.qb_hash, delete_files=True)
            if qb_state in ("deleted", "not_found"):
                # "deleted" = HTTP 200; "not_found" = 404, 视为幂等成功
                # (qB 侧可能已被外部清理). 两种都视作 qb_deleted=True,
                # 让 "qB 已删但 DB 残留" 的半完成状态可以靠重试收敛.
                qb_deleted = True
            else:
                qb_deleted = False
                qb_error = "qBittorrent 删除失败，本地清理继续"
        except Exception as exc:
            qb_error = str(exc)

    # 2. 本地源文件/目录删除
    allowed_roots = [config.downloads_dir, config.watch_dir]
    if dl.content_path:
        target = Path(dl.content_path)
        if _is_safe_to_delete(target, allowed_roots):
            if _delete_path(target):
                files_cleaned.append(str(target))
    elif dl.title and dl.save_path:
        # 无 content_path 时尝试预估路径
        candidate = Path(dl.save_path) / dl.title
        if _is_safe_to_delete(candidate, allowed_roots) and candidate.exists():
            if _delete_path(candidate):
                files_cleaned.append(str(candidate))

    # 3. 清理关联的入库任务（如果存在）
    if dl.ingest_task_id:
        _cascade_delete_ingest_task(session, dl.ingest_task_id)

    # 4. 删除下载记录
    session.delete(dl)
    safe_commit(session)

    return DeleteResult(
        task_id=download_id,
        flow_type="download_only",
        deleted=True,
        qb_deleted=qb_deleted,
        qb_error=qb_error,
        files_cleaned=files_cleaned,
    )


# ── 删除未发布入库任务 ──


def delete_ingest_task(
    session: Session,
    task_id: str,
    config: AppConfig,
) -> DeleteResult:
    """彻底删除一个未发布入库任务。

    1. 检查任务状态，已发布完成的不允许删除
    2. 清理关联的下载任务（如果有 qb_hash 则先通知 qB）
    3. 删除源文件/目录（路径守卫保护）
    4. 级联删除所有业务数据
    5. 删除主任务记录
    """
    task = session.get(IngestTask, task_id)
    if task is None:
        return DeleteResult(
            task_id=task_id, flow_type="ingest", deleted=False,
        )

    # 已发布完成（含兼容完成态）的不允许删除
    if task.status in ("library_import_complete", "completed"):
        return DeleteResult(
            task_id=task_id, flow_type="ingest", deleted=False,
        )

    from media_pilot.resource_discovery.qbittorrent_adapter import QBittorrentAdapter

    adapter = QBittorrentAdapter(config)
    qb_deleted = None
    qb_error = None
    files_cleaned: list[str] = []

    # 1. 处理关联的下载任务
    source_dl_id = task.source_download_task_id
    if source_dl_id:
        dl = session.get(DownloadTask, source_dl_id)
        if dl and dl.qb_hash:
            try:
                qb_state = adapter.delete_torrent(dl.qb_hash, delete_files=True)
                if qb_state in ("deleted", "not_found"):
                    # "deleted" = HTTP 200; "not_found" = 404, 视为幂等成功
                    # (qB 侧可能已被外部清理). 两种都视作 qb_deleted=True,
                    # 让 "qB 已删但 DB 残留" 的半完成状态可以靠重试收敛.
                    qb_deleted = True
                else:
                    qb_deleted = False
                    qb_error = "qBittorrent 删除失败，本地清理继续"
            except Exception as exc:
                qb_error = str(exc)
        if dl:
            session.delete(dl)

    # 2. 删除源文件/目录
    allowed_roots = [config.downloads_dir, config.watch_dir]
    source_path = Path(task.source_path) if task.source_path else None
    if source_path and _is_safe_to_delete(source_path, allowed_roots):
        if _delete_path(source_path):
            files_cleaned.append(str(source_path))

    # 3. 级联删除业务数据 + 主任务
    _cascade_delete_ingest_task(session, task_id)

    safe_commit(session)

    return DeleteResult(
        task_id=task_id,
        flow_type="ingest",
        deleted=True,
        qb_deleted=qb_deleted,
        qb_error=qb_error,
        files_cleaned=files_cleaned,
    )


# ── 级联删除 ──


# ── 撤回后删除任务输入（预检 + 二次确认） ──


@dataclass(frozen=True)
class DeleteInputPreview:
    allowed: bool
    target_path: str | None = None
    path_type: str | None = None  # "file" | "directory"
    outcome_description: str = ""


def _resolve_input_path(session: Session, task_id: str) -> str | None:
    """解析任务输入节点路径，优先使用 MediaSourceSelection。"""
    from media_pilot.repository.repositories import MediaSourceSelectionRepository

    sel = MediaSourceSelectionRepository(session).get_for_task(task_id)
    if sel is not None and sel.selected_path:
        return sel.selected_path

    from media_pilot.repository.repositories import IngestTaskRepository

    task = IngestTaskRepository(session).get(task_id)
    if task is not None:
        return task.source_path

    return None


def preview_delete_input(
    session: Session, task_id: str, config: AppConfig,
) -> DeleteInputPreview:
    """预检删除任务输入，不执行任何文件操作。"""
    from media_pilot.repository.repositories import IngestTaskRepository

    task = IngestTaskRepository(session).get(task_id)
    if task is None:
        return DeleteInputPreview(
            allowed=False,
            outcome_description="任务不存在",
        )

    target = _resolve_input_path(session, task_id)
    if not target:
        return DeleteInputPreview(
            allowed=False,
            outcome_description="无法解析任务输入路径",
        )

    target_path = Path(target)
    # route-adult-movie-library-root: TPDB 成人影片库根 (config.adult_movies_dir)
    # 与 movies_dir / shows_dir 同等地位, 一旦配置就作为受控根本身被拒删;
    # 缺失时不追加, 不影响未启用 TPDB 的部署.
    controlled: list[Path] = [
        config.downloads_dir, config.watch_dir, config.workspace_dir,
        config.movies_dir, config.shows_dir,
    ]
    if config.adult_movies_dir is not None:
        controlled.append(config.adult_movies_dir)

    if not _is_safe_to_delete(target_path, controlled):
        return DeleteInputPreview(
            allowed=False,
            target_path=str(target_path),
            path_type="directory" if target_path.is_dir() else "file",
            outcome_description=f"路径 {target_path} 不在受控根目录内或是受控根目录本身，拒绝删除",
        )

    path_type = "directory" if target_path.is_dir() else "file"
    return DeleteInputPreview(
        allowed=True,
        target_path=str(target_path),
        path_type=path_type,
        outcome_description=(
            f"将删除{'目录' if path_type == 'directory' else '文件'} {target_path}"
        ),
    )


def execute_delete_input(
    session: Session, task_id: str, config: AppConfig,
) -> dict:
    """执行删除任务输入，需在预检通过且用户二次确认后调用。

    删除文件后标记任务为终态 "deleted" 并清理业务数据，
    但保留 IngestTask 主记录、OperationRecord 和 AuditLog 用于审计。
    """
    preview = preview_delete_input(session, task_id, config)
    if not preview.allowed:
        raise ValueError(preview.outcome_description)

    target_path = Path(preview.target_path)

    # 删除任务输入文件/目录
    if target_path.exists():
        if target_path.is_dir():
            shutil.rmtree(target_path)
        else:
            target_path.unlink()

    # 记录审计事件（在清理业务数据前写入，避免被级联删除）
    session.add(OperationRecord(
        task_id=task_id,
        operation_type="delete_task_input",
        permission_level="write",
        source_path=str(target_path),
        status="succeeded",
        details={"path_type": preview.path_type},
    ))

    # 标记任务为终态，保留 IngestTask/OperationRecord/AuditLog 用于审计
    task = session.get(IngestTask, task_id)
    if task is not None:
        task.status = "deleted"
        task.current_step = "delete_task_input"

    # 清理业务数据（不删审计/事件记录和主任务）
    _cascade_delete_task_business_data(session, task_id)
    safe_commit(session)

    return {
        "status": "deleted",
        "outcome": f"已删除任务输入 {target_path} 并清理任务数据",
    }


def _cascade_delete_ingest_task(session: Session, task_id: str) -> None:
    """删除入库任务及其所有关联数据（含审计记录）。"""
    tables_in_order = [
        (AuditLog, AuditLog.task_id),
        (OperationRecord, OperationRecord.task_id),
        (AdapterCall, AdapterCall.task_id),
        (FileAsset, FileAsset.task_id),
        (WriteResult, WriteResult.task_id),
        (MetadataDetail, MetadataDetail.task_id),
        (SearchKeywordRecord, SearchKeywordRecord.task_id),
        (MediaSourceSelection, MediaSourceSelection.task_id),
        (MediaCandidate, MediaCandidate.task_id),
    ]

    for model, col in tables_in_order:
        session.execute(delete(model).where(col == task_id))

    session.execute(delete(IngestTask).where(IngestTask.id == task_id))


def _cascade_delete_task_business_data(session: Session, task_id: str) -> None:
    """清理业务数据但保留 IngestTask、OperationRecord、AuditLog 用于审计。

    用于 delete_task_input 路径：任务标记为终态，不留业务残留，
    但事件时间线和审计记录完整保留。
    """
    tables_in_order = [
        (AdapterCall, AdapterCall.task_id),
        (FileAsset, FileAsset.task_id),
        (WriteResult, WriteResult.task_id),
        (MetadataDetail, MetadataDetail.task_id),
        (SearchKeywordRecord, SearchKeywordRecord.task_id),
        (MediaSourceSelection, MediaSourceSelection.task_id),
        (MediaCandidate, MediaCandidate.task_id),
    ]

    for model, col in tables_in_order:
        session.execute(delete(model).where(col == task_id))
