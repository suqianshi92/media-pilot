"""媒体获取流程列表 read-model 服务.

聚合 `IngestTask + optional DownloadTask` 与 download-only `DownloadTask`,
按 attention priority 排序, 应用 status filter, 分页并返回 total.

只读 view, 不新增持久化实体. 首版允许内存聚合后分页 (design Decision 5);
接口语义必须是后端分页, 前端不再自己拼接 /tasks 与 /downloads.
"""

from __future__ import annotations

import functools
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_pilot.api.task_dtos import (
    AgentStatusSummary,
    DownloadTaskSummary,
    FlowStatusSummary,
    FlowSummary,
    FlowType,
    TaskSummary,
)
from media_pilot.api.task_mapper import (
    map_download_task_to_summary,
    map_to_task_summaries,
)
from media_pilot.repository.models import DownloadTask
from media_pilot.repository.repositories import IngestTaskRepository

# ── attention priority 状态分组 (design Decision 4) ──
# 与前端 `flow-attention-sort` helper 同源, 后端负责统一排序.

PRIORITY_1_WAITING = frozenset({
    "waiting_user",
})

PRIORITY_2_PROCESSING = frozenset({
    "agent_running",
    "processing",
    "queued",
    "waiting_stable",
    "submitted",
    "downloading",
    "awaiting_sync",
    "paused",
})

PRIORITY_3_FAILED = frozenset({
    "agent_failed",
    "failed",
    "sync_failed",
})

PRIORITY_4_DONE = frozenset({
    "library_import_complete",
    "completed_pending_ingest",
    "completed",
})

VALID_FILTERS = frozenset({
    "all",
    "waiting_user",
    "processing",
    "library_import_complete",
    "failed",
    "no_metadata",
})


def _flow_priority(total_status: str) -> int:
    if total_status in PRIORITY_1_WAITING:
        return 1
    if total_status in PRIORITY_2_PROCESSING:
        return 2
    if total_status in PRIORITY_3_FAILED:
        return 3
    if total_status in PRIORITY_4_DONE:
        return 4
    return 5


def _filter_statuses(name: str) -> frozenset[str] | None:
    if name == "all" or name is None:
        return None
    if name == "waiting_user":
        return PRIORITY_1_WAITING
    if name == "processing":
        return PRIORITY_2_PROCESSING
    if name == "library_import_complete":
        return PRIORITY_4_DONE
    if name == "failed":
        return PRIORITY_3_FAILED
    if name == "no_metadata":
        return None
    raise ValueError(f"unknown filter: {name}")


def _cmp_flows(a: FlowSummary, b: FlowSummary) -> int:
    pa = _flow_priority(a.total_status)
    pb = _flow_priority(b.total_status)
    if pa != pb:
        return -1 if pa < pb else 1
    if a.updated_at != b.updated_at:
        return -1 if a.updated_at > b.updated_at else 1
    if a.created_at != b.created_at:
        return -1 if a.created_at > b.created_at else 1
    if a.id != b.id:
        return -1 if a.id < b.id else 1
    return 0


def _ingest_summary_to_flow(task: TaskSummary) -> FlowSummary:
    """从 TaskSummary (linked 或 standalone ingest) 转成 ingest FlowSummary."""

    flow_type: FlowType = "managed_download" if task.download_task else "external_import"

    return FlowSummary(
        id=f"ingest:{task.id}",
        flow_type=flow_type,
        route_target="task_detail",
        ingest_task_id=task.id,
        download_task_id=task.download_task.id if task.download_task else None,
        total_status=task.total_status,
        title=task.title,
        year=task.year,
        media_type=task.media_type,
        metadata_status=task.metadata_status,
        can_confirm=task.can_confirm,
        file_format=task.file_format,
        source_path=task.source_path,
        created_at=task.created_at,
        updated_at=task.updated_at,
        status_summary=FlowStatusSummary(
            status=task.status_summary.status,
            current_step=task.status_summary.current_step,
            failure_reason=task.status_summary.failure_reason,
            confidence=task.status_summary.confidence,
            confidence_level=task.status_summary.confidence_level,
            latest_message=task.status_summary.latest_message,
        ),
        agent_status_summary=task.agent_status_summary,
        download_task=task.download_task,
    )


def _download_only_flow(dl: DownloadTaskSummary) -> FlowSummary:
    """download-only DownloadTask 转成 FlowSummary (route_target=download_detail)."""

    return FlowSummary(
        id=f"download:{dl.id}",
        flow_type="download_only",
        route_target="download_detail",
        ingest_task_id=dl.ingest_task_id,
        download_task_id=dl.id,
        total_status=dl.status,
        title=dl.title,
        year=None,
        media_type=None,
        metadata_status="unknown",
        can_confirm=False,
        file_format=None,
        source_path=dl.save_path,
        created_at=dl.created_at,
        updated_at=dl.updated_at,
        status_summary=FlowStatusSummary(
            status=dl.status,
            current_step=None,
            failure_reason=dl.error_message,
            confidence=None,
            confidence_level="unknown",
            latest_message=dl.error_message or dl.qb_state or dl.status,
        ),
        agent_status_summary=AgentStatusSummary(run_status="none"),
        download_task=dl,
    )


def build_flows(
    session: Session,
    *,
    filter_name: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[FlowSummary], int]:
    """聚合 IngestTask + DownloadTask → FlowSummary 列表.

    返回 (current_page_items, total_after_filter). 首版内存聚合后分页.
    """

    # 1) ingest: 全量查 IngestTask → TaskSummary (内部已关联 download_task 摘要)
    ingest_rows = IngestTaskRepository(session).list()
    ingest_summaries = map_to_task_summaries(session, ingest_rows)
    ingest_flows = [_ingest_summary_to_flow(t) for t in ingest_summaries]

    # 2) download: 全量 DownloadTask. /flows 是统一列表接口, 不得复用
    # /downloads 的"非终态 + 最近 50"截断 — 那个截断是详情/操作端点的
    # 优化, 列表必须看到全部. 首版内存聚合后分页 (design Decision 5).
    all_downloads = list(session.scalars(select(DownloadTask)))

    # 3) 去重: linked download 不再单独作为 download-only 出现 (design Decision 3)
    linked_dl_ids: set[str] = {
        f.download_task_id for f in ingest_flows if f.download_task_id is not None
    }
    orphan_downloads = [d for d in all_downloads if d.id not in linked_dl_ids]
    orphan_flows = [
        _download_only_flow(map_download_task_to_summary(d))
        for d in orphan_downloads
    ]

    flows = ingest_flows + orphan_flows

    # 4) filter
    allowed_statuses = _filter_statuses(filter_name or "all")
    if (filter_name or "all") == "no_metadata":
        flows = [f for f in flows if f.metadata_status == "none"]
    elif allowed_statuses is not None:
        flows = [f for f in flows if f.total_status in allowed_statuses]

    # 5) attention priority 排序 + 同 priority 内 updated_at desc → created_at desc → id asc
    flows.sort(key=functools.cmp_to_key(_cmp_flows))

    # 6) 分页 + total
    total = len(flows)
    start = (page - 1) * page_size
    page_items = flows[start : start + page_size]

    return page_items, total
