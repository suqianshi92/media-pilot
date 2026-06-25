import pytest

from tests.agent_runner_helpers import MockLLMClient, _make_config, _make_task

class TestFreeformMessagePersistence:
    def test_only_persists_raw_user_text_not_context(self, tmp_path):
        """When user_message_text is provided, only raw text is persisted;
        the context-injected prompt (task facts, recent msgs, tool calls)
        is NOT in the persisted message."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        mock.add_text_response("I understand the task. Let me help.")

        context_prompt = "## Task Facts\nTask ID: xyz\nStatus: active\n\n## Recent Messages\n..."

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="freeform", mock_llm_client=mock,
                initial_message=context_prompt,
                user_message_text="帮我检查这个任务的元数据",
            )
            session.commit()

        assert result.status == "completed"

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
                AgentRunRepository,
            )
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            messages = AgentMessageRepository(session).list_by_run(run.id)
            user_msgs = [m for m in messages if m.role == "user"]
            assert len(user_msgs) == 1
            # Only the raw user text, not the context injection
            assert user_msgs[0].content == "帮我检查这个任务的元数据"
            assert "Task Facts" not in user_msgs[0].content
            assert "Recent Messages" not in user_msgs[0].content

    def test_context_injection_not_persisted_as_system_message(self, tmp_path):
        """The llm_context is passed as role=system at LLM call time but
        never persisted to the database."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        mock.add_text_response("Done.")

        context_prompt = "## Task Facts\nStatus: active\n..."

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="freeform", mock_llm_client=mock,
                initial_message=context_prompt,
                user_message_text="hello",
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
                AgentRunRepository,
            )
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            messages = AgentMessageRepository(session).list_by_run(run.id)
            for msg in messages:
                if msg.role == "system":
                    pytest.fail(f"llm_context must not be persisted: {msg.content[:80]}")

    def test_context_injection_sent_to_llm(self, tmp_path):
        """The context-injected prompt must appear in the LLM call messages."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        mock.add_text_response("Got it.")

        context_prompt = "## Task Facts\nStatus: active\nSource: /data/test.mkv"

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="freeform", mock_llm_client=mock,
                initial_message=context_prompt,
                user_message_text="hello",
            )
            session.commit()

        # The LLM must have received the context as a system message
        assert len(mock.calls) >= 1
        llm_messages = mock.calls[0]["messages"]
        system_msgs = [m for m in llm_messages if m["role"] == "system"]
        context_msgs = [m for m in system_msgs if "Task Facts" in m.get("content", "")]
        assert len(context_msgs) == 1

    def test_chat_only_restores_previous_status_and_step(self, tmp_path):
        """Freeform chat-only completion (no tool calls) must restore
        previous_status and previous_step on the task."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(
                session,
                status="library_import_complete",
                current_step="library_import_complete",
            )
            task_id = task.id

        mock = MockLLMClient()
        mock.add_text_response("Here's what I found about your task.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="freeform", mock_llm_client=mock,
                initial_message="Context injection here",
                user_message_text="What's the status?",
            )
            session.commit()

        assert result.status == "completed"

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            # Must restore previous business status, not stay at agent_running
            assert task.status == "library_import_complete"
            assert task.current_step == "library_import_complete"

    def test_chat_only_restores_previous_status_non_default(self, tmp_path):
        """Freeform chat-only must restore even non-library_import_complete statuses."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(
                session,
                status="waiting_user",
                current_step="waiting_user",
            )
            task_id = task.id

        mock = MockLLMClient()
        mock.add_text_response("Let me check that for you.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="freeform", mock_llm_client=mock,
                initial_message="Context injection here",
                user_message_text="Any updates?",
            )
            session.commit()

        assert result.status == "completed"

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "waiting_user"
            assert task.current_step == "waiting_user"


# ══════════════════════════════════════════════════════════════════════
# Freeform Mode — Tool Whitelist
# ══════════════════════════════════════════════════════════════════════


class TestFreeformToolWhitelist:
    def test_freeform_exposes_whitelisted_write_tools(self):
        """Freeform mode must expose WRITE tools in FREEFORM_WRITE_TOOL_WHITELIST."""
        from media_pilot.agent.tool_schema import get_allowed_tool_names, get_allowed_tool_schemas
        from media_pilot.agent.tools.base import (
            PermissionLevel,
            ToolDefinition,
            ToolResult,
        )
        from media_pilot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        revoke_tool = ToolDefinition(
            name="revoke_publish",
            description="Revoke publish",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            permission_level=PermissionLevel.WRITE,
            handler=lambda ctx, inp: ToolResult(status="success", summary="ok"),
        )
        registry.register(revoke_tool)

        schemas = get_allowed_tool_schemas(registry, mode="freeform")
        names = {s["function"]["name"] for s in schemas}
        assert "revoke_publish" in names

        allowed_names = get_allowed_tool_names(registry, mode="freeform")
        assert "revoke_publish" in allowed_names

    def test_freeform_rejects_non_whitelisted_write_tool(self):
        """Freeform mode must reject WRITE tools not in FREEFORM_WRITE_TOOL_WHITELIST."""
        from media_pilot.agent.tool_schema import get_allowed_tool_schemas
        from media_pilot.agent.tools.base import (
            PermissionLevel,
            ToolDefinition,
            ToolResult,
        )
        from media_pilot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="dangerous_delete",
            description="Dangerous tool",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            permission_level=PermissionLevel.WRITE,
            handler=lambda ctx, inp: ToolResult(status="success", summary="deleted"),
        ))

        schemas = get_allowed_tool_schemas(registry, mode="freeform")
        names = {s["function"]["name"] for s in schemas}
        assert "dangerous_delete" not in names

    def test_freeform_rejects_write_tool_in_runner(self, tmp_path):
        """In freeform mode, non-whitelisted WRITE tools must be hard-rejected at runtime."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.base import PermissionLevel, ToolDefinition, ToolResult
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
        register_builtin_tools()

        registry = get_tool_registry()
        test_tool_name = "test_freeform_bad_write"
        registry.register(ToolDefinition(
            name=test_tool_name,
            description="Dangerous write tool",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            permission_level=PermissionLevel.WRITE,
            handler=lambda ctx, inp: ToolResult(status="success", summary="deleted"),
        ))
        try:
            with sf() as session:
                task = _make_task(session)
                task_id = task.id

            mock = MockLLMClient()
            mock.add_tool_calls([{
                "id": "call_bad",
                "type": "function",
                "function": {
                    "name": test_tool_name,
                    "arguments": '{}',
                },
            }])
            mock.add_text_response("Done.")

            with sf() as session:
                from media_pilot.agent.runner import run_agent_turn
                result = run_agent_turn(
                    session=session, config=config, task_id=task_id,
                    mode="freeform", mock_llm_client=mock,
                )
                session.commit()

            assert result.status == "completed"
            assert result.tool_call_count == 1

            with sf() as session:
                from media_pilot.repository.repositories import (
                    AgentRunRepository,
                    AgentToolCallRepository,
                )
                run = AgentRunRepository(session).list_by_task(task_id)[0]
                tcs = AgentToolCallRepository(session).list_by_run(run.id)
                assert len(tcs) == 1
                assert tcs[0].tool_name == test_tool_name
                assert tcs[0].status == "failed"
                assert "not in the allowed tool set for mode 'freeform'" in (tcs[0].error_message or "")
        finally:
            del registry._tools[test_tool_name]

    def test_freeform_allows_revoke_publish_in_runner(self, tmp_path):
        """In freeform mode, revoke_publish (whitelisted) must execute successfully."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.base import PermissionLevel, ToolDefinition, ToolResult
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
        register_builtin_tools()

        registry = get_tool_registry()
        # revoke_publish is already registered as builtin; verify it's whitelisted
        call_count = {"count": 0}

        def revoke_handler(ctx, inp):
            call_count["count"] += 1
            return ToolResult(status="success", summary="Revoked for test")

        # Replace handler for testing (tool is already registered as a WRITE tool)
        from media_pilot.agent.tool_schema import FREEFORM_WRITE_TOOL_WHITELIST
        assert "revoke_publish" in FREEFORM_WRITE_TOOL_WHITELIST, \
            "revoke_publish must be in FREEFORM_WRITE_TOOL_WHITELIST"

        # Use persist_metadata_selection (also in whitelist) for a clean test
        test_tool_name = "test_freeform_allowed_write"
        registry.register(ToolDefinition(
            name=test_tool_name,
            description="Test allowed write tool",
            parameters={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"], "additionalProperties": False},
            permission_level=PermissionLevel.WRITE,
            handler=lambda ctx, inp: ToolResult(status="success", summary=f"OK {inp['task_id']}"),
        ))

        import media_pilot.agent.tool_schema as ts
        original_whitelist = ts.FREEFORM_WRITE_TOOL_WHITELIST
        ts.FREEFORM_WRITE_TOOL_WHITELIST = frozenset({test_tool_name, "revoke_publish"})

        try:
            with sf() as session:
                task = _make_task(session)
                task_id = task.id

            mock = MockLLMClient()
            mock.add_tool_calls([{
                "id": "call_good",
                "type": "function",
                "function": {
                    "name": test_tool_name,
                    "arguments": f'{{"task_id": "{task_id}"}}',
                },
            }])
            mock.add_text_response("Done.")

            with sf() as session:
                from media_pilot.agent.runner import run_agent_turn
                result = run_agent_turn(
                    session=session, config=config, task_id=task_id,
                    mode="freeform", mock_llm_client=mock,
                )
                session.commit()

            assert result.status == "completed"
            assert result.tool_call_count == 1

            with sf() as session:
                from media_pilot.repository.repositories import (
                    AgentRunRepository,
                    AgentToolCallRepository,
                )
                run = AgentRunRepository(session).list_by_task(task_id)[0]
                tcs = AgentToolCallRepository(session).list_by_run(run.id)
                assert len(tcs) == 1
                assert tcs[0].tool_name == test_tool_name
                assert tcs[0].status == "completed"
        finally:
            ts.FREEFORM_WRITE_TOOL_WHITELIST = original_whitelist
            del registry._tools[test_tool_name]


# ══════════════════════════════════════════════════════════════════════
# Freeform API — 409 Conflict Guard
# ══════════════════════════════════════════════════════════════════════


class TestFreeform409Guard:
    def test_freeform_409_when_pending_decision_exists(self, tmp_path):
        """Freeform input must be rejected with 409 when a pending decision exists."""
        from fastapi.testclient import TestClient

        from media_pilot.app import create_app
        from media_pilot.config.settings import AppConfig
        from tests.test_api_v1 import _make_session_factory

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
                status="library_import_complete",
                current_step="library_import_complete",
            ))
            from media_pilot.repository.models import AgentDecisionRequest, AgentRun
            run = AgentRun(task_id=task.id, status="waiting_user")
            session.add(run)
            session.flush()
            dr = AgentDecisionRequest(
                run_id=run.id, task_id=task.id,
                decision_type="post_revoke_action",
                question="选择后续操作",
                status="pending",
                options=[
                    {"id": "opt1", "label": "选项1"},
                    {"id": "opt2", "label": "选项2"},
                ],
            )
            session.add(dr)
            session.commit()
            task_id = task.id

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)

        # Try non-streaming freeform
        resp = client.post(f"/api/v1/tasks/{task_id}/agent-runs", json={"message": "hello"})
        assert resp.status_code == 409

    def test_freeform_409_when_task_deleted(self, tmp_path):
        """Freeform input must be rejected with 409 when task is deleted."""
        from fastapi.testclient import TestClient

        from media_pilot.app import create_app
        from media_pilot.config.settings import AppConfig
        from tests.test_api_v1 import _make_session_factory

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
                status="deleted",
                current_step="delete_task_input",
            ))
            session.commit()
            task_id = task.id

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)

        resp = client.post(f"/api/v1/tasks/{task_id}/agent-runs", json={"message": "hello"})
        assert resp.status_code == 409

    def test_freeform_409_when_active_run_exists(self, tmp_path):
        """Freeform input must be rejected with 409 when an active run exists."""
        from fastapi.testclient import TestClient

        from media_pilot.app import create_app
        from media_pilot.config.settings import AppConfig
        from tests.test_api_v1 import _make_session_factory

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
                status="library_import_complete",
            ))
            from media_pilot.repository.models import AgentRun
            run = AgentRun(task_id=task.id, status="active")
            session.add(run)
            session.commit()
            task_id = task.id

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)

        resp = client.post(f"/api/v1/tasks/{task_id}/agent-runs", json={"message": "hello"})
        assert resp.status_code == 409


# ══════════════════════════════════════════════════════════════════════
# SSE Events
# ══════════════════════════════════════════════════════════════════════


class TestSSEEvents:
    def test_emitter_to_sse_format(self):
        """AgentStreamEvent.to_sse() must produce valid SSE format."""
        from media_pilot.agent.sse import AgentStreamEvent, AgentStreamEventType

        event = AgentStreamEvent(
            event=AgentStreamEventType.ASSISTANT_DELTA,
            data={"delta": "Hello"},
        )
        sse_str = event.to_sse()
        assert sse_str.startswith("event: assistant_delta\n")
        assert "data: " in sse_str
        assert sse_str.endswith("\n\n")
        # Must contain the JSON data
        assert '"delta"' in sse_str
        assert '"Hello"' in sse_str

    def test_emitter_tool_call_event_format(self):
        """Tool call events must produce correct SSE format."""
        from media_pilot.agent.sse import AgentStreamEvent, AgentStreamEventType

        event = AgentStreamEvent(
            event=AgentStreamEventType.TOOL_CALL_STARTED,
            data={"tool_call_id": "call_1", "tool_name": "search_metadata"},
        )
        sse_str = event.to_sse()
        assert sse_str.startswith("event: tool_call_started\n")
        assert '"tool_call_id"' in sse_str
        assert '"search_metadata"' in sse_str

    def test_emitter_iter_stops_on_close(self):
        """AgentStreamEmitter must stop iteration when close() is called."""
        from media_pilot.agent.sse import AgentStreamEmitter, AgentStreamEvent, AgentStreamEventType

        emitter = AgentStreamEmitter()
        emitter.emit(AgentStreamEvent(
            event=AgentStreamEventType.USER_MESSAGE,
            data={"run_id": "test-run"},
        ))
        emitter.close()

        events = list(emitter)
        assert len(events) == 1
        assert events[0].event == AgentStreamEventType.USER_MESSAGE

    def test_emitter_supports_multiple_events(self):
        """Emitter must support multiple events before close."""
        from media_pilot.agent.sse import AgentStreamEmitter, AgentStreamEvent, AgentStreamEventType

        emitter = AgentStreamEmitter()
        emitter.emit(AgentStreamEvent(
            event=AgentStreamEventType.ASSISTANT_DELTA,
            data={"delta": "Hello"},
        ))
        emitter.emit(AgentStreamEvent(
            event=AgentStreamEventType.ASSISTANT_DELTA,
            data={"delta": " World"},
        ))
        emitter.emit(AgentStreamEvent(
            event=AgentStreamEventType.TOOL_CALL_STARTED,
            data={"tool_call_id": "c1", "tool_name": "search"},
        ))
        emitter.emit(AgentStreamEvent(
            event=AgentStreamEventType.TOOL_CALL_FINISHED,
            data={"tool_call_id": "c1", "tool_name": "search", "status": "success"},
        ))
        emitter.emit(AgentStreamEvent(
            event=AgentStreamEventType.RUN_FINISHED,
            data={"status": "completed"},
        ))
        emitter.close()

        events = list(emitter)
        event_types = [e.event for e in events]
        assert event_types == [
            AgentStreamEventType.ASSISTANT_DELTA,
            AgentStreamEventType.ASSISTANT_DELTA,
            AgentStreamEventType.TOOL_CALL_STARTED,
            AgentStreamEventType.TOOL_CALL_FINISHED,
            AgentStreamEventType.RUN_FINISHED,
        ]

    def test_sse_streaming_produces_tool_events(self, tmp_path):
        """Streaming agent turn must emit tool_call_started/finished events."""
        import json

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        mock.add_tool_calls([{
            "id": "call_search",
            "type": "function",
            "function": {
                "name": "get_task_context",
                "arguments": json.dumps({"task_id": task_id}),
            },
        }])
        mock.add_text_response("Task context retrieved. Done.")

        from media_pilot.agent.runner import run_agent_turn_streaming

        emitter, result_holder = run_agent_turn_streaming(
            session_factory=sf,
            config=config,
            task_id=task_id,
            initial_message="Test streaming",
            user_message_text="test",
            mode="freeform",
            mock_llm_client=mock,
        )

        events = list(emitter)
        event_types = [e.event.value for e in events]

        assert "tool_call_started" in event_types
        assert "tool_call_finished" in event_types
        assert "run_finished" in event_types

        # Verify tool_call_started has correct data
        tool_started = [e for e in events if e.event.value == "tool_call_started"][0]
        assert tool_started.data["tool_name"] == "get_task_context"

        # Verify tool_call_finished has correct data
        tool_finished = [e for e in events if e.event.value == "tool_call_finished"][0]
        assert tool_finished.data["tool_name"] == "get_task_context"
        assert tool_finished.data["status"] == "success"

    def test_sse_streaming_produces_assistant_delta(self, tmp_path):
        """Streaming must emit assistant_delta events for text content."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        mock.add_text_response("Hello from streaming!")

        from media_pilot.agent.runner import run_agent_turn_streaming

        emitter, result_holder = run_agent_turn_streaming(
            session_factory=sf,
            config=config,
            task_id=task_id,
            initial_message="Test",
            user_message_text="test",
            mode="freeform",
            mock_llm_client=mock,
        )

        events = list(emitter)
        event_types = [e.event.value for e in events]

        assert "user_message" in event_types
        assert "assistant_delta" in event_types
        assert "assistant_message" in event_types
        assert "run_finished" in event_types

        # Verify assistant_delta carries the content
        delta_events = [e for e in events if e.event.value == "assistant_delta"]
        delta_text = "".join(e.data["delta"] for e in delta_events)
        assert delta_text == "Hello from streaming!"

    def test_sse_streaming_handles_task_not_found(self, tmp_path):
        """Streaming must emit error event when task is not found."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.runner import run_agent_turn_streaming

        emitter, result_holder = run_agent_turn_streaming(
            session_factory=sf,
            config=config,
            task_id="nonexistent-task-id",
            initial_message="Test",
            user_message_text="test",
            mode="freeform",
        )

        events = list(emitter)
        event_types = [e.event.value for e in events]

        assert "error" in event_types
        error_event = [e for e in events if e.event.value == "error"][0]
        assert "not found" in error_event.data["error"].lower()

    def test_to_sse_all_event_types(self):
        """All AgentStreamEventType values must produce valid to_sse() output."""
        from media_pilot.agent.sse import AgentStreamEvent, AgentStreamEventType

        for event_type in AgentStreamEventType:
            event = AgentStreamEvent(event=event_type, data={"test": True})
            sse_str = event.to_sse()
            assert sse_str.startswith(f"event: {event_type.value}\n")
            assert sse_str.endswith("\n\n")
            assert "data: " in sse_str


# ══════════════════════════════════════════════════════════════════════
# RevokePublish — existing_run_id guards against active-run conflict
# ══════════════════════════════════════════════════════════════════════


class TestRevokeExistingRunId:
    def test_revoke_with_existing_run_id_no_active_conflict(self, tmp_path):
        """When existing_run_id is passed, execute_revoke_publish reuses the
        run instead of creating a new one via _ensure_agent_run_for_decision(),
        which would raise ValueError on active-run conflict."""
        from sqlalchemy import select

        from media_pilot.config.settings import AppConfig
        from media_pilot.orchestration.revoke_publish import execute_revoke_publish
        from media_pilot.repository.database import create_session_factory, initialize_database
        from media_pilot.repository.models import (
            MediaSourceSelection,
            WriteResult,
        )
        from media_pilot.repository.repositories import (
            AgentRunCreate,
            AgentRunRepository,
            IngestTaskCreate,
            IngestTaskRepository,
        )

        config = AppConfig(
            downloads_dir=tmp_path / "dl",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "ws",
            movies_dir=tmp_path / "movies",
            shows_dir=tmp_path / "shows",
            database_dir=tmp_path,
        )
        for d in [config.downloads_dir, config.watch_dir, config.workspace_dir,
                   config.movies_dir, config.shows_dir]:
            d.mkdir(parents=True, exist_ok=True)

        initialize_database(config)
        session_factory = create_session_factory(config)

        publish_dir = tmp_path / "movies" / "Test Movie (2026)"
        publish_dir.mkdir(parents=True, exist_ok=True)
        (publish_dir / "test.mkv").write_text("dummy")

        source_path = tmp_path / "dl" / "test.mkv"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("source")

        with session_factory() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path=str(source_path),
                status="library_import_complete",
                current_step="library_import_complete",
            ))
            session.add(MediaSourceSelection(
                task_id=task.id, input_path=str(source_path),
                selected_path=str(source_path), payload={"bdmv_detected": False},
            ))
            session.add(WriteResult(
                task_id=task.id, status="succeeded",
                payload={"target_dir": str(publish_dir)},
            ))
            session.commit()
            task_id = task.id

            # Create an active AgentRun — this would cause a conflict if
            # execute_revoke_publish tried to create a NEW run
            run_repo = AgentRunRepository(session)
            active_run = run_repo.create(AgentRunCreate(
                task_id=task_id,
                current_step="step_1",
            ))
            run_repo.update_status(active_run, status="active", current_step="step_1")
            session.commit()
            active_run_id = active_run.id

        # execute_revoke_publish with existing_run_id must NOT raise ValueError
        with session_factory() as session:
            result = execute_revoke_publish(
                session, task_id=task_id,
                existing_run_id=active_run_id,
            )
            session.commit()

        assert result.status == "waiting_user"
        assert result.decision_id is not None

        # Verify the decision is bound to the EXISTING run, not a new one
        with session_factory() as session:
            from media_pilot.repository.models import AgentDecisionRequest
            decision = session.scalars(
                select(AgentDecisionRequest)
                .where(AgentDecisionRequest.task_id == task_id)
                .where(AgentDecisionRequest.status == "pending")
            ).first()
            assert decision is not None
            assert decision.run_id == active_run_id

    def test_revoke_without_existing_run_id_still_works(self, tmp_path):
        """Without existing_run_id, execute_revoke_publish creates a new
        system AgentRun for the decision (backward compatibility)."""
        from media_pilot.config.settings import AppConfig
        from media_pilot.orchestration.revoke_publish import execute_revoke_publish
        from media_pilot.repository.database import create_session_factory, initialize_database
        from media_pilot.repository.models import (
            MediaSourceSelection,
            WriteResult,
        )
        from media_pilot.repository.repositories import (
            IngestTaskCreate,
            IngestTaskRepository,
        )

        config = AppConfig(
            downloads_dir=tmp_path / "dl",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "ws",
            movies_dir=tmp_path / "movies",
            shows_dir=tmp_path / "shows",
            database_dir=tmp_path,
        )
        for d in [config.downloads_dir, config.watch_dir, config.workspace_dir,
                   config.movies_dir, config.shows_dir]:
            d.mkdir(parents=True, exist_ok=True)

        initialize_database(config)
        session_factory = create_session_factory(config)

        publish_dir = tmp_path / "movies" / "Test Movie (2026)"
        publish_dir.mkdir(parents=True, exist_ok=True)
        (publish_dir / "test.mkv").write_text("dummy")

        source_path = tmp_path / "dl" / "test.mkv"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("source")

        with session_factory() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path=str(source_path),
                status="library_import_complete",
                current_step="library_import_complete",
            ))
            session.add(MediaSourceSelection(
                task_id=task.id, input_path=str(source_path),
                selected_path=str(source_path), payload={"bdmv_detected": False},
            ))
            session.add(WriteResult(
                task_id=task.id, status="succeeded",
                payload={"target_dir": str(publish_dir)},
            ))
            session.commit()
            task_id = task.id

        # No active run exists — backward compat path creates a new run
        with session_factory() as session:
            result = execute_revoke_publish(session, task_id=task_id)
            session.commit()

        assert result.status == "waiting_user"
        assert result.decision_id is not None


# ══════════════════════════════════════════════════════════════════════
# Freeform State Restoration — only restore when tool_call_count == 0
# ══════════════════════════════════════════════════════════════════════


class TestFreeformStateRestoration:
    def test_freeform_with_write_tool_does_not_restore_previous_status(self, tmp_path):
        """When freeform mode executes WRITE tools (tool_call_count > 0),
        the task's previous_status must NOT be restored on completion.
        The WRITE tool has already changed the task state."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.base import PermissionLevel, ToolDefinition, ToolResult
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
        register_builtin_tools()

        registry = get_tool_registry()
        test_tool_name = "test_state_restore_write"

        registry.register(ToolDefinition(
            name=test_tool_name,
            description="Write tool for state restore test",
            parameters={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            permission_level=PermissionLevel.WRITE,
            handler=lambda ctx, inp: ToolResult(status="success", summary="did write"),
        ))

        import media_pilot.agent.tool_schema as ts
        original_whitelist = ts.FREEFORM_WRITE_TOOL_WHITELIST
        ts.FREEFORM_WRITE_TOOL_WHITELIST = frozenset({test_tool_name})

        try:
            with sf() as session:
                # Task starts in library_import_complete — a "published" state
                task = _make_task(
                    session,
                    status="library_import_complete",
                    current_step="library_import_complete",
                )
                task_id = task.id

            mock = MockLLMClient()
            mock.add_tool_calls([{
                "id": "call_write",
                "type": "function",
                "function": {
                    "name": test_tool_name,
                    "arguments": f'{{"task_id": "{task_id}"}}',
                },
            }])
            mock.add_text_response("Write done, task is now different.")

            with sf() as session:
                from media_pilot.agent.runner import run_agent_turn
                result = run_agent_turn(
                    session=session, config=config, task_id=task_id,
                    mode="freeform", mock_llm_client=mock,
                )
                session.commit()

            assert result.status == "completed"
            assert result.tool_call_count == 1

            # Task status must NOT be restored because a WRITE tool was executed
            with sf() as session:
                from media_pilot.repository.models import IngestTask
                task = session.get(IngestTask, task_id)
                assert task is not None
                # Runner must NOT overwrite task status when tools were called.
                # The mock tool didn't explicitly set task status, so it remains
                # "agent_running" (set by run_agent_turn before the loop).
                assert task.status == "agent_running"
        finally:
            ts.FREEFORM_WRITE_TOOL_WHITELIST = original_whitelist
            del registry._tools[test_tool_name]

    def test_freeform_chat_only_restores_previous_status(self, tmp_path):
        """When freeform mode is chat-only (tool_call_count == 0),
        the task's previous_status IS restored on completion.
        This is the existing behavior that must be preserved."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.registry import register_builtin_tools
        register_builtin_tools()

        with sf() as session:
            task = _make_task(
                session,
                status="library_import_complete",
                current_step="library_import_complete",
            )
            task_id = task.id

        mock = MockLLMClient()
        mock.add_text_response("Just chatting, no tools.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="freeform", mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"
        assert result.tool_call_count == 0

        # Task status IS restored because no tools were called (chat-only)
        with sf() as session:
            from media_pilot.repository.models import IngestTask
            task = session.get(IngestTask, task_id)
            assert task is not None
            assert task.status == "library_import_complete"

    def test_revoke_with_skip_no_state_restore(self, tmp_path):
        """After revoke_publish with skip_post_revoke_decision=true completes,
        if no successful republish follows (streaming path final text without
        tools), the runner must NOT erroneously report tool_call_count=0 for
        a run that already executed revoke."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.base import PermissionLevel, ToolDefinition, ToolResult
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
        register_builtin_tools()

        registry = get_tool_registry()
        test_tool_name = "test_skip_revoke"

        call_record = []

        def revoke_like_handler(ctx, inp):
            call_record.append("called")
            return ToolResult(
                status="success",
                summary="Revoked with skip",
                data={"waiting_for_post_revoke_action": False, "status": "completed"},
            )

        registry.register(ToolDefinition(
            name=test_tool_name,
            description="Simulates revoke_publish with skip_post_revoke_decision",
            parameters={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            permission_level=PermissionLevel.WRITE,
            handler=revoke_like_handler,
        ))

        import media_pilot.agent.tool_schema as ts
        original_whitelist = ts.FREEFORM_WRITE_TOOL_WHITELIST
        ts.FREEFORM_WRITE_TOOL_WHITELIST = frozenset({test_tool_name})

        try:
            with sf() as session:
                task = _make_task(
                    session,
                    status="library_import_complete",
                    current_step="library_import_complete",
                )
                task_id = task.id

            # revoke-like tool call, then final text (no more tools)
            mock = MockLLMClient()
            mock.add_tool_calls([{
                "id": "call_skip",
                "type": "function",
                "function": {
                    "name": test_tool_name,
                    "arguments": f'{{"task_id": "{task_id}"}}',
                },
            }])
            mock.add_text_response("Republish failed. Task stays in current state.")

            with sf() as session:
                from media_pilot.agent.runner import run_agent_turn
                result = run_agent_turn(
                    session=session, config=config, task_id=task_id,
                    mode="freeform", mock_llm_client=mock,
                )
                session.commit()

            assert result.status == "completed"
            assert result.tool_call_count == 1
            assert len(call_record) == 1

            # Task must NOT be restored to library_import_complete
            # because a WRITE tool was executed (tool_call_count > 0).
            # Runner must NOT overwrite task status; it stays "agent_running".
            with sf() as session:
                from media_pilot.repository.models import IngestTask
                task = session.get(IngestTask, task_id)
                assert task is not None
                assert task.status == "agent_running"
        finally:
            ts.FREEFORM_WRITE_TOOL_WHITELIST = original_whitelist
            del registry._tools[test_tool_name]

    def test_revoke_creates_decision_and_pauses_runner(self, tmp_path):
        """When revoke_publish returns waiting_for_post_revoke_action=True,
        the runner must set decision_requested and pause the run."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.base import PermissionLevel, ToolDefinition, ToolResult
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
        register_builtin_tools()

        registry = get_tool_registry()
        test_tool_name = "test_revoke_waiting"

        def revoke_waiting_handler(ctx, inp):
            return ToolResult(
                status="success",
                summary="Revoked, user must decide",
                data={
                    "waiting_for_post_revoke_action": True,
                    "status": "waiting_user",
                    "decision_id": "dummy_decision_id",
                },
            )

        registry.register(ToolDefinition(
            name=test_tool_name,
            description="Simulates revoke_publish that creates a decision",
            parameters={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            permission_level=PermissionLevel.WRITE,
            handler=revoke_waiting_handler,
        ))

        import media_pilot.agent.tool_schema as ts
        original_whitelist = ts.FREEFORM_WRITE_TOOL_WHITELIST
        ts.FREEFORM_WRITE_TOOL_WHITELIST = frozenset({test_tool_name})

        try:
            with sf() as session:
                task = _make_task(
                    session,
                    status="library_import_complete",
                    current_step="library_import_complete",
                )
                task_id = task.id

            # revoke that creates decision — no follow-up LLM response needed
            mock = MockLLMClient()
            mock.add_tool_calls([{
                "id": "call_revoke",
                "type": "function",
                "function": {
                    "name": test_tool_name,
                    "arguments": f'{{"task_id": "{task_id}"}}',
                },
            }])

            with sf() as session:
                from media_pilot.agent.runner import run_agent_turn
                result = run_agent_turn(
                    session=session, config=config, task_id=task_id,
                    mode="freeform", mock_llm_client=mock,
                )
                session.commit()

            # Runner must pause with waiting_user, not continue to max_steps
            assert result.status == "waiting_user"
            assert result.tool_call_count == 1
        finally:
            ts.FREEFORM_WRITE_TOOL_WHITELIST = original_whitelist
            del registry._tools[test_tool_name]

    def test_generic_decision_requested_pauses_runner(self, tmp_path):
        """任意 WRITE 工具返回 data.decision_requested=True 都必须让 runner
        暂停为 waiting_user（不只是 request_user_decision 工具名）。
        run.current_step 必须反映 data.decision_type。
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.base import PermissionLevel, ToolDefinition, ToolResult
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
        register_builtin_tools()

        registry = get_tool_registry()
        test_tool_name = "test_generic_decision_requested"

        def generic_decision_handler(ctx, inp):
            return ToolResult(
                status="success",
                summary="Blocked on manual research; needs user decision.",
                data={
                    "decision_requested": True,
                    "decision_type": "manual_research_blocked",
                    "decision_id": "dummy_decision_id",
                },
            )

        registry.register(ToolDefinition(
            name=test_tool_name,
            description="Stub: simulate a WRITE tool that requests a generic decision",
            parameters={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            permission_level=PermissionLevel.WRITE,
            handler=generic_decision_handler,
        ))

        import media_pilot.agent.tool_schema as ts
        original_whitelist = ts.FREEFORM_WRITE_TOOL_WHITELIST
        ts.FREEFORM_WRITE_TOOL_WHITELIST = frozenset({test_tool_name})

        try:
            with sf() as session:
                task = _make_task(
                    session,
                    status="library_import_complete",
                    current_step="library_import_complete",
                )
                task_id = task.id

            mock = MockLLMClient()
            mock.add_tool_calls([{
                "id": "call_generic",
                "type": "function",
                "function": {
                    "name": test_tool_name,
                    "arguments": f'{{"task_id": "{task_id}"}}',
                },
            }])

            with sf() as session:
                from media_pilot.agent.runner import run_agent_turn
                result = run_agent_turn(
                    session=session, config=config, task_id=task_id,
                    mode="freeform", mock_llm_client=mock,
                )
                session.commit()

            # Runner must pause with waiting_user, even though tool name
            # is not "request_user_decision".
            assert result.status == "waiting_user"
            assert result.tool_call_count == 1
        finally:
            ts.FREEFORM_WRITE_TOOL_WHITELIST = original_whitelist
            del registry._tools[test_tool_name]

    def test_freeform_write_tool_sets_library_import_complete_not_overridden(self, tmp_path):
        """When a freeform WRITE tool sets task to library_import_complete,
        the runner's final text response must NOT overwrite it to completed."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.base import PermissionLevel, ToolDefinition, ToolResult
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
        register_builtin_tools()

        registry = get_tool_registry()
        test_tool_name = "test_publish_like_tool"

        def publish_like_handler(ctx, inp):
            # Simulate a tool that publishes and sets task to library_import_complete
            from media_pilot.repository.repositories import IngestTaskRepository
            task_repo = IngestTaskRepository(ctx.session)
            task = task_repo.get(inp["task_id"])
            task_repo.update_status(task, status="library_import_complete",
                                    current_step="library_import_complete")
            return ToolResult(status="success", summary="Published to library")

        registry.register(ToolDefinition(
            name=test_tool_name,
            description="Simulates publish_movie_to_library",
            parameters={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            permission_level=PermissionLevel.WRITE,
            handler=publish_like_handler,
        ))

        import media_pilot.agent.tool_schema as ts
        original_whitelist = ts.FREEFORM_WRITE_TOOL_WHITELIST
        ts.FREEFORM_WRITE_TOOL_WHITELIST = frozenset({test_tool_name})

        try:
            with sf() as session:
                task = _make_task(
                    session,
                    status="library_import_complete",
                    current_step="library_import_complete",
                )
                task_id = task.id

            mock = MockLLMClient()
            mock.add_tool_calls([{
                "id": "call_publish",
                "type": "function",
                "function": {
                    "name": test_tool_name,
                    "arguments": f'{{"task_id": "{task_id}"}}',
                },
            }])
            mock.add_text_response("Publish complete. All done.")

            with sf() as session:
                from media_pilot.agent.runner import run_agent_turn
                result = run_agent_turn(
                    session=session, config=config, task_id=task_id,
                    mode="freeform", mock_llm_client=mock,
                )
                session.commit()

            assert result.status == "completed"
            assert result.tool_call_count == 1

            # Task status set by the tool must NOT be overwritten by runner
            with sf() as session:
                from media_pilot.repository.models import IngestTask
                task = session.get(IngestTask, task_id)
                assert task is not None
                assert task.status == "library_import_complete"
                assert task.current_step == "library_import_complete"
        finally:
            ts.FREEFORM_WRITE_TOOL_WHITELIST = original_whitelist
            del registry._tools[test_tool_name]

    def test_freeform_revoke_skip_task_state_preserved(self, tmp_path):
        """After revoke_publish(skip_post_revoke_decision=true) sets
        task to processing/post_revoke_reingest, the runner's final
        assistant text must not overwrite that business state."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.base import PermissionLevel, ToolDefinition, ToolResult
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
        register_builtin_tools()

        registry = get_tool_registry()
        test_tool_name = "test_revoke_skip_stateful"

        def revoke_skip_handler(ctx, inp):
            # Simulate execute_revoke_publish(skip_post_revoke_decision=True)
            # which sets task to processing + post_revoke_reingest
            from media_pilot.repository.repositories import IngestTaskRepository
            task_repo = IngestTaskRepository(ctx.session)
            task = task_repo.get(inp["task_id"])
            task_repo.update_status(task, status="processing",
                                    current_step="post_revoke_reingest")
            return ToolResult(
                status="success",
                summary="Revoked with skip, context cleaned, ready to re-ingest",
                data={"waiting_for_post_revoke_action": False, "status": "completed"},
            )

        registry.register(ToolDefinition(
            name=test_tool_name,
            description="Simulates revoke_publish with skip that sets processing",
            parameters={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            permission_level=PermissionLevel.WRITE,
            handler=revoke_skip_handler,
        ))

        import media_pilot.agent.tool_schema as ts
        original_whitelist = ts.FREEFORM_WRITE_TOOL_WHITELIST
        ts.FREEFORM_WRITE_TOOL_WHITELIST = frozenset({test_tool_name})

        try:
            with sf() as session:
                task = _make_task(
                    session,
                    status="library_import_complete",
                    current_step="library_import_complete",
                )
                task_id = task.id

            mock = MockLLMClient()
            mock.add_tool_calls([{
                "id": "call_revoke_skip",
                "type": "function",
                "function": {
                    "name": test_tool_name,
                    "arguments": f'{{"task_id": "{task_id}"}}',
                },
            }])
            mock.add_text_response("Revoke with skip done. Ready for reprocessing.")

            with sf() as session:
                from media_pilot.agent.runner import run_agent_turn
                result = run_agent_turn(
                    session=session, config=config, task_id=task_id,
                    mode="freeform", mock_llm_client=mock,
                )
                session.commit()

            assert result.status == "completed"
            assert result.tool_call_count == 1

            # Task state (processing) set by revoke+skip must NOT be overwritten
            with sf() as session:
                from media_pilot.repository.models import IngestTask
                task = session.get(IngestTask, task_id)
                assert task is not None
                assert task.status == "processing"
                assert task.current_step == "post_revoke_reingest"
        finally:
            ts.FREEFORM_WRITE_TOOL_WHITELIST = original_whitelist
            del registry._tools[test_tool_name]


# ══════════════════════════════════════════════════════════════════════
# LLM 工具结果上下文注入 — 防止 LLM 在 auto_confirm 后因看不到
# best_candidate.provider_id / media_type 而循环重读 candidates.
# ══════════════════════════════════════════════════════════════════════
