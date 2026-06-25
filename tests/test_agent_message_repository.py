from media_pilot.repository.repositories import (
    AgentMessageCreate,
    AgentMessageRepository,
    AgentRunCreate,
    AgentRunRepository,
)


class TestAgentMessageRepository:
    def test_create_message(self, tmp_path):
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
            run_repo = AgentRunRepository(session)
            run = run_repo.create(AgentRunCreate(task_id=task_id))
            session.commit()
            run_id = run.id

        with sf() as session:
            msg_repo = AgentMessageRepository(session)
            msg = msg_repo.create(AgentMessageCreate(
                run_id=run_id,
                role="user",
                content="请识别这部影片",
            ))
            session.commit()

            assert msg.id is not None
            assert msg.run_id == run_id
            assert msg.role == "user"
            assert msg.content == "请识别这部影片"

    def test_list_by_run_ordered(self, tmp_path):
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
            run_repo = AgentRunRepository(session)
            run = run_repo.create(AgentRunCreate(task_id=task_id))
            session.commit()
            run_id = run.id

        with sf() as session:
            repo = AgentMessageRepository(session)
            m1 = repo.create(AgentMessageCreate(run_id=run_id, role="system", content="system prompt"))
            m2 = repo.create(AgentMessageCreate(run_id=run_id, role="user", content="user message"))
            m3 = repo.create(AgentMessageCreate(run_id=run_id, role="assistant", content="response"))
            session.commit()

            messages = repo.list_by_run(run_id)
            assert len(messages) == 3
            assert messages[0].id == m1.id
            assert messages[1].id == m2.id
            assert messages[2].id == m3.id

    def test_create_tool_message(self, tmp_path):
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
            run_repo = AgentRunRepository(session)
            run = run_repo.create(AgentRunCreate(task_id=task_id))
            session.commit()
            run_id = run.id

        with sf() as session:
            repo = AgentMessageRepository(session)
            msg = repo.create(AgentMessageCreate(
                run_id=run_id,
                role="tool",
                content='{"title": "Test Movie"}',
                tool_call_id="call_abc123",
                tool_name="search_metadata",
            ))
            session.commit()

            loaded = repo.get(msg.id)
            assert loaded.role == "tool"
            assert loaded.tool_call_id == "call_abc123"
            assert loaded.tool_name == "search_metadata"
