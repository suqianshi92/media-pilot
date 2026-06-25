"""系统级后台 Agent 状态 — 内存 ring buffer + 数据库聚合快照.

设计原则 (D1 / D2 / D3):
- 历史只保留进程内最近 10 条, 服务重启后丢失可接受, 因为这是运行
  诊断, 不是业务审计. 任务事实、AgentRun、AgentMessage 仍由业务表持久化.
- 状态 API 只读. 不暴露 LLM prompt、工具调用原始 JSON、密钥、下载器
  凭据或完整异常堆栈. 工具调用详情仍走任务工作台 Agent 面板.
- 状态分桶: disabled / idle / syncing_downloads / scanning_watch /
  processing_task / needs_attention / recently_failed.
  不直接复用单个 AgentRun.status (那是任务级, 不是系统级).
- ID 边界: snapshot.current_task_id / current_download_id 必须保留完整
  ID, 因为前端需要用它做路由跳转 (/tasks/<id>); 历史事件
  BackgroundHistoryEntry.task_id / download_id 仅用于显示, 写入前截短
  到 8 位, 防止长 ID 噪音淹没摘要.
- 线程安全: 后台处理循环线程和 API 请求线程共享同一单例, 所有读写
  通过 self._lock (RLock) 保护; compute_snapshot 一次性在锁内取一致
  快照, 避免读到半改的 phase/history 组合.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Iterable, Sequence

from sqlalchemy.orm import Session, sessionmaker

from media_pilot.orchestration.state_machine import IngestTaskStatus

logger = logging.getLogger(__name__)

# 历史 ring buffer 上限.
MAX_HISTORY = 10


class BackgroundState(StrEnum):
    """后台 Agent 系统级状态."""

    DISABLED = "disabled"
    IDLE = "idle"
    SYNCING_DOWNLOADS = "syncing_downloads"
    SCANNING_WATCH = "scanning_watch"
    PROCESSING_TASK = "processing_task"
    NEEDS_ATTENTION = "needs_attention"
    RECENTLY_FAILED = "recently_failed"


class HistoryLevel(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, kw_only=True)
class BackgroundHistoryEntry:
    """单条历史事件 — 进程内 ring buffer 元素.

    字段全部为可读摘要. 不得携带 LLM prompt、工具调用原始 JSON、密钥、
    完整异常堆栈或下载器凭据; 调用方负责在写入前完成脱敏.
    task_id / download_id 只用于展示, 写入时已截短到 8 位.
    """

    timestamp: datetime
    phase: str  # e.g. "syncing_downloads" / "scanning_watch" / "processing_task"
    level: HistoryLevel
    summary: str
    task_id: str | None = None
    download_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class BackgroundStatusSnapshot:
    """API 响应的状态快照 (dto).

    字段严格按 spec 暴露, 不重复 AgentRun 状态, 不暴露敏感细节.
    current_task_id / current_download_id 保留完整 ID, 供前端路由跳转;
    历史事件中的短码在 BackgroundHistoryEntry.task_id / download_id 里.
    """

    enabled: bool
    state: BackgroundState
    summary: str
    disabled_reasons: list[str]
    waiting_user_count: int
    agent_failed_count: int
    last_run: datetime | None
    history: list[BackgroundHistoryEntry]
    current_task_id: str | None = None
    current_download_id: str | None = None


def _short_id(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return value
    return value[:8]


# 摘要文本里若含 API key / 路径凭据的常见形态, 强制截短.
# 故意保守: 仅过滤明显像密钥 / 长 hex / 长 base64 的连续 token,
# 不会破坏正常中文摘要.
_SENSITIVE_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")


def _redact_summary(text: str) -> str:
    """对摘要做最保守的脱敏, 避免误把 API key / 凭据原样写入历史."""

    if not text:
        return text
    return _SENSITIVE_TOKEN.sub("[redacted]", text)


class BackgroundStatusService:
    """进程内单例 — 持有 ring buffer 并按需基于数据库现算快照.

    所有可变状态读写都通过 self._lock 保护. 后台处理循环线程写
    _history / _current_phase, API 请求线程读 compute_snapshot(),
    共享同一把锁; 用 RLock 是为了允许 record_event / set_disabled_reasons
    之类的复合操作内部多次加锁, 不至于死锁.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._history: deque[BackgroundHistoryEntry] = deque(maxlen=MAX_HISTORY)
        # 正在进行的阶段, 用于 compute_snapshot 推断 current_task_id 等.
        self._current_phase: BackgroundState | None = None
        self._current_task_id: str | None = None
        self._current_download_id: str | None = None
        self._last_run: datetime | None = None
        self._disabled_reasons: list[str] = []

    def set_disabled_reasons(self, reasons: Sequence[str]) -> None:
        """由启动配置 (LLM 未配置 / 目录缺失) 写入, 仅在 disabled 时使用."""

        with self._lock:
            self._disabled_reasons = list(reasons)

    def begin_phase(
        self,
        phase: BackgroundState,
        *,
        task_id: str | None = None,
        download_id: str | None = None,
    ) -> None:
        """标记当前正在执行的阶段; 用于后续快照的 state / current_* 字段.

        task_id / download_id 保留完整值, 路由跳转依赖完整 ID.
        """

        with self._lock:
            self._current_phase = phase
            self._current_task_id = task_id
            self._current_download_id = download_id

    def clear_phase(self) -> None:
        """阶段结束, 但保留 history 与 last_run; compute_snapshot 据此回退
        到 idle / needs_attention / recently_failed."""

        with self._lock:
            self._current_phase = None
            self._current_task_id = None
            self._current_download_id = None
            self._last_run = datetime.now(UTC)

    def record_event(
        self,
        *,
        phase: str,
        level: HistoryLevel,
        summary: str,
        task_id: str | None = None,
        download_id: str | None = None,
    ) -> None:
        """追加一条历史事件. summary 在写入前自动脱敏.
        history 内的 task_id / download_id 是显示用短码, 与 snapshot 中
        保留完整 ID 的 current_task_id / current_download_id 用途不同."""

        entry = BackgroundHistoryEntry(
            timestamp=datetime.now(UTC),
            phase=phase,
            level=level,
            summary=_redact_summary(summary),
            task_id=_short_id(task_id),
            download_id=_short_id(download_id),
        )
        with self._lock:
            self._history.append(entry)

    @property
    def history_snapshot(self) -> list[BackgroundHistoryEntry]:
        """返回历史副本, 按时间顺序 (最新在尾部). 锁内一致快照."""

        with self._lock:
            return list(self._history)

    def _count_tasks(
        self,
        session_factory: sessionmaker[Session],
        statuses: Iterable[str],
    ) -> int:
        """按 task status 列表现算入库任务数量; 失败容错返回 0."""

        statuses_tuple = tuple(statuses)
        if not statuses_tuple:
            return 0
        try:
            with session_factory() as session:
                from sqlalchemy import func, select

                from media_pilot.repository.models import IngestTask

                stmt = select(func.count()).select_from(IngestTask).where(
                    IngestTask.status.in_(statuses_tuple),
                )
                return int(session.execute(stmt).scalar() or 0)
        except Exception:
            logger.exception("聚合 task 状态失败, 返回 0")
            return 0

    def compute_snapshot(
        self,
        *,
        session_factory: sessionmaker[Session] | None,
        is_enabled: bool,
    ) -> BackgroundStatusSnapshot:
        """组装当前快照. 用于 API 响应.

        锁内一次性读取 current_phase / current_task_id / current_download_id
        / last_run / disabled_reasons / history, 避免读出半改状态.
        数据库聚合 (waiting_user_count / agent_failed_count) 不属于进程内
        状态, 锁外读, 但因为是只读 SQL, 短暂的不一致不影响正确性.

        优先级: 当前阶段 > 阻塞型聚合 > 失败聚合 > idle.
        """

        # 锁内取一致快照
        with self._lock:
            snapshot_phase = self._current_phase
            snapshot_task_id = self._current_task_id
            snapshot_download_id = self._current_download_id
            snapshot_last_run = self._last_run
            snapshot_disabled_reasons = list(self._disabled_reasons)
            snapshot_history = list(self._history)

        # 锁外做 DB 聚合 (不持有锁, 避免长 SQL 阻塞后台写)
        waiting_user_count = 0
        agent_failed_count = 0
        if session_factory is not None:
            waiting_user_count = self._count_tasks(
                session_factory, (IngestTaskStatus.WAITING_USER,),
            )
            agent_failed_count = self._count_tasks(
                session_factory, (IngestTaskStatus.AGENT_FAILED,),
            )

        if not is_enabled:
            return BackgroundStatusSnapshot(
                enabled=False,
                state=BackgroundState.DISABLED,
                summary=self._format_disabled_summary(snapshot_disabled_reasons),
                disabled_reasons=snapshot_disabled_reasons,
                waiting_user_count=waiting_user_count,
                agent_failed_count=agent_failed_count,
                last_run=snapshot_last_run,
                history=snapshot_history,
            )

        # 阶段进行中 — 优先返回该阶段. current_task_id / current_download_id
        # 保留完整 ID, 供前端路由跳转.
        if snapshot_phase in {
            BackgroundState.SYNCING_DOWNLOADS,
            BackgroundState.SCANNING_WATCH,
            BackgroundState.PROCESSING_TASK,
        }:
            return BackgroundStatusSnapshot(
                enabled=True,
                state=snapshot_phase,
                summary=self._format_phase_summary(snapshot_phase),
                disabled_reasons=[],
                waiting_user_count=waiting_user_count,
                agent_failed_count=agent_failed_count,
                last_run=snapshot_last_run,
                history=snapshot_history,
                current_task_id=snapshot_task_id,
                current_download_id=snapshot_download_id,
            )

        # 空闲时, 优先表达阻塞 (waiting_user) > 失败.
        if waiting_user_count > 0:
            return BackgroundStatusSnapshot(
                enabled=True,
                state=BackgroundState.NEEDS_ATTENTION,
                summary=(
                    f"有 {waiting_user_count} 个入库任务等待用户处理"
                ),
                disabled_reasons=[],
                waiting_user_count=waiting_user_count,
                agent_failed_count=agent_failed_count,
                last_run=snapshot_last_run,
                history=snapshot_history,
            )

        if agent_failed_count > 0:
            return BackgroundStatusSnapshot(
                enabled=True,
                state=BackgroundState.RECENTLY_FAILED,
                summary=(
                    f"有 {agent_failed_count} 个入库任务处于失败态"
                ),
                disabled_reasons=[],
                waiting_user_count=waiting_user_count,
                agent_failed_count=agent_failed_count,
                last_run=snapshot_last_run,
                history=snapshot_history,
            )

        return BackgroundStatusSnapshot(
            enabled=True,
            state=BackgroundState.IDLE,
            summary="后台 Agent 空闲, 等待下载完成或 watch 导入",
            disabled_reasons=[],
            waiting_user_count=waiting_user_count,
            agent_failed_count=agent_failed_count,
            last_run=snapshot_last_run,
            history=snapshot_history,
        )

    @staticmethod
    def _format_disabled_summary(reasons: Sequence[str]) -> str:
        if not reasons:
            return "后台 Agent 未启用"
        return "后台 Agent 未启用: " + "; ".join(reasons)

    @staticmethod
    def _format_phase_summary(phase: BackgroundState) -> str:
        return {
            BackgroundState.SYNCING_DOWNLOADS: "正在同步系统内下载",
            BackgroundState.SCANNING_WATCH: "正在扫描 watch 目录",
            BackgroundState.PROCESSING_TASK: "正在处理入库任务",
        }.get(phase, "后台 Agent 处理中")


# 全局单例 — app 启动时由 lifespan 初始化, 测试可通过 reset 重建.
_default_service: BackgroundStatusService | None = None
_default_lock = threading.Lock()


def get_default_background_status_service() -> BackgroundStatusService:
    """返回进程内单例; 未初始化时构造一个空实例 (主要用于测试)."""

    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = BackgroundStatusService()
        return _default_service


def set_default_background_status_service(
    service: BackgroundStatusService | None,
) -> None:
    """注入单例; 传 None 可重置 (用于测试隔离)."""

    global _default_service
    with _default_lock:
        _default_service = service


def reset_default_background_status_service() -> None:
    """测试 helper: 丢弃单例."""

    set_default_background_status_service(None)
