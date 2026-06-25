"""后台处理器回归测试 — Agent 主线 only"""

from pathlib import Path

from sqlalchemy import select

from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import IngestTask
from media_pilot.services.agent_background_status import (
    BackgroundState,
    BackgroundStatusService,
    HistoryLevel,
)
from media_pilot.worker import Worker


def make_config(root: Path) -> AppConfig:
    config = AppConfig(
        downloads_dir=root / "downloads",
        watch_dir=root / "watch",
        workspace_dir=root / "workspace",
        movies_dir=root / "library" / "movies",
        shows_dir=root / "library" / "shows",
        database_dir=root / "db",
        tmdb_api_key="test-key",
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        qbittorrent_url="http://qbittorrent.test",
        # 关闭 watch 稳定窗口, 让 background_processor 测试专注于
        # processor 自身的状态机 / 处理循环, 而不是 watch 稳定检测.
        watch_stable_window_seconds=0,
    )
    for directory in (
        config.downloads_dir,
        config.watch_dir,
        config.workspace_dir,
        config.movies_dir,
        config.shows_dir,
        config.database_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return config


def test_background_processor_run_once_creates_and_processes_task(
    tmp_path: Path, monkeypatch,
) -> None:
    """scan_once 创建一个 discovered 任务, run_once 通过 Agent mock 处理。"""
    import media_pilot.agent.runner as runner_mod
    from media_pilot.agent.runner import AgentRunResult

    config = make_config(tmp_path)
    source_path = config.watch_dir / "Example.Movie.2026.1080p.mkv"
    source_path.write_bytes(b"movie")
    initialize_database(config)
    session_factory = create_session_factory(config)

    def mock_run_agent_turn(*, session, config, task_id, mode="default",
                            mock_llm_client=None, initial_message=None):
        return AgentRunResult(
            run_id="mock-run", status="completed",
            message_count=1, tool_call_count=0,
        )

    monkeypatch.setattr(runner_mod, "run_agent_turn", mock_run_agent_turn)

    from media_pilot.orchestration.background_processor import BackgroundProcessor

    worker = Worker(config)
    processor = BackgroundProcessor(worker)
    # make_config 默认 watch_stable_window_seconds=0 (稳定窗口关闭), 本轮
    # run_once 应同时完成 watch 扫描建任务与 auto_ingest Agent 处理.
    result = processor.run_once(session_factory)

    # 扫描创建了 1 个新任务, 且本轮已由 Agent 处理完成
    assert result.scanned > 0
    assert result.pending >= 0
    assert result.failed == 0
    assert result.errors == []
    # 任务应已完成处理
    assert result.succeeded > 0


def test_background_processor_skips_when_worker_disabled(tmp_path: Path) -> None:
    """无 config 的 Worker 是 disabled 状态 — 本轮直接返回空结果。"""
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    from media_pilot.orchestration.background_processor import BackgroundProcessor

    # 无 config 的 Worker 是 disabled 状态
    processor = BackgroundProcessor(Worker())
    result = processor.run_once(session_factory)

    assert result.scanned == 0
    assert result.created == 0
    assert result.pending == 0
    assert result.succeeded == 0
    assert result.failed == 0
    # disabled 时不应抛错, 也不应做下载同步 / 扫描
    assert result.errors == []


def test_background_processor_skips_when_llm_missing(tmp_path: Path) -> None:
    """LLM 未配置时 BackgroundProcessor 整个 run_once 直接返回空,
    不进行下载同步、watch 扫描或任务推进。"""
    from media_pilot.config import AppConfig as Cfg

    config = Cfg(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    for d in (config.downloads_dir, config.watch_dir, config.workspace_dir,
              config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    # 显式留一个外部文件, 不应被扫描到
    (config.watch_dir / "Should.Not.Be.Scanned.mkv").write_bytes(b"movie")
    initialize_database(config)
    session_factory = create_session_factory(config)

    from media_pilot.orchestration.background_processor import BackgroundProcessor

    processor = BackgroundProcessor(Worker(config))
    result = processor.run_once(session_factory)

    assert result.scanned == 0
    assert result.pending == 0
    assert result.failed == 0
    # 关键: 没有创建任何任务
    with session_factory() as session:
        tasks = session.scalars(select(IngestTask)).all()
    assert tasks == []


# ── 双入口集成回归 — managed download + watch ──


def _stub_agent_completed(monkeypatch):
    """把 run_agent_turn 替成返回 completed 的桩."""
    import media_pilot.agent.runner as runner_mod
    from media_pilot.agent.runner import AgentRunResult

    def mock_run_agent_turn(*, session, config, task_id, mode="default",
                            mock_llm_client=None, initial_message=None):
        return AgentRunResult(
            run_id="mock-run", status="completed",
            message_count=1, tool_call_count=0,
        )

    monkeypatch.setattr(runner_mod, "run_agent_turn", mock_run_agent_turn)


def test_watch_import_creates_ingest_and_runs_agent(
    tmp_path: Path, monkeypatch,
) -> None:
    """watch 外部导入 → 入库任务 → auto_ingest Agent 自动启动并完成."""

    _stub_agent_completed(monkeypatch)
    config = make_config(tmp_path)
    (config.watch_dir / "External.Movie.2026.mkv").write_bytes(b"movie")
    initialize_database(config)
    session_factory = create_session_factory(config)

    from media_pilot.orchestration.background_processor import BackgroundProcessor

    status = BackgroundStatusService()
    processor = BackgroundProcessor(Worker(config), status_service=status)
    # make_config 默认 watch_stable_window_seconds=0 (稳定窗口关闭), 本轮
    # run_once 应同时完成 watch 扫描建任务与 auto_ingest Agent 处理.
    result = processor.run_once(session_factory)

    assert result.scanned == 1
    assert result.succeeded == 1
    # 历史记录应包含 watch 扫描新建任务 + 处理完成两条
    phases = {e.phase for e in status.history_snapshot}
    assert "scanning_watch" in phases
    assert "processing_task" in phases
    # 至少一条带"外部导入"字样
    assert any("外部导入" in e.summary for e in status.history_snapshot)


def test_managed_download_creates_ingest_and_runs_agent(
    tmp_path: Path, monkeypatch,
) -> None:
    """managed download 完成 → 入库任务 → auto_ingest Agent 自动启动并完成."""

    _stub_agent_completed(monkeypatch)
    config = make_config(tmp_path)
    # 让 watch 目录保持为空, 避免扫描路径额外创建任务.
    initialize_database(config)
    session_factory = create_session_factory(config)

    # 预置一个已 downloading 的下载任务 + 真实文件 + 桩 qBittorrent 返回 completed.
    from media_pilot.repository.models import DownloadTask
    from media_pilot.repository.repositories import DownloadTaskCreate, DownloadTaskRepository
    from media_pilot.resource_discovery.types import QBTorrentInfo
    import media_pilot.services.download_sync as ds_module

    class _StubAdapter:
        def get_torrent_info(self, hashes):
            return [QBTorrentInfo(
                hash="managed-hash-001",
                name="Managed.Movie.2026.mkv",
                save_path=str(config.downloads_dir),
                content_path=str(config.downloads_dir / "Managed.Movie.2026.mkv"),
                progress=1.0,
                dlspeed=0,
                state="uploading",
            )]

    monkeypatch.setattr(ds_module, "QBittorrentAdapter", lambda cfg: _StubAdapter())

    movie_file = config.downloads_dir / "Managed.Movie.2026.mkv"
    movie_file.write_bytes(b"movie")
    with session_factory() as session:
        repo = DownloadTaskRepository(session)
        repo.create(DownloadTaskCreate(
            qb_hash="managed-hash-001",
            title="Managed.Movie.2026.mkv",
            source="qbittorrent",
            save_path=str(config.downloads_dir),
        ))
        session.commit()

    from media_pilot.orchestration.background_processor import BackgroundProcessor

    status = BackgroundStatusService()
    processor = BackgroundProcessor(Worker(config), status_service=status)
    result = processor.run_once(session_factory)

    # managed download 路径上: scan_once 不创建新任务; 但 sync_downloads 转入库
    assert result.scanned == 0
    # 下载同步新建了 1 个入库任务, 后续被 auto_ingest 处理完成
    assert result.succeeded == 1
    # 历史应包含下载同步的"系统内下载"摘要
    sync_events = [
        e for e in status.history_snapshot if e.phase == "syncing_downloads"
    ]
    assert sync_events, "应有 syncing_downloads 阶段事件"
    assert any("系统内下载" in e.summary for e in sync_events)

    # DB 中应存在从 managed download 转过来的入库任务
    with session_factory() as session:
        ingest_tasks = session.scalars(select(IngestTask)).all()
    assert len(ingest_tasks) == 1
    assert ingest_tasks[0].source_download_task_id is not None
    # 注: mocked run_agent_turn 不修改 IngestTask.status, 这里只校验
    # "managed download → 入库任务" 这条物理链路是否打通. 实际 IngestTask
    # 状态推进由真实 Agent 路径完成, 已在 test_auto_ingest_services 覆盖.


def test_waiting_user_does_not_block_other_tasks(
    tmp_path: Path, monkeypatch,
) -> None:
    """一个任务 waiting_user 时, 后台处理器仍应拾取并处理其他任务."""

    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    # 预置一个 waiting_user 任务 + 一个 discovered 任务
    from media_pilot.repository.models import IngestTask
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

    with session_factory() as session:
        repo = IngestTaskRepository(session)
        waiting = repo.create(IngestTaskCreate(
            source_path="/media/downloads/waiting.mkv",
            status="waiting_user", current_step="manual_selection_blocked",
        ))
        pending = repo.create(IngestTaskCreate(
            source_path="/media/downloads/pending.mkv",
            status="discovered", current_step="download_scan",
        ))
        session.commit()

    # Agent mock: 第一个调用返回 waiting_user(对应 waiting), 第二个返回 completed.
    import media_pilot.agent.runner as runner_mod
    from media_pilot.agent.runner import AgentRunResult

    call_log: list[str] = []

    def mock_run_agent_turn(*, session, config, task_id, mode="default",
                            mock_llm_client=None, initial_message=None):
        call_log.append(task_id)
        if task_id == waiting.id:
            return AgentRunResult(
                run_id="run-waiting", status="waiting_user",
                message_count=1, tool_call_count=0,
            )
        return AgentRunResult(
            run_id="run-completed", status="completed",
            message_count=1, tool_call_count=0,
        )

    monkeypatch.setattr(runner_mod, "run_agent_turn", mock_run_agent_turn)

    from media_pilot.orchestration.background_processor import BackgroundProcessor

    status = BackgroundStatusService()
    processor = BackgroundProcessor(Worker(config), status_service=status)
    result = processor.run_once(session_factory)

    # waiting_user 不在 _AGENT_PROCESSABLE_STATUSES 中, 不应被拾取
    assert waiting.id not in call_log
    # discovered 任务被处理
    assert pending.id in call_log
    # 状态服务在空闲时聚合应反映 waiting_user 存在
    snapshot = status.compute_snapshot(session_factory=session_factory, is_enabled=True)
    assert snapshot.state == BackgroundState.NEEDS_ATTENTION
    assert snapshot.waiting_user_count == 1


def test_agent_failed_count_aggregates_in_status(
    tmp_path: Path, monkeypatch,
) -> None:
    """agent_failed 任务在后台空闲时聚合, 用于首页展示最近失败."""

    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

    with session_factory() as session:
        repo = IngestTaskRepository(session)
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/failed1.mkv",
            status="agent_failed", current_step="agent_failure",
        ))
        session.commit()

    # 不让 run_once 拉起新任务, 直接用 status 服务读聚合.
    from media_pilot.services.agent_background_status import BackgroundStatusService

    status = BackgroundStatusService()
    snapshot = status.compute_snapshot(session_factory=session_factory, is_enabled=True)
    assert snapshot.state == BackgroundState.RECENTLY_FAILED
    assert snapshot.agent_failed_count == 1
    # 历史可能为空 (本轮未跑), 但 state/计数 仍聚合
    assert "1" in snapshot.summary


def test_disabled_worker_does_not_emit_history(
    tmp_path: Path,
) -> None:
    """worker 禁用时, run_once 不应留下任何历史阶段事件."""

    from dataclasses import replace

    config = make_config(tmp_path)
    config = replace(config, llm_api_key=None, llm_base_url=None, llm_model=None)
    initialize_database(config)
    session_factory = create_session_factory(config)

    from media_pilot.orchestration.background_processor import BackgroundProcessor
    from media_pilot.services.agent_background_status import BackgroundStatusService

    status = BackgroundStatusService()
    processor = BackgroundProcessor(Worker(config), status_service=status)
    result = processor.run_once(session_factory)

    assert result.succeeded == 0
    assert result.scanned == 0
    # disabled 时不进入任何 phase, 不会留下阶段事件
    assert status.history_snapshot == []


def test_processing_phase_resets_between_tasks(tmp_path: Path, monkeypatch) -> None:
    """phase 在每个任务结束时应清空, 不残留上一个任务的 current_task_id."""

    _stub_agent_completed(monkeypatch)
    config = make_config(tmp_path)
    (config.watch_dir / "A.mkv").write_bytes(b"a")
    (config.watch_dir / "B.mkv").write_bytes(b"b")
    initialize_database(config)
    session_factory = create_session_factory(config)

    from media_pilot.orchestration.background_processor import BackgroundProcessor

    status = BackgroundStatusService()
    processor = BackgroundProcessor(Worker(config), status_service=status)
    # make_config 默认 watch_stable_window_seconds=0 (稳定窗口关闭), 本轮
    # run_once 应同时完成 watch 扫描建任务与 auto_ingest Agent 处理.
    processor.run_once(session_factory)

    # run_once 结束后 phase 已清空
    assert status._current_phase is None
    assert status._current_task_id is None
    # 至少有 1 条 processing_task 阶段历史事件
    phases = {e.phase for e in status.history_snapshot}
    assert "processing_task" in phases


def test_scan_exception_does_not_poison_subsequent_phases(
    tmp_path: Path, monkeypatch,
) -> None:
    """watch 扫描阶段异常时, 不应影响后续 phase 状态机清理."""

    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    import media_pilot.worker as worker_module
    from media_pilot.worker import Worker, WorkerScanResult

    def boom_scan(*args, **kwargs):
        raise RuntimeError("watch 扫描失败")

    monkeypatch.setattr(Worker, "scan_once", boom_scan)

    from media_pilot.orchestration.background_processor import BackgroundProcessor
    from media_pilot.services.agent_background_status import BackgroundStatusService

    status = BackgroundStatusService()
    processor = BackgroundProcessor(Worker(config), status_service=status)
    result = processor.run_once(session_factory)

    assert "scan_failed" in result.errors
    # 失败应被记录
    error_events = [
        e for e in status.history_snapshot
        if e.level == HistoryLevel.ERROR
    ]
    assert any(e.phase == "scanning_watch" for e in error_events)
    # phase 状态机已清理
    assert status._current_phase is None


# 注: 旧 `confirmed` / `needs_confirmation` 行为测试（拒绝/原样返回）已删除；
# 本分支允许 DB 清库, 不再为 legacy 状态保留业务行为契约。
# 旧状态写入约束由 test_normalize_agent_task_status_model 中 source-audit 覆盖。
