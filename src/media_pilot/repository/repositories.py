from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

_UNSET = object()

from media_pilot.repository.models import (
    AgentDecisionRequest,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    DownloadTask,
    EpisodeMapping,
    IngestTask,
    MediaCandidate,
    MediaSourceSelection,
    MetadataDetail,
    SearchKeywordRecord,
    WritePlan,
    WriteResult,
    utc_now,
)
from media_pilot.repository.task_sort import build_task_list_order_by


@dataclass(frozen=True, kw_only=True)
class IngestTaskCreate:
    source_path: str
    status: str
    current_step: str | None = None
    source_size_bytes: int | None = None
    source_modified_at: datetime | None = None
    discovered_at: datetime | None = None
    media_type: str | None = None
    confidence: float | None = None
    failure_reason: str | None = None
    source_download_task_id: str | None = None
    # 元数据预选事实 — 由 DownloadTask 派发时透传. Agent 链路 (prepare
    # select_metadata_candidate_decision / check_eligibility) 看到这
    # 三个字段都存在时, 必须视作强事实, 不得向用户确认.
    preselected_metadata_profile: str | None = None
    preselected_metadata_provider: str | None = None
    preselected_metadata_external_id: str | None = None


class IngestTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: IngestTaskCreate) -> IngestTask:
        task = IngestTask(
            source_path=data.source_path,
            source_size_bytes=data.source_size_bytes,
            source_modified_at=data.source_modified_at,
            preselected_metadata_profile=data.preselected_metadata_profile,
            preselected_metadata_provider=data.preselected_metadata_provider,
            preselected_metadata_external_id=data.preselected_metadata_external_id,
            discovered_at=data.discovered_at,
            status=data.status,
            current_step=data.current_step,
            media_type=data.media_type,
            confidence=data.confidence,
            failure_reason=data.failure_reason,
            source_download_task_id=data.source_download_task_id,
        )
        self._session.add(task)
        self._session.flush()
        return task

    def get(self, task_id: str) -> IngestTask | None:
        return self._session.get(IngestTask, task_id)

    def get_by_source_path(self, source_path: str) -> IngestTask | None:
        statement = select(IngestTask).where(IngestTask.source_path == source_path)
        return self._session.scalars(statement).first()

    def list(
        self,
        *,
        status: str | None = None,
    ) -> list[IngestTask]:
        statement = select(IngestTask).order_by(*build_task_list_order_by())
        if status is not None:
            statement = statement.where(IngestTask.status == status)
        return list(self._session.scalars(statement))

    def list_page(
        self,
        *,
        status: str | None = None,
        limit: int,
        offset: int,
    ) -> "list[IngestTask]":
        """SQL 分页查询. 排序复用 `build_task_list_order_by()` 保持跨页稳定."""

        statement = (
            select(IngestTask)
            .order_by(*build_task_list_order_by())
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            statement = statement.where(IngestTask.status == status)
        return list(self._session.scalars(statement))

    def count(
        self,
        *,
        status: str | None = None,
    ) -> int:
        """SQL `COUNT(*)` 全量或按 status filter 统计 ingest task 行数."""

        statement = select(func.count()).select_from(IngestTask)
        if status is not None:
            statement = statement.where(IngestTask.status == status)
        return int(self._session.execute(statement).scalar_one())

    def update_status(
        self,
        task: IngestTask,
        *,
        status: str,
        current_step: str,
        failure_reason: str | None = None,
    ) -> IngestTask:
        task.status = status
        task.current_step = current_step
        task.failure_reason = failure_reason
        self._session.flush()
        return task


@dataclass(frozen=True, kw_only=True)
class DownloadTaskCreate:
    title: str
    source: str
    save_path: str
    indexer: str | None = None
    qb_hash: str | None = None
    qb_name: str | None = None
    status: str = "submitted"
    # 元数据预选
    preselected_metadata_profile: str | None = None
    preselected_metadata_provider: str | None = None
    preselected_metadata_external_id: str | None = None


# ── 下载任务 Repository ──


class DownloadTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: DownloadTaskCreate) -> DownloadTask:
        task = DownloadTask(
            title=data.title,
            source=data.source,
            save_path=data.save_path,
            indexer=data.indexer,
            qb_hash=data.qb_hash,
            qb_name=data.qb_name,
            status=data.status,
            preselected_metadata_profile=data.preselected_metadata_profile,
            preselected_metadata_provider=data.preselected_metadata_provider,
            preselected_metadata_external_id=data.preselected_metadata_external_id,
        )
        self._session.add(task)
        self._session.flush()
        return task

    def get(self, task_id: str) -> DownloadTask | None:
        return self._session.get(DownloadTask, task_id)

    def get_by_qb_hash(self, qb_hash: str) -> DownloadTask | None:
        statement = select(DownloadTask).where(DownloadTask.qb_hash == qb_hash)
        return self._session.scalars(statement).first()

    def list_non_terminal(self) -> list[DownloadTask]:
        """查询非终态下载任务（需要同步状态）。

        sync_failed 是可恢复状态而非终态：下载已提交但因 qB 临时不可达
        或 hash 暂未可见导致同步失败，后续同步周期必须继续纳入该任务。
        """
        terminal = {"completed", "failed"}
        statement = (
            select(DownloadTask)
            .where(DownloadTask.status.notin_(terminal))
            .order_by(DownloadTask.created_at.asc())
        )
        return list(self._session.scalars(statement))

    def list_occupied_paths(self) -> "frozenset":  # noqa: F821  # frozenset[Path]
        """返回非终态下载任务的内容路径集合（不含 save_path 根目录）。

        只占用 torrent 的顶层内容路径，不占用统一的下载根目录，
        以保证外部输入（PikPak / 手动拷贝）仍可被扫描器发现。

        当 content_path 未补齐时，用 save_path + title 预估预留路径，
        防止 hash 补齐窗口期内扫描器误捡 qB 正在写入的文件。
        """
        from pathlib import Path

        from media_pilot.orchestration.ingestion import MEDIA_EXTENSIONS

        paths: set[Path] = set()
        for task in self.list_non_terminal():
            if task.content_path:
                paths.add(Path(task.content_path))
            elif task.title and task.save_path:
                # 预估占用：覆盖目录 torrent（父目录匹配）和常见单文件扩展名
                base = Path(task.save_path) / task.title
                paths.add(base)
                for ext in MEDIA_EXTENSIONS:
                    paths.add(Path(f"{base}{ext}"))
        return frozenset(paths)

    def update_sync_status(
        self,
        task: DownloadTask,
        *,
        progress: float | None = None,
        download_speed_bytes_per_second: int | None = None,
        upload_speed_bytes_per_second: int | None = None,
        seeders: int | None = None,
        leechers: int | None = None,
        connections: int | None = None,
        qb_state: str | None = None,
        qb_hash: str | None = None,
        qb_name: str | None = None,
        content_path: str | None = None,
        status: str | None = None,
        error_message: str | None = _UNSET,
    ) -> DownloadTask:
        if progress is not None:
            task.progress = progress
        if download_speed_bytes_per_second is not None:
            task.download_speed_bytes_per_second = download_speed_bytes_per_second
        if upload_speed_bytes_per_second is not None:
            task.upload_speed_bytes_per_second = upload_speed_bytes_per_second
        if seeders is not None:
            task.seeders = seeders
        if leechers is not None:
            task.leechers = leechers
        if connections is not None:
            task.connections = connections
        if qb_state is not None:
            task.qb_state = qb_state
        if qb_hash is not None:
            task.qb_hash = qb_hash
        if qb_name is not None:
            task.qb_name = qb_name
        if content_path is not None:
            task.content_path = content_path
        if status is not None:
            task.status = status
        if error_message is not _UNSET:
            task.error_message = error_message
        self._session.flush()
        return task

    def bind_ingest_task(
        self, task: DownloadTask, ingest_task_id: str
    ) -> DownloadTask:
        task.ingest_task_id = ingest_task_id
        self._session.flush()
        return task


class MediaCandidateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_candidate(
        self,
        task_id: str,
        *,
        source: str,
        media_type: str,
        title: str | None,
        original_title: str | None,
        year: int | None,
        external_id: str | None,
        confidence: float | None,
        reason: str | None,
        payload: dict,
    ) -> MediaCandidate:
        # 同 (task_id, source, external_id, media_type) 已落库 → 复用
        # 既有行, 不重复插入. 真实场景: search_metadata 重复搜索 (LLM
        # 重试 / 备选 keyword 路径) 会把同一 TMDB 条目 (中文 vs 英文
        # 标题) 多次落库, 让 has_clear_winner 误把同 external_id 的
        # 两个候选当 close competitor 触发 no_clear_winner, 阻塞
        # auto_ingest. dedup 必须在 repository 层做, 单一入口, 不依赖
        # 上层 service 各自去重.
        if external_id:
            existing = self._session.scalar(
                select(MediaCandidate)
                .where(
                    MediaCandidate.task_id == task_id,
                    MediaCandidate.source == source,
                    MediaCandidate.external_id == external_id,
                    MediaCandidate.media_type == media_type,
                )
                .limit(1)
            )
            if existing is not None:
                # 标题 / 年份 / confidence / payload 哪个版本更新用哪个.
                # 但 (task_id, source, external_id, media_type) 同 key 下
                # 不应出现 confidence 倒退的情况 — 若新值更高, 升上去
                # 以保证 has_clear_winner 的 margin 边界稳定.
                if (confidence or 0) > (existing.confidence or 0):
                    existing.confidence = confidence
                if title and not existing.title:
                    existing.title = title
                if original_title and not existing.original_title:
                    existing.original_title = original_title
                if year is not None and existing.year is None:
                    existing.year = year
                if reason and not existing.reason:
                    existing.reason = reason
                # payload 合并: 浅合并新非空 key, 不覆盖旧值.
                if payload:
                    merged = dict(existing.payload or {})
                    for k, v in payload.items():
                        merged.setdefault(k, v)
                    existing.payload = merged
                self._session.flush()
                return existing
        media_candidate = MediaCandidate(
            task_id=task_id,
            source=source,
            media_type=media_type,
            title=title,
            original_title=original_title,
            year=year,
            season=None,
            episode=None,
            language=None,
            version=None,
            external_id=external_id,
            confidence=confidence,
            reason=reason,
            payload=payload,
        )
        self._session.add(media_candidate)
        self._session.flush()
        return media_candidate

    def list_for_task(self, task_id: str) -> list[MediaCandidate]:
        statement = (
            select(MediaCandidate)
            .where(MediaCandidate.task_id == task_id)
            .order_by(MediaCandidate.created_at.asc())
        )
        return list(self._session.scalars(statement))


class MediaSourceSelectionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        task_id: str,
        *,
        input_path: str,
        selected_path: str | None,
        confidence: float | None,
        reason: str | None,
        payload: dict,
    ) -> MediaSourceSelection:
        record = MediaSourceSelection(
            task_id=task_id,
            input_path=input_path,
            selected_path=selected_path,
            confidence=confidence,
            reason=reason,
            payload=payload,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def get_for_task(self, task_id: str) -> MediaSourceSelection | None:
        statement = (
            select(MediaSourceSelection)
            .where(MediaSourceSelection.task_id == task_id)
            .order_by(MediaSourceSelection.created_at.desc())
        )
        return self._session.scalars(statement).first()


class SearchKeywordRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        task_id: str,
        *,
        keyword: str,
        source: str,
        confidence: float | None,
        reason: str | None,
        payload: dict,
    ) -> SearchKeywordRecord:
        record = SearchKeywordRecord(
            task_id=task_id,
            keyword=keyword,
            source=source,
            confidence=confidence,
            reason=reason,
            payload=payload,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def list_for_task(self, task_id: str) -> list[SearchKeywordRecord]:
        statement = (
            select(SearchKeywordRecord)
            .where(SearchKeywordRecord.task_id == task_id)
            .order_by(SearchKeywordRecord.created_at.asc())
        )
        return list(self._session.scalars(statement))


class MetadataDetailRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        task_id: str,
        *,
        provider: str,
        provider_id: str,
        media_type: str,
        title: str | None,
        original_title: str | None,
        year: int | None,
        payload: dict,
    ) -> MetadataDetail:
        record = MetadataDetail(
            task_id=task_id,
            provider=provider,
            provider_id=provider_id,
            media_type=media_type,
            title=title,
            original_title=original_title,
            year=year,
            payload=payload,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def get_for_task(self, task_id: str) -> MetadataDetail | None:
        statement = (
            select(MetadataDetail)
            .where(MetadataDetail.task_id == task_id)
            .order_by(MetadataDetail.created_at.desc())
        )
        return self._session.scalars(statement).first()


class WritePlanRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        task_id: str,
        *,
        target_dir: str,
        target_file: str | None,
        nfo_path: str | None,
        payload: dict,
    ) -> WritePlan:
        record = WritePlan(
            task_id=task_id,
            target_dir=target_dir,
            target_file=target_file,
            nfo_path=nfo_path,
            payload=payload,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def get_for_task(self, task_id: str) -> WritePlan | None:
        statement = (
            select(WritePlan)
            .where(WritePlan.task_id == task_id)
            .order_by(WritePlan.created_at.desc())
        )
        return self._session.scalars(statement).first()


class WriteResultRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        task_id: str,
        *,
        status: str,
        payload: dict,
    ) -> WriteResult:
        record = WriteResult(
            task_id=task_id,
            status=status,
            payload=payload,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def get_for_task(self, task_id: str) -> WriteResult | None:
        statement = (
            select(WriteResult)
            .where(WriteResult.task_id == task_id)
            .order_by(WriteResult.created_at.desc())
        )
        return self._session.scalars(statement).first()


class EpisodeMappingRepository:
    """剧集文件映射持久化"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save_mappings(
        self,
        task_id: str,
        entries: list[dict],
    ) -> list[EpisodeMapping]:
        # 清除该任务的旧映射
        self._session.query(EpisodeMapping).filter_by(task_id=task_id).delete()
        records = [
            EpisodeMapping(
                task_id=task_id,
                file_path=e["file_path"],
                season=e["season"],
                episode=e["episode"],
                source=e.get("source", "filename"),
            )
            for e in entries
        ]
        self._session.add_all(records)
        self._session.flush()
        return records

    def get_by_task(self, task_id: str) -> list[EpisodeMapping]:
        return list(
            self._session.scalars(
                select(EpisodeMapping).where(EpisodeMapping.task_id == task_id)
            ).all()
        )


# ---------------------------------------------------------------------------
# Agent runtime persistence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class AgentRunCreate:
    task_id: str
    current_step: str | None = None


class AgentRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: AgentRunCreate) -> AgentRun:
        active = self.get_active_by_task(data.task_id)
        if active is not None:
            raise ValueError(
                f"Task {data.task_id} already has an active AgentRun {active.id}"
            )
        run = AgentRun(
            task_id=data.task_id,
            status="active",
            current_step=data.current_step,
        )
        self._session.add(run)
        self._session.flush()
        return run

    def get(self, run_id: str) -> AgentRun | None:
        return self._session.get(AgentRun, run_id)

    def get_active_by_task(self, task_id: str) -> AgentRun | None:
        stmt = select(AgentRun).where(
            AgentRun.task_id == task_id, AgentRun.status == "active"
        )
        return self._session.scalars(stmt).first()

    def get_active_or_waiting_by_task(self, task_id: str) -> AgentRun | None:
        stmt = select(AgentRun).where(
            AgentRun.task_id == task_id,
            AgentRun.status.in_(["active", "waiting_user"]),
        )
        return self._session.scalars(stmt).first()

    def list_by_task(self, task_id: str) -> list[AgentRun]:
        stmt = (
            select(AgentRun)
            .where(AgentRun.task_id == task_id)
            .order_by(AgentRun.created_at.desc())
        )
        return list(self._session.scalars(stmt))

    def list_active(self) -> list[AgentRun]:
        stmt = select(AgentRun).where(AgentRun.status == "active")
        return list(self._session.scalars(stmt))

    def update_status(
        self,
        run: AgentRun,
        *,
        status: str,
        current_step: str | None = None,
        error_message: str | None = None,
    ) -> AgentRun:
        run.status = status
        if current_step is not None:
            run.current_step = current_step
        if error_message is not None:
            run.error_message = error_message
        self._session.flush()
        return run


@dataclass(frozen=True, kw_only=True)
class AgentMessageCreate:
    run_id: str
    role: str
    content: str | None = None
    tool_calls: dict | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None


class AgentMessageRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: AgentMessageCreate) -> AgentMessage:
        msg = AgentMessage(
            run_id=data.run_id,
            role=data.role,
            content=data.content,
            tool_calls=data.tool_calls,
            tool_call_id=data.tool_call_id,
            tool_name=data.tool_name,
        )
        self._session.add(msg)
        self._session.flush()
        return msg

    def get(self, message_id: str) -> AgentMessage | None:
        return self._session.get(AgentMessage, message_id)

    def list_by_run(self, run_id: str) -> list[AgentMessage]:
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.run_id == run_id)
            .order_by(AgentMessage.created_at.asc())
        )
        return list(self._session.scalars(stmt))

    def list_by_task(self, task_id: str) -> list[AgentMessage]:
        stmt = (
            select(AgentMessage)
            .join(AgentRun, AgentMessage.run_id == AgentRun.id)
            .where(AgentRun.task_id == task_id)
            .order_by(AgentMessage.created_at.asc())
        )
        return list(self._session.scalars(stmt))


@dataclass(frozen=True, kw_only=True)
class AgentToolCallCreate:
    run_id: str
    tool_name: str
    input: dict
    message_id: str | None = None
    tool_call_id: str | None = None
    status: str = "pending"


class AgentToolCallRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: AgentToolCallCreate) -> AgentToolCall:
        tc = AgentToolCall(
            run_id=data.run_id,
            tool_name=data.tool_name,
            input=data.input,
            message_id=data.message_id,
            tool_call_id=data.tool_call_id,
            status=data.status,
        )
        self._session.add(tc)
        self._session.flush()
        return tc

    def get(self, tool_call_id: str) -> AgentToolCall | None:
        return self._session.get(AgentToolCall, tool_call_id)

    def list_by_run(self, run_id: str) -> list[AgentToolCall]:
        stmt = (
            select(AgentToolCall)
            .where(AgentToolCall.run_id == run_id)
            .order_by(AgentToolCall.created_at.asc())
        )
        return list(self._session.scalars(stmt))

    def list_by_task(self, task_id: str) -> list[AgentToolCall]:
        stmt = (
            select(AgentToolCall)
            .join(AgentRun, AgentToolCall.run_id == AgentRun.id)
            .where(AgentRun.task_id == task_id)
            .order_by(AgentToolCall.created_at.asc())
        )
        return list(self._session.scalars(stmt))

    def update_status(
        self,
        tc: AgentToolCall,
        *,
        status: str,
        output: dict | None = None,
        error_message: str | None = None,
        duration_ms: int | None = None,
    ) -> AgentToolCall:
        tc.status = status
        if output is not None:
            tc.output = output
        if error_message is not None:
            tc.error_message = error_message
        if duration_ms is not None:
            tc.duration_ms = duration_ms
        self._session.flush()
        return tc


@dataclass(frozen=True, kw_only=True)
class AgentDecisionRequestCreate:
    run_id: str
    task_id: str
    decision_type: str
    question: str | None = None
    free_text_allowed: bool = False
    options: list[dict] | None = None
    payload: dict | None = None


class AgentDecisionRequestRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: AgentDecisionRequestCreate) -> AgentDecisionRequest:
        from media_pilot.orchestration.state_machine import IngestTaskStatus

        # Enforce at most one pending decision per AgentRun
        existing = self.list_pending_by_run(data.run_id)
        if existing:
            raise ValueError(
                f"AgentRun {data.run_id} already has a pending decision (id={existing[0].id})"
            )

        req = AgentDecisionRequest(
            run_id=data.run_id,
            task_id=data.task_id,
            decision_type=data.decision_type,
            question=data.question,
            free_text_allowed=data.free_text_allowed,
            options=data.options or [],
            payload=data.payload or {},
            status="pending",
        )
        self._session.add(req)
        # 创建 pending 决策时自动将任务状态联动为 waiting_user
        task = self._session.get(IngestTask, data.task_id)
        if task is not None:
            task.status = IngestTaskStatus.WAITING_USER
        self._session.flush()
        return req

    def get(self, decision_id: str) -> AgentDecisionRequest | None:
        return self._session.get(AgentDecisionRequest, decision_id)

    def list_pending_by_task(self, task_id: str) -> list[AgentDecisionRequest]:
        stmt = (
            select(AgentDecisionRequest)
            .where(
                AgentDecisionRequest.task_id == task_id,
                AgentDecisionRequest.status == "pending",
            )
            .order_by(AgentDecisionRequest.created_at.asc())
        )
        return list(self._session.scalars(stmt))

    def list_pending_by_run(self, run_id: str) -> list[AgentDecisionRequest]:
        stmt = (
            select(AgentDecisionRequest)
            .where(
                AgentDecisionRequest.run_id == run_id,
                AgentDecisionRequest.status == "pending",
            )
            .order_by(AgentDecisionRequest.created_at.asc())
        )
        return list(self._session.scalars(stmt))

    def list_pending(self) -> list[AgentDecisionRequest]:
        stmt = (
            select(AgentDecisionRequest)
            .where(AgentDecisionRequest.status == "pending")
            .order_by(AgentDecisionRequest.created_at.asc())
        )
        return list(self._session.scalars(stmt))

    def save_decision(
        self, decision_id: str, *, decision: dict, decided_by: str
    ) -> AgentDecisionRequest | None:
        req = self._session.get(AgentDecisionRequest, decision_id)
        if req is None:
            return None
        req.status = "decided"
        req.decision = decision
        req.decided_by = decided_by
        req.decided_at = utc_now()
        self._session.flush()
        return req
