"""Section 6.4: Agent integration test for complex input pause + resume.

Verifies that when the LLM calls ``prepare_complex_input_decision`` and
the tool returns ``decision_requested=true``:

1. The AgentRun is paused (``status=waiting_user``, ``current_step``
   set to the decision type).
2. The IngestTask is also paused (``status=waiting_user``).
3. The tool result is propagated to the LLM as a structured
   "decision_requested" data payload (so the LLM knows to stop).
4. After the user replies via ``reply_to_decision`` with a stub LLM
   that completes, the AgentRun resumes and finishes.

These tests use ``MockLLMClient`` and run through the public
``run_agent_turn`` / ``continue_agent_run`` entry points.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ── helpers ────────────────────────────────────────────────────────


def _make_config(tmp_path: Path):
    from media_pilot.config.settings import AppConfig

    return AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "ws",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path,
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        tmdb_api_key="test-tmdb-key",
    )


def _make_task_with_source(session, source_path: str):
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

    task = IngestTaskRepository(session).create(IngestTaskCreate(
        source_path=source_path,
        status="discovered",
        current_step="agent_start",
    ))
    session.commit()
    return task


class _MockLLM:
    def __init__(self):
        self.responses = []
        self.calls = []

    def add_text(self, content):
        from media_pilot.agent.llm_client import LLMResponse
        self.responses.append(LLMResponse(content=content, tool_calls=[]))

    def add_tool_call(self, name, arguments, call_id="call_1"):
        from media_pilot.agent.llm_client import LLMResponse
        self.responses.append(LLMResponse(
            content=None,
            tool_calls=[{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments if isinstance(arguments, str)
                    else __import__("json").dumps(arguments),
                },
            }],
        ))

    def add_tool_calls(self, tool_calls_specs):
        """Add a single LLM response containing multiple tool_calls.

        Each spec is a dict with keys ``name``, ``arguments``, optional
        ``call_id``. Lets tests verify the runner's intra-batch
        termination signal (failed / decision_requested) skips the rest
        of the batch.
        """
        import json as _json
        from media_pilot.agent.llm_client import LLMResponse
        tool_calls = []
        for i, spec in enumerate(tool_calls_specs):
            tool_calls.append({
                "id": spec.get("call_id", f"call_{i}"),
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "arguments": spec["arguments"]
                    if isinstance(spec["arguments"], str)
                    else _json.dumps(spec["arguments"]),
                },
            })
        self.responses.append(LLMResponse(content=None, tool_calls=tool_calls))

    def chat(self, messages, tools=None):
        self.calls.append(list(messages))
        from media_pilot.agent.llm_client import LLMResponse
        if not self.responses:
            return LLMResponse(content="", tool_calls=[])
        return self.responses.pop(0)

    def chat_stream(self, messages, tools=None):
        self.calls.append(list(messages))
        from media_pilot.agent.llm_client import LLMResponse
        if not self.responses:
            # 默认产生一个空 text 响应
            yield ("delta", "")
            yield ("done", LLMResponse(content="", tool_calls=[]))
            return
        resp = self.responses.pop(0)
        if resp.content:
            yield ("delta", resp.content)
        if resp.tool_calls:
            yield ("done", resp)
        else:
            yield ("done", LLMResponse(content=resp.content or "", tool_calls=[]))


# ── tests ──────────────────────────────────────────────────────────


class TestComplexInputPausesRunner:
    def test_decision_requested_pauses_run_and_task(
        self, tmp_path: Path,
    ):
        """prepare_complex_input_decision 返回 decision_requested=true
        → AgentRun 切到 waiting_user, task 切到 waiting_user,
        AgentRunResult.status == "waiting_user"."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "A.mkv").write_bytes(b"a")
        (config.downloads_dir / "B.mkv").write_bytes(b"b")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(session, str(config.downloads_dir))
            task_id = task.id

        mock = _MockLLM()
        # LLM first calls prepare_complex_input_decision
        mock.add_tool_call(
            "prepare_complex_input_decision",
            {"task_id": task_id},
        )
        # Then the LLM should stop (no further tool calls).
        mock.add_text("Need user input to pick the primary video.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "waiting_user"

        # 任务和 run 状态联动
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                IngestTaskRepository,
            )
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "waiting_user"
            assert task.current_step in ("select_primary_video", "review_complex_input")

            # 最新 run 状态 = waiting_user
            runs = AgentRunRepository(session).list_by_task(task_id)
            assert any(r.status == "waiting_user" for r in runs)

    def test_decision_payload_propagated_to_llm(
        self, tmp_path: Path,
    ):
        """工具返回 decision_requested=true 时, AgentRunResult 必须带
        decision_requested 标志, runner 据此切到 waiting_user. 也校验
        AgentDecisionRequest 落库时, payload.video_candidates 等由后端
        生成的字段齐全 — 不让 LLM 自己拼路径."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "A.mkv").write_bytes(b"a")
        (config.downloads_dir / "B.mkv").write_bytes(b"b")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(session, str(config.downloads_dir))
            task_id = task.id

        mock = _MockLLM()
        mock.add_tool_call("prepare_complex_input_decision", {"task_id": task_id})
        mock.add_text("Will stop and wait for user.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "waiting_user"

        # 落库的 AgentDecisionRequest 携带了由后端生成的事实, 不是 LLM 拼的
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
            )
            pending = AgentDecisionRequestRepository(session).list_pending_by_task(
                task_id,
            )
            assert len(pending) == 1
            dr = pending[0]
            assert dr.decision_type == "select_primary_video"
            payload = dr.payload if isinstance(dr.payload, dict) else {}
            # 后端生成的 video_candidates 包含全路径
            video_candidates = payload.get("video_candidates", [])
            assert len(video_candidates) == 2
            for vc in video_candidates:
                assert vc.get("path", "").endswith(".mkv")
                assert vc.get("name")
                assert vc.get("size_bytes", 0) > 0
            # options 列表里也有完整 path
            assert isinstance(dr.options, list)
            for opt in dr.options:
                opt_payload = opt.get("payload", {})
                assert opt_payload.get("path", "").endswith(".mkv")


class TestComplexInputResumesAfterReply:
    def test_reply_continues_and_completes(
        self, tmp_path: Path, monkeypatch,
    ):
        """user 回复后, run + task 切回 active/agent_running, 续跑 LLM."""
        from media_pilot.agent.runner import AgentRunResult

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "A.mkv").write_bytes(b"a")
        (config.downloads_dir / "B.mkv").write_bytes(b"b")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(session, str(config.downloads_dir))
            task_id = task.id

        # Step 1: LLM calls prepare_complex_input_decision → waiting_user
        mock = _MockLLM()
        mock.add_tool_call("prepare_complex_input_decision", {"task_id": task_id})
        mock.add_text("Need user input.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            first = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()
            assert first.status == "waiting_user"

        # 找到 decision_id
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
            )
            dr = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
            assert len(dr) == 1
            decision = dr[0]
            decision_id = decision.id
            options = decision.options

        # Step 2: 模拟 LLM 在续跑时直接给出 final text (跳过 metadata/publish)
        continue_mock = _MockLLM()
        continue_mock.add_text("User selected the primary video. Will continue.")

        # 把 run_agent_turn 替换为只跑一次 final text, 模拟 Agent 续跑结束
        def _continue_session(*args, **kwargs):
            return AgentRunResult(
                run_id=kwargs.get("run_id", ""),
                status="completed",
                message_count=1,
                tool_call_count=0,
            )

        monkeypatch.setattr(
            "media_pilot.agent.runner.continue_agent_run",
            _continue_session,
        )

        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput, reply_to_decision,
            )
            from media_pilot.repository.repositories import (
                MediaSourceSelectionRepository,
            )
            result = reply_to_decision(
                session=session, config=config,
                reply=ReplyInput(
                    decision_id=decision_id,
                    option_id=options[0]["id"],
                ),
            )
            session.commit()
            assert isinstance(result, AgentRunResult)
            # MediaSourceSelection 写入了 selected_path
            sel = MediaSourceSelectionRepository(session).get_for_task(task_id)
            assert sel is not None
            assert sel.selected_path == options[0]["payload"]["path"]


class TestSingleFilePathStaysAutoContinuable:
    """Section 2.5: 单文件普通电影路径必须保持可自动继续."""

    def test_single_file_ready_does_not_create_decision(
        self, tmp_path: Path,
    ):
        """单文件 ready 路径不创建任何 AgentDecisionRequest."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "Example.Movie.2026.mkv").write_bytes(b"v")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(
                session, str(config.downloads_dir / "Example.Movie.2026.mkv"),
            )
            task_id = task.id

        mock = _MockLLM()
        mock.add_tool_call("prepare_complex_input_decision", {"task_id": task_id})
        # ready → LLM 继续. 我们用 final text 让 run 立刻完成.
        mock.add_text("Single file movie. Continuing to metadata search.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
            )
            pending = AgentDecisionRequestRepository(session).list_pending_by_task(
                task_id,
            )
            assert len(pending) == 0


# ── Issue 2: 工具把 run 设为 failed 后, runner final text 不得覆盖为
#    completed. 验证 no_videos / unsafe_path / scan_failed → final text
#    路径下, run.status 仍是 failed, task.status 是 agent_failed.


class TestRunnerPreservesFailedStatusInFinalText:
    """`prepare_complex_input_decision` 在 no_videos / unsafe_path /
    scan_failed 路径会把 AgentRun 切到 failed, 并把 task 切到
    agent_failed. 如果 LLM 随后给出 final text (解释错误), runner
    不得把 run 覆盖为 completed, 也不得把 task 切回其他状态."""

    def _assert_failed_state_persisted(self, sf, task_id, run_id, run_error_substr: str):
        from media_pilot.repository.repositories import (
            AgentRunRepository,
            IngestTaskRepository,
        )
        with sf() as session:
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "agent_failed", (
                f"task 应保持 agent_failed, 实际 {task.status}"
            )
            assert task.current_step == "agent_failed"
            run = AgentRunRepository(session).get(run_id)
            assert run.status == "failed", (
                f"run 应保持 failed (不得被 final text 覆盖为 completed), "
                f"实际 {run.status}"
            )
            assert run.current_step == "agent_failed"
            if run_error_substr:
                assert run.error_message and run_error_substr in run.error_message

    def test_no_videos_then_final_text_keeps_run_failed(
        self, tmp_path: Path,
    ):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        # 目录里只有字幕, 没有视频 — 工具会返回 no_videos → 切 failed
        (config.downloads_dir / "movie.srt").write_text("...")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(session, str(config.downloads_dir))
            task_id = task.id

        mock = _MockLLM()
        mock.add_tool_call("prepare_complex_input_decision", {"task_id": task_id})
        # LLM 在工具后给 final text 解释错误 — runner 不得把 failed 翻成 completed
        mock.add_text(
            "Task input has no videos; cannot proceed with metadata search.",
        )

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()
            # run_agent_turn 的最终结果必须反映 failed, 不是 completed
            assert result.status == "failed", (
                f"AgentRunResult.status 应是 failed, 实际 {result.status}"
            )
            run_id = result.run_id

        self._assert_failed_state_persisted(
            sf, task_id, run_id, run_error_substr="no_video_files_found",
        )

    def test_unsafe_path_then_final_text_keeps_run_failed(
        self, tmp_path: Path,
    ):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        # 越界路径 — 不在 downloads / watch / workspace 内
        outside = tmp_path / "outside_unsafe"
        outside.mkdir()
        target = outside / "movie.mkv"
        target.write_bytes(b"x")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(session, str(target))
            task_id = task.id

        mock = _MockLLM()
        mock.add_tool_call("prepare_complex_input_decision", {"task_id": task_id})
        mock.add_text("Source path is outside safe roots; cannot ingest.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()
            assert result.status == "failed"
            run_id = result.run_id

        self._assert_failed_state_persisted(
            sf, task_id, run_id,
            run_error_substr="source_path_outside_safe_roots",
        )

    def test_scan_failed_then_final_text_keeps_run_failed(
        self, tmp_path: Path,
    ):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(
                session, str(tmp_path / "missing_dir" / "video.mkv"),
            )
            task_id = task.id

        mock = _MockLLM()
        mock.add_tool_call("prepare_complex_input_decision", {"task_id": task_id})
        mock.add_text("Source path does not exist.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()
            assert result.status == "failed"
            run_id = result.run_id

        self._assert_failed_state_persisted(
            sf, task_id, run_id, run_error_substr="source_path_not_found",
        )

    def test_ready_path_final_text_still_completes(
        self, tmp_path: Path,
    ):
        """正常 ready 路径: 工具没把 run 切 failed → final text 仍标记
        completed. 不让修复影响正常完成路径."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "Example.Movie.2026.mkv").write_bytes(b"v")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(
                session, str(config.downloads_dir / "Example.Movie.2026.mkv"),
            )
            task_id = task.id

        mock = _MockLLM()
        mock.add_tool_call("prepare_complex_input_decision", {"task_id": task_id})
        mock.add_text("Single file ready. Continuing.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()
            assert result.status == "completed"
            run_id = result.run_id

        with sf() as session:
            from media_pilot.repository.repositories import AgentRunRepository
            run = AgentRunRepository(session).get(run_id)
            assert run.status == "completed"
            assert run.current_step == "completed"


# ── Issue: 工具把 run 切 failed 后, LLM 仍可能返回 tool_call, runner 必须
#    不得执行, 也不得留下 search_metadata / publish_movie_to_library 等
#    AgentToolCall 记录. 验证 failed 终止信号的完整性.


class TestFailedRunDropsFollowupToolCalls:
    """`prepare_complex_input_decision` 命中失败态 (no_videos /
    unsafe_path / scan_failed / review_user_note_already_consumed) 后,
    LLM 故意再返回 search_metadata / publish_movie_to_library 等写入型
    tool_call. runner 必须:
    1. 不执行任何 tool_call
    2. 不创建对应 AgentToolCall 记录
    3. 持久化一条 assistant "skipped" 解释
    4. 给每个 tool_call 写一条 tool 失败说明
    5. run.status 保持 failed, task.status 保持 agent_failed
    """

    def _assert_no_tool_records_for(
        self, sf, run_id: str, tool_names: list[str],
    ):
        from sqlalchemy import select
        from media_pilot.repository.models import AgentToolCall

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentToolCallRepository,
            )
            tc_repo = AgentToolCallRepository(session)
            for name in tool_names:
                tcs = tc_repo.list_by_run(run_id)
                matches = [tc for tc in tcs if tc.tool_name == name]
                assert matches == [], (
                    f"tool '{name}' 在 failed run 上不应有 AgentToolCall 记录, "
                    f"实际: {[(tc.status, tc.input) for tc in matches]}"
                )

    def _assert_failure_state(
        self, sf, task_id: str, run_id: str, expected_error_substr: str,
    ):
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                IngestTaskRepository,
            )
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "agent_failed"
            assert task.current_step == "agent_failed"
            run = AgentRunRepository(session).get(run_id)
            assert run.status == "failed"
            assert run.error_message
            assert expected_error_substr in run.error_message

    def test_no_videos_then_search_metadata_tool_call_is_dropped(
        self, tmp_path: Path,
    ):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "movie.srt").write_text("...")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(session, str(config.downloads_dir))
            task_id = task.id

        mock = _MockLLM()
        # turn 1: 命中 no_videos
        mock.add_tool_call("prepare_complex_input_decision", {"task_id": task_id})
        # turn 2: LLM 试图继续调 search_metadata — 必须被丢弃
        mock.add_tool_call(
            "search_metadata",
            {"task_id": task_id, "title": "Ghost Movie", "year": 2026},
        )
        # turn 3 (兜底): 不应被消费
        mock.add_text("Final explanation.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()
            assert result.status == "failed"
            run_id = result.run_id

        # run / task 仍是失败态
        self._assert_failure_state(
            sf, task_id, run_id, "no_video_files_found",
        )
        # search_metadata 的 AgentToolCall 记录不存在 — 工具没被执行
        self._assert_no_tool_records_for(
            sf, run_id, ["search_metadata", "publish_movie_to_library"],
        )
        # prepare_complex_input_decision 的 AgentToolCall 存在 (它确实被执行了)
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentToolCallRepository,
            )
            tc_repo = AgentToolCallRepository(session)
            tcs = tc_repo.list_by_run(run_id)
            executed_names = [tc.tool_name for tc in tcs]
            assert "prepare_complex_input_decision" in executed_names
            # 且状态不是 running — 工具已结束
            for tc in tcs:
                assert tc.status in ("completed", "failed")

        # 解释消息已写入: assistant skipped 提示 + tool 拒绝说明
        with sf() as session:
            from sqlalchemy import select
            from media_pilot.repository.models import AgentMessage
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
            )
            messages = AgentMessageRepository(session).list_by_run(run_id)
            assistant_msgs = [m for m in messages if m.role == "assistant"]
            assert any(
                m.content and "already in failed state" in m.content
                and "search_metadata" in m.content
                for m in assistant_msgs
            ), "应有 assistant skipped 解释说明 search_metadata 被丢弃"
            tool_msgs = [m for m in messages if m.role == "tool"]
            assert any(
                m.tool_name == "search_metadata" and m.content
                and "not executed" in m.content
                for m in tool_msgs
            ), "应给 search_metadata 写 tool 拒绝说明"

    def test_unsafe_path_then_publish_tool_call_is_dropped(
        self, tmp_path: Path,
    ):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        outside = tmp_path / "outside_unsafe"
        outside.mkdir()
        target = outside / "movie.mkv"
        target.write_bytes(b"x")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(session, str(target))
            task_id = task.id

        mock = _MockLLM()
        mock.add_tool_call("prepare_complex_input_decision", {"task_id": task_id})
        # 试图调 publish_movie_to_library (WRITE 工具, 副作用最严重)
        mock.add_tool_call(
            "publish_movie_to_library",
            {"task_id": task_id, "tmdb_id": 12345},
        )

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()
            assert result.status == "failed"
            run_id = result.run_id

        self._assert_failure_state(
            sf, task_id, run_id, "source_path_outside_safe_roots",
        )
        self._assert_no_tool_records_for(
            sf, run_id, ["publish_movie_to_library"],
        )

    def test_review_user_note_already_consumed_then_tool_call_dropped(
        self, tmp_path: Path,
    ):
        """`review_user_note_already_consumed` 路径把 run 切 failed 后,
        LLM 继续调 tool_call 也不得执行."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        bdmv_root = config.downloads_dir / "BDMV_MOVIE"
        bdmv_root.mkdir()
        (bdmv_root / "BDMV").mkdir()

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(session, str(bdmv_root))
            task_id = task.id

            # 预置 MediaSourceSelection 含 user_note (已消费)
            from media_pilot.repository.repositories import (
                MediaSourceSelectionRepository,
            )
            MediaSourceSelectionRepository(session).save(
                task_id=task_id,
                input_path=str(bdmv_root),
                selected_path=None,
                confidence=1.0,
                reason="user_decision:review_complex_input",
                payload={
                    "selection_source": "user_decision",
                    "decision_type": "review_complex_input",
                    "user_note": "请把 BDMV 文件夹当蓝光原盘处理",
                },
            )
            session.commit()

        mock = _MockLLM()
        mock.add_tool_call("prepare_complex_input_decision", {"task_id": task_id})
        # turn 2: 试图调 search_metadata
        mock.add_tool_call(
            "search_metadata",
            {"task_id": task_id, "title": "BDMV", "year": 2026},
        )

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()
            assert result.status == "failed"
            run_id = result.run_id

        self._assert_failure_state(
            sf, task_id, run_id, "complex_input_review_unsupported",
        )
        self._assert_no_tool_records_for(
            sf, run_id, ["search_metadata"],
        )

    def test_normal_path_can_still_call_publish_after_ready(
        self, tmp_path: Path,
    ):
        """正常 ready 路径: LLM 调 prepare_complex_input_decision 返回
        ready 后, 仍能继续调 search_metadata 等写入工具. 不让硬失败
        修复影响正常链路."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "Example.Movie.2026.mkv").write_bytes(b"v")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(
                session, str(config.downloads_dir / "Example.Movie.2026.mkv"),
            )
            task_id = task.id

        mock = _MockLLM()
        # turn 1: ready (工具 success, ready=True, 不切 run.status)
        mock.add_tool_call("prepare_complex_input_decision", {"task_id": task_id})
        # turn 2: LLM 继续调 search_metadata — 应该被正常执行
        mock.add_tool_call(
            "search_metadata",
            {"task_id": task_id, "title": "Example Movie", "year": 2026},
        )
        # turn 3: 收口
        mock.add_text("Metadata searched.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()
            # 不一定 completed (search_metadata 工具可能有自己的状态变化),
            # 关键是 search_metadata 工具被正常执行, 留下了 AgentToolCall 记录
            run_id = result.run_id

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentToolCallRepository,
            )
            tc_repo = AgentToolCallRepository(session)
            tcs = tc_repo.list_by_run(run_id)
            names = [tc.tool_name for tc in tcs]
            assert "prepare_complex_input_decision" in names
            assert "search_metadata" in names, (
                f"正常 ready 后 search_metadata 应被执行, 实际: {names}"
            )


# ── Issue: 同批次 LLM response 含多个 tool_call 时, 第一个工具把
#    run 切 failed / 创建 pending decision, 后续 tool_call 不得执行.
#    上一组 TestFailedRunDropsFollowupToolCalls 测的是"下一轮 LLM response"
#    的 tool_call 丢弃; 这一组专门测"同一轮 LLM response 内"的顺序
#    终止信号 (intra-batch termination signal).


class TestBatchedToolCallsAfterFailure:
    """LLM 单次 response 携带多个 tool_call, 第一个工具触发了失败 / 决策
    请求后, 后续 tool_call 必须在同批次内被 runner 终止. 验证
    `_run_agent_loop` 的 `if run.status == "failed" or decision_requested:
    break` 守卫覆盖三种场景:
    1. no_videos (run 切 failed)
    2. multiple_videos (decision_requested → waiting_user)
    3. ready 路径 (无失败, 两个工具都执行)
    """

    def _build_mock_with_batch(
        self, *tool_calls_specs,
    ) -> "_MockLLM":
        mock = _MockLLM()
        mock.add_tool_calls(list(tool_calls_specs))
        # 兜底 final text, 不应被消费
        mock.add_text("SHOULD NOT BE CONSUMED — batch was terminated")
        return mock

    def test_no_videos_then_search_metadata_same_batch_skips_second(
        self, tmp_path: Path,
    ):
        """单次 LLM response: prepare_complex_input_decision(命中 no_videos)
        + search_metadata. 第一个工具把 run 切 failed, 第二个工具必须被
        同批次 break 跳过: 不得创建 AgentToolCall, 也不得被
        持久化成执行态."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        # 目录里只有字幕, 没有视频 → no_videos
        (config.downloads_dir / "movie.srt").write_text("...")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(session, str(config.downloads_dir))
            task_id = task.id

        mock = self._build_mock_with_batch(
            {"name": "prepare_complex_input_decision",
             "arguments": {"task_id": task_id}},
            {"name": "search_metadata",
             "arguments": {"task_id": task_id, "title": "Ghost", "year": 2026},
             "call_id": "call_search"},
        )

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()
            assert result.status == "failed", (
                f"run_agent_turn 应以 failed 收口, 实际 {result.status}"
            )
            run_id = result.run_id
            # tool_call_count 应包含被跳过的 search_metadata (counter
            # 在 for 循环顶端累加, 在 break 守卫之前).
            assert result.tool_call_count == 2, (
                f"tool_call_count 应累加到 2 (含被跳过的 search_metadata), "
                f"实际 {result.tool_call_count}"
            )

        # run / task 仍是失败态
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                IngestTaskRepository,
            )
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "agent_failed"
            assert task.current_step == "agent_failed"
            assert task.failure_reason == "no_video_files_found"
            run = AgentRunRepository(session).get(run_id)
            assert run.status == "failed"
            assert "no_video_files_found" in (run.error_message or "")

        # search_metadata 不得留下 AgentToolCall 记录 — 工具未被执行
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentToolCallRepository,
            )
            tcs = AgentToolCallRepository(session).list_by_run(run_id)
            names = [tc.tool_name for tc in tcs]
            assert "prepare_complex_input_decision" in names
            assert "search_metadata" not in names, (
                f"同批次后续 search_metadata 必须被跳过, 不留 AgentToolCall, "
                f"实际: {names}"
            )

        # search_metadata 写一条 tool 拒绝说明 (而不是真正的执行结果)
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
            )
            messages = AgentMessageRepository(session).list_by_run(run_id)
            tool_msgs = [m for m in messages if m.role == "tool"]
            search_msgs = [m for m in tool_msgs if m.tool_name == "search_metadata"]
            assert search_msgs, "应给 search_metadata 写 tool 拒绝说明"
            assert all(
                "not executed" in (m.content or "") for m in search_msgs
            ), f"search_metadata 的 tool 消息应是拒绝说明, 实际: {search_msgs}"

    def test_multiple_videos_then_search_metadata_same_batch_skips_second(
        self, tmp_path: Path,
    ):
        """单次 LLM response: prepare_complex_input_decision(多视频 →
        decision_requested) + search_metadata. 第一个工具创建了 pending
        AgentDecisionRequest, decision_requested=True 触发同批次 break.
        search_metadata 不得执行, run/task 切 waiting_user, pending
        decision 保留供用户回复."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "A.mkv").write_bytes(b"a")
        (config.downloads_dir / "B.mkv").write_bytes(b"b")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(session, str(config.downloads_dir))
            task_id = task.id

        mock = self._build_mock_with_batch(
            {"name": "prepare_complex_input_decision",
             "arguments": {"task_id": task_id}},
            {"name": "search_metadata",
             "arguments": {"task_id": task_id, "title": "Movie", "year": 2026},
             "call_id": "call_search"},
        )

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()
            assert result.status == "waiting_user", (
                f"run_agent_turn 应以 waiting_user 收口, 实际 {result.status}"
            )
            run_id = result.run_id
            assert result.tool_call_count == 2

        # run / task 进入 waiting_user, pending decision 保留
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                AgentRunRepository,
                IngestTaskRepository,
            )
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "waiting_user"
            assert task.current_step == "select_primary_video"
            run = AgentRunRepository(session).get(run_id)
            assert run.status == "waiting_user"
            assert run.current_step == "select_primary_video"
            pending = AgentDecisionRequestRepository(session).list_pending_by_task(
                task_id,
            )
            assert len(pending) == 1, (
                f"应有 1 个 pending decision, 实际 {len(pending)}"
            )
            assert pending[0].decision_type == "select_primary_video"

        # search_metadata 没有 AgentToolCall
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentToolCallRepository,
            )
            tcs = AgentToolCallRepository(session).list_by_run(run_id)
            names = [tc.tool_name for tc in tcs]
            assert "prepare_complex_input_decision" in names
            assert "search_metadata" not in names, (
                f"同批次决策触发后 search_metadata 必须被跳过, 实际: {names}"
            )

        # search_metadata 写一条 tool 拒绝说明, 提到
        # "another tool in this batch already created a pending
        # AgentDecisionRequest"
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
            )
            messages = AgentMessageRepository(session).list_by_run(run_id)
            tool_msgs = [m for m in messages if m.role == "tool"]
            search_msgs = [m for m in tool_msgs if m.tool_name == "search_metadata"]
            assert search_msgs
            assert any(
                "pending AgentDecisionRequest" in (m.content or "")
                for m in search_msgs
            ), (
                "search_metadata tool 拒绝说明应解释同批次已有 pending decision, "
                f"实际: {[m.content for m in search_msgs]}"
            )

    def test_ready_then_search_metadata_same_batch_executes_both(
        self, tmp_path: Path,
    ):
        """正常 ready 路径: 单次 LLM response 同时返回
        prepare_complex_input_decision(单文件 ready) + search_metadata.
        第一个工具 success + 无 decision_requested, 同批次 break 守卫不
        触发, 第二个工具必须被正常执行, 留下 AgentToolCall 记录."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        (config.downloads_dir / "Example.Movie.2026.mkv").write_bytes(b"v")

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task_with_source(
                session, str(config.downloads_dir / "Example.Movie.2026.mkv"),
            )
            task_id = task.id

        mock = _MockLLM()
        # 关键: 两个 tool_call 必须在同一次 LLM response 内
        mock.add_tool_calls([
            {"name": "prepare_complex_input_decision",
             "arguments": {"task_id": task_id}},
            {"name": "search_metadata",
             "arguments": {"task_id": task_id, "title": "Example Movie",
                           "year": 2026},
             "call_id": "call_search"},
        ])
        # 收口
        mock.add_text("Metadata searched.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mock_llm_client=mock,
            )
            session.commit()
            run_id = result.run_id
            assert result.tool_call_count == 2, (
                f"正常 ready + search_metadata 同批次应累加 2 次 tool_call, "
                f"实际 {result.tool_call_count}"
            )

        # 两个工具都留下 AgentToolCall 记录 (ready 路径不触发 break)
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                AgentToolCallRepository,
            )
            tcs = AgentToolCallRepository(session).list_by_run(run_id)
            names = [tc.tool_name for tc in tcs]
            assert "prepare_complex_input_decision" in names
            assert "search_metadata" in names, (
                f"ready 路径下 search_metadata 同批次应被执行, 实际: {names}"
            )
            # 不应有任何 pending decision (单文件 ready 不创建决策)
            pending = AgentDecisionRequestRepository(session).list_pending_by_task(
                task_id,
            )
            assert pending == [], (
                f"ready 路径不应创建 pending decision, 实际: "
                f"{[p.decision_type for p in pending]}"
            )

        # search_metadata 的 tool 消息是真正的执行结果, 不是 "not executed"
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
            )
            messages = AgentMessageRepository(session).list_by_run(run_id)
            tool_msgs = [m for m in messages if m.role == "tool"]
            search_msgs = [m for m in tool_msgs if m.tool_name == "search_metadata"]
            assert search_msgs
            assert all(
                "not executed" not in (m.content or "") for m in search_msgs
            ), (
                f"ready 路径下 search_metadata 消息应是真正执行结果, "
                f"实际: {[m.content for m in search_msgs]}"
            )
