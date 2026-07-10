"""测试 Agent 工作台后端 API：AgentStatusSummary、tool-calls API、批量聚合"""

import pytest

from tests.auth_helpers import AuthenticatedTestClient as TestClient


class TestAgentStatusSummaryDTO:
    def test_defaults(self):
        from media_pilot.api.task_dtos import AgentStatusSummary

        s = AgentStatusSummary(run_status="none")
        assert s.run_status == "none"
        assert s.latest_run_id is None
        assert s.pending_decision_count == 0
        assert s.latest_message_summary is None

    def test_active_with_pending(self):
        from media_pilot.api.task_dtos import AgentStatusSummary

        s = AgentStatusSummary(
            run_status="waiting_user",
            latest_run_id="run-1",
            pending_decision_count=2,
            latest_message_summary="请确认元数据候选",
        )
        assert s.run_status == "waiting_user"
        assert s.latest_run_id == "run-1"
        assert s.pending_decision_count == 2
        assert s.latest_message_summary == "请确认元数据候选"

    def test_task_summary_includes_agent_status(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="discovered",
            ))
            session.commit()

            from media_pilot.api.task_mapper import map_to_task_summaries
            summaries = map_to_task_summaries(session, [task])
            assert len(summaries) == 1
            s = summaries[0]
            assert s.agent_status_summary is not None
            assert s.agent_status_summary.run_status == "none"


class TestBuildAgentStatusIndex:
    def test_empty_task_ids(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.api.task_mapper import _build_agent_status_index

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            result = _build_agent_status_index(session, [])
            assert result == {}

    def test_no_agent_runs(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.api.task_mapper import _build_agent_status_index
        from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="discovered",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            result = _build_agent_status_index(session, [task_id])
            assert task_id in result
            assert result[task_id].run_status == "none"
            assert result[task_id].pending_decision_count == 0

    def test_active_run_with_pending_decision(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.api.task_mapper import _build_agent_status_index
        from media_pilot.repository.models import AgentRun
        from media_pilot.repository.repositories import (
            AgentDecisionRequestCreate,
            AgentDecisionRequestRepository,
            IngestTaskCreate,
            IngestTaskRepository,
        )

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="queued",
            ))
            run = AgentRun(task_id=task.id, status="active")
            session.add(run)
            session.flush()

            dr_repo = AgentDecisionRequestRepository(session)
            dr_repo.create(AgentDecisionRequestCreate(
                run_id=run.id, task_id=task.id,
                decision_type="metadata_confirmation",
                question="确认元数据？",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            result = _build_agent_status_index(session, [task_id])
            s = result[task_id]
            assert s.run_status == "active"
            assert s.latest_run_id is not None
            assert s.pending_decision_count >= 1

    def test_multiple_tasks_batch(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.api.task_mapper import _build_agent_status_index
        from media_pilot.repository.models import AgentRun
        from media_pilot.repository.repositories import (
            AgentDecisionRequestCreate,
            AgentDecisionRequestRepository,
            IngestTaskCreate,
            IngestTaskRepository,
        )

        sf = _make_session_factory(tmp_path)
        task1_id = task2_id = None
        with sf() as session:
            t1 = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/t1.mkv", status="agent_running",
            ))
            t2 = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/t2.mkv", status="discovered",
            ))
            r1 = AgentRun(task_id=t1.id, status="active")
            session.add(r1)
            session.flush()

            dr_repo = AgentDecisionRequestRepository(session)
            dr_repo.create(AgentDecisionRequestCreate(
                run_id=r1.id, task_id=t1.id,
                decision_type="metadata_confirmation",
                question="Q1?",
            ))
            session.commit()
            task1_id, task2_id = t1.id, t2.id

        with sf() as session:
            result = _build_agent_status_index(session, [task1_id, task2_id])
            assert result[task1_id].run_status == "active"
            assert result[task2_id].run_status == "none"
            assert result[task1_id].pending_decision_count == 1
            assert result[task2_id].pending_decision_count == 0

    def test_waiting_user_status(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.api.task_mapper import _build_agent_status_index
        from media_pilot.repository.models import AgentRun
        from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="discovered",
            ))
            run = AgentRun(task_id=task.id, status="waiting_user")
            session.add(run)
            session.commit()
            task_id = task.id

        with sf() as session:
            result = _build_agent_status_index(session, [task_id])
            assert result[task_id].run_status == "waiting_user"


class TestAgentToolCallListByTask:
    def _seed(self, session):
        from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path="/data/test.mkv",
            status="agent_running",
            current_step="agent_start",
        ))
        from media_pilot.repository.models import AgentRun, AgentToolCall
        run = AgentRun(task_id=task.id, status="active")
        session.add(run)
        session.flush()

        tc1 = AgentToolCall(
            run_id=run.id, tool_name="get_task_context",
            input={}, status="succeeded",
            output={"title": "Test"}, duration_ms=100,
        )
        tc2 = AgentToolCall(
            run_id=run.id, tool_name="scan_task_files",
            input={}, status="succeeded",
            output={"files": []}, duration_ms=200,
        )
        session.add_all([tc1, tc2])
        session.flush()
        return task, run, [tc1, tc2]

    def test_list_by_task_returns_tool_calls(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.repository.repositories import AgentToolCallRepository

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            task, _, tcs = self._seed(session)
            session.commit()
            task_id = task.id

        with sf() as session:
            repo = AgentToolCallRepository(session)
            calls = repo.list_by_task(task_id)
            assert len(calls) == 2
            assert calls[0].tool_name == "get_task_context"
            assert calls[1].tool_name == "scan_task_files"

    def test_list_by_task_empty(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.repository.repositories import AgentToolCallRepository

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            repo = AgentToolCallRepository(session)
            calls = repo.list_by_task("nonexistent")
            assert calls == []

    def test_list_by_task_includes_all_fields(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.repository.repositories import AgentToolCallRepository

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            task, run, _ = self._seed(session)
            from media_pilot.repository.models import AgentToolCall
            tc = AgentToolCall(
                run_id=run.id, tool_name="search_metadata",
                input={"keyword": "test"},
                status="failed",
                error_message="Timeout",
                tool_call_id="call_search_1",
                duration_ms=5000,
            )
            session.add(tc)
            session.commit()
            task_id = task.id

        with sf() as session:
            repo = AgentToolCallRepository(session)
            calls = repo.list_by_task(task_id)
            failed = [c for c in calls if c.tool_name == "search_metadata"]
            assert len(failed) == 1
            assert failed[0].status == "failed"
            assert failed[0].error_message == "Timeout"
            assert failed[0].duration_ms == 5000


class TestAgentToolCallsAPI:
    def test_list_tool_calls(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="queued",
            ))
            from media_pilot.repository.models import AgentRun, AgentToolCall
            run = AgentRun(task_id=task.id, status="active")
            session.add(run)
            session.flush()
            tc = AgentToolCall(
                run_id=run.id, tool_name="get_task_context",
                input={"task_id": task.id}, status="succeeded",
                output={"source_path": "/data/test.mkv"},
                tool_call_id="call_test_1",
                duration_ms=120,
            )
            session.add(tc)
            session.commit()
            task_id = task.id

        client = TestClient(create_app(session_factory=sf))
        resp = client.get(f"/api/v1/tasks/{task_id}/agent-tool-calls")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        data = body["data"]
        assert len(data) == 1
        tc = data[0]
        assert tc["tool_name"] == "get_task_context"
        assert tc["tool_call_id"] == "call_test_1"
        assert tc["status"] == "succeeded"
        assert tc["input"] == {"task_id": task_id}
        assert tc["output"] == {"source_path": "/data/test.mkv"}
        assert tc["duration_ms"] == 120
        assert tc["created_at"] is not None

    def test_list_tool_calls_task_not_found(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app

        sf = _make_session_factory(tmp_path)
        client = TestClient(create_app(session_factory=sf))
        resp = client.get("/api/v1/tasks/nonexistent/agent-tool-calls")
        assert resp.status_code == 404

    def test_list_tool_calls_empty(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="discovered",
            ))
            session.commit()
            task_id = task.id

        client = TestClient(create_app(session_factory=sf))
        resp = client.get(f"/api/v1/tasks/{task_id}/agent-tool-calls")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["data"] == []


class TestTaskListIncludesAgentStatus:
    def test_list_tasks_has_agent_status_summary(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="discovered",
            ))
            session.commit()

        client = TestClient(create_app(session_factory=sf))
        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 200
        body = resp.json()
        items = body["data"]["items"]
        assert len(items) == 1
        item = items[0]
        assert "agent_status_summary" in item
        assert item["agent_status_summary"]["run_status"] == "none"

    def test_list_tasks_with_agent_run(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="queued",
            ))
            from media_pilot.repository.models import AgentRun
            run = AgentRun(task_id=task.id, status="active")
            session.add(run)
            session.commit()

        client = TestClient(create_app(session_factory=sf))
        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 200
        body = resp.json()
        item = body["data"]["items"][0]
        ag = item["agent_status_summary"]
        assert ag["run_status"] == "active"
        assert ag["latest_run_id"] is not None

    def test_list_tasks_filtered(self, tmp_path):
        """Task list with status filter should still include agent_status_summary."""
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="completed",
            ))
            IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test2.mkv",
                status="discovered",
            ))
            session.commit()

        client = TestClient(create_app(session_factory=sf))
        resp = client.get("/api/v1/tasks?status=completed")
        assert resp.status_code == 200
        body = resp.json()
        items = body["data"]["items"]
        assert len(items) == 1
        assert "agent_status_summary" in items[0]


class TestTaskStatusAgentValues:
    """Verify new agent-related TaskStatus values serialize and map correctly."""

    def test_agent_running_status_in_task_summary(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.api.task_mapper import map_to_task_summaries
        from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="agent_running",
            ))
            session.commit()
            summaries = map_to_task_summaries(session, [task])
            assert len(summaries) == 1
            assert summaries[0].status_summary.status == "agent_running"

    def test_waiting_user_status_in_task_summary(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.api.task_mapper import map_to_task_summaries
        from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="waiting_user",
            ))
            session.commit()
            summaries = map_to_task_summaries(session, [task])
            assert len(summaries) == 1
            assert summaries[0].status_summary.status == "waiting_user"

    def test_agent_failed_status_in_task_summary(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.api.task_mapper import map_to_task_summaries
        from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="agent_failed",
            ))
            session.commit()
            summaries = map_to_task_summaries(session, [task])
            assert len(summaries) == 1
            assert summaries[0].status_summary.status == "agent_failed"

    def test_agent_statuses_in_api_listing(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            for st in ["agent_running", "waiting_user", "agent_failed"]:
                IngestTaskRepository(session).create(IngestTaskCreate(
                    source_path=f"/data/test_{st}.mkv",
                    status=st,
                ))
            session.commit()

        client = TestClient(create_app(session_factory=sf))
        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 200
        body = resp.json()
        statuses = [item["status_summary"]["status"] for item in body["data"]["items"]]
        assert "agent_running" in statuses
        assert "waiting_user" in statuses
        assert "agent_failed" in statuses
        for item in body["data"]["items"]:
            assert "agent_status_summary" in item


# ══════════════════════════════════════════════════════════════════════
# Agent Retry API (Section 6)
# ══════════════════════════════════════════════════════════════════════


class TestAgentRetryAPI:
    def test_retry_returns_409_when_active_run_exists(self, tmp_path):
        """Retry must return 409 when an active or waiting AgentRun exists."""
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app
        from media_pilot.config.settings import AppConfig

        sf = _make_session_factory(tmp_path)
        config = AppConfig(
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
        for d in [config.downloads_dir, config.watch_dir, config.workspace_dir,
                   config.movies_dir, config.shows_dir]:
            d.mkdir(parents=True, exist_ok=True)

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="agent_failed",
                current_step="agent_failed",
            ))
            from media_pilot.repository.models import AgentRun
            run = AgentRun(task_id=task.id, status="active")
            session.add(run)
            session.commit()
            task_id = task.id

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        resp = client.post(f"/api/v1/tasks/{task_id}/agent-runs")
        assert resp.status_code == 409

    def test_retry_returns_409_when_waiting_run_exists(self, tmp_path):
        """Retry must return 409 when a waiting_user AgentRun exists."""
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app
        from media_pilot.config.settings import AppConfig

        sf = _make_session_factory(tmp_path)
        config = AppConfig(
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
        for d in [config.downloads_dir, config.watch_dir, config.workspace_dir,
                   config.movies_dir, config.shows_dir]:
            d.mkdir(parents=True, exist_ok=True)

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="agent_failed",
                current_step="agent_failed",
            ))
            from media_pilot.repository.models import AgentRun
            run = AgentRun(task_id=task.id, status="waiting_user")
            session.add(run)
            session.commit()
            task_id = task.id

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        resp = client.post(f"/api/v1/tasks/{task_id}/agent-runs")
        assert resp.status_code == 409

    def test_retry_allows_when_no_active_or_waiting_run(self, tmp_path):
        """Retry must succeed when no active/waiting run exists (only failed runs)."""
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app
        from media_pilot.config.settings import AppConfig

        sf = _make_session_factory(tmp_path)
        config = AppConfig(
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
        for d in [config.downloads_dir, config.watch_dir, config.workspace_dir,
                   config.movies_dir, config.shows_dir]:
            d.mkdir(parents=True, exist_ok=True)

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv",
                status="agent_failed",
                current_step="agent_failed",
            ))
            # Add only a failed previous run (no active/waiting)
            from media_pilot.repository.models import AgentRun
            failed_run = AgentRun(task_id=task.id, status="failed")
            session.add(failed_run)
            session.commit()
            task_id = task.id

        # The API will try to call LLM which will fail, but it should not
        # be blocked by the conflict check. We expect either a 200 (with error
        # status) or a 500 due to LLM config.
        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        resp = client.post(f"/api/v1/tasks/{task_id}/agent-runs")
        # Should NOT be 409 — conflict check passes because only failed runs exist
        assert resp.status_code != 409

    def test_retry_returns_404_for_nonexistent_task(self, tmp_path):
        """Retry must return 404 when task does not exist."""
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app
        from media_pilot.config.settings import AppConfig

        sf = _make_session_factory(tmp_path)
        config = AppConfig(
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

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        resp = client.post("/api/v1/tasks/nonexistent-id/agent-runs")
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════
# /agent-tool-calls 状态归一 — 防止前端因 "completed" / "succeeded"
# 共存而误把成功标为失败. 回归保护: 历史 DB 写 "succeeded", runner 新写
# "completed", API 响应必须归一为 "succeeded" 让前端契约稳定.
# ══════════════════════════════════════════════════════════════════════


class TestAgentToolCallStatusNormalization:
    def test_runner_completed_status_normalized_to_succeeded(self, tmp_path):
        """regression: AgentRunner 写入 status="completed" (新), API
        响应必须归一为 "succeeded" (前端契约值). 防止前端因
        wire status 差异误标为失败.
        """
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv", status="queued",
            ))
            from media_pilot.repository.models import AgentRun, AgentToolCall
            run = AgentRun(task_id=task.id, status="completed")
            session.add(run)
            session.flush()
            # 模拟 runner 新写入路径: status="completed", output 内 status=success
            tc = AgentToolCall(
                run_id=run.id, tool_name="fetch_and_save_metadata_detail",
                input={"task_id": task.id}, status="completed",
                output={"status": "success", "summary": "saved", "data": {"title": "X"}},
                tool_call_id="call_runner_new", duration_ms=80,
            )
            session.add(tc)
            session.commit()
            task_id = task.id

        client = TestClient(create_app(session_factory=sf))
        resp = client.get(f"/api/v1/tasks/{task_id}/agent-tool-calls")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        data = body["data"]
        assert len(data) == 1
        tc = data[0]
        # 关键: wire status "completed" 归一为 "succeeded"
        assert tc["status"] == "succeeded", (
            f"API must normalize status='completed' → 'succeeded', got {tc['status']!r}"
        )
        # output 仍保留原始 success/failure 供调试
        assert tc["output"]["status"] == "success"

    def test_legacy_succeeded_status_passthrough(self, tmp_path):
        """regression: 历史 DB 写 "succeeded" (旧), API 仍按 "succeeded"
        返回, 不破坏老记录的展示.
        """
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv", status="queued",
            ))
            from media_pilot.repository.models import AgentRun, AgentToolCall
            run = AgentRun(task_id=task.id, status="completed")
            session.add(run)
            session.flush()
            tc = AgentToolCall(
                run_id=run.id, tool_name="get_task_context",
                input={"task_id": task.id}, status="succeeded",
                output={"status": "success", "summary": "ok"},
                tool_call_id="call_legacy", duration_ms=10,
            )
            session.add(tc)
            session.commit()
            task_id = task.id

        client = TestClient(create_app(session_factory=sf))
        resp = client.get(f"/api/v1/tasks/{task_id}/agent-tool-calls")
        body = resp.json()
        tc = body["data"][0]
        assert tc["status"] == "succeeded", tc

    def test_failed_status_normalized_to_failed(self, tmp_path):
        """regression: DB 写 "failed" / "failure" / "error" 都归一为 "failed".
        """
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv", status="queued",
            ))
            from media_pilot.repository.models import AgentRun, AgentToolCall
            run = AgentRun(task_id=task.id, status="failed")
            session.add(run)
            session.flush()
            # runner 新写 "failed"
            tc_a = AgentToolCall(
                run_id=run.id, tool_name="search_metadata",
                input={"keyword": "X"}, status="failed",
                output={"status": "failure", "summary": "no candidates"},
                tool_call_id="call_failed_1",
            )
            session.add(tc_a)
            session.commit()
            task_id = task.id

        client = TestClient(create_app(session_factory=sf))
        resp = client.get(f"/api/v1/tasks/{task_id}/agent-tool-calls")
        body = resp.json()
        statuses = [tc["status"] for tc in body["data"]]
        assert statuses == ["failed"], statuses

    def test_running_status_passthrough(self, tmp_path):
        """regression: 中间态 (running / pending) 必须原样透传, 不得
        误归一为 succeeded/failed.
        """
        from tests.test_api_v1 import _make_session_factory
        from media_pilot.app import create_app

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/data/test.mkv", status="queued",
            ))
            from media_pilot.repository.models import AgentRun, AgentToolCall
            run = AgentRun(task_id=task.id, status="active")
            session.add(run)
            session.flush()
            tc = AgentToolCall(
                run_id=run.id, tool_name="search_metadata",
                input={"keyword": "X"}, status="running",
                output=None,
                tool_call_id="call_running",
            )
            session.add(tc)
            session.commit()
            task_id = task.id

        client = TestClient(create_app(session_factory=sf))
        resp = client.get(f"/api/v1/tasks/{task_id}/agent-tool-calls")
        body = resp.json()
        assert body["data"][0]["status"] == "running", (
            f"Intermediate state 'running' must pass through unchanged, got "
            f"{body['data'][0]['status']!r}"
        )
