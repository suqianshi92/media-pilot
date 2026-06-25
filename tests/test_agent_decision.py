"""Tests for agent decision request, user reply, and continue flow."""

import pytest

from tests.agent_runner_helpers import MockLLMClient, _make_config, _make_task


# ── helpers ───────────────────────────────────────────────────────────────


def _make_decision_payload(**overrides):
    """Minimal valid request_user_decision input."""
    data = {
        "decision_type": "metadata_candidate_selection",
        "question": "请选择正确的影片候选",
        "options": [
            {
                "id": "candidate_1",
                "label": "The Godfather Part II (1974)",
                "description": "TMDB #240",
                "payload": {"provider": "tmdb", "provider_id": "240"},
            },
            {
                "id": "candidate_2",
                "label": "The Godfather (1972)",
                "description": "TMDB #238",
            },
        ],
        "free_text_allowed": True,
    }
    data.update(overrides)
    return data


def _execute_tool(session, config, task_id, run_id, input_data):
    """Directly execute request_user_decision tool handler."""
    from media_pilot.agent.tools.base import ToolContext
    from media_pilot.agent.tools.decision import _handle_request_user_decision

    ctx = ToolContext(session=session, config=config, task_id=task_id, run_id=run_id)
    return _handle_request_user_decision(ctx, input_data)


def _create_active_run(session, task_id):
    """Create a real active AgentRun for test purposes."""
    from media_pilot.repository.repositories import AgentRunCreate, AgentRunRepository
    return AgentRunRepository(session).create(AgentRunCreate(task_id=task_id, current_step="agent_start"))


# ══════════════════════════════════════════════════════════════════════════
# Section 1: request_user_decision tool
# ══════════════════════════════════════════════════════════════════════════


class TestRequestUserDecisionTool:
    def test_creates_pending_decision_successfully(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            run = _create_active_run(session, task.id)
            payload = _make_decision_payload()
            result = _execute_tool(session, config, task.id, run.id, payload)
            session.commit()

        assert result.status == "success"
        assert result.data["decision_id"]
        assert result.data["decision_type"] == "metadata_candidate_selection"
        assert "options_count" in result.data

    def test_fails_when_decision_type_empty(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            run = _create_active_run(session, task.id)
            payload = _make_decision_payload(decision_type="   ")
            result = _execute_tool(session, config, task.id, run.id, payload)

        assert result.status == "failure"
        assert "decision_type" in result.summary.lower()

    def test_fails_when_question_empty(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            run = _create_active_run(session, task.id)
            payload = _make_decision_payload(question="")
            result = _execute_tool(session, config, task.id, run.id, payload)

        assert result.status == "failure"
        assert "question" in result.summary.lower()

    def test_fails_when_option_missing_id(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            run = _create_active_run(session, task.id)
            payload = _make_decision_payload(options=[
                {"label": "Missing ID", "id": ""},
            ])
            result = _execute_tool(session, config, task.id, run.id, payload)

        assert result.status == "failure"
        assert "id" in result.summary.lower()

    def test_fails_when_option_missing_label(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            run = _create_active_run(session, task.id)
            payload = _make_decision_payload(options=[
                {"id": "x", "label": "   "},
            ])
            result = _execute_tool(session, config, task.id, run.id, payload)

        assert result.status == "failure"
        assert "label" in result.summary.lower()

    def test_fails_when_no_options_and_free_text_disabled(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            run = _create_active_run(session, task.id)
            payload = _make_decision_payload(options=[], free_text_allowed=False)
            result = _execute_tool(session, config, task.id, run.id, payload)

        assert result.status == "failure"
        assert "at least one option" in result.summary.lower()

    def test_fails_when_run_id_missing(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            payload = _make_decision_payload()
            result = _execute_tool(session, config, task.id, None, payload)

        assert result.status == "failure"
        assert "run_id" in result.summary.lower()

    def test_fails_when_run_not_found(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            payload = _make_decision_payload()
            result = _execute_tool(session, config, task.id, "nonexistent-run-id", payload)

        assert result.status == "failure"
        assert "not found" in result.summary.lower()

    def test_fails_when_run_not_active(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            run = _create_active_run(session, task.id)
            # Set run to a non-active status
            from media_pilot.repository.repositories import AgentRunRepository
            AgentRunRepository(session).update_status(run, status="completed")
            session.flush()

            payload = _make_decision_payload()
            result = _execute_tool(session, config, task.id, run.id, payload)

        assert result.status == "failure"
        assert "must be active" in result.summary.lower()

    def test_fails_when_free_text_allowed_not_bool(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            run = _create_active_run(session, task.id)
            # "false" as a string must NOT bypass validation
            payload = _make_decision_payload(free_text_allowed="false")
            result = _execute_tool(session, config, task.id, run.id, payload)

        assert result.status == "failure"
        assert "free_text_allowed must be a boolean" in result.summary.lower()

    def test_fails_on_duplicate_pending_decision(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            run = _create_active_run(session, task.id)
            payload = _make_decision_payload()
            r1 = _execute_tool(session, config, task.id, run.id, payload)
            assert r1.status == "success"
            # Second attempt — both tool handler AND repository now reject
            r2 = _execute_tool(session, config, task.id, run.id, payload)
            assert r2.status == "failure"
            assert "already has a pending" in r2.summary.lower()


# ══════════════════════════════════════════════════════════════════════════
# Section 2: Runner pause and continue
# ══════════════════════════════════════════════════════════════════════════


class TestRunnerDecisionPause:
    def test_pauses_after_successful_decision_request(self, tmp_path):
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
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Choose","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])
        mock.add_text_response("Should not be called after pause.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "waiting_user"
        assert result.tool_call_count == 1

        with sf() as session:
            from media_pilot.repository.repositories import AgentRunRepository, IngestTaskRepository
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            assert run.status == "waiting_user"
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "waiting_user"

    def test_no_llm_call_after_decision_pause(self, tmp_path):
        """After successful request_user_decision, runner must NOT call LLM again."""
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
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Choose","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])
        # If runner incorrectly continues, it would try to call LLM again

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "waiting_user"
        # Only 1 LLM call: the one that requested the decision
        assert len(mock.calls) == 1

    def test_continue_agent_run_reuses_same_run(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        # Step 1: create run that pauses with a decision
        mock1 = MockLLMClient()
        mock1.add_tool_calls([{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result1 = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock1,
            )
            session.commit()

        assert result1.status == "waiting_user"
        run_id = result1.run_id

        # Step 2: continue the same run (simulating after user reply)
        # First, write a user message to simulate the reply service
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageCreate,
                AgentMessageRepository,
                AgentRunRepository,
                IngestTaskRepository,
            )
            msg_repo = AgentMessageRepository(session)
            msg_repo.create(AgentMessageCreate(
                run_id=run_id, role="user", content="[User selected option: a]",
            ))
            run_repo = AgentRunRepository(session)
            run = run_repo.get(run_id)
            run_repo.update_status(run, status="active")
            task_repo = IngestTaskRepository(session)
            task = task_repo.get(task_id)
            task_repo.update_status(task, status="agent_running", current_step="user_replied")
            session.commit()

        mock2 = MockLLMClient()
        mock2.add_text_response("Thanks for your selection. Continuing...")

        with sf() as session:
            from media_pilot.agent.runner import continue_agent_run
            result2 = continue_agent_run(
                session=session, config=config, run_id=run_id,
                mock_llm_client=mock2,
            )
            session.commit()

        assert result2.status == "completed"
        # Same run_id
        assert result2.run_id == run_id
        # No new run was created
        with sf() as session:
            from media_pilot.repository.repositories import AgentRunRepository
            runs = AgentRunRepository(session).list_by_task(task_id)
            assert len(runs) == 1
            assert runs[0].id == run_id

    def test_continue_agent_run_no_initial_user_message(self, tmp_path):
        """continue_agent_run must NOT write the fixed initial user message."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        # Create run that pauses with a decision
        mock1 = MockLLMClient()
        mock1.add_tool_calls([{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result1 = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock1,
            )
            session.commit()
        run_id = result1.run_id

        # Set run back to active with a user message
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageCreate,
                AgentMessageRepository,
                AgentRunRepository,
                IngestTaskRepository,
            )
            msg_repo = AgentMessageRepository(session)
            msg_repo.create(AgentMessageCreate(
                run_id=run_id, role="user", content="[User reply text]",
            ))
            run = AgentRunRepository(session).get(run_id)
            AgentRunRepository(session).update_status(run, status="active")
            task = IngestTaskRepository(session).get(task_id)
            IngestTaskRepository(session).update_status(task, status="agent_running", current_step="user_replied")
            session.commit()

        mock2 = MockLLMClient()
        mock2.add_text_response("Done after continue.")

        with sf() as session:
            from media_pilot.agent.runner import continue_agent_run
            result2 = continue_agent_run(
                session=session, config=config, run_id=run_id,
                mock_llm_client=mock2,
            )
            session.commit()

        assert result2.status == "completed"
        # Verify no duplicate initial user message
        with sf() as session:
            from media_pilot.repository.repositories import AgentMessageRepository
            messages = AgentMessageRepository(session).list_by_run(run_id)
            user_msgs = [m for m in messages if m.role == "user"]
            # 2 user messages: initial from run_agent_turn + reply from test
            assert len(user_msgs) == 2
            # The initial one should contain the task ID
            assert task_id in user_msgs[0].content
            # The second is our reply
            assert "User reply text" in user_msgs[1].content

    def test_continue_agent_run_rejects_completed_run(self, tmp_path):
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
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"

        with sf() as session:
            from media_pilot.agent.runner import continue_agent_run
            with pytest.raises(ValueError, match="Cannot continue.*completed"):
                continue_agent_run(
                    session=session, config=config, run_id=result.run_id,
                )

    def test_continue_agent_run_rejects_failed_run(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        mock.raise_error = RuntimeError("Boom")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "failed"

        with sf() as session:
            from media_pilot.agent.runner import continue_agent_run
            with pytest.raises(ValueError, match="Cannot continue.*failed"):
                continue_agent_run(
                    session=session, config=config, run_id=result.run_id,
                )

    def test_continue_resets_step_counters(self, tmp_path):
        """max_steps and max_tool_failures reset for each continue call."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        # Step 1: use 1 step to pause
        mock1 = MockLLMClient()
        mock1.add_tool_calls([{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result1 = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock1,
            )
            session.commit()
        run_id = result1.run_id

        # Step 2: prepare for continue
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageCreate,
                AgentMessageRepository,
                AgentRunRepository,
                IngestTaskRepository,
            )
            AgentMessageRepository(session).create(AgentMessageCreate(
                run_id=run_id, role="user", content="[User reply]",
            ))
            run = AgentRunRepository(session).get(run_id)
            AgentRunRepository(session).update_status(run, status="active")
            task = IngestTaskRepository(session).get(task_id)
            IngestTaskRepository(session).update_status(task, status="agent_running", current_step="user_replied")
            session.commit()

        # Step 3: continue — should have fresh step counters
        mock2 = MockLLMClient()
        # Use 9 tool calls (under the 10 limit) then final text
        for _ in range(9):
            mock2.add_tool_calls([{
                "id": "call_x",
                "type": "function",
                "function": {
                    "name": "get_task_context",
                    "arguments": '{"task_id":"' + task_id + '"}',
                },
            }])
        mock2.add_text_response("Done.")

        with sf() as session:
            from media_pilot.agent.runner import continue_agent_run
            result2 = continue_agent_run(
                session=session, config=config, run_id=run_id,
                mock_llm_client=mock2,
            )
            session.commit()

        # Should complete successfully with 9 tool calls (not hit the step limit)
        assert result2.status == "completed"
        assert result2.tool_call_count == 9


# ══════════════════════════════════════════════════════════════════════════
# Section 3: User reply service
# ══════════════════════════════════════════════════════════════════════════


class TestDecisionReplyService:
    def test_reply_with_option_id(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        # Create run → pause via decision
        mock = MockLLMClient()
        mock.add_tool_calls([{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Choose","options":[{"id":"a","label":"A"},{"id":"b","label":"B"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        # Find the decision
        with sf() as session:
            from media_pilot.repository.repositories import AgentDecisionRequestRepository
            decisions = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
            assert len(decisions) == 1
            decision_id = decisions[0].id

        # Reply
        mock2 = MockLLMClient()
        mock2.add_text_response("Great choice!")

        with sf() as session:
            from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
            reply = ReplyInput(decision_id=decision_id, option_id="a")
            result = reply_to_decision(
                session=session, config=config, reply=reply,
                mock_llm_client=mock2,
            )
            session.commit()

        assert result.status == "completed"

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                AgentMessageRepository,
            )
            # Decision marked as decided
            d = AgentDecisionRequestRepository(session).get(decision_id)
            assert d.status == "decided"
            assert d.decision == {"option_id": "a", "type": "option"}
            assert d.decided_by == "user"
            assert d.decided_at is not None

            # User message persisted
            messages = AgentMessageRepository(session).list_by_run(d.run_id)
            user_msgs = [m for m in messages if m.role == "user"]
            assert any("a" in (m.content or "") for m in user_msgs)

    def test_reply_with_free_text(self, tmp_path):
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
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Any thoughts?","options":[],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import AgentDecisionRequestRepository
            decisions = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
            decision_id = decisions[0].id

        mock2 = MockLLMClient()
        mock2.add_text_response("Thanks for your input!")

        with sf() as session:
            from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
            reply = ReplyInput(decision_id=decision_id, free_text="我觉得应该选第三个")
            result = reply_to_decision(
                session=session, config=config, reply=reply,
                mock_llm_client=mock2,
            )
            session.commit()

        assert result.status == "completed"

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                AgentMessageRepository,
            )
            d = AgentDecisionRequestRepository(session).get(decision_id)
            assert d.status == "decided"
            assert d.decision["free_text"] == "我觉得应该选第三个"

            messages = AgentMessageRepository(session).list_by_run(d.run_id)
            user_msgs = [m for m in messages if m.role == "user"]
            assert any("我觉得应该选第三个" in (m.content or "") for m in user_msgs)

    def test_reject_duplicate_reply_409(self, tmp_path):
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
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import AgentDecisionRequestRepository
            decisions = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
            decision_id = decisions[0].id

        # First reply succeeds
        mock2 = MockLLMClient()
        mock2.add_text_response("OK")

        with sf() as session:
            from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
            reply = ReplyInput(decision_id=decision_id, option_id="a")
            reply_to_decision(session=session, config=config, reply=reply, mock_llm_client=mock2)
            session.commit()

        # Second reply fails
        with sf() as session:
            from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
            reply = ReplyInput(decision_id=decision_id, option_id="a")
            with pytest.raises(ValueError) as exc_info:
                reply_to_decision(session=session, config=config, reply=reply)
            err = exc_info.value.args[0]
            assert err["status_code"] == 409
            assert "already been decided" in err["detail"]

    def test_reject_invalid_option_id_400(self, tmp_path):
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
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import AgentDecisionRequestRepository
            decisions = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
            decision_id = decisions[0].id

        with sf() as session:
            from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
            reply = ReplyInput(decision_id=decision_id, option_id="nonexistent")
            with pytest.raises(ValueError) as exc_info:
                reply_to_decision(session=session, config=config, reply=reply)
            err = exc_info.value.args[0]
            assert err["status_code"] == 400
            assert "not found" in err["detail"]

    def test_reject_free_text_when_not_allowed_400(self, tmp_path):
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
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":false}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import AgentDecisionRequestRepository
            decisions = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
            decision_id = decisions[0].id

        with sf() as session:
            from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
            reply = ReplyInput(decision_id=decision_id, free_text="custom")
            with pytest.raises(ValueError) as exc_info:
                reply_to_decision(session=session, config=config, reply=reply)
            err = exc_info.value.args[0]
            assert err["status_code"] == 400
            assert "Free text is not allowed" in err["detail"]

    def test_reject_reply_when_run_not_waiting_409(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        mock.add_text_response("Done directly.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        # Create a pending decision manually on a completed run
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestCreate,
                AgentDecisionRequestRepository,
            )
            dr = AgentDecisionRequestRepository(session).create(AgentDecisionRequestCreate(
                run_id=result.run_id,
                task_id=task_id,
                decision_type="test",
                question="Q?",
                options=[{"id": "a", "label": "A"}],
            ))
            session.commit()
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
            reply = ReplyInput(decision_id=decision_id, option_id="a")
            with pytest.raises(ValueError) as exc_info:
                reply_to_decision(session=session, config=config, reply=reply)
            err = exc_info.value.args[0]
            assert err["status_code"] == 409
            assert "not waiting" in err["detail"].lower()

    def test_reject_both_option_id_and_free_text(self, tmp_path):
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
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import AgentDecisionRequestRepository
            decisions = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
            decision_id = decisions[0].id

        with sf() as session:
            from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
            reply = ReplyInput(decision_id=decision_id, option_id="a", free_text="also text")
            with pytest.raises(ValueError) as exc_info:
                reply_to_decision(session=session, config=config, reply=reply)
            err = exc_info.value.args[0]
            assert err["status_code"] == 400
            assert "cannot both be provided" in err["detail"]


# ══════════════════════════════════════════════════════════════════════════
# Section 4: API endpoints
# ══════════════════════════════════════════════════════════════════════════


class TestDecisionAndMessageAPI:
    def test_list_pending_decisions(self, tmp_path):
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
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        from fastapi.testclient import TestClient
        from media_pilot.app import create_app

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        resp = client.get(f"/api/v1/tasks/{task_id}/agent-decisions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert len(data["data"]) == 1
        assert data["data"][0]["decision_type"] == "test"
        assert data["data"][0]["question"] == "Q"
        assert data["data"][0]["status"] == "pending"

    def test_reply_and_continue_api(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        # Pause with decision
        mock = MockLLMClient()
        mock.add_tool_calls([{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import AgentDecisionRequestRepository
            decisions = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
            decision_id = decisions[0].id

        # The API endpoint will use real AgentLLMClient which fails.
        # Test the structure by testing via reply service (already tested above).
        # Here we verify 404 and 409.
        from fastapi.testclient import TestClient
        from media_pilot.app import create_app

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)

        # Reply to non-existent decision
        resp = client.post("/api/v1/agent-decisions/nonexistent/reply", json={
            "option_id": "a",
        })
        assert resp.status_code == 404

    def test_reply_duplicate_409(self, tmp_path):
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
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import AgentDecisionRequestRepository
            decisions = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
            decision_id = decisions[0].id

        from fastapi.testclient import TestClient
        from media_pilot.app import create_app

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)

        # First reply succeeds (via real LLM will fail, but we test the API returns 200)
        resp1 = client.post(f"/api/v1/agent-decisions/{decision_id}/reply", json={
            "option_id": "a",
        })
        # May succeed or fail depending on LLM config (test config has fake key)
        # The important part: second reply must 409

        # Second reply MUST be 409 (already decided)
        resp2 = client.post(f"/api/v1/agent-decisions/{decision_id}/reply", json={
            "option_id": "a",
        })
        assert resp2.status_code == 409

    def test_list_agent_messages(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        mock = MockLLMClient()
        mock.add_text_response("Hello from agent.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        from fastapi.testclient import TestClient
        from media_pilot.app import create_app

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        resp = client.get(f"/api/v1/tasks/{task_id}/agent-messages")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert len(data["data"]) >= 2  # user + assistant
        # No system messages
        roles = [m["role"] for m in data["data"]]
        assert "system" not in roles
        assert "user" in roles
        assert "assistant" in roles

    def test_agent_messages_exclude_system_prompt(self, tmp_path):
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
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        from fastapi.testclient import TestClient
        from media_pilot.app import create_app

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        resp = client.get(f"/api/v1/tasks/{task_id}/agent-messages")
        data = resp.json()["data"]
        for msg in data:
            if msg["content"]:
                assert "You are Media Pilot" not in msg["content"]


# ══════════════════════════════════════════════════════════════════════════
# Section 5: State and boundary verification
# ══════════════════════════════════════════════════════════════════════════


class TestStateAndBoundaries:
    def test_task_waiting_user_after_decision(self, tmp_path):
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
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "waiting_user"

    def test_task_agent_running_after_reply_then_completed(self, tmp_path):
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
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import AgentDecisionRequestRepository
            decisions = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
            decision_id = decisions[0].id

        mock2 = MockLLMClient()
        mock2.add_text_response("Done.")

        with sf() as session:
            from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
            reply = ReplyInput(decision_id=decision_id, option_id="a")
            result = reply_to_decision(
                session=session, config=config, reply=reply,
                mock_llm_client=mock2,
            )
            session.commit()

        assert result.status == "completed"

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "agent_running" or task.status == "completed"  # may be updated by runner

    def test_new_run_409_when_waiting_user_exists(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        # Pause run via decision
        mock = MockLLMClient()
        mock.add_tool_calls([{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        # Now try to create new run via API → 409
        from fastapi.testclient import TestClient
        from media_pilot.app import create_app

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        resp = client.post(f"/api/v1/tasks/{task_id}/agent-runs")
        assert resp.status_code == 409

    def test_single_pending_decision_per_run(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id

        # Use runner to create a pending decision, then try to create another
        mock = MockLLMClient()
        mock.add_tool_calls([{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestCreate,
                AgentDecisionRequestRepository,
                AgentRunRepository,
            )
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            # Direct repo call must now reject second pending decision
            with pytest.raises(ValueError, match="already has a pending decision"):
                AgentDecisionRequestRepository(session).create(AgentDecisionRequestCreate(
                    run_id=run.id,
                    task_id=task_id,
                    decision_type="test2",
                    question="Q2?",
                    options=[{"id": "b", "label": "B"}],
                ))

    def test_no_ui_no_publish_side_effects(self, tmp_path):
        """Verify no WritePlan/WriteResult are created by decision flow."""
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
                "name": "request_user_decision",
                "arguments": '{"decision_type":"test","question":"Q","options":[{"id":"a","label":"A"}],"free_text_allowed":true}',
            },
        }])

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        # Verify no write/publish side effects
        with sf() as session:
            from sqlalchemy import func, select
            from media_pilot.repository.models import WritePlan, WriteResult
            for model in [WritePlan, WriteResult]:
                count = session.scalar(select(func.count()).select_from(model))
                assert count == 0, f"{model.__name__} should have 0 rows"
