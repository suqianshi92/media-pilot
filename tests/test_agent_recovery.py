from media_pilot.orchestration.agent_recovery import recover_stale_agent_runs
from media_pilot.orchestration.state_machine import IngestTaskStatus
from media_pilot.repository.repositories import (
    AgentRunCreate,
    AgentRunRepository,
)


class TestAgentRecovery:
    def test_recover_stale_runs_marks_as_failed(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)

        # 创建一个 active AgentRun 模拟重启前状态
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

            task_repo = IngestTaskRepository(session)
            task = task_repo.create(IngestTaskCreate(
                source_path="/data/downloads/test.mkv",
                status=IngestTaskStatus.AGENT_RUNNING,
                current_step="agent_processing",
            ))
            run_repo = AgentRunRepository(session)
            run_repo.create(AgentRunCreate(task_id=task.id, current_step="identify"))
            session.commit()
            run_id = run_repo._session is not None  # dummy to suppress unused var

        # 执行恢复
        recovered = recover_stale_agent_runs(sf)
        assert recovered == 1

        # 验证：AgentRun 标记为 failed
        with sf() as session:
            run_repo = AgentRunRepository(session)
            active = run_repo.list_active()
            assert len(active) == 0

    def test_recover_updates_task_status(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

            task_repo = IngestTaskRepository(session)
            task = task_repo.create(IngestTaskCreate(
                source_path="/data/downloads/test.mkv",
                status=IngestTaskStatus.AGENT_RUNNING,
                current_step="agent_processing",
            ))
            run_repo = AgentRunRepository(session)
            run_repo.create(AgentRunCreate(task_id=task.id))
            session.commit()
            task_id = task.id

        recover_stale_agent_runs(sf)

        with sf() as session:
            task_repo = IngestTaskRepository(session)
            task = task_repo.get(task_id)
            assert task.status == IngestTaskStatus.AGENT_FAILED
            assert task.current_step == "agent_interrupted"

    def test_recover_skips_completed_runs(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

            task_repo = IngestTaskRepository(session)
            task = task_repo.create(IngestTaskCreate(
                source_path="/data/downloads/test.mkv",
                status=IngestTaskStatus.COMPLETED,
                current_step="library_import_complete",
            ))
            run_repo = AgentRunRepository(session)
            run = run_repo.create(AgentRunCreate(task_id=task.id))
            run_repo.update_status(run, status="completed")
            session.commit()

        # 恢复不应该影响已完成的 run
        recovered = recover_stale_agent_runs(sf)
        assert recovered == 0

    def test_recover_on_empty_database(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)

        # 只初始化不创建任何 AgentRun
        recovered = recover_stale_agent_runs(sf)
        assert recovered == 0
