import pytest

from tests.agent_runner_helpers import MockLLMClient, _make_config, _make_task
from tests.auth_helpers import AuthenticatedTestClient as TestClient

# ══════════════════════════════════════════════════════════════════════
# AgentLLMClient
# ══════════════════════════════════════════════════════════════════════


class TestAgentLLMClient:
    def test_raises_when_api_key_missing(self, tmp_path):
        from media_pilot.config.settings import AppConfig

        config = AppConfig(
            database_dir=tmp_path,
            downloads_dir=tmp_path / "dl",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "ws",
            movies_dir=tmp_path / "movies",
            shows_dir=tmp_path / "shows",
        )
        from media_pilot.agent.llm_client import AgentLLMClient, LLMConfigurationError

        with pytest.raises(LLMConfigurationError, match="llm_api_key"):
            AgentLLMClient(config)

    def test_raises_when_base_url_missing(self, tmp_path):
        from media_pilot.config.settings import AppConfig

        config = AppConfig(
            database_dir=tmp_path,
            downloads_dir=tmp_path / "dl",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "ws",
            movies_dir=tmp_path / "movies",
            shows_dir=tmp_path / "shows",
            llm_api_key="sk-test",
        )
        from media_pilot.agent.llm_client import AgentLLMClient, LLMConfigurationError

        with pytest.raises(LLMConfigurationError, match="llm_base_url"):
            AgentLLMClient(config)

    def test_raises_when_model_missing(self, tmp_path):
        from media_pilot.config.settings import AppConfig

        config = AppConfig(
            database_dir=tmp_path,
            downloads_dir=tmp_path / "dl",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "ws",
            movies_dir=tmp_path / "movies",
            shows_dir=tmp_path / "shows",
            llm_api_key="sk-test",
            llm_base_url="https://test.example.com/v1",
        )
        from media_pilot.agent.llm_client import AgentLLMClient, LLMConfigurationError

        with pytest.raises(LLMConfigurationError, match="llm_model"):
            AgentLLMClient(config)

    def test_does_not_require_agent_specific_config(self, tmp_path):
        """Verify no Agent-dedicated LLM config keys are needed."""
        config = _make_config(tmp_path)
        from media_pilot.agent.llm_client import AgentLLMClient

        client = AgentLLMClient(config)
        assert client._model == "test-model"
        assert client._client.api_key == "test-key"
        assert str(client._client.base_url).rstrip("/") == "https://test.example.com/v1"


class TestAgentRunTransactionBoundaries:
    def test_initial_progress_is_committed_before_llm_call(self, tmp_path):
        """LLM 网络等待前, run/message/task 初始写入必须已提交可见。"""
        from media_pilot.agent.runner import run_agent_turn
        from media_pilot.repository.database import create_session_factory, initialize_database
        from media_pilot.repository.repositories import (
            AgentMessageRepository,
            AgentRunRepository,
            IngestTaskRepository,
        )

        config = _make_config(tmp_path)
        initialize_database(config)
        session_factory = create_session_factory(config)

        with session_factory() as session:
            task = _make_task(session)
            task_id = task.id

        observations: dict[str, object] = {}

        class InspectingLLM(MockLLMClient):
            def chat(self, messages, tools=None):
                with session_factory() as verify_session:
                    task_db = IngestTaskRepository(verify_session).get(task_id)
                    runs = AgentRunRepository(verify_session).list_by_task(task_id)
                    run = runs[0]
                    messages_db = AgentMessageRepository(
                        verify_session
                    ).list_by_run(run.id)
                    observations["task_status"] = task_db.status
                    observations["run_status"] = run.status
                    observations["message_count"] = len(messages_db)
                return super().chat(messages, tools)

        mock = InspectingLLM()
        mock.add_text_response("Done.")

        with session_factory() as session:
            result = run_agent_turn(
                session=session,
                config=config,
                task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"
        assert observations == {
            "task_status": "agent_running",
            "run_status": "active",
            "message_count": 1,
        }

    def test_tool_call_running_record_is_committed_before_handler(self, tmp_path, monkeypatch):
        """工具执行前, assistant/tool_call 记录必须已提交可见。"""
        import json

        from media_pilot.agent.runner import run_agent_turn
        from media_pilot.agent.tools.base import (
            PermissionLevel,
            ToolDefinition,
            ToolResult,
        )
        from media_pilot.agent.tools.registry import ToolRegistry
        from media_pilot.repository.database import create_session_factory, initialize_database
        from media_pilot.repository.repositories import AgentToolCallRepository

        config = _make_config(tmp_path)
        initialize_database(config)
        session_factory = create_session_factory(config)

        with session_factory() as session:
            task = _make_task(session)
            task_id = task.id

        observations: dict[str, object] = {}

        def _handler(ctx, _input):
            with session_factory() as verify_session:
                calls = AgentToolCallRepository(
                    verify_session
                ).list_by_run(ctx.run_id)
            observations["tool_call_count"] = len(calls)
            observations["tool_status"] = calls[0].status
            observations["tool_name"] = calls[0].tool_name
            return ToolResult(status="success", summary="ok")

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="inspect_committed_tool_call",
            description="Inspect committed tool call",
            parameters={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            permission_level=PermissionLevel.READ_ONLY,
            handler=_handler,
        ))

        import media_pilot.agent.runner as runner_mod

        monkeypatch.setattr(runner_mod, "get_tool_registry", lambda: registry)
        monkeypatch.setattr(runner_mod, "register_builtin_tools", lambda: None)

        mock = MockLLMClient()
        mock.add_tool_calls([{
            "id": "call_inspect",
            "type": "function",
            "function": {
                "name": "inspect_committed_tool_call",
                "arguments": json.dumps({"task_id": task_id}),
            },
        }])
        mock.add_text_response("Done.")

        with session_factory() as session:
            result = run_agent_turn(
                session=session,
                config=config,
                task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"
        assert observations == {
            "tool_call_count": 1,
            "tool_status": "running",
            "tool_name": "inspect_committed_tool_call",
        }


# ══════════════════════════════════════════════════════════════════════
# Tool Schema
# ══════════════════════════════════════════════════════════════════════


class TestToolSchema:
    def test_only_exposes_read_only_and_draft(self):
        from media_pilot.agent.tool_schema import get_allowed_tool_schemas
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

        register_builtin_tools()
        registry = get_tool_registry()
        schemas = get_allowed_tool_schemas(registry)
        names = {s["function"]["name"] for s in schemas}
        # All built-in READ_ONLY/DRAFT tools (default mode = no whitelist)
        assert names == {
            "get_task_context",
            "scan_task_files",
            "get_current_metadata",
            "search_metadata",
            "get_metadata_candidates",
            "draft_metadata_replacement",
            "draft_publish_plan",
            "request_user_decision",
            "get_auto_ingest_eligibility",
            "prepare_complex_input_decision",
            "prepare_select_metadata_candidate_decision",
            "prepare_show_structure",
        }

    def test_excludes_write_level_tool_in_default_mode(self):
        """Default mode must exclude WRITE tools even if registered."""
        from media_pilot.agent.tool_schema import get_allowed_tool_schemas
        from media_pilot.agent.tools.base import (
            PermissionLevel,
            ToolDefinition,
            ToolResult,
        )
        from media_pilot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="publish_movie_to_library",
            description="Publish movie",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            permission_level=PermissionLevel.WRITE,
            handler=lambda ctx, inp: ToolResult(status="success", summary="ok"),
        ))

        # Default mode excludes WRITE tools
        schemas = get_allowed_tool_schemas(registry, mode="default")
        names = {s["function"]["name"] for s in schemas}
        assert "publish_movie_to_library" not in names

        # get_allowed_tool_names also excludes WRITE in default mode
        from media_pilot.agent.tool_schema import get_allowed_tool_names
        allowed_names = get_allowed_tool_names(registry, mode="default")
        assert "publish_movie_to_library" not in allowed_names

    def test_auto_ingest_mode_exposes_whitelisted_write_tools(self):
        """Auto_ingest mode must expose whitelisted WRITE tools."""
        from media_pilot.agent.tool_schema import get_allowed_tool_names, get_allowed_tool_schemas
        from media_pilot.agent.tools.base import (
            PermissionLevel,
            ToolDefinition,
            ToolResult,
        )
        from media_pilot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        # Use a unique name not in the global whitelist
        write_tool = ToolDefinition(
            name="test_custom_write_tool",
            description="Custom write tool",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            permission_level=PermissionLevel.WRITE,
            handler=lambda ctx, inp: ToolResult(status="success", summary="ok"),
        )
        registry.register(write_tool)

        # Without whitelist entry, auto_ingest excludes the WRITE tool
        schemas = get_allowed_tool_schemas(registry, mode="auto_ingest")
        names = {s["function"]["name"] for s in schemas}
        assert "test_custom_write_tool" not in names

        # Patch the whitelist to include it
        import media_pilot.agent.tool_schema as ts
        original = ts.AUTO_INGEST_WRITE_TOOL_WHITELIST
        try:
            ts.AUTO_INGEST_WRITE_TOOL_WHITELIST = frozenset({"test_custom_write_tool"})

            schemas = get_allowed_tool_schemas(registry, mode="auto_ingest")
            names = {s["function"]["name"] for s in schemas}
            assert "test_custom_write_tool" in names

            allowed_names = get_allowed_tool_names(registry, mode="auto_ingest")
            assert "test_custom_write_tool" in allowed_names

        finally:
            ts.AUTO_INGEST_WRITE_TOOL_WHITELIST = original

    def test_auto_ingest_excludes_non_whitelisted_write(self):
        """Auto_ingest mode must not expose WRITE tools not in the whitelist."""
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
            handler=lambda ctx, inp: ToolResult(status="success", summary="ok"),
        ))

        schemas = get_allowed_tool_schemas(registry, mode="auto_ingest")
        names = {s["function"]["name"] for s in schemas}
        assert "dangerous_delete" not in names

    def test_auto_ingest_exposes_publish_show_to_library(self):
        """publish_show_to_library is whitelisted for auto_ingest mode.

        Show 发布工具必须能在 auto_ingest runner 模式中被 LLM 调用;
        否则同季连续多集剧集在 publish 阶段会因为 schema 中没有该工具
        而被迫走 freeform 通道, 违背 auto_ingest 主线意图.
        """
        from media_pilot.agent.tool_schema import (
            AUTO_INGEST_WRITE_TOOL_WHITELIST,
            get_allowed_tool_names,
            get_allowed_tool_schemas,
        )
        from media_pilot.agent.tools.registry import (
            get_tool_registry,
            register_builtin_tools,
        )

        # whitelist 静态包含
        assert "publish_show_to_library" in AUTO_INGEST_WRITE_TOOL_WHITELIST

        # 注册所有内置工具后, auto_ingest schema 必须包含该工具
        register_builtin_tools()
        registry = get_tool_registry()

        schemas = get_allowed_tool_schemas(registry, mode="auto_ingest")
        names = {s["function"]["name"] for s in schemas}
        assert "publish_show_to_library" in names

        allowed_names = get_allowed_tool_names(registry, mode="auto_ingest")
        assert "publish_show_to_library" in allowed_names

        # schema 的 permission 描述是 WRITE, 符合 whitelist 预期
        show_schema = next(
            s for s in schemas if s["function"]["name"] == "publish_show_to_library"
        )
        # 工具定义的 description 字段含 "publish" / "show" 关键词, 防止
        # 误把同名占位工具加进白名单.
        desc = show_schema["function"]["description"].lower()
        assert "show" in desc

    def test_publish_show_to_library_default_mode_excluded(self):
        """publish_show_to_library 在 default 模式不可见, 避免越权发布."""
        from media_pilot.agent.tool_schema import get_allowed_tool_names
        from media_pilot.agent.tools.registry import (
            get_tool_registry,
            register_builtin_tools,
        )

        register_builtin_tools()
        registry = get_tool_registry()

        names = get_allowed_tool_names(registry, mode="default")
        assert "publish_show_to_library" not in names

    def test_runner_can_dispatch_publish_show_to_library(self, tmp_path):
        """Auto_ingest runner 必须能调用 publish_show_to_library 工具.

        验证 schema → 工具名分发闭环: tool_schema 把
        publish_show_to_library 暴露给 LLM 后, runner.register_tool_calls
        阶段能根据 name 找到对应 handler 并落库 AgentToolCall.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, media_type="show")
            task_id = task.id

        mock = MockLLMClient()
        mock.add_tool_calls([{
            "id": "call_show_pub",
            "type": "function",
            "function": {
                "name": "publish_show_to_library",
                "arguments": '{"task_id": "' + task_id + '"}',
            },
        }])
        mock.add_text_response("Show published.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        # 工具成功分发, runner 完成了 1 次 tool_call.
        assert result.tool_call_count == 1
        # 缺 EpisodeMapping / MetadataDetail 时, handler 应当返回 failure
        # 但 runner 不应崩溃. AgentToolCall 状态是 success / failure 都可,
        # 关键是不能 _unhandled_ (runner 报 unknown tool).
        from media_pilot.repository.repositories import (
            AgentRunRepository,
            AgentToolCallRepository,
        )
        with sf() as session:
            runs = AgentRunRepository(session).list_by_task(task_id)
            assert len(runs) >= 1
            run = runs[0]
            tc = AgentToolCallRepository(session).list_by_run(run.id)
        assert len(tc) == 1
        assert tc[0].tool_name == "publish_show_to_library"


# ══════════════════════════════════════════════════════════════════════
# Runner Loop
# ══════════════════════════════════════════════════════════════════════


class TestRunnerBasicFlow:
    def test_completes_on_final_text_response(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        mock.add_text_response("Task analysis complete. No further actions needed.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"
        assert result.tool_call_count == 0
        # Exactly 2 messages: initial user + assistant
        assert result.message_count == 2

    def test_executes_tool_calls_and_continues(self, tmp_path):
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
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "get_task_context",
                "arguments": '{"task_id": "' + task_id + '"}',
            },
        }])
        mock.add_text_response("Got task context. Done.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"
        assert result.tool_call_count == 1

    def test_tool_failure_increments_counter(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        # Call a real tool with bad args → failure
        mock.add_tool_calls([{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "get_task_context",
                "arguments": '{}',  # missing required task_id
            },
        }])
        mock.add_text_response("Tool failed, but I'll continue.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"

    def test_rejects_unallowed_tool_name(self, tmp_path):
        """Tool names not in READ_ONLY/DRAFT must be recorded as failed AgentToolCall."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        # LLM requests a tool that doesn't exist / isn't allowed
        mock.add_tool_calls([{
            "id": "call_bad",
            "type": "function",
            "function": {
                "name": "delete_everything",
                "arguments": '{}',
            },
        }])
        mock.add_text_response("Done.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        # Run should complete (failure counted but not enough to hit limit)
        assert result.status == "completed"
        assert result.tool_call_count == 1

        # Verify the unallowed tool was recorded as failed
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
                AgentRunRepository,
                AgentToolCallRepository,
            )
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            tcs = AgentToolCallRepository(session).list_by_run(run.id)
            assert len(tcs) == 1
            assert tcs[0].tool_name == "delete_everything"
            assert tcs[0].status == "failed"
            assert "not in the allowed" in (tcs[0].error_message or "")

            # Tool message must be persisted for LLM context
            messages = AgentMessageRepository(session).list_by_run(run.id)
            tool_msgs = [m for m in messages if m.role == "tool"]
            assert len(tool_msgs) == 1
            assert "failure" in (tool_msgs[0].content or "")

    def test_default_mode_rejects_write_tool(self, tmp_path):
        """In default mode, WRITE tools must be rejected even if registered."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        # Register a WRITE-level tool
        from media_pilot.agent.tools.base import PermissionLevel, ToolDefinition, ToolResult
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
        register_builtin_tools()
        registry = get_tool_registry()
        test_tool_name = "test_default_mode_write_tool"
        registry.register(ToolDefinition(
            name=test_tool_name,
            description="Persist metadata selection",
            parameters={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"], "additionalProperties": False},
            permission_level=PermissionLevel.WRITE,
            handler=lambda ctx, inp: ToolResult(status="success", summary="ok"),
        ))
        try:
            with sf() as session:
                task = _make_task(session)
                task_id = task.id

            mock = MockLLMClient()
            mock.add_tool_calls([{
                "id": "call_write",
                "type": "function",
                "function": {
                    "name": test_tool_name,
                    "arguments": '{"task_id": "' + task_id + '"}',
                },
            }])
            mock.add_text_response("Done.")

            with sf() as session:
                from media_pilot.agent.runner import run_agent_turn
                result = run_agent_turn(
                    session=session, config=config, task_id=task_id,
                    mode="default", mock_llm_client=mock,
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
                assert "not in the allowed tool set for mode 'default'" in (tcs[0].error_message or "")
        finally:
            del registry._tools[test_tool_name]

    def test_auto_ingest_mode_allows_whitelisted_write_tool(self, tmp_path):
        """In auto_ingest mode, whitelisted WRITE tools must be executable."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.base import PermissionLevel, ToolDefinition, ToolResult
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
        register_builtin_tools()

        # Register a WRITE tool
        registry = get_tool_registry()
        test_tool_name = "test_auto_ingest_write_tool"
        registry.register(ToolDefinition(
            name=test_tool_name,
            description="Persist metadata selection",
            parameters={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"], "additionalProperties": False},
            permission_level=PermissionLevel.WRITE,
            handler=lambda ctx, inp: ToolResult(
                status="success",
                summary=f"Persisted metadata selection for {inp['task_id']}",
            ),
        ))

        # Whitelist it
        import media_pilot.agent.tool_schema as ts
        original_whitelist = ts.AUTO_INGEST_WRITE_TOOL_WHITELIST
        ts.AUTO_INGEST_WRITE_TOOL_WHITELIST = frozenset({test_tool_name})

        try:
            with sf() as session:
                task = _make_task(session)
                task_id = task.id

            mock = MockLLMClient()
            mock.add_tool_calls([{
                "id": "call_write",
                "type": "function",
                "function": {
                    "name": test_tool_name,
                    "arguments": '{"task_id": "' + task_id + '"}',
                },
            }])
            mock.add_text_response("Persisted. Done.")

            with sf() as session:
                from media_pilot.agent.runner import run_agent_turn
                result = run_agent_turn(
                    session=session, config=config, task_id=task_id,
                    mode="auto_ingest", mock_llm_client=mock,
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
                assert tcs[0].output is not None
                assert tcs[0].output["status"] == "success"

        finally:
            ts.AUTO_INGEST_WRITE_TOOL_WHITELIST = original_whitelist
            del registry._tools[test_tool_name]

    def test_auto_ingest_mode_rejects_non_whitelisted_write_tool(self, tmp_path):
        """In auto_ingest mode, non-whitelisted WRITE tools must be hard-rejected."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.base import PermissionLevel, ToolDefinition, ToolResult
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
        register_builtin_tools()

        registry = get_tool_registry()
        test_tool_name = "test_auto_ingest_bad_write"
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
                    mode="auto_ingest", mock_llm_client=mock,
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
                assert "not in the allowed tool set for mode 'auto_ingest'" in (tcs[0].error_message or "")
        finally:
            del registry._tools[test_tool_name]

    def test_runner_auto_registers_builtin_tools(self, tmp_path):
        """Runner must auto-register built-in tools so LLM receives them without manual setup."""
        from media_pilot.agent.tools.registry import get_tool_registry
        from tests.test_api_v1 import _make_session_factory

        # Clear the global singleton so the runner must register from scratch
        r = get_tool_registry()
        old_tools = dict(r._tools)
        r._tools.clear()
        try:
            sf = _make_session_factory(tmp_path)
            config = _make_config(tmp_path)
            with sf() as session:
                task = _make_task(session)
                task_id = task.id

            mock = MockLLMClient()
            mock.add_text_response("Done.")

            with sf() as session:
                from media_pilot.agent.runner import run_agent_turn
                run_agent_turn(
                    session=session, config=config, task_id=task_id,
                    mock_llm_client=mock,
                )
                session.commit()

            # The mock LLM must have received non-empty tools from the runner
            assert len(mock.calls) >= 1
            tools_sent = mock.calls[0]["tools"]
            assert len(tools_sent) > 0
            tool_names = {t["function"]["name"] for t in tools_sent}
            assert "get_task_context" in tool_names
            assert "scan_task_files" in tool_names
        finally:
            r._tools = old_tools


class TestRunnerLimits:
    def test_fails_on_max_steps(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        # Keep returning tool calls to exhaust max_steps
        for _ in range(15):
            mock.add_tool_calls([{
                "id": "call_x",
                "type": "function",
                "function": {
                    "name": "get_task_context",
                    "arguments": '{"task_id": "' + task_id + '"}',
                },
            }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "failed"
        assert "max_steps" in result.error_message or "Exceeded max_steps" in (result.error_message or "")

        # Verify task is agent_failed
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "agent_failed"

    def test_fails_on_max_tool_failures(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        # Each step: one failing tool call (bad args).
        # No text responses in between — just tool_calls repeatedly until failure limit.
        for _ in range(5):
            mock.add_tool_calls([{
                "id": "call_fail",
                "type": "function",
                "function": {
                    "name": "get_task_context",
                    "arguments": '{}',  # invalid — missing task_id
                },
            }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "failed"
        assert "tool failure" in (result.error_message or "").lower()

    def test_fails_on_llm_api_error(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        mock.raise_error = RuntimeError("Connection timeout")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "failed"
        assert "Connection timeout" in (result.error_message or "")

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "agent_failed"


class TestRunnerPersistence:
    def test_system_prompt_not_persisted(self, tmp_path):
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

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(session=session, config=config, task_id=task_id, mock_llm_client=mock)
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
                AgentRunRepository,
            )
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            messages = AgentMessageRepository(session).list_by_run(run.id)
            for msg in messages:
                # system prompt must never appear as a persisted message
                assert msg.role != "system"
                if msg.content:
                    assert "You are Media Pilot" not in msg.content

    def test_messages_persisted_in_order(self, tmp_path):
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
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "get_task_context",
                "arguments": '{"task_id": "' + task_id + '"}',
            },
        }])
        mock.add_text_response("All done.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(session=session, config=config, task_id=task_id, mock_llm_client=mock)
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
                AgentRunRepository,
            )
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            messages = AgentMessageRepository(session).list_by_run(run.id)
            roles = [m.role for m in messages]
            # Expected: user → assistant (tool_calls) → tool → assistant (final)
            assert roles[0] == "user"
            assert "assistant" in roles
            assert "tool" in roles
            assert roles[-1] == "assistant"

    def test_tool_call_recorded_with_output_and_duration(self, tmp_path):
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
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "get_task_context",
                "arguments": '{"task_id": "' + task_id + '"}',
            },
        }])
        mock.add_text_response("Done.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(session=session, config=config, task_id=task_id, mock_llm_client=mock)
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                AgentToolCallRepository,
            )
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            tcs = AgentToolCallRepository(session).list_by_run(run.id)
            assert len(tcs) == 1
            assert tcs[0].tool_name == "get_task_context"
            assert tcs[0].status == "completed"
            assert tcs[0].output is not None
            assert tcs[0].output["status"] == "success"
            assert tcs[0].duration_ms is not None
            assert tcs[0].duration_ms >= 0

    def test_run_marked_completed_on_final_response(self, tmp_path):
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

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(session=session, config=config, task_id=task_id, mock_llm_client=mock)
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import AgentRunRepository
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            assert run.status == "completed"
            assert run.error_message is None

    def test_message_count_matches_persisted_messages(self, tmp_path):
        """AgentRunResult.message_count must reflect actual persisted message count."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        # Simulate a multi-turn: tool call → tool response → final text
        mock = MockLLMClient()
        mock.add_tool_calls([{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "get_task_context",
                "arguments": '{"task_id": "' + task_id + '"}',
            },
        }])
        mock.add_text_response("All done after tool call.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        # Verify result.message_count equals actual DB count
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
                AgentRunRepository,
            )
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            actual_count = len(AgentMessageRepository(session).list_by_run(run.id))
            # Messages: user, assistant(tool_calls), tool, assistant(final) = 4
            assert actual_count == 4
            assert result.message_count == actual_count

    def test_run_marked_failed_on_config_error(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        from media_pilot.config.settings import AppConfig

        config = AppConfig(
            database_dir=tmp_path,
            downloads_dir=tmp_path / "dl",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "ws",
            movies_dir=tmp_path / "movies",
            shows_dir=tmp_path / "shows",
            # No LLM config
        )
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
            )
            session.commit()

        assert result.status == "failed"
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "agent_failed"

    def test_no_decision_request_created(self, tmp_path):
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

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(session=session, config=config, task_id=task_id, mock_llm_client=mock)
            session.commit()

        with sf() as session:
            from sqlalchemy import func, select

            from media_pilot.repository.models import AgentDecisionRequest
            from media_pilot.repository.repositories import AgentRunRepository
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            assert run.status == "completed"
            assert run.status != "waiting_user"
            dr_count = session.scalar(
                select(func.count()).select_from(AgentDecisionRequest).where(
                    AgentDecisionRequest.run_id == run.id,
                    AgentDecisionRequest.status == "pending",
                )
            )
            assert dr_count == 0

    def test_no_waiting_user_status_created(self, tmp_path):
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
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "get_task_context",
                "arguments": '{"task_id": "' + task_id + '"}',
            },
        }])
        mock.add_text_response("Done.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(session=session, config=config, task_id=task_id, mock_llm_client=mock)
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import AgentRunRepository
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            assert run.status != "waiting_user"


# ══════════════════════════════════════════════════════════════════════
# API endpoint
# ══════════════════════════════════════════════════════════════════════


class TestAgentRunAPI:
    def test_returns_409_when_active_run_exists(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        # Create an active run first
        with sf() as session:
            from media_pilot.repository.repositories import AgentRunCreate, AgentRunRepository
            AgentRunRepository(session).create(AgentRunCreate(task_id=task_id))
            session.commit()

        # Now try to create another via API

        from media_pilot.app import create_app

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        resp = client.post(f"/api/v1/tasks/{task_id}/agent-runs")
        assert resp.status_code == 409

    def test_returns_404_when_task_not_found(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)


        from media_pilot.app import create_app

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        resp = client.post("/api/v1/tasks/nonexistent-id/agent-runs")
        assert resp.status_code == 404

    def test_successful_run_returns_summary(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        # We need to inject the mock LLM. Since the API creates its own session,
        # we can't easily inject the mock client. We'll test via the runner directly
        # for mock-based testing and use the API test only for 404/409.
        # This test verifies the API returns the correct structure.
        mock = MockLLMClient()
        mock.add_text_response("Task analysis done.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"
        assert result.run_id
        assert result.message_count >= 2


# ══════════════════════════════════════════════════════════════════════
# Agent Retry (Section 6)
# ══════════════════════════════════════════════════════════════════════


class TestAgentRetry:
    def test_run_agent_turn_accepts_initial_message(self, tmp_path):
        """run_agent_turn must use custom initial_message when provided."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        mock.add_text_response("Recovery done.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
                initial_message="This is a recovery retry for the failed task.",
            )
            session.commit()

        assert result.status == "completed"

        # Verify the custom message was persisted
        with sf() as session:
            from media_pilot.repository.repositories import AgentMessageRepository
            msgs = AgentMessageRepository(session).list_by_run(result.run_id)
            user_msgs = [m for m in msgs if m.role == "user"]
            assert len(user_msgs) == 1
            assert "recovery retry" in user_msgs[0].content

    def test_retry_agent_failed_task_creates_new_run(self, tmp_path):
        """Retrying an agent_failed task must create a new AgentRun."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, status="agent_failed", current_step="agent_failed")
            task_id = task.id

            # Add a failed previous run
            from media_pilot.repository.models import AgentRun
            failed_run = AgentRun(task_id=task.id, status="failed")
            session.add(failed_run)
            session.commit()

        mock = MockLLMClient()
        mock.add_text_response("Retry completed successfully.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
                initial_message="Retry recovery message.",
            )
            session.commit()

        assert result.status == "completed"

        # Verify task status: non-freeform modes leave task lifecycle to tools,
        # so the task remains "agent_running" (set by run_agent_turn before loop).
        # In a real auto_ingest run, publish_movie_to_library would set it to
        # "library_import_complete".
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "agent_running"

    def test_retry_with_recovery_message_uses_auto_ingest_mode(self, tmp_path):
        """Retry should use auto_ingest mode so WRITE tools are available."""
        import json

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, status="agent_failed", current_step="agent_failed")
            task_id = task.id

        mock = MockLLMClient()
        # Try to use a whitelisted WRITE tool
        mock.add_tool_calls([{
            "id": "call_persist",
            "type": "function",
            "function": {
                "name": "persist_metadata_selection",
                "arguments": json.dumps({
                    "task_id": task_id,
                    "provider_name": "tmdb",
                    "provider_id": "123",
                    "media_type": "movie",
                    "title": "Test Movie",
                }),
            },
        }])
        mock.add_text_response("Metadata persisted, task advanced.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
                initial_message="Retry recovery message.",
            )
            session.commit()

        # safety net 介入: agent 调 persist_metadata_selection 创建 candidate,
        # 但停在 final-text 没继续 fetch + publish. auto_ingest mode 下
        # safety net 用 fake provider_id="123" 尝试 fetch 失败 → run.status=failed.
        # 本测试关心 retry 走 auto_ingest mode (WRITE 工具可用) 而不是
        # 最终是否 publish 成功.
        assert result.status == "failed"
        assert result.tool_call_count == 1

    def test_retry_fails_when_active_run_exists(self, tmp_path):
        """Retry must raise ValueError when an active AgentRun exists for the task."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, status="agent_running")
            task_id = task.id

            from media_pilot.repository.repositories import AgentRunCreate, AgentRunRepository
            AgentRunRepository(session).create(AgentRunCreate(task_id=task_id))
            session.commit()

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            with pytest.raises(ValueError, match="active"):
                run_agent_turn(
                    session=session, config=config, task_id=task_id,
                    mock_llm_client=MockLLMClient(),
                )


# ══════════════════════════════════════════════════════════════════════
# Agent Prompt & Decision Behavior (Section 7)
# ══════════════════════════════════════════════════════════════════════


class TestAutoIngestPromptBehavior:
    """Mock LLM tests for auto-ingest prompt and decision behavior."""

    def test_happy_path_single_movie_metadata(self, tmp_path):
        """Happy path: single-file movie — Agent inspects, searches, persists metadata."""
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
        # Step 1: get task context
        mock.add_tool_calls([{
            "id": "call_ctx",
            "type": "function",
            "function": {"name": "get_task_context", "arguments": json.dumps({"task_id": task_id})},
        }])
        # Step 2: scan task files
        mock.add_tool_calls([{
            "id": "call_scan",
            "type": "function",
            "function": {"name": "scan_task_files", "arguments": json.dumps({"task_id": task_id})},
        }])
        # Step 3: search metadata with clear winner
        mock.add_tool_calls([{
            "id": "call_search",
            "type": "function",
            "function": {"name": "search_metadata", "arguments": json.dumps({"keyword": "Test Movie 2026"})},
        }])
        # Step 4: persist the winning metadata selection
        mock.add_tool_calls([{
            "id": "call_persist",
            "type": "function",
            "function": {"name": "persist_metadata_selection", "arguments": json.dumps({
                "task_id": task_id, "provider_name": "tmdb", "provider_id": "123",
                "media_type": "movie", "title": "Test Movie", "year": 2026,
                "confidence": 0.95,
            })},
        }])
        # Step 5: Agent provides final summary (no more tool calls)
        mock.add_text_response(
            "Metadata for 'Test Movie (2026)' has been persisted. "
            "The task is ready for metadata detail fetch and library publish."
        )

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
            )
            session.commit()

        # safety net 介入: agent 持久化 candidate 后停在 final-text 没继续
        # fetch + publish. auto_ingest mode 下 safety net 用 fake provider_id
        # 尝试 fetch 失败 → run.status=failed. 本测试关心 happy path 的
        # 工具调用序列和 candidate 持久化, 而非最终 publish 状态.
        assert result.status == "failed"
        assert result.tool_call_count == 4

        # Verify metadata was persisted
        with sf() as session:
            from media_pilot.repository.repositories import MediaCandidateRepository
            candidates = MediaCandidateRepository(session).list_for_task(task_id)
            agent_candidates = [c for c in candidates if c.source == "agent"]
            assert len(agent_candidates) == 1
            assert agent_candidates[0].title == "Test Movie"
            assert agent_candidates[0].year == 2026

    def test_low_confidence_requests_decision(self, tmp_path):
        """When confidence is below threshold, Agent should request user decision."""
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
        # Step 1: get task context
        mock.add_tool_calls([{
            "id": "call_ctx",
            "type": "function",
            "function": {"name": "get_task_context", "arguments": json.dumps({"task_id": task_id})},
        }])
        # Step 2: scan task files
        mock.add_tool_calls([{
            "id": "call_scan",
            "type": "function",
            "function": {"name": "scan_task_files", "arguments": json.dumps({"task_id": task_id})},
        }])
        # Step 3: request decision (low confidence scenario)
        mock.add_tool_calls([{
            "id": "call_dec",
            "type": "function",
            "function": {"name": "request_user_decision", "arguments": json.dumps({
                "decision_type": "metadata_confirmation",
                "question": "Low confidence on metadata match. Please review.",
                "options": [
                    {"id": "opt1", "label": "Candidate A (confidence: 0.45)"},
                    {"id": "opt2", "label": "Candidate B (confidence: 0.40)"},
                ],
            })},
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "waiting_user"
        assert result.tool_call_count >= 1

    def test_target_conflict_requests_decision(self, tmp_path):
        """When Agent determines a complex situation, it should request user decision."""
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
        # Step 1: get task context
        mock.add_tool_calls([{
            "id": "call_ctx",
            "type": "function",
            "function": {"name": "get_task_context", "arguments": json.dumps({"task_id": task_id})},
        }])
        # Step 2: scan task files — discovers it's a complex directory
        mock.add_tool_calls([{
            "id": "call_scan",
            "type": "function",
            "function": {"name": "scan_task_files", "arguments": json.dumps({"task_id": task_id})},
        }])
        # Step 3: request decision about complex input (e.g. multiple videos or ISO detected)
        mock.add_tool_calls([{
            "id": "call_dec",
            "type": "function",
            "function": {"name": "request_user_decision", "arguments": json.dumps({
                "decision_type": "manual_intervention_required",
                "question": "This task appears to be a complex directory or non-movie input. Manual review required.",
                "options": [
                    {"id": "review", "label": "I will review manually"},
                    {"id": "cancel", "label": "Skip this task"},
                ],
            })},
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "waiting_user"

    def test_max_tool_failures_triggers_agent_failed(self, tmp_path):
        """When max tool failures reached, Agent should fail gracefully."""

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        # Send 3 tool calls that will all fail (invalid tool name)
        for i in range(3):
            mock.add_tool_calls([{
                "id": f"call_bad_{i}",
                "type": "function",
                "function": {"name": "nonexistent_tool", "arguments": "{}"},
            }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "failed"
        assert result.error_message is not None
        assert "Max tool failures" in result.error_message

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "agent_failed"

# ══════════════════════════════════════════════════════════════════════
# Show Path — Complex Input → Show Structure Integration
# ══════════════════════════════════════════════════════════════════════


class TestShowPathIntegration:
    """Show 输入在复杂电影门禁阶段不被拦截, 直接进入剧集结构分析.

    验证关键路径:
    1. 同季连续多集 SxxExx 目录触发 prepare_complex_input_decision
       后, 工具返回 ready=true + is_show=true, 不创建任何决策.
    2. LLM 据此调用 prepare_show_structure, 工具返回 auto_publishable.
    3. 整条链路在同一次 run_agent_turn 周期内不创建
       review_complex_input / select_primary_video 决策.
    """

    def _setup_show_dir(self, tmp_path):
        """构造一个同季连续多集 SxxExx 目录, 落到 config.downloads_dir."""
        import shutil

        config = _make_config(tmp_path)
        # 每次清空 downloads_dir 避免上一轮用例残留.
        if config.downloads_dir.exists():
            shutil.rmtree(config.downloads_dir)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        show_dir = config.downloads_dir / "Example.Show.S01.1080p"
        show_dir.mkdir()
        (show_dir / "Example.Show.S01E01.mkv").write_bytes(b"ep1" * 1024)
        (show_dir / "Example.Show.S01E02.mkv").write_bytes(b"ep2" * 1024)
        (show_dir / "Example.Show.S01E03.mkv").write_bytes(b"ep3" * 1024)
        return config, show_dir

    def test_complex_input_passes_show_like_to_show_structure(
        self, tmp_path,
    ):
        """完整 Agent runner 集成: 多集 SxxExx 目录走
        prepare_complex_input_decision → is_show=true, 继续调
        prepare_show_structure → auto_publishable, 不得创建
        review_complex_input 决策."""
        import json

        from media_pilot.repository.repositories import (
            AgentDecisionRequestRepository,
            AgentRunRepository,
            AgentToolCallRepository,
        )
        from tests.test_api_v1 import _make_session_factory

        config, show_dir = self._setup_show_dir(tmp_path)
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(
                session, source_path=str(show_dir), media_type="show",
            )
            task_id = task.id

        mock = MockLLMClient()
        # 1. LLM 先调 prepare_complex_input_decision; 工具应该返回
        #    is_show=true, 不创建决策.
        mock.add_tool_calls([{
            "id": "call_complex",
            "type": "function",
            "function": {
                "name": "prepare_complex_input_decision",
                "arguments": json.dumps({"task_id": task_id}),
            },
        }])
        # 2. 拿到 is_show=true 后, LLM 接着调 prepare_show_structure.
        mock.add_tool_calls([{
            "id": "call_show",
            "type": "function",
            "function": {
                "name": "prepare_show_structure",
                "arguments": json.dumps({"task_id": task_id}),
            },
        }])
        # 3. 收尾: 给一个 text response 让 runner 进入 completed.
        mock.add_text_response("Show structure ready, single-season continuous.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
            )
            session.commit()

        # 两次工具调用都成功.
        assert result.tool_call_count == 2

        with sf() as session:
            runs = AgentRunRepository(session).list_by_task(task_id)
            run = runs[0]
            tcs = AgentToolCallRepository(session).list_by_run(run.id)
            drs = AgentDecisionRequestRepository(session).list_pending_by_run(run.id)

        # 工具调用顺序: complex → show_structure.
        tool_names = [tc.tool_name for tc in tcs]
        assert "prepare_complex_input_decision" in tool_names
        assert "prepare_show_structure" in tool_names

        # 关键断言: 没有创建 review_complex_input / select_primary_video /
        # select_subtitles 决策. 这是 Issue 2 修复的判定.
        assert drs == [], (
            f"show-like 目录在 complex 阶段不应创建决策, 实际: "
            f"{[dr.decision_type for dr in drs]}"
        )

    def test_auto_ingest_rezero_absolute_numbering_recovers_after_detail(
        self, tmp_path, monkeypatch,
    ):
        import json
        from dataclasses import replace

        from media_pilot.agent.tools.base import ToolResult
        from media_pilot.agent.tools.registry import (
            get_tool_registry,
            register_builtin_tools,
        )
        from media_pilot.adapters.metadata import MetadataDetail
        from media_pilot.repository.models import AgentToolCall
        from media_pilot.repository.repositories import (
            AgentRunRepository,
            EpisodeMappingRepository,
            IngestTaskRepository,
            MetadataDetailRepository,
        )
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        rezero_dir = config.downloads_dir / "ReZero 3rd Season"
        rezero_dir.mkdir(parents=True, exist_ok=True)
        for n in range(51, 67):
            (rezero_dir / f"[{n}].mkv").write_bytes(b"ep")

        with sf() as session:
            register_builtin_tools()
            task = _make_task(
                session,
                source_path=str(rezero_dir),
                media_type=None,
                status="discovered",
            )
            task_id = task.id

        registry = get_tool_registry()

        def _fake_search(ctx, inp):
            return ToolResult(
                status="success",
                summary="1 candidate",
                data={
                    "candidates": [{
                        "candidate_id": "cand-rezero",
                        "provider": "tmdb",
                        "provider_id": "show:rezero",
                        "external_id": "show:rezero",
                        "media_type": "show",
                        "title": "Re:Zero",
                        "year": 2016,
                        "confidence": 0.97,
                    }],
                    "has_clear_winner": True,
                    "best_candidate": {
                        "candidate_id": "cand-rezero",
                        "provider": "tmdb",
                        "provider_id": "show:rezero",
                        "external_id": "show:rezero",
                        "media_type": "show",
                        "title": "Re:Zero",
                        "year": 2016,
                        "confidence": 0.97,
                    },
                    "confidence_threshold": 0.9,
                    "margin": 0.3,
                },
            )

        def _fake_fetch(ctx, inp):
            MetadataDetailRepository(ctx.session).save(
                task_id=inp["task_id"],
                provider=inp["provider_name"],
                provider_id=inp["provider_id"],
                media_type="show",
                title="Re:Zero",
                original_title="Re:Zero",
                year=2016,
                payload={
                    "plot": "x",
                    "genres": ["Animation"],
                    "studios": ["White Fox"],
                    "directors": [],
                    "actors": [],
                    "images": {
                        "poster_url": None,
                        "backdrop_url": None,
                        "logo_url": None,
                    },
                    "external_ids": {"imdb_id": None},
                    "raw": {
                        "seasons": [
                            {"season_number": 1, "episode_count": 66},
                        ]
                    },
                },
            )
            return ToolResult(
                status="success",
                summary="detail saved",
                data={"provider": "tmdb", "provider_id": "show:rezero"},
            )

        monkeypatch.setitem(
            registry._tools,
            "search_metadata",
            replace(registry._tools["search_metadata"], handler=_fake_search),
        )
        monkeypatch.setitem(
            registry._tools,
            "fetch_and_save_metadata_detail",
            replace(
                registry._tools["fetch_and_save_metadata_detail"],
                handler=_fake_fetch,
            ),
        )

        class _StubWriteResult:
            status = "succeeded"
            warnings: list[str] = []

        from media_pilot.orchestration import jellyfin_show_writer

        def _fake_execute_show_write(
            session, *, task_id, detail, plan, client, progress_callback=None,
            provider="tmdb", force_overwrite=False,
        ):
            return _StubWriteResult()

        monkeypatch.setattr(
            jellyfin_show_writer,
            "execute_show_write",
            _fake_execute_show_write,
        )

        mock = MockLLMClient()
        mock.add_tool_calls([{
            "id": "call_complex",
            "type": "function",
            "function": {
                "name": "prepare_complex_input_decision",
                "arguments": json.dumps({"task_id": task_id}),
            },
        }])
        mock.add_tool_calls([{
            "id": "call_show",
            "type": "function",
            "function": {
                "name": "prepare_show_structure",
                "arguments": json.dumps({"task_id": task_id}),
            },
        }])
        mock.add_tool_calls([{
            "id": "call_search",
            "type": "function",
            "function": {
                "name": "search_metadata",
                "arguments": json.dumps({
                    "task_id": task_id,
                    "keyword": "Re:Zero",
                    "provider": "tmdb",
                    "media_type": "show",
                }),
            },
        }])
        mock.add_tool_calls([{
            "id": "call_persist",
            "type": "function",
            "function": {
                "name": "persist_metadata_selection",
                "arguments": json.dumps({
                    "task_id": task_id,
                    "provider_name": "tmdb",
                    "provider_id": "show:rezero",
                    "media_type": "show",
                    "title": "Re:Zero",
                    "year": 2016,
                    "confidence": 0.97,
                }),
            },
        }])
        mock.add_tool_calls([{
            "id": "call_fetch",
            "type": "function",
            "function": {
                "name": "fetch_and_save_metadata_detail",
                "arguments": json.dumps({
                    "task_id": task_id,
                    "provider_name": "tmdb",
                    "provider_id": "show:rezero",
                    "media_type": "show",
                }),
            },
        }])
        mock.add_tool_calls([{
            "id": "call_publish",
            "type": "function",
            "function": {
                "name": "publish_show_to_library",
                "arguments": json.dumps({"task_id": task_id}),
            },
        }])
        mock.add_text_response("Published Re:Zero.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn

            result = run_agent_turn(
                session=session,
                config=config,
                task_id=task_id,
                mode="auto_ingest",
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"
        assert result.tool_call_count == 6

        with sf() as session:
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "library_import_complete"
            assert task.failure_reason is None

            mappings = EpisodeMappingRepository(session).get_by_task(task_id)
            assert len(mappings) == 16
            assert mappings[0].season == 1
            assert mappings[0].episode == 51
            assert mappings[-1].episode == 66

            runs = AgentRunRepository(session).list_by_task(task_id)
            tool_calls = (
                session.query(AgentToolCall)
                .filter(AgentToolCall.run_id == runs[0].id)
                .all()
            )
            show_call = next(
                tc for tc in tool_calls if tc.tool_name == "prepare_show_structure"
            )
            assert show_call.output["status"] == "success"
            assert (
                show_call.output["data"]["requires_metadata_detail"] is True
            )

    def test_complex_input_keeps_iso_as_unsupported(self, tmp_path):
        """ISO 输入仍走 review_complex_input 路径 — 硬失败边界."""
        from media_pilot.agent.tools.base import ToolContext
        from media_pilot.agent.tools.complex_input import (
            _handle_prepare_complex_input_decision,
        )
        from media_pilot.repository.repositories import (
            AgentDecisionRequestRepository,
            AgentRunCreate,
            AgentRunRepository,
            IngestTaskCreate,
            IngestTaskRepository,
        )
        from tests.test_api_v1 import _make_session_factory

        config, _ = self._setup_show_dir(tmp_path)
        iso_path = config.downloads_dir / "Some.Movie.2024.iso"
        iso_path.write_bytes(b"iso")

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path=str(iso_path), status="agent_running",
                current_step="agent_start", media_type="movie",
            ))
            run = AgentRunRepository(session).create(AgentRunCreate(
                task_id=task.id, current_step="agent_start",
            ))
            session.commit()
            task_id, run_id = task.id, run.id

            # 用同一个 session 调用 handler, 让 flush 的数据在同一事务里可见.
            ctx = ToolContext(
                session=session, config=config,
                task_id=task_id, run_id=run_id,
            )
            result = _handle_prepare_complex_input_decision(
                context=ctx, input_data={"task_id": task_id},
            )
            session.commit()

            drs = AgentDecisionRequestRepository(session).list_pending_by_run(run_id)

        # ISO 必须创建 review_complex_input 决策, 不得进入发布路径.
        assert result.data.get("ready") is not True
        assert result.data.get("decision_requested") is True
        assert result.data["decision_type"] == "review_complex_input"
        assert len(drs) == 1
        assert drs[0].decision_type == "review_complex_input"
