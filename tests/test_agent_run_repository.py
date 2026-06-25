import pytest

from media_pilot.repository.repositories import (
    AgentRunCreate,
    AgentRunRepository,
)


class TestAgentRunRepository:
    def test_create_agent_run(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

            task_repo = IngestTaskRepository(session)
            task = task_repo.create(IngestTaskCreate(
                source_path="/data/downloads/test.mkv",
                status="agent_running",
                current_step="agent_start",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            repo = AgentRunRepository(session)
            run = repo.create(AgentRunCreate(task_id=task_id, current_step="identify"))
            session.commit()

            assert run.id is not None
            assert run.task_id == task_id
            assert run.status == "active"
            assert run.current_step == "identify"

    def test_get_active_by_task(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

            task_repo = IngestTaskRepository(session)
            task = task_repo.create(IngestTaskCreate(
                source_path="/data/downloads/test.mkv",
                status="agent_running",
                current_step="agent_start",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            repo = AgentRunRepository(session)
            repo.create(AgentRunCreate(task_id=task_id))
            session.commit()

            active = repo.get_active_by_task(task_id)
            assert active is not None
            assert active.status == "active"

    def test_create_rejects_duplicate_active(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

            task_repo = IngestTaskRepository(session)
            task = task_repo.create(IngestTaskCreate(
                source_path="/data/downloads/test.mkv",
                status="agent_running",
                current_step="agent_start",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            repo = AgentRunRepository(session)
            repo.create(AgentRunCreate(task_id=task_id))
            session.commit()

            with pytest.raises(ValueError, match="already has an active AgentRun"):
                repo.create(AgentRunCreate(task_id=task_id))

    def test_allows_multiple_completed_runs(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

            task_repo = IngestTaskRepository(session)
            task = task_repo.create(IngestTaskCreate(
                source_path="/data/downloads/test.mkv",
                status="agent_running",
                current_step="agent_start",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            repo = AgentRunRepository(session)
            run1 = repo.create(AgentRunCreate(task_id=task_id))
            repo.update_status(run1, status="completed")
            session.commit()

            # 第一个 run 完成后可以创建新的 active run
            run2 = repo.create(AgentRunCreate(task_id=task_id))
            assert run2.status == "active"

    def test_update_status(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

            task_repo = IngestTaskRepository(session)
            task = task_repo.create(IngestTaskCreate(
                source_path="/data/downloads/test.mkv",
                status="agent_running",
                current_step="agent_start",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            repo = AgentRunRepository(session)
            run = repo.create(AgentRunCreate(task_id=task_id))
            repo.update_status(run, status="failed", error_message="tool call timeout")
            session.commit()

            loaded = repo.get(run.id)
            assert loaded.status == "failed"
            assert loaded.error_message == "tool call timeout"

    def test_list_by_task(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

            task_repo = IngestTaskRepository(session)
            task = task_repo.create(IngestTaskCreate(
                source_path="/data/downloads/test.mkv",
                status="agent_running",
                current_step="agent_start",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            repo = AgentRunRepository(session)
            run1 = repo.create(AgentRunCreate(task_id=task_id))
            repo.update_status(run1, status="completed")
            run2 = repo.create(AgentRunCreate(task_id=task_id))
            repo.update_status(run2, status="failed")
            session.commit()

            runs = repo.list_by_task(task_id)
            assert len(runs) == 2
            # 按 created_at desc 排序，最新的在前
            assert runs[0].id == run2.id

    def test_list_active(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

            task_repo = IngestTaskRepository(session)
            task = task_repo.create(IngestTaskCreate(
                source_path="/data/downloads/test.mkv",
                status="agent_running",
                current_step="agent_start",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            repo = AgentRunRepository(session)
            repo.create(AgentRunCreate(task_id=task_id))
            session.commit()

            active_runs = repo.list_active()
            assert len(active_runs) >= 1
            assert all(r.status == "active" for r in active_runs)
