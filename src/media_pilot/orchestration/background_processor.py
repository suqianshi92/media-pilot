"""后台任务处理器 — 按职责拆分为三段：
1. 下载状态同步 (download sync worker)
2. watch 目录扫描 (watch scanner worker)
3. 入库任务处理 (ingest processor)

LLM 未配置时本处理器在第 0 步直接退出：不进行下载同步、watch 扫描或任务推进。

后台状态埋点: 每个阶段向 BackgroundStatusService 写入可读历史, 用于
Dashboard / API 暴露; 任务 / 下载 ID 只截短到 8 位, summary 文本由调用方
提供, 写入前自动脱敏.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.repository.models import IngestTask
from media_pilot.services.agent_background_status import (
    BackgroundState,
    BackgroundStatusService,
    HistoryLevel,
    get_default_background_status_service,
)
from media_pilot.worker import Worker

logger = logging.getLogger(__name__)


# Agent 主线可自动推进的状态。
_AGENT_PROCESSABLE_STATUSES = ("discovered", "created", "queued")


@dataclass
class BackgroundProcessResult:
    scanned: int = 0
    created: int = 0
    pending: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class BackgroundProcessor:
    """后台单轮处理器，可被后台线程或测试直接调用"""

    def __init__(
        self,
        worker: Worker,
        *,
        status_service: BackgroundStatusService | None = None,
    ) -> None:
        self._worker = worker
        # 允许测试注入, 普通运行取进程内单例.
        self._status: BackgroundStatusService = (
            status_service or get_default_background_status_service()
        )

    def run_once(self, session_factory: sessionmaker[Session]) -> BackgroundProcessResult:
        result = BackgroundProcessResult()

        # ── 0. LLM / Agent 前提检查 ──
        # Agent 主线要求 LLM 已配置；未配置时本轮全部跳过，不进行下载同步、
        # watch 扫描或任务推进。下一轮扫描会重新尝试，无需手动触发。
        if not self._worker.is_enabled():
            logger.info("Worker 未启用 (LLM 未配置或目录/配置缺失), 跳过本轮处理")
            return result

        # ── 1. 下载状态同步 (download sync worker) ──
        sync_result = None
        try:
            self._status.begin_phase(BackgroundState.SYNCING_DOWNLOADS)
            sync_result = self._worker.sync_downloads(session_factory)
            if sync_result.synced > 0 or sync_result.failed > 0:
                logger.info(
                    "下载同步: synced=%d failed=%d skipped=%d ingested=%d",
                    sync_result.synced, sync_result.failed,
                    sync_result.skipped, sync_result.ingested,
                )
            if sync_result.ingested > 0:
                self._status.record_event(
                    phase="syncing_downloads",
                    level=HistoryLevel.SUCCESS,
                    summary=(
                        f"下载同步: 完成 {sync_result.synced} 个, "
                        f"失败 {sync_result.failed} 个, "
                        f"新建 {sync_result.ingested} 个入库任务 (来源: 系统内下载)"
                    ),
                )
            elif sync_result.failed > 0:
                self._status.record_event(
                    phase="syncing_downloads",
                    level=HistoryLevel.WARNING,
                    summary=(
                        f"下载同步: 完成 {sync_result.synced} 个, "
                        f"失败 {sync_result.failed} 个"
                    ),
                )
        except Exception:
            self._status.record_event(
                phase="syncing_downloads",
                level=HistoryLevel.ERROR,
                summary="下载同步异常, 本轮跳过",
            )
            logger.exception("下载状态同步失败")
        finally:
            self._status.clear_phase()

        # ── 2. watch 目录扫描 (watch scanner worker) ──
        try:
            self._status.begin_phase(BackgroundState.SCANNING_WATCH)
            scan_result = self._worker.scan_once(session_factory)
            result.scanned = scan_result.created_tasks
            if scan_result.created_tasks > 0:
                logger.info("外部导入扫描发现 %d 个新任务", scan_result.created_tasks)
                self._status.record_event(
                    phase="scanning_watch",
                    level=HistoryLevel.SUCCESS,
                    summary=(
                        f"watch 扫描: 新建 {scan_result.created_tasks} "
                        f"个入库任务 (来源: 外部导入)"
                    ),
                )
        except Exception:
            self._status.record_event(
                phase="scanning_watch",
                level=HistoryLevel.ERROR,
                summary="watch 扫描异常, 本轮跳过",
            )
            logger.exception("外部导入扫描失败")
            result.errors.append("scan_failed")
            return result
        finally:
            self._status.clear_phase()

        # ── 3. 入库任务处理 (ingest processor) ──
        with session_factory() as session:
            pending = list(
                session.scalars(
                    select(IngestTask).where(
                        IngestTask.status.in_(_AGENT_PROCESSABLE_STATUSES)
                    )
                )
            )

        result.pending = len(pending)
        if pending:
            logger.info(
                "拾取 %d 个待处理任务 (discovered/created/queued)", len(pending)
            )
        else:
            return result

        for task in pending:
            try:
                self._status.begin_phase(
                    BackgroundState.PROCESSING_TASK, task_id=task.id,
                )
                process_result = self._worker.process_task(session_factory, task.id)
                # 仅当 Worker 显式推进到 Agent 完成态时计入 succeeded。
                if process_result.status in {
                    "library_import_complete",
                    "agent_completed",
                }:
                    result.succeeded += 1
                    self._status.record_event(
                        phase="processing_task",
                        level=HistoryLevel.SUCCESS,
                        summary=(
                            f"任务处理完成: {process_result.status}"
                        ),
                        task_id=task.id,
                    )
                elif process_result.status == "not_configured":
                    result.skipped += 1
                    self._status.record_event(
                        phase="processing_task",
                        level=HistoryLevel.WARNING,
                        summary="任务处理跳过: 配置缺失",
                        task_id=task.id,
                    )
                elif process_result.status == "db_locked":
                    # DB locked 是瞬时锁竞争, 任务保持原状, 下一轮 processor
                    # 自然会重试. 记录为 warning, 不计入 failed 也不打断整轮.
                    result.skipped += 1
                    self._status.record_event(
                        phase="processing_task",
                        level=HistoryLevel.WARNING,
                        summary="任务处理跳过: DB 暂时被占用, 下一轮重试",
                        task_id=task.id,
                    )
                elif process_result.status == "waiting_user":
                    # waiting_user 是阻塞态, 但应让其他任务继续; 记录为 info.
                    self._status.record_event(
                        phase="processing_task",
                        level=HistoryLevel.INFO,
                        summary="任务进入等待用户处理",
                        task_id=task.id,
                    )
                    result.skipped += 1
                elif process_result.status == "agent_failed":
                    self._status.record_event(
                        phase="processing_task",
                        level=HistoryLevel.ERROR,
                        summary="Agent 处理失败",
                        task_id=task.id,
                    )
                    result.skipped += 1
                else:
                    self._status.record_event(
                        phase="processing_task",
                        level=HistoryLevel.INFO,
                        summary=(
                            f"任务处理结果: {process_result.status}"
                        ),
                        task_id=task.id,
                    )
                    result.skipped += 1
                logger.info(
                    "任务 %s 处理完成 -> %s", task.id, process_result.status
                )
            except Exception:
                result.failed += 1
                self._status.record_event(
                    phase="processing_task",
                    level=HistoryLevel.ERROR,
                    summary="任务处理异常",
                    task_id=task.id,
                )
                logger.exception("任务 %s 处理异常", task.id)
                result.errors.append(f"task_{task.id[:8]}")
            finally:
                self._status.clear_phase()

        return result
