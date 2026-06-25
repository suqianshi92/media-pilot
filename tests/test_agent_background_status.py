"""BackgroundStatusService 单元测试 — DTO、ring buffer、快照与脱敏.

覆盖 tasks 1.1 ~ 1.4:
- 1.1 DTO 字段覆盖 (BackgroundStatusSnapshot + BackgroundHistoryEntry)
- 1.2 ring buffer 最多 10 条
- 1.3 compute_snapshot 在 disabled / idle / needs_attention / recently_failed /
      processing_task 等场景下分桶正确
- 1.4 历史不含 LLM prompt / 工具原始 JSON / 密钥 / 完整堆栈 — 写入前自动脱敏
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import IngestTask
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
from media_pilot.services.agent_background_status import (
    MAX_HISTORY,
    BackgroundState,
    BackgroundStatusService,
    HistoryLevel,
    _redact_summary,
    reset_default_background_status_service,
    set_default_background_status_service,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """每个测试前后丢弃全局单例, 避免状态泄漏."""

    reset_default_background_status_service()
    yield
    reset_default_background_status_service()


# ---- 1.1 DTO 字段 ----


def test_dto_fields_cover_spec_requirements() -> None:
    """BackgroundStatusSnapshot 必须包含 spec 列出的全部字段."""

    snapshot = BackgroundStatusService().compute_snapshot(
        session_factory=None,
        is_enabled=True,
    )
    for field in (
        "enabled",
        "state",
        "summary",
        "disabled_reasons",
        "waiting_user_count",
        "agent_failed_count",
        "last_run",
        "history",
    ):
        assert hasattr(snapshot, field), f"BackgroundStatusSnapshot 缺少字段: {field}"
    # current_task_id / current_download_id 在 phase 模式下才会填, 默认 None.
    assert snapshot.current_task_id is None
    assert snapshot.current_download_id is None


# ---- 1.2 ring buffer 截断 ----


def test_history_ring_buffer_max_ten_entries() -> None:
    service = BackgroundStatusService()
    for i in range(15):
        service.record_event(
            phase="processing_task",
            level=HistoryLevel.INFO,
            summary=f"event-{i}",
        )
    history = service.history_snapshot
    assert len(history) == MAX_HISTORY == 10
    # 最新事件在尾部
    assert history[-1].summary == "event-14"
    # 最旧的两条已被挤出
    assert "event-0" not in {e.summary for e in history}
    assert "event-1" not in {e.summary for e in history}


# ---- 1.3 compute_snapshot 状态分桶 ----


def _init_session_with_tasks(tmp_path, tasks: list[tuple[str, str]]) -> object:
    """建立 DB 并写入若干 (source_path, status) 任务, 返回 session_factory."""

    from media_pilot.config import AppConfig

    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
    )
    for d in (
        config.downloads_dir, config.watch_dir, config.workspace_dir,
        config.movies_dir, config.shows_dir, config.database_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    sf = create_session_factory(config)
    with sf() as session:
        repo = IngestTaskRepository(session)
        for path, status in tasks:
            repo.create(IngestTaskCreate(
                source_path=path, status=status, current_step="seed",
            ))
        session.commit()
    return sf


def test_snapshot_disabled_when_worker_disabled() -> None:
    service = BackgroundStatusService()
    service.set_disabled_reasons(["LLM 未配置", "工作目录缺失"])
    snapshot = service.compute_snapshot(session_factory=None, is_enabled=False)
    assert snapshot.enabled is False
    assert snapshot.state == BackgroundState.DISABLED
    assert snapshot.disabled_reasons == ["LLM 未配置", "工作目录缺失"]
    assert "LLM 未配置" in snapshot.summary
    assert "工作目录缺失" in snapshot.summary


def test_snapshot_disabled_falls_back_to_default_summary() -> None:
    service = BackgroundStatusService()
    snapshot = service.compute_snapshot(session_factory=None, is_enabled=False)
    assert snapshot.state == BackgroundState.DISABLED
    assert snapshot.summary == "后台 Agent 未启用"


def test_snapshot_idle_when_no_blocking_state(tmp_path) -> None:
    sf = _init_session_with_tasks(tmp_path, [
        ("/media/downloads/a.mkv", "library_import_complete"),
        ("/media/downloads/b.mkv", "agent_failed"),
    ])
    service = BackgroundStatusService()
    # 删除 agent_failed 那条, 保持真正空闲
    with sf() as session:
        for t in session.query(IngestTask).all():
            if t.source_path.endswith("b.mkv"):
                session.delete(t)
        session.commit()
    snapshot = service.compute_snapshot(session_factory=sf, is_enabled=True)
    assert snapshot.state == BackgroundState.IDLE
    assert snapshot.waiting_user_count == 0
    assert snapshot.agent_failed_count == 0


def test_snapshot_needs_attention_when_waiting_user_exists(tmp_path) -> None:
    sf = _init_session_with_tasks(tmp_path, [
        ("/media/watch/a.mkv", "waiting_user"),
        ("/media/watch/b.mkv", "discovered"),
    ])
    service = BackgroundStatusService()
    snapshot = service.compute_snapshot(session_factory=sf, is_enabled=True)
    assert snapshot.state == BackgroundState.NEEDS_ATTENTION
    assert snapshot.waiting_user_count == 1
    assert "1" in snapshot.summary


def test_snapshot_needs_attention_takes_precedence_over_failed(tmp_path) -> None:
    """同时存在 waiting_user 和 agent_failed 时, 优先表达阻塞型需求."""

    sf = _init_session_with_tasks(tmp_path, [
        ("/media/watch/a.mkv", "waiting_user"),
        ("/media/watch/b.mkv", "agent_failed"),
    ])
    service = BackgroundStatusService()
    snapshot = service.compute_snapshot(session_factory=sf, is_enabled=True)
    assert snapshot.state == BackgroundState.NEEDS_ATTENTION
    assert snapshot.waiting_user_count == 1
    assert snapshot.agent_failed_count == 1


def test_snapshot_recently_failed_when_only_failures(tmp_path) -> None:
    sf = _init_session_with_tasks(tmp_path, [
        ("/media/watch/c.mkv", "agent_failed"),
    ])
    service = BackgroundStatusService()
    snapshot = service.compute_snapshot(session_factory=sf, is_enabled=True)
    assert snapshot.state == BackgroundState.RECENTLY_FAILED
    assert snapshot.agent_failed_count == 1
    assert "1" in snapshot.summary


def test_snapshot_processing_task_phase_overrides_blocking(tmp_path) -> None:
    """正在处理任务时, 即便库中存在 waiting_user, 仍优先表达 phase.

    原因: 当前任务运行中时, 阻塞型需求仍由 phase 反映; 用户需要看到的是
    "现在在做什么", 而不是 "那边还有卡住的".
    """

    sf = _init_session_with_tasks(tmp_path, [
        ("/media/watch/a.mkv", "waiting_user"),
    ])
    service = BackgroundStatusService()
    full_id = "abcdef00-1111-2222-3333-444455556666"
    service.begin_phase(
        BackgroundState.PROCESSING_TASK,
        task_id=full_id,
    )
    service.record_event(
        phase="processing_task",
        level=HistoryLevel.INFO,
        summary="正在处理任务",
        task_id=full_id,
    )
    snapshot = service.compute_snapshot(session_factory=sf, is_enabled=True)
    assert snapshot.state == BackgroundState.PROCESSING_TASK
    # current_task_id 必须保留完整 ID, 供前端路由跳转 (/tasks/<id>);
    # 显示用短码只出现在 history 条目里.
    assert snapshot.current_task_id == full_id
    assert snapshot.current_task_id != "abcdef00"
    # history 条目里 task_id 是 8 位短码, 与 current_task_id 用途分离
    assert snapshot.history[-1].task_id == "abcdef00"
    assert snapshot.waiting_user_count == 1


def test_snapshot_db_failure_returns_zero_counts() -> None:
    """session_factory 不可用时, 不抛错, 计数退化为 0."""

    service = BackgroundStatusService()

    class _BrokenFactory:
        def __call__(self):
            raise RuntimeError("db down")

    # 不抛错即可
    snapshot = service.compute_snapshot(session_factory=_BrokenFactory(), is_enabled=True)
    assert snapshot.state == BackgroundState.IDLE
    assert snapshot.waiting_user_count == 0
    assert snapshot.agent_failed_count == 0


# ---- 1.4 脱敏 ----


def test_redact_summary_strips_long_token() -> None:
    raw = "x = abcdef0123456789abcdef0123456789"  # 30 chars A-Za-z0-9
    redacted = _redact_summary(raw)
    assert "[redacted]" in redacted
    assert "abcdef0123456789abcdef0123456789" not in redacted


def test_redact_summary_keeps_normal_text_untouched() -> None:
    raw = "任务 abc 处理失败: LLM 返回空 response"
    assert _redact_summary(raw) == raw


def test_redact_summary_handles_empty_string() -> None:
    assert _redact_summary("") == ""


def test_record_event_redacts_summary() -> None:
    service = BackgroundStatusService()
    secret = "sk-abcdefghijklmnopqrstuvwx"  # 26 chars
    service.record_event(
        phase="processing_task",
        level=HistoryLevel.ERROR,
        summary=f"调用外部 API 失败: {secret}",
    )
    history = service.history_snapshot
    assert len(history) == 1
    assert secret not in history[0].summary
    assert "[redacted]" in history[0].summary


def test_history_entry_shortens_long_ids() -> None:
    service = BackgroundStatusService()
    full_id = "abcdef00-1111-2222-3333-444455556666"
    service.record_event(
        phase="syncing_downloads",
        level=HistoryLevel.INFO,
        summary="下载同步完成",
        download_id=full_id,
        task_id=full_id,
    )
    entry = service.history_snapshot[0]
    assert entry.task_id == "abcdef00"
    assert entry.download_id == "abcdef00"
    assert full_id not in entry.task_id
    assert full_id not in entry.download_id


# ---- lifecycle / 单例管理 ----


def test_clear_phase_records_last_run_and_resets_current() -> None:
    service = BackgroundStatusService()
    service.begin_phase(BackgroundState.PROCESSING_TASK, task_id="t-1")
    assert service._current_phase == BackgroundState.PROCESSING_TASK
    service.clear_phase()
    assert service._current_phase is None
    assert service._current_task_id is None
    assert service._last_run is not None
    assert isinstance(service._last_run, datetime)


def test_singleton_set_and_reset() -> None:
    custom = BackgroundStatusService()
    set_default_background_status_service(custom)
    from media_pilot.services.agent_background_status import (
        get_default_background_status_service,
    )
    assert get_default_background_status_service() is custom
    reset_default_background_status_service()
    assert get_default_background_status_service() is not custom


def test_history_event_has_required_fields() -> None:
    """记录事件时, 字段应齐备: timestamp / phase / level / summary."""

    service = BackgroundStatusService()
    service.record_event(
        phase="scanning_watch",
        level=HistoryLevel.SUCCESS,
        summary="watch 扫描新建 1 个任务",
    )
    entry = service.history_snapshot[0]
    assert entry.phase == "scanning_watch"
    assert entry.level == HistoryLevel.SUCCESS
    assert entry.summary == "watch 扫描新建 1 个任务"
    assert entry.task_id is None
    assert entry.download_id is None
    # 时间戳应接近当前
    delta = (datetime.now(UTC) - entry.timestamp).total_seconds()
    assert 0 <= delta < 5


# ---- 边界: 不应被业务日志淹没 ----


def test_disabled_reasons_are_preserved_through_snapshot() -> None:
    """disabled_reasons 在 disabled 状态下必须原样保留, 不被截断."""

    reasons = [
        "LLM_API_KEY 未设置",
        "MEDIA_PILOT_LLM_BASE_URL 未设置",
        "目录 /data/workspace 不可写",
    ]
    service = BackgroundStatusService()
    service.set_disabled_reasons(reasons)
    snapshot = service.compute_snapshot(session_factory=None, is_enabled=False)
    assert snapshot.disabled_reasons == reasons


# ---- 覆盖率: logger 异常路径 ----


def test_count_tasks_logs_and_returns_zero_on_failure(caplog) -> None:
    """DB 不可用时, _count_tasks 记录异常并返回 0, 不应向调用方抛错."""

    service = BackgroundStatusService()

    class _Boom:
        def __call__(self):
            raise RuntimeError("db gone")

    with caplog.at_level(logging.ERROR, logger="media_pilot.services.agent_background_status"):
        result = service._count_tasks(_Boom(), ("discovered",))
    assert result == 0
    assert any("聚合 task 状态失败" in rec.message for rec in caplog.records)


# ---- 线程安全 ----


def test_concurrent_writers_and_readers_dont_corrupt_history() -> None:
    """后台线程写 history, API 线程读 compute_snapshot 不会抛错或读到
    半改状态; history 长度始终不超过 MAX_HISTORY.

    用 threading.Barrier 同步起跑, 写出 N 倍 MAX_HISTORY 条事件,
    同时若干读线程反复 compute_snapshot. 验证:
    1. 写期间读不抛异常 (无 RuntimeError / 死锁)
    2. history 长度始终 <= MAX_HISTORY (deque 截断 + 锁内一致)
    3. compute_snapshot 返回的 history 长度同样 <= MAX_HISTORY
    """

    import threading

    from media_pilot.services.agent_background_status import (
        BackgroundState,
        HistoryLevel,
        MAX_HISTORY,
    )

    service = BackgroundStatusService()
    barrier = threading.Barrier(parties=8)
    write_count = MAX_HISTORY * 4
    errors: list[BaseException] = []

    def writer(idx: int) -> None:
        try:
            barrier.wait(timeout=2.0)
            for i in range(write_count):
                service.record_event(
                    phase="processing_task",
                    level=HistoryLevel.INFO,
                    summary=f"writer-{idx}-event-{i}",
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def reader(_idx: int) -> None:
        try:
            barrier.wait(timeout=2.0)
            for _ in range(write_count):
                snap = service.compute_snapshot(
                    session_factory=None, is_enabled=True,
                )
                # history 长度始终受 MAX_HISTORY 约束
                assert len(snap.history) <= MAX_HISTORY
                # state / phase 不应是 garbage
                assert isinstance(snap.state, BackgroundState)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(i,)) for i in range(4)
    ] + [
        threading.Thread(target=reader, args=(i,)) for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
        assert not t.is_alive(), f"线程 {t.name} 死锁/未结束"

    assert not errors, f"并发读写抛出异常: {errors!r}"
    # 写完后 history 长度被 deque(maxlen=MAX_HISTORY) 截断
    assert len(service.history_snapshot) == MAX_HISTORY
    final_snap = service.compute_snapshot(session_factory=None, is_enabled=True)
    assert len(final_snap.history) == MAX_HISTORY


def test_concurrent_phase_begin_clear_does_not_mix_state() -> None:
    """并发 begin_phase / clear_phase / record_event 不会留下残缺状态.
    compute_snapshot 拿到的 (state, current_task_id) 必须配对 —
    要么都是非 phase (idle/needs_attention/...), 要么 phase 有 task_id."""

    import threading

    from media_pilot.services.agent_background_status import (
        BackgroundState,
        HistoryLevel,
    )

    service = BackgroundStatusService()
    barrier = threading.Barrier(parties=4)
    errors: list[BaseException] = []

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=2.0)
            for i in range(200):
                tid = f"task-{idx}-{i}"
                service.begin_phase(
                    BackgroundState.PROCESSING_TASK, task_id=tid,
                )
                service.record_event(
                    phase="processing_task",
                    level=HistoryLevel.INFO,
                    summary=f"event {tid}",
                    task_id=tid,
                )
                service.clear_phase()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def reader(_idx: int) -> None:
        try:
            barrier.wait(timeout=2.0)
            for _ in range(400):
                snap = service.compute_snapshot(
                    session_factory=None, is_enabled=True,
                )
                # 若当前是 processing phase, current_task_id 必须非空
                if snap.state == BackgroundState.PROCESSING_TASK:
                    assert snap.current_task_id is not None, (
                        "phase=processing_task 时 current_task_id 不应为空"
                    )
                # 若当前不是 phase, current_task_id 必须为 None
                else:
                    assert snap.current_task_id is None, (
                        f"state={snap.state} 时 current_task_id 应为 None, "
                        f"实际 {snap.current_task_id!r}"
                    )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(i,)) for i in range(3)
    ] + [threading.Thread(target=reader, args=(0,))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
        assert not t.is_alive(), f"线程 {t.name} 死锁/未结束"

    assert not errors, f"并发读写抛出异常: {errors!r}"


def test_history_snapshot_is_a_defensive_copy() -> None:
    """history_snapshot 返回的 list 不得是 _history 的别名, 否则
    外部可变操作会污染内部 ring buffer."""

    service = BackgroundStatusService()
    service.record_event(
        phase="processing_task",
        level=HistoryLevel.INFO,
        summary="a",
    )
    snap = service.history_snapshot
    snap.clear()
    # 内部 ring buffer 仍应有这条事件
    assert len(service.history_snapshot) == 1


def test_set_disabled_reasons_is_isolated_from_internal_list() -> None:
    """set_disabled_reasons 应拷贝入参, 外部修改不影响内部状态."""

    service = BackgroundStatusService()
    reasons = ["LLM 未配置"]
    service.set_disabled_reasons(reasons)
    reasons.append("外部追加")
    snap = service.compute_snapshot(
        session_factory=None, is_enabled=False,
    )
    # 内部状态不应被外部追加污染
    assert snap.disabled_reasons == ["LLM 未配置"]
