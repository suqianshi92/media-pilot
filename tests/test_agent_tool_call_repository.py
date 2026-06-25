from media_pilot.repository.repositories import (
    AgentRunCreate,
    AgentRunRepository,
    AgentToolCallCreate,
    AgentToolCallRepository,
)


class TestAgentToolCallRepository:
    def _seed_run(self, session):
        from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

        task_repo = IngestTaskRepository(session)
        task = task_repo.create(IngestTaskCreate(
            source_path="/data/downloads/test.mkv",
            status="agent_running",
            current_step="agent_start",
        ))
        run_repo = AgentRunRepository(session)
        run = run_repo.create(AgentRunCreate(task_id=task.id))
        session.flush()
        return run

    def test_create_tool_call(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        run_id = None
        with sf() as session:
            run = self._seed_run(session)
            session.commit()
            run_id = run.id

        with sf() as session:
            repo = AgentToolCallRepository(session)
            tc = repo.create(AgentToolCallCreate(
                run_id=run_id,
                tool_name="get_task_context",
                input={"task_id": "test"},
            ))
            session.commit()

            assert tc.id is not None
            assert tc.tool_name == "get_task_context"
            assert tc.input == {"task_id": "test"}
            assert tc.status == "pending"

    def test_update_status_to_succeeded(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        run_id = None
        with sf() as session:
            run = self._seed_run(session)
            session.commit()
            run_id = run.id

        with sf() as session:
            repo = AgentToolCallRepository(session)
            tc = repo.create(AgentToolCallCreate(
                run_id=run_id,
                tool_name="get_task_context",
                input={"task_id": "test"},
            ))
            repo.update_status(
                tc,
                status="succeeded",
                output={"source_path": "/data/test.mkv"},
                duration_ms=150,
            )
            session.commit()

            loaded = repo.get(tc.id)
            assert loaded.status == "succeeded"
            assert loaded.output == {"source_path": "/data/test.mkv"}
            assert loaded.duration_ms == 150

    def test_update_status_to_failed(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        run_id = None
        with sf() as session:
            run = self._seed_run(session)
            session.commit()
            run_id = run.id

        with sf() as session:
            repo = AgentToolCallRepository(session)
            tc = repo.create(AgentToolCallCreate(
                run_id=run_id,
                tool_name="search_metadata",
                input={"query": "unknown"},
            ))
            repo.update_status(
                tc,
                status="failed",
                error_message="TMDB returned 404",
            )
            session.commit()

            loaded = repo.get(tc.id)
            assert loaded.status == "failed"
            assert loaded.error_message == "TMDB returned 404"

    def test_list_by_run(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        run_id = None
        with sf() as session:
            run = self._seed_run(session)
            session.commit()
            run_id = run.id

        with sf() as session:
            repo = AgentToolCallRepository(session)
            tc1 = repo.create(AgentToolCallCreate(
                run_id=run_id,
                tool_name="get_task_context",
                input={},
            ))
            tc2 = repo.create(AgentToolCallCreate(
                run_id=run_id,
                tool_name="scan_task_files",
                input={},
            ))
            session.commit()

            calls = repo.list_by_run(run_id)
            assert len(calls) == 2
            assert calls[0].id == tc1.id
            assert calls[1].id == tc2.id
