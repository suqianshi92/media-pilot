from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.config import AppConfig, validate_startup_config
from media_pilot.orchestration.ingestion import create_ingest_task, scan_downloads
from media_pilot.orchestration.state_machine import IngestTaskStatus
from media_pilot.orchestration.watch_stability import WatchStableDetector
from media_pilot.repository.repositories import IngestTaskRepository

logger = logging.getLogger(__name__)


AUTO_CONFIRM_CONFIDENCE = 0.8


@dataclass(frozen=True)
class ProcessTaskResult:
    status: str


@dataclass(frozen=True)
class WorkerScanResult:
    created_tasks: int


class Worker:
    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config
        # 进程内 watch 路径快照稳定窗口状态机. 跨多次 scan_once 保持状态;
        # 进程重启即清空, 由 BackgroundProcessor / 配置变更等自然重新建立.
        self._watch_stable_detector = WatchStableDetector()

    def is_enabled(self) -> bool:
        if self._config is None:
            return False
        return validate_startup_config(self._config).can_start_worker and self._is_agent_ready()

    def _is_agent_ready(self) -> bool:
        if self._config is None:
            return False
        return (
            self._config.llm_api_key is not None
            and self._config.llm_base_url is not None
            and self._config.llm_model is not None
        )

    def scan_once(self, session_factory: sessionmaker[Session]) -> WorkerScanResult:
        if not self.is_enabled() or self._config is None:
            return WorkerScanResult(created_tasks=0)

        with session_factory() as session:
            from media_pilot.repository.repositories import DownloadTaskRepository
            download_repo = DownloadTaskRepository(session)
            occupied = download_repo.list_occupied_paths()

        # `watch_stable_window_seconds <= 0` 视为关闭稳定窗口: 不传 detector,
        # 首次扫描即创建候选. scan_downloads 内部也做了同样防御.
        watch_window = self._config.watch_stable_window_seconds
        effective_detector = (
            self._watch_stable_detector if watch_window > 0 else None
        )
        scan_result = scan_downloads(
            self._config.watch_dir,
            now=time.time(),
            stable_window_seconds=watch_window,
            occupied_paths=occupied,
            stable_detector=effective_detector,
        )
        created_tasks = 0
        discovered_at = datetime.now(UTC)

        with session_factory() as session:
            repository = IngestTaskRepository(session)
            for candidate in scan_result.candidates:
                if repository.get_by_source_path(str(candidate.path)) is not None:
                    continue
                create_ingest_task(repository, candidate, discovered_at=discovered_at)
                created_tasks += 1
            session.commit()

        return WorkerScanResult(created_tasks=created_tasks)

    def process_task(
        self,
        session_factory: sessionmaker[Session],
        task_id: str,
        *,
        auto_confirm_confidence: float = AUTO_CONFIRM_CONFIDENCE,
    ) -> ProcessTaskResult:
        # `auto_confirm_confidence` is accepted for API stability with existing callers
        # but is no longer consumed by the Agent path. It is retained so that any
        # future hooks (e.g. a small focused service) can keep using the same call shape.
        del auto_confirm_confidence

        with session_factory() as session:
            repository = IngestTaskRepository(session)
            task = repository.get(task_id)
            if task is None:
                return ProcessTaskResult(status="not_found")

            if self._config is None:
                return ProcessTaskResult(status="not_configured")

            if not self._is_agent_ready():
                return ProcessTaskResult(status="not_configured")

            # ── Agent-driven ingest entrypoint ──────────────────────
            # The Worker only routes Agent-processable start states to the
            # Agent orchestration. Any other state is returned as-is below.
            if task.status in {
                IngestTaskStatus.DISCOVERED,
                IngestTaskStatus.CREATED,
                IngestTaskStatus.QUEUED,
            }:
                return self._run_agent_orchestration(session_factory, task)

            # Do not auto-continue these states
            if task.status == IngestTaskStatus.WAITING_USER:
                return ProcessTaskResult(status=task.status)

            if task.status == IngestTaskStatus.AGENT_FAILED:
                return ProcessTaskResult(status=task.status)

            if task.status in {
                IngestTaskStatus.LIBRARY_IMPORT_COMPLETE,
                IngestTaskStatus.COMPLETED,
            }:
                return ProcessTaskResult(status=task.status)

            if task.status == IngestTaskStatus.DELETED:
                return ProcessTaskResult(status=task.status)

            # 任何不在 Agent 推进集合里的状态都原样返回。
            return ProcessTaskResult(status=task.status)

    def _run_agent_orchestration(
        self,
        session_factory: sessionmaker[Session],
        task,
    ) -> ProcessTaskResult:
        """Create or continue an auto-ingest AgentRun for the given task."""
        from media_pilot.repository.repositories import AgentRunRepository

        config = self._config
        if config is None:
            return ProcessTaskResult(status="not_configured")

        # Check for existing active/waiting AgentRun
        with session_factory() as session:
            run_repo = AgentRunRepository(session)
            active_runs = [
                r for r in run_repo.list_by_task(task.id)
                if r.status in ("active", "queued", "waiting_user")
            ]
            if active_runs:
                return ProcessTaskResult(status="agent_running")

        # Launch Agent run in auto_ingest mode. This intentionally uses the
        # ack-only path so Worker does not hold a DB transaction across LLM
        # calls, tool execution, or file publishing.
        try:
            from media_pilot.agent.runner import run_agent_turn_async
            run_agent_turn_async(
                session_factory=session_factory,
                config=config,
                task_id=task.id,
                mode="auto_ingest",
            )
        except OperationalError as exc:
            # DB locked 是瞬时错误, 不应把任务标记为 agent_failed.
            # 任务状态保持 discovered/created/queued, 下一轮 processor 会重试.
            if "locked" in str(exc).lower():
                logger.warning(
                    "Agent run 遇 DB locked, 本轮跳过 task=%s: %s",
                    task.id[:8], exc,
                )
                return ProcessTaskResult(status="db_locked")
            return ProcessTaskResult(status="agent_failed")
        except Exception:
            return ProcessTaskResult(status="agent_failed")

        return ProcessTaskResult(status="agent_started")

    def sync_downloads(
        self, session_factory: sessionmaker[Session]
    ) -> DownloadSyncResult:
        from media_pilot.services.download_sync import (
            DownloadSyncResult,
            DownloadSyncService,
        )

        if not self._is_agent_ready():
            return DownloadSyncResult()
        if self._config is None:
            return DownloadSyncResult()
        service = DownloadSyncService(self._config)
        return service.sync_once(session_factory)
