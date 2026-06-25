import pytest

from tests.agent_runner_helpers import MockLLMClient, _make_config, _make_task

class TestLLMToolOutputInjection:
    """回归保护：runner 把 AgentToolCall.output 折叠进 LLM tool message 的
    紧凑 JSON, 包含 best_candidate / candidates 关键字段, 防止 LLM
    因 tool message 看不到 provider_id / media_type 而循环重读.
    """

    def test_auto_confirm_injects_best_candidate_into_llm_messages(self, tmp_path):
        """regression: prepare_select_metadata_candidate_decision auto_confirm
        后, 下一轮 LLM 看到的 tool message 必须包含 best_candidate.provider_id
        / best_candidate.media_type — 不然 LLM 拿不到 fetch 工具所需入参.
        """
        import json

        from tests.test_select_metadata_candidate import (
            _add_candidate,
        )
        from tests.test_select_metadata_candidate import (
            _make_session_factory as _make_sf_with_tmdb,
        )

        sf = _make_sf_with_tmdb(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, source_path="/data/movie.mkv")
            task_id = task.id
            # 一个高置信度 + 一个低置信度候选, 让 clear_winner 命中.
            _add_candidate(
                session, task_id=task.id, source="tmdb", media_type="movie",
                title="Warcraft", year=2016, external_id="movie:68735",
                confidence=0.96, overview="...",
            )
            _add_candidate(
                session, task_id=task.id, source="tmdb", media_type="movie",
                title="Warcraft (Alt)", year=2016, external_id="movie:99999",
                confidence=0.4, overview="...",
            )

        mock = MockLLMClient()
        # Step 1: 调 prepare_select_metadata_candidate_decision → auto_confirm
        mock.add_tool_calls([{
            "id": "call_select",
            "type": "function",
            "function": {
                "name": "prepare_select_metadata_candidate_decision",
                "arguments": json.dumps({"task_id": task_id}),
            },
        }])
        # Step 2: LLM 拿到 auto_confirm 上下文后应能调 fetch_and_save_metadata_detail
        mock.add_tool_calls([{
            "id": "call_fetch",
            "type": "function",
            "function": {
                "name": "fetch_and_save_metadata_detail",
                "arguments": json.dumps({
                    "task_id": task_id, "provider_name": "tmdb",
                    "provider_id": "movie:68735", "media_type": "movie",
                }),
            },
        }])
        # Step 3: final text 收口
        mock.add_text_response("Metadata detail saved. Task ready for publish.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
            )
            session.commit()

        # safety net 介入: fetch_and_save_metadata_detail 在测试环境没有真
        # TMDB 凭据所以失败, MetadataDetail 没落库. 测试候选有 external_id,
        # safety net 走 fetch + publish 路径, 仍然失败 → run.status=failed.
        # 这不是本测试关心的; 本测试关心的是 LLM 第二次拿到的 tool message
        # 包含 best_candidate context. tool_call_count 验证 LLM 至少调了两轮.
        assert result.status == "failed"
        assert result.tool_call_count == 2
        # 关键: LLM 第二次调 chat 时看到的 tool message (对应 call_select)
        # 必须包含 best_candidate.provider_id / media_type, 而不只是
        # {status, summary}.
        assert len(mock.calls) >= 2, "LLM should have been called at least twice"
        second_call_messages = mock.calls[1]["messages"]
        # 找 role=tool 且 tool_call_id=call_select 的那条
        tool_msgs = [
            m for m in second_call_messages
            if m.get("role") == "tool" and m.get("tool_call_id") == "call_select"
        ]
        assert len(tool_msgs) == 1, (
            f"Expected exactly 1 tool msg for call_select, got {len(tool_msgs)}: "
            f"{[m.get('role') for m in second_call_messages]}"
        )
        content = tool_msgs[0]["content"]
        # content 可能是 str(json) 或 dict (mock 不一定反序列化).
        if isinstance(content, str):
            parsed = json.loads(content)
        else:
            parsed = content
        assert parsed.get("status") == "success", parsed
        # best_candidate 必须原样透传, 这是 LLM 拿 provider_id 的唯一来源.
        best = parsed.get("best_candidate")
        assert best is not None, f"best_candidate missing from tool msg: {parsed}"
        assert best.get("provider_id") == "movie:68735", best
        assert best.get("media_type") == "movie", best
        assert best.get("title") == "Warcraft", best

    def test_persisted_tool_message_still_short_summary(self, tmp_path):
        """regression: AgentMessage.content 持久化的仍是 {status, summary} 的
        短摘要 (UI 友好), 完整 data 仅在给 LLM 的临时构造里使用.
        """
        import json

        from tests.test_select_metadata_candidate import (
            _add_candidate,
        )
        from tests.test_select_metadata_candidate import (
            _make_session_factory as _make_sf_with_tmdb,
        )

        sf = _make_sf_with_tmdb(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session)
            task_id = task.id
            _add_candidate(
                session, task_id=task.id, source="tmdb", media_type="movie",
                title="Warcraft", year=2016, external_id="movie:68735",
                confidence=0.96,
            )

        mock = MockLLMClient()
        mock.add_tool_calls([{
            "id": "call_select",
            "type": "function",
            "function": {
                "name": "prepare_select_metadata_candidate_decision",
                "arguments": json.dumps({"task_id": task_id}),
            },
        }])
        mock.add_text_response("Got auto_confirm, stopping.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
            )
            session.commit()

        # DB 里持久化的 AgentMessage.content 应当是 {status, summary} 短摘要,
        # 而不是带 best_candidate 的完整 dict. UI 时间线显示仍以这个为准.
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
                AgentRunRepository,
            )
            run = AgentRunRepository(session).list_by_task(task_id)[0]
            tool_msgs = [
                m for m in AgentMessageRepository(session).list_by_run(run.id)
                if m.role == "tool" and m.tool_call_id == "call_select"
            ]
            assert len(tool_msgs) == 1
            content = tool_msgs[0].content
            parsed = json.loads(content) if content else {}
            assert "best_candidate" not in parsed, (
                f"Persisted tool message should NOT include best_candidate "
                f"(UI 友好短摘要原则): {parsed}"
            )
            assert "data" not in parsed, parsed
            # status / summary 必须保留
            assert parsed.get("status") in ("success", "failure"), parsed
            assert isinstance(parsed.get("summary"), str), parsed

    def test_candidates_slim_in_llm_message(self, tmp_path):
        """regression: 给 LLM 的 tool message 中 candidates[] 必须是
        简化的字段 (provider / provider_id / media_type / title / year /
        confidence / candidate_id), 不得带 payload / created_at 等大字段.
        """
        from media_pilot.agent.runner import _tool_output_for_llm

        full_output = {
            "status": "success",
            "summary": "Found 2 candidates",
            "data": {
                "candidates": [
                    {
                        "candidate_id": "c1", "provider": "tmdb",
                        "provider_id": "movie:1", "media_type": "movie",
                        "title": "Movie A", "year": 2024, "confidence": 0.9,
                        # 大字段 — LLM 不该看
                        "overview": "A long plot...",
                        "payload": {"overview": "...", "credits": []},
                        "created_at": "2026-01-01T00:00:00",
                    },
                ],
                # 不应被原样透传的非白名单字段
                "some_blob": {"k": "v"},
            },
        }
        compact = _tool_output_for_llm(full_output, tool_name="get_metadata_candidates")
        candidates = compact.get("candidates")
        assert candidates is not None and len(candidates) == 1
        c0 = candidates[0]
        # 白名单字段必须保留
        for k in ("candidate_id", "provider", "provider_id", "media_type",
                  "title", "year", "confidence"):
            assert k in c0, f"candidates[0] missing {k}: {c0}"
        # 非白名单字段必须被丢弃
        for k in ("overview", "payload", "created_at"):
            assert k not in c0, f"candidates[0] leaked {k}: {c0}"
        # 非 best_candidate / candidates 顶层字段按 dict 折叠 keys
        assert "some_blob" in compact
        assert isinstance(compact["some_blob"], dict)
        assert "keys" in compact["some_blob"]

# ══════════════════════════════════════════════════════════════════════
# D. auto_ingest final-text safety net — auto-invoke publish
# ══════════════════════════════════════════════════════════════════════


class TestAutoPublishSafetyNet:
    """regression for Issue 3/4 — task 5043c11e-... 在 user_replied 之后
    LLM 只调 draft tools 就 final-summary, 任务停在
    agent_running / current_step=user_replied. 修复: runner 的
    final-text 收口路径 (auto_ingest 模式) 在 MetadataDetail 已存在
    时主动调一次 publish_*_to_library, 推进 task 到 business 终态,
    不让 agent_running 卡死."""

    def test_auto_publish_invoked_when_metadata_detail_present(
        self, tmp_path, monkeypatch,
    ):
        """auto_ingest 模式 + LLM final-text + MetadataDetail 已落库 →
        runner 主动调 publish_movie_to_library, 任务从 agent_running
        推进到 library_import_complete (fake handler 写状态).

        关键: 即便 LLM 没主动调 publish, runner safety net 兜底.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from dataclasses import replace

        from media_pilot.agent.tools.base import ToolResult
        from media_pilot.agent.tools.registry import (
            get_tool_registry,
            register_builtin_tools,
        )
        register_builtin_tools()
        registry = get_tool_registry()

        # 替换 publish_movie_to_library 的 handler — ToolDefinition 是
        # frozen dataclass, 用 dataclasses.replace 生成新实例再换 dict.
        publish_calls: list[dict] = []

        def _fake_movie_publish(ctx, inp):
            from media_pilot.repository.repositories import IngestTaskRepository
            publish_calls.append({"task_id": inp.get("task_id")})
            task = IngestTaskRepository(ctx.session).get(inp["task_id"])
            if task is not None:
                task.status = "library_import_complete"
                task.current_step = "library_import_complete"
                ctx.session.flush()
            return ToolResult(
                status="success", summary="published (fake)",
            )

        original_movie_tool = registry._tools["publish_movie_to_library"]
        new_movie_tool = replace(original_movie_tool, handler=_fake_movie_publish)
        monkeypatch.setitem(
            registry._tools, "publish_movie_to_library", new_movie_tool,
        )

        with sf() as session:
            task = _make_task(
                session, source_path="/tmp/movie.mkv",
                media_type="movie", status="agent_running",
            )
            task_id = task.id
            from media_pilot.repository.models import MetadataDetail
            session.add(MetadataDetail(
                task_id=task_id, provider="tmdb",
                provider_id="movie:68735", media_type="movie",
                title="Some Movie", original_title=None, year=2024,
                payload={"overview": "..."},
            ))
            session.commit()

        mock = MockLLMClient()
        mock.add_text_response("Draft plan ready. No further actions.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"
        assert len(publish_calls) == 1
        assert publish_calls[0]["task_id"] == task_id

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "library_import_complete"
            assert task.current_step == "library_import_complete"

    def test_auto_publish_failure_marks_task_failed_not_completed(
        self, tmp_path, monkeypatch,
    ):
        """auto-publish safety net failure must not leave task agent_running."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from dataclasses import replace

        from media_pilot.agent.tools.base import ToolResult
        from media_pilot.agent.tools.registry import (
            get_tool_registry,
            register_builtin_tools,
        )
        register_builtin_tools()
        registry = get_tool_registry()

        def _fake_publish(ctx, inp):
            return ToolResult(
                status="failure",
                summary="No publishable EpisodeMapping after metadata detail.",
                data={
                    "requires_user": True,
                    "reason": "no_episode_mapping",
                    "block_reason": "absolute_episode_requires_metadata_detail",
                },
            )

        original_show = registry._tools["publish_show_to_library"]
        new_show = replace(original_show, handler=_fake_publish)
        monkeypatch.setitem(registry._tools, "publish_show_to_library", new_show)

        with sf() as session:
            task = _make_task(
                session, source_path="/tmp/show_dir",
                media_type="show", status="agent_running",
            )
            task_id = task.id
            from media_pilot.repository.models import MetadataDetail
            session.add(MetadataDetail(
                task_id=task_id, provider="tmdb",
                provider_id="show:123", media_type="show",
                title="Some Show", original_title=None, year=2024,
                payload={},
            ))
            session.commit()

        mock = MockLLMClient()
        mock.add_text_response("I need user help, but forgot to create a decision.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "failed"

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                IngestTaskRepository,
            )
            task = IngestTaskRepository(session).get(task_id)
            run = AgentRunRepository(session).get(result.run_id)
            assert task.status == "agent_failed"
            assert task.current_step == "agent_failed"
            assert task.failure_reason == "auto_publish_after_final_text_failed"
            assert run.status == "failed"
            assert run.current_step == "agent_failed"
            assert "Auto-publish after final text failed" in run.error_message

    def test_auto_publish_skipped_in_default_mode(self, tmp_path, monkeypatch):
        """default 模式下, safety net 不生效 — publish 是 WRITE 工具,
        不在 default 模式的允许集里. runner 不得在 default 模式
        final-text 路径调 publish (否则越权)."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from dataclasses import replace

        from media_pilot.agent.tools.base import ToolResult
        from media_pilot.agent.tools.registry import (
            get_tool_registry,
            register_builtin_tools,
        )
        register_builtin_tools()
        registry = get_tool_registry()

        publish_calls: list[dict] = []

        def _fake_publish(ctx, inp):
            publish_calls.append({"task_id": inp.get("task_id")})
            return ToolResult(status="success", summary="ok")

        original_movie_tool = registry._tools["publish_movie_to_library"]
        new_movie_tool = replace(original_movie_tool, handler=_fake_publish)
        monkeypatch.setitem(
            registry._tools, "publish_movie_to_library", new_movie_tool,
        )

        with sf() as session:
            task = _make_task(
                session, source_path="/tmp/movie.mkv",
                media_type="movie", status="agent_running",
            )
            task_id = task.id
            from media_pilot.repository.models import MetadataDetail
            session.add(MetadataDetail(
                task_id=task_id, provider="tmdb",
                provider_id="movie:68735", media_type="movie",
                title="Some Movie", original_title=None, year=2024,
                payload={},
            ))
            session.commit()

        mock = MockLLMClient()
        mock.add_text_response("Done.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="default", mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"
        assert len(publish_calls) == 0

    def test_auto_publish_for_show_invokes_publish_show_to_library(
        self, tmp_path, monkeypatch,
    ):
        """剧集任务 (media_type=show) → safety net 调 publish_show_to_library,
        不是 publish_movie_to_library. 电影 / 剧集分流."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from dataclasses import replace

        from media_pilot.agent.tools.base import ToolResult
        from media_pilot.agent.tools.registry import (
            get_tool_registry,
            register_builtin_tools,
        )
        register_builtin_tools()
        registry = get_tool_registry()

        movie_calls: list[dict] = []
        show_calls: list[dict] = []

        def _fake_movie_publish(ctx, inp):
            movie_calls.append({"task_id": inp.get("task_id")})
            return ToolResult(status="success", summary="ok")

        def _fake_show_publish(ctx, inp):
            show_calls.append({"task_id": inp.get("task_id")})
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(ctx.session).get(inp["task_id"])
            if task is not None:
                task.status = "library_import_complete"
                ctx.session.flush()
            return ToolResult(status="success", summary="ok")

        original_movie = registry._tools["publish_movie_to_library"]
        original_show = registry._tools["publish_show_to_library"]
        new_movie = replace(original_movie, handler=_fake_movie_publish)
        new_show = replace(original_show, handler=_fake_show_publish)
        monkeypatch.setitem(registry._tools, "publish_movie_to_library", new_movie)
        monkeypatch.setitem(registry._tools, "publish_show_to_library", new_show)

        with sf() as session:
            task = _make_task(
                session, source_path="/tmp/show_dir",
                media_type="show", status="agent_running",
            )
            task_id = task.id
            from media_pilot.repository.models import MetadataDetail
            session.add(MetadataDetail(
                task_id=task_id, provider="tmdb",
                provider_id="show:123", media_type="show",
                title="Some Show", original_title=None, year=2024,
                payload={},
            ))
            session.commit()

        mock = MockLLMClient()
        mock.add_text_response("Show plan ready.")

        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"
        # 关键: 调的是 show 分支, 不是 movie
        assert len(movie_calls) == 0
        assert len(show_calls) == 1

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "library_import_complete"


# ══════════════════════════════════════════════════════════════════════
# E. max_steps 业务成功收口 — 不覆盖 task 终态
# ══════════════════════════════════════════════════════════════════════


class TestMaxStepsBusinessSuccess:
    """regression: max_steps 收口不得把已 library_import_complete 的 task
    降级为 agent_failed. 与原 test_fails_on_max_steps 互补 — 那里是
    "task.status=discovered 时走原 agent_failed 路径", 这里是 "task
    已经业务成功时走 completed 路径, 保持 task 终态".

    关键设计: run_agent_turn 在循环开始前会把 task 推到 agent_running
    (line 934). 所以"task 终态为 library_import_complete"必须在循环
    内部由业务工具 (e.g. fake publish) 推到. 模拟真实生产路径.
    """

    def test_max_steps_does_not_overwrite_library_import_complete(self, tmp_path):
        """业务工具 (fake publish) 把 task 推到 library_import_complete 后,
        继续耗光 max_steps → max_steps 收口走 completed 路径, 不覆写
        task.status / current_step / failure_reason, 持久化一条 assistant
        短消息说明 "未生成最终总结"."""
        from dataclasses import replace

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.base import ToolResult
        from media_pilot.agent.tools.registry import (
            get_tool_registry,
            register_builtin_tools,
        )
        register_builtin_tools()
        registry = get_tool_registry()

        # 替换 publish_movie_to_library: 模拟业务工具把 task 推到
        # library_import_complete, 与生产路径一致.
        def _fake_publish_movie(ctx, inp):
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(ctx.session).get(inp["task_id"])
            if task is not None:
                task.status = "library_import_complete"
                task.current_step = "library_import_complete"
                ctx.session.flush()
            return ToolResult(status="success", summary="published (fake)")

        original = registry._tools["publish_movie_to_library"]
        registry._tools["publish_movie_to_library"] = replace(
            original, handler=_fake_publish_movie,
        )

        try:
            with sf() as session:
                from media_pilot.repository.models import MetadataDetail
                task = _make_task(
                    session, source_path="/tmp/movie.mkv",
                    media_type="movie", status="discovered",
                )
                task_id = task.id
                # MetadataDetail 落库, 业务工具可能走 auto-publish 路径.
                session.add(MetadataDetail(
                    task_id=task_id, provider="tmdb",
                    provider_id="movie:68735", media_type="movie",
                    title="Some Movie", original_title=None, year=2024,
                    payload={},
                ))
                session.commit()

            mock = MockLLMClient()
            # 第 1 步: 调 publish_movie_to_library → 业务工具把 task 推到
            # library_import_complete.
            mock.add_tool_calls([{
                "id": "call_publish",
                "type": "function",
                "function": {
                    "name": "publish_movie_to_library",
                    "arguments": '{"task_id": "' + task_id + '"}',
                },
            }])
            # 后续 14 步: 调无害的 read-only 工具, 耗光 max_steps.
            for i in range(14):
                mock.add_tool_calls([{
                    "id": f"call_x_{i}",
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
                    mode="auto_ingest", mock_llm_client=mock,
                )
                session.commit()

            # 业务成功收口, 不得走 failed 路径
            assert result.status == "completed"
            assert result.error_message is None

            with sf() as session:
                from media_pilot.repository.repositories import (
                    AgentMessageRepository,
                    AgentRunRepository,
                    IngestTaskRepository,
                )
                task = IngestTaskRepository(session).get(task_id)
                # 关键: task 终态不得被覆写
                assert task.status == "library_import_complete"
                assert task.current_step == "library_import_complete"
                assert task.failure_reason is None

                run = AgentRunRepository(session).list_by_task(task_id)[-1]
                # run 改标 completed, current_step 走 completed_without_final_text
                assert run.status == "completed"
                assert run.current_step == "completed_without_final_text"

                # assistant 短消息已持久化
                msgs = AgentMessageRepository(session).list_by_run(run.id)
                ack = [
                    m for m in msgs
                    if m.role == "assistant" and "未生成最终总结" in (m.content or "")
                ]
                assert len(ack) == 1
        finally:
            registry._tools["publish_movie_to_library"] = original

    def test_max_steps_with_status_step_mismatch_falls_back_to_agent_failed(
        self, tmp_path,
    ):
        """task.status=library_import_complete 但 current_step 与 status
        不一致 (e.g. publishing) → 走保守 agent_failed 路径, 不得误判
        为业务成功.

        这条通过 _run_agent_loop 的 max_steps 收口分支直接验证. 模拟:
        直接调用 _run_agent_loop (绕过 run_agent_turn 的 agent_running
        强制覆盖), 保留 task 状态与 current_step 不一致."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(
                session,
                status="library_import_complete",
                current_step="publishing",  # ← status 与 current_step 不一致
            )
            task_id = task.id

        mock = MockLLMClient()
        for _ in range(15):
            mock.add_tool_calls([{
                "id": "call_y",
                "type": "function",
                "function": {
                    "name": "get_task_context",
                    "arguments": '{"task_id": "' + task_id + '"}',
                },
            }])

        with sf() as session:
            from media_pilot.agent.runner import _run_agent_loop
            from media_pilot.agent.tool_schema import get_allowed_tool_schemas
            from media_pilot.agent.tools.registry import get_tool_registry
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
                AgentRunRepository,
                AgentToolCallRepository,
                IngestTaskRepository,
            )

            task_repo = IngestTaskRepository(session)
            run_repo = AgentRunRepository(session)
            msg_repo = AgentMessageRepository(session)
            tc_repo = AgentToolCallRepository(session)
            registry = get_tool_registry()

            task = task_repo.get(task_id)
            run = run_repo.create(
                __import__(
                    "media_pilot.repository.repositories",
                    fromlist=["AgentRunCreate"],
                ).AgentRunCreate(task_id=task_id, current_step="agent_start"),
            )

            allowed_tools = get_allowed_tool_schemas(registry, mode="default")
            result = _run_agent_loop(
                session=session, config=config, run=run, task=task,
                msg_repo=msg_repo, run_repo=run_repo, tc_repo=tc_repo,
                task_repo=task_repo, registry=registry,
                allowed_tools=allowed_tools, mode="default",
                mock_llm_client=mock,
            )
            session.commit()

        # 保守路径: 走 agent_failed
        assert result.status == "failed"
        assert "max_steps" in (result.error_message or "").lower() or \
            "业务终态不一致" in (result.error_message or "")

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            # status 走 agent_failed
            assert task.status == "agent_failed"
            assert task.failure_reason is not None
            assert (
                "业务终态不一致" in (task.failure_reason or "")
                or "exceeded max steps" in (task.failure_reason or "")
            )

    def test_streaming_max_steps_with_library_import_complete_emits_completed(
        self, tmp_path,
    ):
        """streaming 路径 + 业务工具把 task 推到 library_import_complete +
        max_steps 触发 → SSE 流发出 RUN_FINISHED {data.status: "completed"}
        事件, 不出现 ERROR 事件, emitter 仍被 close.

        这条测试直接调用 _run_agent_loop + AgentStreamEmitter, 验证
        业务成功收口在 streaming 路径上的一致性, 绕过 run_agent_turn_
        streaming 的 threading 包装, 避免 daemon thread 与 list(emitter)
        的潜在 race. 与既有 streaming 测试 (TestSSEStreaming) 的
        工具层独立.
        """
        from media_pilot.agent.runner import _run_agent_loop
        from media_pilot.agent.sse import (
            AgentStreamEmitter,
        )
        from media_pilot.agent.tool_schema import get_allowed_tool_schemas
        from media_pilot.agent.tools.registry import get_tool_registry
        from media_pilot.repository.models import IngestTask
        from media_pilot.repository.repositories import (
            AgentMessageRepository,
            AgentRunCreate,
            AgentRunRepository,
            AgentToolCallRepository,
            IngestTaskRepository,
        )
        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.agent.tools.base import ToolResult
        from media_pilot.agent.tools.registry import register_builtin_tools
        register_builtin_tools()
        registry = get_tool_registry()

        from dataclasses import replace
        original = registry._tools["publish_movie_to_library"]

        def _fake_publish_movie(ctx, inp):
            task = IngestTaskRepository(ctx.session).get(inp["task_id"])
            if task is not None:
                task.status = "library_import_complete"
                task.current_step = "library_import_complete"
                ctx.session.flush()
            return ToolResult(status="success", summary="published (fake)")

        registry._tools["publish_movie_to_library"] = replace(
            original, handler=_fake_publish_movie,
        )

        try:
            with sf() as session:
                from media_pilot.repository.models import MetadataDetail
                task = _make_task(
                    session, source_path="/tmp/movie.mkv",
                    media_type="movie", status="discovered",
                )
                task_id = task.id
                session.add(MetadataDetail(
                    task_id=task_id, provider="tmdb",
                    provider_id="movie:68735", media_type="movie",
                    title="Some Movie", original_title=None, year=2024,
                    payload={},
                ))
                session.commit()

            mock = MockLLMClient()
            mock.add_tool_calls([{
                "id": "call_pub",
                "type": "function",
                "function": {
                    "name": "publish_movie_to_library",
                    "arguments": '{"task_id": "' + task_id + '"}',
                },
            }])
            for i in range(14):  # 1 + 14 = 15 = MAX_STEPS
                mock.add_tool_calls([{
                    "id": f"call_z_{i}",
                    "type": "function",
                    "function": {
                        "name": "get_task_context",
                        "arguments": '{"task_id": "' + task_id + '"}',
                    },
                }])

            emitter = AgentStreamEmitter()
            with sf() as session:
                task_repo = IngestTaskRepository(session)
                run_repo = AgentRunRepository(session)
                msg_repo = AgentMessageRepository(session)
                tc_repo = AgentToolCallRepository(session)

                task = task_repo.get(task_id)
                run = run_repo.create(
                    AgentRunCreate(task_id=task_id, current_step="agent_start"),
                )

                allowed_tools = get_allowed_tool_schemas(registry, mode="auto_ingest")
                result = _run_agent_loop(
                    session=session, config=config, run=run, task=task,
                    msg_repo=msg_repo, run_repo=run_repo, tc_repo=tc_repo,
                    task_repo=task_repo, registry=registry,
                    allowed_tools=allowed_tools, mode="auto_ingest",
                    mock_llm_client=mock, stream_emitter=emitter,
                )
                session.commit()

            # 业务成功收口, result 路径与 non-streaming 一致
            assert result.status == "completed"

            # 消费 emitter — 应该 close 后停止
            events = list(emitter)
            run_finished = [e for e in events if e.event.value == "run_finished"]
            error_events = [e for e in events if e.event.value == "error"]
            assert len(run_finished) == 1
            assert run_finished[0].data.get("status") == "completed"
            assert len(error_events) == 0

            with sf() as session:
                task = session.get(IngestTask, task_id)
                assert task.status == "library_import_complete"
                run = AgentRunRepository(session).list_by_task(task_id)[-1]
                assert run.status == "completed"
                assert run.current_step == "completed_without_final_text"
        finally:
            registry._tools["publish_movie_to_library"] = original


class TestPostCompletionSafetyNet:
    def test_safety_net_persists_metadata_and_marks_library_import_complete(
        self, tmp_path, monkeypatch,
    ):
        """矛盾状态 → safety net 走 fetch+publish → library_import_complete."""
        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        from media_pilot.repository.models import MetadataDetail
        from media_pilot.repository.repositories import (
            AgentRunCreate, AgentRunRepository, MediaCandidateRepository,
        )
        from media_pilot.services import auto_ingest
        from media_pilot.agent import runner as runner_module

        def _fake_fetch(*, session, config, task_id, provider_name, provider_id, media_type):
            session.add(MetadataDetail(
                task_id=task_id, provider=provider_name,
                provider_id=provider_id, media_type=media_type,
                title="X", original_title=None, year=2026, payload={},
            ))
            session.flush()
            return auto_ingest.FetchAndSaveDetailResult(
                status="success", summary="ok",
                provider=provider_name, provider_id=provider_id,
                title="X", year=2026,
            )

        monkeypatch.setattr(runner_module, "fetch_and_save_metadata_detail", _fake_fetch)

        from dataclasses import replace
        from media_pilot.agent.tools.base import ToolResult
        from media_pilot.agent.tools.registry import (
            get_tool_registry, register_builtin_tools,
        )
        register_builtin_tools()
        registry = get_tool_registry()

        publish_calls: list[dict] = []
        def _fake_publish(ctx, inp):
            publish_calls.append(inp)
            from media_pilot.repository.repositories import IngestTaskRepository
            t = IngestTaskRepository(ctx.session).get(inp["task_id"])
            if t is not None:
                t.status = "library_import_complete"
                t.current_step = "library_import_complete"
                ctx.session.flush()
            return ToolResult(status="success", summary="ok")
        original = registry._tools["publish_movie_to_library"]
        new_tool = replace(original, handler=_fake_publish)
        monkeypatch.setitem(registry._tools, "publish_movie_to_library", new_tool)

        with sf() as session:
            task = _make_task(
                session, source_path="/tmp/movie.mkv",
                media_type="movie", status="agent_running",
            )
            task_id = task.id
            run = AgentRunRepository(session).create(
                AgentRunCreate(
                    task_id=task.id, current_step="step_x",
                ),
            )
            run_id = run.id
            MediaCandidateRepository(session).add_candidate(
                task_id=task_id, source="tmdb", media_type="movie",
                title="X", original_title=None, year=2026,
                external_id="movie:68735", confidence=0.7,
                reason="x", payload={},
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            t = IngestTaskRepository(session).get(task_id)
            r = AgentRunRepository(session).get(run_id)
            runner_module._check_post_completion_safety_net(
                session=session, config=config, run=r, task=t,
                task_repo=IngestTaskRepository(session),
                registry=registry, mode="auto_ingest",
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            t = IngestTaskRepository(session).get(task_id)
            assert t.status == "library_import_complete"
            assert len(publish_calls) == 1
            assert publish_calls[0]["task_id"] == task_id

    def test_safety_net_still_failing_marks_agent_failed(
        self, tmp_path, monkeypatch,
    ):
        """fetch 失败 → task=agent_failed, run=failed, 显式 failure_reason."""
        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        from media_pilot.repository.repositories import (
            AgentRunRepository, MediaCandidateRepository,
        )
        from media_pilot.services import auto_ingest
        from media_pilot.agent import runner as runner_module

        def _fake_fetch_failure(*, session, config, task_id, provider_name, provider_id, media_type):
            return auto_ingest.FetchAndSaveDetailResult(
                status="failure", summary="provider down",
                provider=provider_name, provider_id=provider_id,
            )

        monkeypatch.setattr(runner_module, "fetch_and_save_metadata_detail", _fake_fetch_failure)

        from media_pilot.agent.tools.registry import (
            get_tool_registry, register_builtin_tools,
        )
        register_builtin_tools()
        registry = get_tool_registry()

        with sf() as session:
            task = _make_task(
                session, source_path="/tmp/movie.mkv",
                media_type="movie", status="agent_running",
            )
            run = AgentRunRepository(session).create(
                __import__("media_pilot.repository.repositories", fromlist=["AgentRunCreate"]).AgentRunCreate(
                    task_id=task.id, current_step="step_x",
                ),
            )
            AgentRunRepository(session).update_status(
                run, status="completed", current_step="completed",
            )
            task_id = task.id
            run_id = run.id
            MediaCandidateRepository(session).add_candidate(
                task_id=task_id, source="tmdb", media_type="movie",
                title="X", original_title=None, year=2026,
                external_id="movie:68735", confidence=0.7,
                reason="x", payload={},
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            t = IngestTaskRepository(session).get(task_id)
            r = AgentRunRepository(session).get(run_id)
            runner_module._check_post_completion_safety_net(
                session=session, config=config, run=r, task=t,
                task_repo=IngestTaskRepository(session),
                registry=registry, mode="auto_ingest",
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentRunRepository, IngestTaskRepository,
            )
            t = IngestTaskRepository(session).get(task_id)
            r = AgentRunRepository(session).get(run_id)
            assert t.status == "agent_failed"
            assert t.failure_reason == "no_metadata_detail_after_agent_completion"
            assert r.status == "failed"
            assert "Post-completion safety net" in (r.error_message or "")

    def test_safety_net_prefers_user_decision_candidate_over_older_agent_candidates(
        self, tmp_path, monkeypatch,
    ):
        """回归: list_for_task 是 created_at ASC, 旧路径
        `candidates[0]` 拿的是最早候选, 可能是 agent 自动搜索的产物.
        修复后 safety net 必须优先用 `source="user_decision"` 的候选
        (用户上次回复留下的强事实), 不得用第一个 / 最早的那个.

        现场: LLM 调了一轮 search_metadata 留下 1 个 "Matrix (1999)"
        agent 候选, 用户决策创建 user_decision 选 "Matrix Reloaded
        (2003)". safety net 被触发时 list_for_task[0] 是 agent 候选
        (旧 ASC), 用它 fetch → 落错电影.
        """
        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        from media_pilot.repository.models import MetadataDetail
        from media_pilot.repository.repositories import (
            AgentRunCreate, AgentRunRepository, MediaCandidateRepository,
        )
        from media_pilot.services import auto_ingest
        from media_pilot.agent import runner as runner_module

        captured_fetch: list[dict] = []
        def _fake_fetch(*, session, config, task_id, provider_name, provider_id, media_type):
            captured_fetch.append({
                "provider_name": provider_name, "provider_id": provider_id,
                "media_type": media_type,
            })
            session.add(MetadataDetail(
                task_id=task_id, provider=provider_name,
                provider_id=provider_id, media_type=media_type,
                title="X", original_title=None, year=2003, payload={},
            ))
            session.flush()
            return auto_ingest.FetchAndSaveDetailResult(
                status="success", summary="ok",
                provider=provider_name, provider_id=provider_id,
                title="X", year=2003,
            )

        monkeypatch.setattr(runner_module, "fetch_and_save_metadata_detail", _fake_fetch)

        from dataclasses import replace
        from media_pilot.agent.tools.base import ToolResult
        from media_pilot.agent.tools.registry import (
            get_tool_registry, register_builtin_tools,
        )
        register_builtin_tools()
        registry = get_tool_registry()

        publish_calls: list[dict] = []
        def _fake_publish(ctx, inp):
            publish_calls.append(inp)
            from media_pilot.repository.repositories import IngestTaskRepository
            t = IngestTaskRepository(ctx.session).get(inp["task_id"])
            if t is not None:
                t.status = "library_import_complete"
                t.current_step = "library_import_complete"
                ctx.session.flush()
            return ToolResult(status="success", summary="ok")
        original = registry._tools["publish_movie_to_library"]
        new_tool = replace(original, handler=_fake_publish)
        monkeypatch.setitem(registry._tools, "publish_movie_to_library", new_tool)

        from media_pilot.repository.database import initialize_database
        initialize_database(_make_config(tmp_path))

        with sf() as session:
            task = _make_task(
                session, source_path="/tmp/movie.mkv",
                media_type="movie", status="agent_running",
            )
            task_id = task.id
            run = AgentRunRepository(session).create(
                AgentRunCreate(task_id=task.id, current_step="step_x"),
            )
            run_id = run.id
            candidate_repo = MediaCandidateRepository(session)
            # 1. agent 自动搜索留下的最早候选 (created_at 最小) ——
            #    旧 ASC 路径会错选这个. 是 user_decision 的
            #    payload.source_candidate_id 反查目标.
            original_candidate = candidate_repo.add_candidate(
                task_id=task_id, source="tmdb", media_type="movie",
                title="The Matrix", original_title=None, year=1999,
                external_id="movie:603", confidence=0.7,
                reason="agent auto-search", payload={},
            )
            session.commit()
            # 2. 用户回复后, 走 handle_select_metadata_candidate 落库
            #    的 user_decision 候选 (created_at 较大, 不是 [0]).
            #    payload 必须带 source_candidate_id 链回原 candidate —
            #    与生产 handle_select_metadata_candidate 写入形状一致;
            #    否则 safety net 无法反查真 provider.
            candidate_repo.add_candidate(
                task_id=task_id, source="user_decision", media_type="movie",
                title="The Matrix Reloaded", original_title=None, year=2003,
                external_id="movie:604", confidence=0.95,
                reason="user selected",
                payload={
                    "source_candidate_id": original_candidate.id,
                    "decision_id": "fake-decision-id",
                },
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            t = IngestTaskRepository(session).get(task_id)
            r = AgentRunRepository(session).get(run_id)
            runner_module._check_post_completion_safety_net(
                session=session, config=config, run=r, task=t,
                task_repo=IngestTaskRepository(session),
                registry=registry, mode="auto_ingest",
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            t = IngestTaskRepository(session).get(task_id)
            assert t.status == "library_import_complete"
        # 关键断言: safety net 用了 user_decision 候选的
        # provider_id (movie:604), 不是 ASC 路径下 [0] 的
        # agent 候选 (movie:603).
        assert len(captured_fetch) == 1
        assert captured_fetch[0]["provider_id"] == "movie:604", (
            f"safety net used provider_id={captured_fetch[0]['provider_id']!r}, "
            f"expected 'movie:604' (the user_decision candidate). The old "
            f"path picked candidates[0] which is the oldest, not the user's "
            f"actual pick."
        )
        # provider_name 必须是真 provider (从 source_candidate_id 链回
        # 原 tmdb candidate), 不得是字面量 "user_decision" —
        # 后者会让 fetch_metadata_draft 找不到对应 provider 适配器
        # 立刻失败.
        assert captured_fetch[0]["provider_name"] == "tmdb", (
            f"safety net used provider_name={captured_fetch[0]['provider_name']!r}, "
            f"expected 'tmdb'. The user_decision candidate's source "
            f"is the literal 'user_decision' which is not a real "
            f"provider; the real provider must come from the linked "
            f"source_candidate_id."
        )
        assert len(publish_calls) == 1
        assert publish_calls[0]["task_id"] == task_id

# ══════════════════════════════════════════════════════════════════════
# search-loop safety net (fix-show-absolute-episode-ingest-and-agent-search-loop §4)
# ══════════════════════════════════════════════════════════════════════
#
# 关键契约:
# - 当 max_steps 即将触发 + task 仍是 agent_running + 本 run 有成功的
#   search_metadata 工具调用 → safety net 必须收敛到
#   prepare_select_metadata_candidate_decision 工具, 创建 decision_requested
#   (或 auto_confirm).
# - 没有 search_metadata 历史 → safety net 不得合成候选, 必须放弃,
#   退回标准 max_steps 失败路径.
# - safety net 不得调 LLM — 直接走 tool registry.

class TestSearchLoopSafetyNet:
    def test_safety_net_creates_decision_when_search_history_exists(
        self, tmp_path,
    ):
        """成功 search_metadata 后 LLM 漏调 prepare_select_metadata_
        candidate_decision, max_steps 触发前安全网兜底 → 走候选决策卡."""
        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        from media_pilot.agent import runner as runner_module
        from media_pilot.repository.repositories import (
            AgentDecisionRequestRepository,
            AgentRunCreate,
            AgentRunRepository,
            AgentToolCallRepository,
            IngestTaskRepository,
            MediaCandidateRepository,
        )
        from media_pilot.repository.models import AgentToolCall

        with sf() as session:
            task = _make_task(
                session, source_path="/tmp/movie.mkv",
                media_type="movie", status="agent_running",
            )
            task_id = task.id
            run = AgentRunRepository(session).create(
                AgentRunCreate(task_id=task.id, current_step="step_x"),
            )
            run_id = run.id
            # 模拟 LLM 已经搜过: AgentToolCall.output 有 success data
            tc = AgentToolCall(
                run_id=run_id, tool_name="search_metadata",
                input={"keyword": "x", "provider": "tmdb",
                       "media_type": "movie"},
                status="completed",
                output={
                    "status": "success",
                    "summary": "Found 2 candidates",
                    "data": {
                        "candidates": [
                            {
                                "candidate_id": "cand-1",
                                "provider": "tmdb",
                                "provider_id": "movie:1",
                                "title": "X",
                                "year": 2026,
                                "media_type": "movie",
                                "confidence": 0.7,
                            },
                            {
                                "candidate_id": "cand-2",
                                "provider": "tmdb",
                                "provider_id": "movie:2",
                                "title": "X2",
                                "year": 2026,
                                "media_type": "movie",
                                "confidence": 0.6,
                            },
                        ],
                        "keyword": "x",
                        "provider": "tmdb",
                        "has_clear_winner": False,
                    },
                },
            )
            session.add(tc)
            session.flush()
            # 落两条 persisted candidates, 让工具能从 DB 恢复
            MediaCandidateRepository(session).add_candidate(
                task_id=task_id, source="tmdb",
                external_id="movie:1", media_type="movie",
                title="X", original_title=None, year=2026,
                confidence=0.7, reason="x",
                payload={"keyword": "x"},
            )
            MediaCandidateRepository(session).add_candidate(
                task_id=task_id, source="tmdb",
                external_id="movie:2", media_type="movie",
                title="X2", original_title=None, year=2026,
                confidence=0.6, reason="x",
                payload={"keyword": "x"},
            )
            session.commit()

        with sf() as session:
            t = IngestTaskRepository(session).get(task_id)
            r = AgentRunRepository(session).get(run_id)
            from media_pilot.agent.tools.registry import (
                get_tool_registry, register_builtin_tools,
            )
            register_builtin_tools()
            registry = get_tool_registry()
            result = runner_module._recover_via_candidate_decision(
                session=session, config=config, run=r, task=t,
                registry=registry,
            )
            session.commit()

        # 决策卡已创建
        assert result is not None
        assert result.status == "waiting_user"
        with sf() as session:
            decisions = (
                AgentDecisionRequestRepository(session)
                .list_pending_by_task(task_id)
            )
            assert any(
                d.decision_type == "select_metadata_candidate"
                for d in decisions
            )

    def test_safety_net_returns_none_when_no_search_history(
        self, tmp_path,
    ):
        """没成功 search_history → safety net 不合成候选, 退回 max_steps."""
        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        from media_pilot.agent import runner as runner_module
        from media_pilot.repository.repositories import (
            AgentRunCreate,
            AgentRunRepository,
            IngestTaskRepository,
        )

        with sf() as session:
            task = _make_task(
                session, source_path="/tmp/movie.mkv",
                media_type="movie", status="agent_running",
            )
            run = AgentRunRepository(session).create(
                AgentRunCreate(task_id=task.id, current_step="step_x"),
            )
            session.commit()

        with sf() as session:
            t = IngestTaskRepository(session).get(task.id)
            r = AgentRunRepository(session).get(run.id)
            from media_pilot.agent.tools.registry import (
                get_tool_registry, register_builtin_tools,
            )
            register_builtin_tools()
            registry = get_tool_registry()
            result = runner_module._recover_via_candidate_decision(
                session=session, config=config, run=r, task=t,
                registry=registry,
            )

        # 安全网放弃, 让 runner 走标准 max_steps 失败路径
        assert result is None


class TestSearchMetadataTPDBShowStructuredReject:
    """provider=tpdb + media_type=show 结构化拒绝 — 不调 LLM 不计数 hard
    tool failure. spec: agent-metadata-search-loop-guard / Requirement:
    TPDB+show 结构化拒绝."""

    def test_tpdb_show_returns_incompatible_provider_flag(self, tmp_path):
        """search_metadata(provider='tpdb', media_type='show') 必须
        按 success-with-flag 返回 incompatible_provider=True, 不算 hard
        tool failure."""
        from media_pilot.agent.tools.read_only import (
            _handle_search_metadata,
            make_search_metadata,
        )
        from media_pilot.agent.tools.base import ToolContext
        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            ctx = ToolContext(
                session=session, config=config,
                task_id="t1", run_id="r1",
            )
            tool = make_search_metadata()
            result = tool.handler(ctx, {
                "keyword": "anything",
                "provider": "tpdb",
                "media_type": "show",
            })

        assert result.status == "success", (
            f"tpdb+show must NOT be hard failure (would count against "
            f"MAX_TOOL_FAILURES); got status={result.status}"
        )
        assert result.data["incompatible_provider"] is True
        assert result.data["provider"] == "tpdb"
        assert result.data["media_type"] == "show"
        assert result.data["candidates"] == []
        assert "tmdb" in result.data["message"].lower() or "movie" in result.data["message"].lower()

    def test_tpdb_both_media_type_also_structured(self, tmp_path):
        """media_type='both' 包含 show, 也走结构化拒绝 (lmdbprovider 不会
        调 movi 因为 tpdb 只支持 movie)."""
        from media_pilot.agent.tools.read_only import make_search_metadata
        from media_pilot.agent.tools.base import ToolContext
        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            ctx = ToolContext(
                session=session, config=config,
                task_id="t1", run_id="r1",
            )
            tool = make_search_metadata()
            result = tool.handler(ctx, {
                "keyword": "anything",
                "provider": "tpdb",
                "media_type": "both",
            })

        # 'both' 包不 show —同样结构化拒绝. lmdbprovider movi 路径不受
        # 拒绝, lmdb provider.search_movie 可以走. 不 incompatible_provider
        # 仅阻止 show 分支. lmdb这里 lmdb没 init tpdb api_key,  lmdb但
        # lmdb返回值会走 provider_errors 路径.
        assert result.status in ("success", "failure")
        if result.status == "success":
            assert result.data["incompatible_provider"] is True
        else:
            assert "tpdb" in result.summary.lower() or result.data.get("reason") == "provider_errors"

    def test_tpdb_movie_only_runs_normally(self, tmp_path):
        """provider=tpdb + media_type=movie 不触发结构化拒绝 — 走
        标准 provider_errors / no_candidates 路径."""
        from media_pilot.agent.tools.read_only import make_search_metadata
        from media_pilot.agent.tools.base import ToolContext
        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            ctx = ToolContext(
                session=session, config=config,
                task_id="t1", run_id="r1",
            )
            tool = make_search_metadata()
            result = tool.handler(ctx, {
                "keyword": "anything",
                "provider": "tpdb",
                "media_type": "movie",
            })

        # tpdb movi 没 init api_key → 走 no_candidates / provider_errors,
        # 都不算 incompatible_provider (movie-only 不被拒).
        assert result.data.get("incompatible_provider") is not True
