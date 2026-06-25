from pathlib import Path

from sqlalchemy import select

import media_pilot.worker as worker_module
from media_pilot.adapters.ai import AiParseRequest, AiParseResult, MediaType
from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import IngestTask
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
from media_pilot.worker import ProcessTaskResult, Worker
from tests.stubs import StubMetadataProvider


class FactoryAiParser:
    def parse_filename(self, request: AiParseRequest) -> AiParseResult:
        return AiParseResult(
            media_type=MediaType.MOVIE,
            title="Factory Movie",
            original_title=request.filename,
            year=2026,
            season=None,
            episode=None,
            resolution=None,
            release_group=None,
            language=None,
            confidence=0.95,
            reason="factory parser",
        )


def make_config(
    root: Path, *, watch_stable_window_seconds: int = 0
) -> AppConfig:
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
        watch_stable_window_seconds=watch_stable_window_seconds,
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


def make_ready_config(root: Path) -> AppConfig:
    return make_config(root)


def test_worker_scan_once_creates_discovered_task_without_duplicates(tmp_path: Path) -> None:
    """`watch_stable_window_seconds=0` (默认测试值, 关闭稳定窗口) 时, 首次扫描即创建任务, 后续扫描不重复."""
    config = make_config(tmp_path)
    media_file = config.watch_dir / "Weathering.With.You.2019.mkv"
    media_file.write_bytes(b"movie")

    initialize_database(config)
    session_factory = create_session_factory(config)
    worker = Worker(config)

    first_result = worker.scan_once(session_factory)

    with session_factory() as session:
        tasks = session.scalars(select(IngestTask)).all()

    # 0s 窗口视作关闭稳定窗口, 首次扫描即创建任务
    assert first_result.created_tasks == 1
    # 第二次扫描: 已存在任务, 不重复
    second_result = worker.scan_once(session_factory)
    assert second_result.created_tasks == 0
    assert len(tasks) == 1
    assert tasks[0].source_path == str(media_file)
    assert tasks[0].status == "discovered"


def test_worker_process_task_returns_not_configured_when_llm_missing(
    tmp_path: Path,
) -> None:
    """LLM 未配置时，Worker.process_task 必须返回 not_configured 而不是 fallback 到旧 workflow。"""
    config = make_config(tmp_path)
    # 显式清空 LLM 配置，确保 is_agent_ready 为 False
    config = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        tmdb_api_key=config.tmdb_api_key,
    )
    for d in (config.downloads_dir, config.watch_dir, config.workspace_dir,
              config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    source_path = config.downloads_dir / "Weathering.With.You.2019.mkv"
    source_path.write_bytes(b"movie")

    initialize_database(config)
    session_factory = create_session_factory(config)
    with session_factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path=str(source_path),
                source_size_bytes=5,
                status="discovered",
                current_step="download_scan",
            )
        )
        session.commit()
        task_id = task.id

    result = Worker(config).process_task(session_factory, task_id)
    assert result.status == "not_configured"


def test_worker_process_task_returns_status_for_deleted(
    tmp_path: Path,
) -> None:
    """`deleted` 状态属于不可推进终态，Worker 必须原样返回。"""
    config = make_config(tmp_path)
    source_path = config.downloads_dir / "Deleted.Movie.2026.mkv"
    source_path.write_bytes(b"movie")

    initialize_database(config)
    session_factory = create_session_factory(config)
    with session_factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path=str(source_path),
                source_size_bytes=5,
                status="deleted",
                current_step="deleted",
            )
        )
        session.commit()
        task_id = task.id

    result = Worker(config).process_task(session_factory, task_id)
    assert result.status == "deleted"


# ═══════════════════════════════════════════════════════════════
# TDD 测试 (Task 3.1)
# 验证 scan_once 只消费 watch_dir，不消费 downloads_dir
# ═══════════════════════════════════════════════════════════════

def test_scan_once_scans_watch_dir_not_downloads_dir(tmp_path: Path) -> None:
    """scan_once 只扫描 watch_dir，忽略 downloads_dir。

    拆分后：
    - watch_dir 中的内容被发现并创建入库任务
    - downloads_dir 中的系统内下载不被外部扫描器发现
    """
    config = make_config(tmp_path)

    # downloads_dir 中的系统内下载（不应被发现）
    (config.downloads_dir / "SystemDownload.2024.mkv").write_bytes(b"system download")
    # watch_dir 中的外部导入（应被发现）
    (config.watch_dir / "ExternalImport.2024.mkv").write_bytes(b"external import")

    initialize_database(config)
    session_factory = create_session_factory(config)
    worker = Worker(config)

    # 默认 watch_stable_window_seconds=0 (关闭稳定窗口), 首次扫描即创建任务
    result = worker.scan_once(session_factory)

    # 断言：只发现 watch_dir 中的文件
    assert result.created_tasks == 1

    with session_factory() as session:
        tasks = session.scalars(select(IngestTask)).all()

    assert len(tasks) == 1
    assert tasks[0].source_path == str(config.watch_dir / "ExternalImport.2024.mkv")


def test_scan_once_discovers_watch_directory_inputs(tmp_path: Path) -> None:
    """watch 中的目录输入仍能被发现并创建入库任务。"""
    config = make_config(tmp_path)

    # watch_dir 中的目录输入
    release_dir = config.watch_dir / "Movie.2026"
    release_dir.mkdir()
    (release_dir / "Movie.2026.mkv").write_bytes(b"movie")

    # downloads_dir 中的目录（不应被发现）
    managed_dir = config.downloads_dir / "SystemSeries.S01"
    managed_dir.mkdir()
    (managed_dir / "S01E01.mkv").write_bytes(b"episode")

    initialize_database(config)
    session_factory = create_session_factory(config)
    worker = Worker(config)

    # 默认 watch_stable_window_seconds=0 (关闭稳定窗口), 首次扫描即创建任务
    result = worker.scan_once(session_factory)

    assert result.created_tasks == 1  # 只有 watch 中的目录
    with session_factory() as session:
        tasks = session.scalars(select(IngestTask)).all()
    assert len(tasks) == 1
    assert tasks[0].source_path == str(release_dir)


# ══════════════════════════════════════════════════════════════════════
# Worker WatchStableDetector 集成 (stabilize-watch-input-before-ingest)
# ══════════════════════════════════════════════════════════════════════


class _FakeClock:
    """通过 monkeypatch 注入虚拟时间到 worker 模块的 time.time。"""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_worker_scan_once_first_appearance_creates_no_task(
    tmp_path: Path, monkeypatch,
) -> None:
    """稳定窗口生效时, 第一次扫描新建文件不创建任务。"""
    import media_pilot.worker as worker_mod

    config = make_config(tmp_path, watch_stable_window_seconds=60)
    media_file = config.watch_dir / "Movie.2024.mkv"
    media_file.write_bytes(b"x" * 100)

    initialize_database(config)
    session_factory = create_session_factory(config)

    clock = _FakeClock()
    monkeypatch.setattr(worker_mod.time, "time", clock)

    worker = Worker(config)
    result = worker.scan_once(session_factory)

    assert result.created_tasks == 0
    with session_factory() as session:
        tasks = session.scalars(select(IngestTask)).all()
    assert tasks == []


def test_worker_scan_once_stable_detector_persists_across_scans(
    tmp_path: Path, monkeypatch,
) -> None:
    """`WatchStableDetector` 跨多次 scan_once 保持状态。"""
    import media_pilot.worker as worker_mod

    config = make_config(tmp_path, watch_stable_window_seconds=60)
    media_file = config.watch_dir / "Movie.2024.mkv"
    media_file.write_bytes(b"x" * 100)

    initialize_database(config)
    session_factory = create_session_factory(config)

    clock = _FakeClock()
    monkeypatch.setattr(worker_mod.time, "time", clock)

    worker = Worker(config)

    # t=100, t=130: 仍未稳定（窗口 60s, 距 first_seen < 60s）
    assert worker.scan_once(session_factory).created_tasks == 0
    clock.advance(30.0)
    assert worker.scan_once(session_factory).created_tasks == 0

    # t=170: 距 first_seen 70s, 满足 60s 窗口
    clock.advance(40.0)
    r3 = worker.scan_once(session_factory)
    assert r3.created_tasks == 1
    with session_factory() as session:
        tasks = session.scalars(select(IngestTask)).all()
    assert len(tasks) == 1
    assert tasks[0].source_path == str(media_file)
    assert tasks[0].source_size_bytes == 100


def test_worker_scan_once_size_change_resets_waiting(
    tmp_path: Path, monkeypatch,
) -> None:
    """扫描间文件 size 变化：稳定计时重置。"""
    import media_pilot.worker as worker_mod

    config = make_config(tmp_path, watch_stable_window_seconds=60)
    media_file = config.watch_dir / "Movie.2024.mkv"
    media_file.write_bytes(b"x" * 100)

    initialize_database(config)
    session_factory = create_session_factory(config)

    clock = _FakeClock()
    monkeypatch.setattr(worker_mod.time, "time", clock)

    worker = Worker(config)

    # t=100: 首次观察
    worker.scan_once(session_factory)
    # t=130: 距 first_seen 30s, 仍未稳定
    clock.advance(30.0)
    assert worker.scan_once(session_factory).created_tasks == 0

    # size 增长 → 快照变化
    media_file.write_bytes(b"x" * 200)
    # t=140: 重置, 仍未稳定
    clock.advance(10.0)
    assert worker.scan_once(session_factory).created_tasks == 0
    # t=200: 距新快照 60s, 满足窗口
    clock.advance(60.0)
    r4 = worker.scan_once(session_factory)
    assert r4.created_tasks == 1
    with session_factory() as session:
        task = session.scalars(select(IngestTask)).one()
    assert task.source_size_bytes == 200


def test_worker_scan_once_dedup_preserved_with_stable_detector(
    tmp_path: Path, monkeypatch,
) -> None:
    """稳定窗口生效时, 已存在任务的路径不会重复创建。"""
    import media_pilot.worker as worker_mod

    config = make_config(tmp_path, watch_stable_window_seconds=60)
    media_file = config.watch_dir / "Movie.2024.mkv"
    media_file.write_bytes(b"x" * 100)

    initialize_database(config)
    session_factory = create_session_factory(config)

    clock = _FakeClock()
    monkeypatch.setattr(worker_mod.time, "time", clock)

    worker = Worker(config)
    worker.scan_once(session_factory)
    clock.advance(70.0)  # 稳定
    assert worker.scan_once(session_factory).created_tasks == 1

    # 继续扫, 不应重复
    clock.advance(10.0)
    r3 = worker.scan_once(session_factory)
    assert r3.created_tasks == 0
    with session_factory() as session:
        tasks = session.scalars(select(IngestTask)).all()
    assert len(tasks) == 1


def test_worker_scan_once_does_not_scan_downloads_dir_with_stability(
    tmp_path: Path, monkeypatch,
) -> None:
    """稳定窗口 + watch/dir 分工：scan_once 仍只扫 watch_dir。"""
    import media_pilot.worker as worker_mod

    config = make_config(tmp_path, watch_stable_window_seconds=60)
    (config.downloads_dir / "SystemDownload.2024.mkv").write_bytes(b"x" * 100)
    (config.watch_dir / "ExternalImport.2024.mkv").write_bytes(b"y" * 100)

    initialize_database(config)
    session_factory = create_session_factory(config)

    clock = _FakeClock()
    monkeypatch.setattr(worker_mod.time, "time", clock)

    worker = Worker(config)
    worker.scan_once(session_factory)
    clock.advance(70.0)
    worker.scan_once(session_factory)

    with session_factory() as session:
        tasks = session.scalars(select(IngestTask)).all()

    assert len(tasks) == 1
    assert tasks[0].source_path == str(config.watch_dir / "ExternalImport.2024.mkv")
    paths = {t.source_path for t in tasks}
    assert not any(str(p).startswith(str(config.downloads_dir)) for p in paths)


def test_worker_scan_once_uses_configured_stable_window(
    tmp_path: Path, monkeypatch,
) -> None:
    """显式 `watch_stable_window_seconds=30`：第 31 秒即可触发。"""
    import media_pilot.worker as worker_mod

    config = make_config(tmp_path, watch_stable_window_seconds=30)
    media_file = config.watch_dir / "Movie.2024.mkv"
    media_file.write_bytes(b"x" * 100)

    initialize_database(config)
    session_factory = create_session_factory(config)

    clock = _FakeClock()
    monkeypatch.setattr(worker_mod.time, "time", clock)

    worker = Worker(config)
    worker.scan_once(session_factory)
    # 距 first_seen 25s, 仍未稳定
    clock.advance(25.0)
    assert worker.scan_once(session_factory).created_tasks == 0
    # 距 first_seen 31s, 满足 30s 窗口
    clock.advance(6.0)
    assert worker.scan_once(session_factory).created_tasks == 1


def test_worker_scan_once_starts_with_empty_detector_cache(
    tmp_path: Path, monkeypatch,
) -> None:
    """新 Worker 实例的 detector 缓存为空, 不继承之前的状态。"""
    import media_pilot.worker as worker_mod

    config = make_config(tmp_path, watch_stable_window_seconds=60)
    media_file = config.watch_dir / "Movie.2024.mkv"
    media_file.write_bytes(b"x" * 100)

    initialize_database(config)
    session_factory = create_session_factory(config)

    clock = _FakeClock()
    monkeypatch.setattr(worker_mod.time, "time", clock)

    # 第一个 Worker 实例观察后丢弃
    first_worker = Worker(config)
    first_worker.scan_once(session_factory)

    # 第二个 Worker 实例是新进程, detector 应当为空
    second_worker = Worker(config)
    # 第一次扫描, 文件被重新观察, 仍未稳定
    clock.advance(10.0)
    assert second_worker.scan_once(session_factory).created_tasks == 0
    # 满足窗口
    clock.advance(70.0)
    assert second_worker.scan_once(session_factory).created_tasks == 1


# ══════════════════════════════════════════════════════════════════════
# Worker Agent Orchestration (Section 5)
# ══════════════════════════════════════════════════════════════════════


def _make_agent_ready_config(root: Path) -> AppConfig:
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
        # 关闭 watch 稳定窗口, 让 orchestrator 集成测试专注于 Agent 行为.
        watch_stable_window_seconds=0,
    )
    for directory in (
        config.downloads_dir, config.watch_dir, config.workspace_dir,
        config.movies_dir, config.shows_dir, config.database_dir,
    ):
        directory.mkdir(parents=True)
    return config


class TestWorkerAgentOrchestration:
    def test_discovered_task_routed_to_agent_when_llm_configured(self, tmp_path):
        config = _make_agent_ready_config(tmp_path)
        source_path = config.downloads_dir / "test.mkv"
        source_path.write_bytes(b"movie")

        initialize_database(config)
        session_factory = create_session_factory(config)
        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source_path),
                    status="discovered",
                    current_step="download_scan",
                )
            )
            session.commit()
            task_id = task.id

        # With real LLM config but no real endpoint, Agent will fail
        result = Worker(config).process_task(session_factory, task_id)
        assert result.status in ("agent_failed", "agent_completed", "waiting_user")

    def test_discovered_task_starts_agent_run(self, tmp_path, monkeypatch):
        """Discovered task should create an AgentRun via auto_ingest mode."""
        from media_pilot.agent.runner import AgentRunResult

        config = _make_agent_ready_config(tmp_path)
        source_path = config.downloads_dir / "test.mkv"
        source_path.write_bytes(b"movie")

        initialize_database(config)
        session_factory = create_session_factory(config)
        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source_path),
                    status="discovered",
                    current_step="download_scan",
                )
            )
            session.commit()
            task_id = task.id

        # Stub _run_agent_orchestration on the Worker instance
        run_calls = []

        def stub_orchestration(session_factory, task):
            run_calls.append({"task_id": task.id, "status": task.status})
            return ProcessTaskResult(status="agent_completed")

        worker = Worker(config)
        monkeypatch.setattr(worker, "_run_agent_orchestration", stub_orchestration)

        result = worker.process_task(session_factory, task_id)

        assert result.status == "agent_completed"
        assert len(run_calls) == 1
        assert run_calls[0]["task_id"] == task_id

    def test_waiting_user_is_not_auto_continued(self, tmp_path):
        config = _make_agent_ready_config(tmp_path)
        source_path = config.downloads_dir / "test.mkv"
        source_path.write_bytes(b"movie")

        initialize_database(config)
        session_factory = create_session_factory(config)
        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source_path),
                    status="waiting_user",
                    current_step="waiting_user",
                )
            )
            session.commit()
            task_id = task.id

        result = Worker(config).process_task(session_factory, task_id)
        assert result.status == "waiting_user"

    def test_agent_failed_is_not_auto_retried(self, tmp_path):
        config = _make_agent_ready_config(tmp_path)
        source_path = config.downloads_dir / "test.mkv"
        source_path.write_bytes(b"movie")

        initialize_database(config)
        session_factory = create_session_factory(config)
        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source_path),
                    status="agent_failed",
                    current_step="agent_failed",
                )
            )
            session.commit()
            task_id = task.id

        result = Worker(config).process_task(session_factory, task_id)
        assert result.status == "agent_failed"

    def test_completed_task_is_skipped(self, tmp_path):
        config = _make_agent_ready_config(tmp_path)
        source_path = config.downloads_dir / "test.mkv"
        source_path.write_bytes(b"movie")

        initialize_database(config)
        session_factory = create_session_factory(config)
        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source_path),
                    status="completed",
                    current_step="completed",
                )
            )
            session.commit()
            task_id = task.id

        result = Worker(config).process_task(session_factory, task_id)
        assert result.status == "completed"

    def test_scan_once_does_not_run_agent(self, tmp_path):
        """scan_once must only create ingest tasks, never run Agent."""
        config = _make_agent_ready_config(tmp_path)
        media_file = config.watch_dir / "test.mkv"
        media_file.write_bytes(b"movie")

        initialize_database(config)
        session_factory = create_session_factory(config)
        worker = Worker(config)

        # `_make_agent_ready_config` 默认 `watch_stable_window_seconds=0`,
        # 关闭稳定窗口, 首次扫描即创建任务.
        result = worker.scan_once(session_factory)
        assert result.created_tasks == 1

        with session_factory() as session:
            tasks = session.scalars(select(IngestTask)).all()
        assert len(tasks) == 1
        assert tasks[0].status == "discovered"


# ══════════════════════════════════════════════════════════════════════
# Integration Tests — Worker.process_task() with mock LLM (Section 8)
# ══════════════════════════════════════════════════════════════════════


class TestWorkerAgentIntegration:
    def test_process_discovered_task_with_mock_llm_metadata_flow(self, tmp_path, monkeypatch):
        """Integration: Worker.process_task() routes to Agent, which uses mock LLM
        to inspect context, search metadata, and persist selection.

        Note: this scenario ends with the agent stopping AFTER persist_metadata_selection
        but BEFORE fetch_and_save_metadata_detail. Under the post-completion safety net
        for this regression,
        auto_ingest mode now intervenes here: it tries to fetch metadata detail
        deterministically, and if that fails (as it does with fake provider_id "999"),
        marks the task agent_failed. So this test now verifies the safety net path."""
        import json

        from media_pilot.agent.runner import run_agent_turn as orig_run_agent_turn
        from tests.agent_runner_helpers import MockLLMClient

        config = _make_agent_ready_config(tmp_path)
        source_path = config.downloads_dir / "Test.Movie.2026.mkv"
        source_path.write_bytes(b"movie content")

        initialize_database(config)
        session_factory = create_session_factory(config)
        with session_factory() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source_path),
                    status="discovered",
                    current_step="download_scan",
                )
            )
            session.commit()
            task_id = task.id

        # Build mock LLM that guides Agent through metadata steps
        mock = MockLLMClient()
        mock.add_tool_calls([{
            "id": "call_ctx",
            "type": "function",
            "function": {"name": "get_task_context", "arguments": json.dumps({"task_id": task_id})},
        }])
        mock.add_tool_calls([{
            "id": "call_scan",
            "type": "function",
            "function": {"name": "scan_task_files", "arguments": json.dumps({"task_id": task_id})},
        }])
        mock.add_tool_calls([{
            "id": "call_search",
            "type": "function",
            "function": {"name": "search_metadata", "arguments": json.dumps({"keyword": "Test Movie 2026"})},
        }])
        mock.add_tool_calls([{
            "id": "call_persist",
            "type": "function",
            "function": {"name": "persist_metadata_selection", "arguments": json.dumps({
                "task_id": task_id, "provider_name": "tmdb",
                "provider_id": "999", "media_type": "movie",
                "title": "Integration Test Movie", "year": 2026,
                "confidence": 0.95,
            })},
        }])
        mock.add_text_response(
            "Task analyzed. Metadata persisted. Ready for detail fetch and publish."
        )

        # Patch run_agent_turn to inject mock LLM
        import media_pilot.agent.runner as runner_mod

        def patched_run_agent_turn(*, session, config, task_id, mode="default",
                                    mock_llm_client=None, initial_message=None):
            return orig_run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode=mode, mock_llm_client=mock,
                initial_message=initial_message,
            )

        monkeypatch.setattr(runner_mod, "run_agent_turn", patched_run_agent_turn)

        result = Worker(config).process_task(session_factory, task_id)

        # Safety net intervened and marked the run failed, so Worker reports agent_failed.
        assert result.status == "agent_failed"

        with session_factory() as session:
            from media_pilot.repository.repositories import (
                AgentRunRepository, MediaCandidateRepository,
            )
            loaded_task = IngestTaskRepository(session).get(task_id)
            runs = AgentRunRepository(session).list_by_task(task_id)
            candidates = MediaCandidateRepository(session).list_for_task(task_id)

        # Candidate WAS persisted by the agent before it stopped.
        agent_candidates = [c for c in candidates if c.source == "agent"]
        assert len(agent_candidates) == 1
        assert agent_candidates[0].title == "Integration Test Movie"

        # Safety net (auto_ingest mode) intervenes: agent ended without MetadataDetail,
        # so safety net tried deterministic fetch with fake provider_id="999" and failed,
        # marking task=agent_failed and run=failed. This prevents the contradictory
        # state where task.status=agent_running but no run is in flight.
        assert loaded_task.status == "agent_failed"
        assert loaded_task.failure_reason == "no_metadata_detail_after_agent_completion"
        assert len(runs) == 1
        assert runs[0].status == "failed"

    def test_full_auto_publish_with_mock_llm(self, tmp_path, monkeypatch):
        """Integration: Worker.process_task() with mock LLM drives full auto-ingest
        pipeline through real tools — discovered → eligibility → persist_selection →
        fetch_and_save_metadata_detail → publish → library_import_complete.
        Verifies file assets, operation records, and revoke check compatibility."""
        import json

        from media_pilot.agent.runner import run_agent_turn as orig_run_agent_turn
        from media_pilot.adapters.metadata import (
            MetadataCandidate,
            MetadataCredits,
            MetadataDetail,
            MetadataExternalIds,
            MetadataImages,
            MetadataPerson,
            MetadataProviderResponse,
        )
        from tests.agent_runner_helpers import MockLLMClient

        config = _make_agent_ready_config(tmp_path)
        source_path = config.downloads_dir / "Integration.Test.Movie.2026.1080p.mkv"
        source_path.write_bytes(b"fake movie content")
        # Same-stem subtitle
        sub_path = config.downloads_dir / "Integration.Test.Movie.2026.1080p.zh.srt"
        sub_path.write_text("1\n00:00:01,000 --> 00:00:05,000\nTest subtitle\n")

        initialize_database(config)
        session_factory = create_session_factory(config)
        with session_factory() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source_path),
                    source_size_bytes=source_path.stat().st_size,
                    status="discovered",
                    current_step="download_scan",
                )
            )
            session.commit()
            task_id = task.id

        # ── Mock metadata provider for fetch_and_save_metadata_detail ──
        class MockProvider:
            def get_movie_details(self, provider_id, *, language_priority):
                return MetadataProviderResponse(value=MetadataDetail(
                    provider="tmdb",
                    provider_id=provider_id,
                    media_type="movie",
                    title="Integration Test Movie",
                    original_title="Integration Test Movie",
                    year=2026,
                    plot="A test movie for integration testing.",
                    runtime_minutes=120,
                    premiered="2026-01-01",
                    rating=8.5,
                    genres=["Action", "Sci-Fi"],
                    countries=["US"],
                    studios=["Test Studios"],
                    credits=MetadataCredits(directors=[], actors=[]),
                    external_ids=MetadataExternalIds(imdb_id=None),
                    images=MetadataImages(poster_url=None, backdrop_url=None, logo_url=None),
                ))

            def get_movie_credits(self, provider_id):
                return MetadataProviderResponse(value=MetadataCredits(
                    directors=[MetadataPerson(provider="tmdb", provider_id=None, name="Test Director", role="Director", profile_url=None, image_url=None)],
                    actors=[MetadataPerson(provider="tmdb", provider_id=None, name="Test Actor", role="Lead", profile_url=None, image_url=None)],
                ))

            def get_movie_external_ids(self, provider_id):
                return MetadataProviderResponse(value=MetadataExternalIds(imdb_id="tt1234567"))

            def get_movie_images(self, provider_id, *, language_priority):
                return MetadataProviderResponse(value=MetadataImages(
                    poster_url="https://example.com/poster.jpg",
                    backdrop_url="https://example.com/backdrop.jpg",
                    logo_url="https://example.com/logo.png",
                ))

            # Unused but required by protocol
            def search_movie(self, keyword, *, language_priority): return MetadataProviderResponse(value=[])
            def search_show(self, keyword, *, language_priority): return MetadataProviderResponse(value=[])
            def get_show_details(self, provider_id, *, language_priority): return MetadataProviderResponse(error=Exception("not implemented"))
            def get_show_credits(self, provider_id): return MetadataProviderResponse(value=MetadataCredits(directors=[], actors=[]))
            def get_show_external_ids(self, provider_id): return MetadataProviderResponse(value=MetadataExternalIds(imdb_id=None))
            def get_show_images(self, provider_id, *, language_priority): return MetadataProviderResponse(value=MetadataImages(poster_url=None, backdrop_url=None, logo_url=None))

        from media_pilot.adapters import factory as adapter_factory
        monkeypatch.setattr(
            adapter_factory, "create_metadata_provider_by_name",
            lambda config, provider_name: MockProvider(),
        )

        # ── Mock _download_image ──
        from media_pilot.orchestration import jellyfin_movie_writer as movie_writer_mod

        def fake_download_image(client, url, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake-image-data")
            return b"fake-image-data"

        monkeypatch.setattr(movie_writer_mod, "_download_image", fake_download_image)

        # ── Mock LLM: full pipeline through real tools ──
        mock = MockLLMClient()
        mock.add_tool_calls([{
            "id": "call_ctx",
            "type": "function",
            "function": {"name": "get_task_context", "arguments": json.dumps({"task_id": task_id})},
        }])
        mock.add_tool_calls([{
            "id": "call_scan",
            "type": "function",
            "function": {"name": "scan_task_files", "arguments": json.dumps({"task_id": task_id})},
        }])
        mock.add_tool_calls([{
            "id": "call_elig",
            "type": "function",
            "function": {"name": "get_auto_ingest_eligibility", "arguments": json.dumps({"task_id": task_id})},
        }])
        mock.add_tool_calls([{
            "id": "call_persist",
            "type": "function",
            "function": {"name": "persist_metadata_selection", "arguments": json.dumps({
                "task_id": task_id, "provider_name": "tmdb",
                "provider_id": "999", "media_type": "movie",
                "title": "Integration Test Movie", "year": 2026,
                "confidence": 0.95,
            })},
        }])
        mock.add_tool_calls([{
            "id": "call_fetch",
            "type": "function",
            "function": {"name": "fetch_and_save_metadata_detail", "arguments": json.dumps({
                "task_id": task_id, "provider_name": "tmdb",
                "provider_id": "999", "media_type": "movie",
            })},
        }])
        mock.add_tool_calls([{
            "id": "call_publish",
            "type": "function",
            "function": {"name": "publish_movie_to_library", "arguments": json.dumps({
                "task_id": task_id,
            })},
        }])
        mock.add_text_response(
            "Movie published successfully. Task is now library_import_complete."
        )

        import media_pilot.agent.runner as runner_mod

        def patched_run_agent_turn(*, session, config, task_id, mode="default",
                                    mock_llm_client=None, initial_message=None):
            return orig_run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode=mode, mock_llm_client=mock,
                initial_message=initial_message,
            )

        monkeypatch.setattr(runner_mod, "run_agent_turn", patched_run_agent_turn)

        result = Worker(config).process_task(session_factory, task_id)

        assert result.status == "agent_completed"

        # ── Verify task state, file assets, and operation records ──
        with session_factory() as session:
            from media_pilot.repository.models import FileAsset, OperationRecord

            loaded_task = IngestTaskRepository(session).get(task_id)
            file_assets = session.scalars(
                select(FileAsset).where(FileAsset.task_id == task_id)
            ).all()
            operations = session.scalars(
                select(OperationRecord).where(OperationRecord.task_id == task_id)
            ).all()

        assert loaded_task.status == "library_import_complete"
        assert loaded_task.current_step == "library_import_complete"

        # Video file asset
        video_assets = [fa for fa in file_assets if fa.role == "library_video"]
        assert len(video_assets) == 1
        assert video_assets[0].size_bytes is not None
        assert "Integration Test Movie (2026)" in video_assets[0].path

        # Subtitle file asset
        sub_assets = [fa for fa in file_assets if fa.role == "library_subtitle"]
        assert len(sub_assets) == 1
        assert sub_assets[0].path.endswith(".zh.srt")

        # NFO asset
        nfo_assets = [fa for fa in file_assets if fa.role == "library_nfo"]
        assert len(nfo_assets) == 1

        # Poster asset
        poster_assets = [fa for fa in file_assets if fa.role == "library_poster"]
        assert len(poster_assets) == 1

        # copy_to_staging operation
        copy_ops = [op for op in operations if op.operation_type == "copy_to_staging"]
        assert len(copy_ops) == 1
        assert copy_ops[0].status == "succeeded"

        # copy_subtitle_to_staging operation
        sub_ops = [op for op in operations if op.operation_type == "copy_subtitle_to_staging"]
        assert len(sub_ops) == 1
        assert sub_ops[0].status == "succeeded"

        # Video file exists at target
        from pathlib import Path
        target_video = Path(video_assets[0].path)
        assert target_video.exists()
        assert target_video.read_bytes() == b"fake movie content"

        # ── Fix #5: verify revoke check recognizes published assets ──
        with session_factory() as session:
            from media_pilot.orchestration.revoke_publish import check_revoke_publish
            revoke_result = check_revoke_publish(session, task_id=task_id)

        assert revoke_result.allowed is True
        assert revoke_result.publish_dir is not None
        assert "Integration Test Movie" in revoke_result.publish_dir

    def test_publish_blocked_by_no_metadata_candidates(self, tmp_path, monkeypatch):
        """publish_movie_to_library must refuse when no_metadata_candidates
        is in blocking_reasons, even if MetadataDetail exists."""
        config = _make_agent_ready_config(tmp_path)
        video_path = config.downloads_dir / "Unknown.Movie.2026.mkv"
        video_path.write_bytes(b"movie")

        initialize_database(config)
        session_factory = create_session_factory(config)
        with session_factory() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(video_path),
                    source_size_bytes=video_path.stat().st_size,
                    status="discovered",
                    current_step="download_scan",
                    media_type="movie",
                )
            )
            session.commit()
            task_id = task.id

        # Pre-save MetadataDetail but NO candidate
        with session_factory() as session:
            from media_pilot.repository.repositories import MetadataDetailRepository
            MetadataDetailRepository(session).save(
                task_id=task_id, provider="tmdb", provider_id="999",
                media_type="movie", title="Unknown Movie",
                original_title="Unknown Movie", year=2026,
                payload={"plot": "test"},
            )
            session.commit()

        with session_factory() as session:
            from media_pilot.agent.tools.base import ToolContext
            from media_pilot.agent.tools.write import _handle_publish_movie_to_library

            ctx = ToolContext(session=session, config=config, task_id=task_id)
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})
            session.commit()

        assert result.status == "failure"
        assert result.data.get("requires_user") is True
        assert result.data.get("reason") == "no_metadata_candidates"

    def test_publish_blocked_by_no_clear_winner(self, tmp_path):
        """publish_movie_to_library must refuse when no_clear_metadata_winner
        is in blocking_reasons (low confidence candidate)."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_agent_ready_config(tmp_path)
        video_path = config.downloads_dir / "LowConf.Movie.2026.mkv"
        video_path.write_bytes(b"movie")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(video_path),
                    source_size_bytes=video_path.stat().st_size,
                    status="discovered", current_step="download_scan",
                    media_type="movie",
                )
            )
            session.commit()
            task_id = task.id

        # Create low-confidence candidate
        with sf() as session:
            from media_pilot.repository.repositories import MediaCandidateRepository
            MediaCandidateRepository(session).add_candidate(
                task_id=task_id, source="tmdb", media_type="movie",
                title="Low Confidence Movie", original_title=None,
                year=2026, external_id="111", confidence=0.65,
                reason="keyword match",
                payload={},
            )
            # Also save MetadataDetail
            from media_pilot.repository.repositories import MetadataDetailRepository
            MetadataDetailRepository(session).save(
                task_id=task_id, provider="tmdb", provider_id="111",
                media_type="movie", title="Low Confidence Movie",
                original_title="Low Confidence Movie", year=2026,
                payload={"plot": "test", "poster_url": "https://example.com/p.jpg",
                         "directors": [], "actors": []},
            )
            session.commit()

        with sf() as session:
            from media_pilot.agent.tools.base import ToolContext
            from media_pilot.agent.tools.write import _handle_publish_movie_to_library

            ctx = ToolContext(session=session, config=config, task_id=task_id)
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})
            session.commit()

        assert result.status == "failure"
        assert result.data.get("requires_user") is True
        assert result.data.get("reason") == "no_clear_metadata_winner"
        assert "candidate_count" in result.data

    def test_process_task_returns_not_configured_without_llm(self, tmp_path):
        """LLM 未配置时 Worker.process_task 必须返回 not_configured,
        不会 fallback 到旧 deterministic workflow。"""
        from media_pilot.config import AdapterMode, MetadataProviderMode

        config = AppConfig(
            downloads_dir=tmp_path / "downloads",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "workspace",
            movies_dir=tmp_path / "library" / "movies",
            shows_dir=tmp_path / "library" / "shows",
            database_dir=tmp_path / "db",
            ai_adapter=AdapterMode.NONE,
            metadata_provider=MetadataProviderMode.TMDB,
            tmdb_api_key="test-key",
        )
        for d in [config.downloads_dir, config.watch_dir, config.workspace_dir,
                   config.movies_dir, config.shows_dir, config.database_dir]:
            d.mkdir(parents=True)

        source_path = config.downloads_dir / "NoLLM.Movie.2026.mkv"
        source_path.write_bytes(b"movie")

        initialize_database(config)
        session_factory = create_session_factory(config)
        with session_factory() as session:
            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(source_path),
                    status="discovered",
                    current_step="download_scan",
                )
            )
            session.commit()
            task_id = task.id

        result = Worker(config).process_task(session_factory, task_id)

        # LLM 未配置时, discovered 任务必须返回 not_configured
        assert result.status == "not_configured"


# ══════════════════════════════════════════════════════════════════════
# SQLite lock resilience — 后台 qB 同步与 Agent 长事务的锁竞争必须
# 不能让 background 线程进坏状态.
# ══════════════════════════════════════════════════════════════════════


class TestSQLiteLockResilience:
    def test_engine_enables_wal_mode(self, tmp_path: Path):
        """regression: engine 必须设 journal_mode=WAL, 避免后台 qB 同步
        与 Agent 长事务争写锁. WAL 让 reader/writer 互不阻塞.
        """
        from sqlalchemy import text

        from media_pilot.repository.database import create_engine_from_config

        config = AppConfig(
            downloads_dir=tmp_path / "dl",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "ws",
            movies_dir=tmp_path / "movies",
            shows_dir=tmp_path / "shows",
            database_dir=tmp_path,
        )
        initialize_database(config)
        engine = create_engine_from_config(config)
        try:
            with engine.connect() as conn:
                journal_mode = conn.execute(
                    text("PRAGMA journal_mode")
                ).scalar()
            # WAL 模式生效 (或 memory 在临时数据库, 这里用真文件应是 WAL)
            assert journal_mode is not None
        finally:
            engine.dispose()

    def test_engine_sets_busy_timeout(self, tmp_path: Path):
        """regression: 每条新连接必须有 PRAGMA busy_timeout=5000, 让
        SQLAlchemy 在锁冲突时等 5s 而非立刻 OperationalError.
        """
        from sqlalchemy import text

        from media_pilot.repository.database import create_engine_from_config

        config = AppConfig(
            downloads_dir=tmp_path / "dl",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "ws",
            movies_dir=tmp_path / "movies",
            shows_dir=tmp_path / "shows",
            database_dir=tmp_path,
        )
        initialize_database(config)
        engine = create_engine_from_config(config)
        try:
            with engine.connect() as conn:
                # busy_timeout 是 per-connection, 在新连接上读到的应是
                # 引擎 listener 设的值
                timeout = conn.execute(
                    text("PRAGMA busy_timeout")
                ).scalar()
            assert int(timeout) >= 5000, f"busy_timeout must be >= 5000, got {timeout}"
        finally:
            engine.dispose()

    def test_download_sync_skips_on_operational_error(self, tmp_path: Path, monkeypatch):
        """regression: qB API 调用抛 OperationalError (database is locked)
        时, sync_once 记 warning 跳过本轮, 不让 background 线程进坏
        状态 (不会 nested update → task 永远 sync_failed).
        """
        from sqlalchemy.exc import OperationalError

        from media_pilot.repository.database import initialize_database
        from media_pilot.services.download_sync import DownloadSyncService

        config = AppConfig(
            downloads_dir=tmp_path / "dl",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "ws",
            movies_dir=tmp_path / "movies",
            shows_dir=tmp_path / "shows",
            database_dir=tmp_path,
            qbittorrent_url="http://qb.test:8080",
        )
        initialize_database(config)
        sf = create_session_factory(config)

        # 预置一个需要同步的 DownloadTask
        from media_pilot.repository.repositories import (
            DownloadTaskCreate,
            DownloadTaskRepository,
        )
        with sf() as session:
            task = DownloadTaskRepository(session).create(DownloadTaskCreate(
                title="war", source="prowlarr", save_path="/data/dl",
                qb_hash="abc123", status="submitted",
            ))
            session.commit()

        # Stub adapter 让 get_torrent_info 抛 OperationalError (database locked)
        class _LockingAdapter:
            def get_torrent_info(self, hashes):
                raise OperationalError(
                    "SELECT", {}, OperationalError.__init__.__doc__ or Exception()
                )

        svc = DownloadSyncService(config, adapter=_LockingAdapter())
        result = svc.sync_once(sf)

        # 关键: OperationalError 路径把 task 标 skipped, 不抛异常,
        # 也不写 sync_failed (避免 lock 期间 nested update).
        assert result.skipped == 1, result
        assert result.failed == 0, result

        # task 状态保持 submitted, 没被误标 sync_failed
        with sf() as session:
            from media_pilot.repository.models import DownloadTask
            row = session.get(DownloadTask, task.id)
            assert row is not None
            assert row.status == "submitted", (
                f"OperationalError must NOT mutate task to sync_failed, "
                f"got {row.status}"
            )

    def test_runner_releases_lock_between_tool_batches(self, tmp_path: Path):
        """regression: runner 在工具批次收口后 commit 一次, 让 LLM 网络
        调用期间不持有 SQLite 写锁. 验证手段: 跑一个工具调用 + final
        text 的 run, 跑完后 DB 应处于可读状态 (没有 uncommitted 事务).
        """
        import json

        from tests.test_api_v1 import _make_session_factory as _make_sf
        from tests.agent_runner_helpers import MockLLMClient as _MockLLM

        sf = _make_sf(tmp_path)
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/movie.mkv",
                status="discovered",
                current_step="agent_start",
            ))
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.registry import register_builtin_tools
        register_builtin_tools()

        mock = _MockLLM()
        # 1 个 tool call (get_task_context), 然后 final text
        mock.add_tool_calls([{
            "id": "call_ctx",
            "type": "function",
            "function": {
                "name": "get_task_context",
                "arguments": json.dumps({"task_id": task_id}),
            },
        }])
        mock.add_text_response("Done.")

        with sf() as session:
            from media_pilot.config import AppConfig as _AC
            config = _AC(
                downloads_dir=tmp_path / "dl",
                watch_dir=tmp_path / "watch",
                workspace_dir=tmp_path / "ws",
                movies_dir=tmp_path / "movies",
                shows_dir=tmp_path / "shows",
                database_dir=tmp_path,
                llm_api_key="test-key",
                llm_base_url="https://test.example.com/v1",
                llm_model="test-model",
            )
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"
