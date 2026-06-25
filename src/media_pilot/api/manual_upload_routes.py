"""手动上传 API 路由 — .torrent / magnet 批量导入 → 下载任务"""

from __future__ import annotations

import re
from io import BytesIO

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.api.schemas import ApiEnvelope, ApiMessage
from media_pilot.config import AppConfig
from media_pilot.repository.repositories import (
    DownloadTaskCreate,
    DownloadTaskRepository,
)
from media_pilot.resource_discovery.qbittorrent_adapter import QBittorrentAdapter
from media_pilot.services.torrent_parser import parse_torrent_meta

router = APIRouter(prefix="/api/v1/manual-upload")

_MAGNET_RE = re.compile(r"^magnet:\?xt=urn:btih:[a-zA-Z0-9]+", re.IGNORECASE)


# ── 请求 / 响应模型 ──


class ManualUploadItem(BaseModel):
    """parse 阶段返回的单条待导入条目"""

    key: str = Field(..., description="批次内标识")
    kind: str = Field(..., description="torrent / magnet")
    source_index: int = Field(..., description="原始输入数组中的位置索引")
    display_name: str = Field("", description="展示名")
    size_bytes: int | None = Field(None, description="总大小（未知时为 None）")
    size_label: str = Field("未知", description="人类可读大小")
    valid: bool = Field(True)
    error: str | None = Field(None)


class ParseResult(BaseModel):
    items: list[ManualUploadItem] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)


class SubmitItem(BaseModel):
    key: str
    kind: str  # torrent | magnet
    torrent_data_b64: str | None = Field(None, description="torrent 文件 base64")
    magnet_uri: str | None = Field(None)
    display_name: str = ""
    preselected_profile: str | None = None
    preselected_provider: str | None = None
    preselected_external_id: str | None = None


class SubmitBody(BaseModel):
    items: list[SubmitItem] = Field(..., min_length=1, max_length=5)


class SubmitItemResult(BaseModel):
    key: str
    success: bool
    download_task_id: str | None = None
    message: str = ""


class SubmitResult(BaseModel):
    results: list[SubmitItemResult] = Field(default_factory=list)


# ── 辅助 ──


def _format_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "未知"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _magnet_display_name(magnet: str) -> str:
    """从 magnet URI 中提取 dn 参数作为显示名。"""
    match = re.search(r"[?&]dn=([^&]+)", magnet, re.IGNORECASE)
    if match:
        from urllib.parse import unquote
        return unquote(match.group(1))
    return magnet[:80]


# ── 路由 ──


@router.post("/parse")
def parse_upload(
    request: Request,
    torrents: list[UploadFile] = File(default_factory=list),
    magnets: str = Form(default=""),
) -> ApiEnvelope[ParseResult]:
    """解析上传的 .torrent 文件和 magnet 文本，返回待导入条目列表。

    接收 multipart/form-data：
    - torrents: 多个 .torrent 文件
    - magnets: 多行 magnet URI 文本
    """
    items: list[ManualUploadItem] = []
    errors: list[dict] = []

    # 处理 magnet 行
    magnet_lines = [line.strip() for line in magnets.splitlines() if line.strip()]

    # 单次新增上限校验（torrent + magnet 合并计数）
    total_input = len(torrents) + len(magnet_lines)
    if total_input > 5:
        return ApiEnvelope(
            status="error",
            data=ParseResult(items=[], errors=[]),
            messages=[ApiMessage(
                level="error",
                code="too_many_items",
                text=f"单次最多新增 5 个条目，当前提供了 {total_input} 个（{len(torrents)} 个 .torrent + {len(magnet_lines)} 个 magnet）",
            )],
            meta={"total": 0},
        )
    for idx, line in enumerate(magnet_lines):
        if not _MAGNET_RE.match(line):
            errors.append({
                "index": idx,
                "line": line[:120],
                "error": f"第 {idx + 1} 行不是有效的 magnet 链接",
            })
            continue
        key = f"magnet-{idx}"
        items.append(ManualUploadItem(
            key=key,
            kind="magnet",
            source_index=idx,
            display_name=_magnet_display_name(line),
            size_bytes=None,
            size_label="未知",
        ))

    # 处理 torrent 文件
    for idx, f in enumerate(torrents):
        if not f.filename:
            errors.append({"file": "", "error": "收到无名 torrent 文件"})
            continue
        try:
            raw = f.file.read()
        except Exception:
            errors.append({"file": f.filename, "error": f"无法读取 {f.filename}"})
            continue

        if not raw:
            errors.append({"file": f.filename, "error": f"{f.filename} 是空文件"})
            continue

        meta = parse_torrent_meta(raw)
        if meta is None:
            errors.append({"file": f.filename, "error": f"无法解析 {f.filename}，可能不是有效的 torrent 文件"})
            continue

        key = f"torrent-{idx}"
        items.append(ManualUploadItem(
            key=key,
            kind="torrent",
            source_index=idx,
            display_name=meta.display_name,
            size_bytes=meta.total_size_bytes,
            size_label=_format_size(meta.total_size_bytes),
        ))

    return ApiEnvelope(
        status="success",
        data=ParseResult(items=items, errors=errors),
        messages=[],
        meta={"total": len(items)},
    )


@router.post("/submit")
def submit_uploads(
    body: SubmitBody,
    request: Request,
) -> ApiEnvelope[SubmitResult]:
    """将待导入条目提交为正式下载任务。"""
    config: AppConfig | None = getattr(request.app.state, "config", None)
    if config is None:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(level="error", code="not_configured", text="未配置服务")],
            meta={},
        )

    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )

    adapter = QBittorrentAdapter(config)
    results: list[SubmitItemResult] = []

    for item in body.items:
        try:
            result = _submit_one(item, config, adapter, session_factory)
            results.append(result)
        except Exception as exc:
            results.append(SubmitItemResult(
                key=item.key,
                success=False,
                message=f"提交失败：{exc}",
            ))

    success_count = sum(1 for r in results if r.success)
    return ApiEnvelope(
        status="success" if success_count > 0 else "error",
        data=SubmitResult(results=results),
        messages=[ApiMessage(
            level="info" if success_count > 0 else "error",
            code="submitted",
            text=f"成功提交 {success_count}/{len(results)} 个下载任务",
        )],
        meta={},
    )


def _submit_one(
    item: SubmitItem,
    config: AppConfig,
    adapter: QBittorrentAdapter,
    session_factory: sessionmaker[Session] | None,
) -> SubmitItemResult:
    """提交单个条目：创建 DownloadTask → 提交给 qB → 更新状态。"""
    title = item.display_name or item.key
    sid = None

    if session_factory is not None:
        with session_factory() as session:
            sid = _create_download_task(session, item, config, title)
            if sid is None:
                return SubmitItemResult(key=item.key, success=False, message="创建下载任务失败")

    # 提交到 qBittorrent
    tag = f"media-pilot:{sid}" if sid else None

    if item.kind == "torrent" and item.torrent_data_b64:
        import base64
        try:
            torrent_data = base64.b64decode(item.torrent_data_b64)
        except Exception:
            return SubmitItemResult(key=item.key, success=False, message="torrent 数据解码失败")
        result = adapter.add_torrent_file(torrent_data, tag=tag)
        if sid and result.status == "submitted":
            _mark_submitted(session_factory, sid, title)
    elif item.kind == "magnet" and item.magnet_uri:
        from media_pilot.resource_discovery.types import DownloadRequest
        result = adapter.add_download(
            DownloadRequest(
                download_url=None,
                magnet_url=item.magnet_uri,
                title=title,
                source="manual_upload",
                indexer="manual_upload",
            ),
            tag=tag,
        )
        if sid and result.status == "submitted":
            _mark_submitted(session_factory, sid, title)
    else:
        return SubmitItemResult(key=item.key, success=False, message="无效条目：缺少数据")

    if result.status == "submitted":
        return SubmitItemResult(
            key=item.key,
            success=True,
            download_task_id=sid,
            message=f"已提交：{title}",
        )

    # 提交失败，标记下载任务失败
    if sid:
        _mark_failed(session_factory, sid, result.message)
    return SubmitItemResult(key=item.key, success=False, message=result.message)


def _create_download_task(
    session: Session,
    item: SubmitItem,
    config: AppConfig,
    title: str,
) -> str | None:
    """在数据库中创建 DownloadTask 记录并返回其 ID。"""
    try:
        repo = DownloadTaskRepository(session)
        task = repo.create(DownloadTaskCreate(
            title=title,
            source="manual_upload",
            save_path=str(config.qbittorrent_save_path),
            indexer="manual_upload",
            status="submitting",
            preselected_metadata_profile=item.preselected_profile,
            preselected_metadata_provider=item.preselected_provider,
            preselected_metadata_external_id=item.preselected_external_id,
        ))
        session.commit()
        return task.id
    except Exception:
        session.rollback()
        return None


def _mark_submitted(
    session_factory: sessionmaker[Session] | None,
    task_id: str,
    qb_name: str,
) -> None:
    if session_factory is None:
        return
    try:
        with session_factory() as session:
            repo = DownloadTaskRepository(session)
            task = repo.get(task_id)
            if task is not None:
                repo.update_sync_status(task, status="submitted", qb_name=qb_name)
                session.commit()
    except Exception:
        pass


def _mark_failed(
    session_factory: sessionmaker[Session] | None,
    task_id: str,
    error_message: str,
) -> None:
    if session_factory is None:
        return
    try:
        with session_factory() as session:
            repo = DownloadTaskRepository(session)
            task = repo.get(task_id)
            if task is not None:
                repo.update_sync_status(task, status="failed", error_message=error_message)
                session.commit()
    except Exception:
        pass
