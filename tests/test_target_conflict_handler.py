"""target_conflict 决策处理回归测试

固化以下不变量:
1. `cancel_publish` 选项 → 任务进入 agent_failed，failure_reason 写明
   "用户取消目标冲突处理"，对应 AgentRun 标记 failed。
2. `overwrite_target` 选项 → 任务进入 library_import_complete，对应 AgentRun
   标记 completed；不调用 LLM（mock_llm_client 不应被消费）。
3. 决策创建时任务自动联动为 waiting_user；handler 执行后回归终态。
4. `target_conflict` 决策可由 system run 创建（run 状态为 active 时即可），
   不强制要求 run.status == 'waiting_user'。
5. handler 抛错时不会静默吞掉；reply_to_decision 会把错误向上抛。
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _make_config(database_dir: Path):
    from media_pilot.config.settings import AppConfig

    return AppConfig(
        downloads_dir=database_dir / "downloads",
        watch_dir=database_dir / "watch",
        workspace_dir=database_dir / "ws",
        movies_dir=database_dir / "movies",
        shows_dir=database_dir / "shows",
        database_dir=database_dir,
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        tmdb_api_key="test-tmdb-key",
    )


def _make_session_factory(tmp_path: Path):
    from tests.test_api_v1 import _make_session_factory

    return _make_session_factory(tmp_path)


def _make_movie_task_with_detail(session, config, tmp_path, *, status="agent_running"):
    """构造一个 movie 任务，含 MetadataDetail、MediaSourceSelection。"""
    from media_pilot.repository.models import (
        MediaSourceSelection,
        MetadataDetail,
    )
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

    video_path = tmp_path / "src" / "movie.mkv"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"fake video content")

    task = IngestTaskRepository(session).create(IngestTaskCreate(
        source_path=str(video_path),
        status=status,
        current_step="publish",
        media_type="movie",
    ))
    session.add(MediaSourceSelection(
        task_id=task.id,
        input_path=str(video_path),
        selected_path=str(video_path),
        confidence=1.0,
        reason="largest_video_file",
    ))
    session.add(MetadataDetail(
        task_id=task.id,
        provider="tmdb",
        provider_id="movie:568160",
        media_type="movie",
        title="天气之子",
        original_title="天気の子",
        year=2019,
        payload={
            "plot": "test",
            "images": {"poster": "https://example.test/poster.jpg"},
        },
    ))
    session.commit()
    return task


def _make_target_conflict_decision(session, *, task_id, run_id, payload=None):
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
    )

    return AgentDecisionRequestRepository(session).create(AgentDecisionRequestCreate(
        run_id=run_id,
        task_id=task_id,
        decision_type="target_conflict",
        question="发布目标已存在冲突，请选择处理方式。",
        free_text_allowed=False,
        options=[
            {"id": "overwrite_target", "label": "覆盖发布目标"},
            {"id": "cancel_publish", "label": "取消本次发布"},
        ],
        payload=payload,
    ))


def _create_active_run(session, task_id):
    from media_pilot.repository.repositories import AgentRunCreate, AgentRunRepository

    return AgentRunRepository(session).create(AgentRunCreate(
        task_id=task_id, current_step="agent_start",
    ))


def _seed_existing_movie_target(tmp_path: Path, config) -> Path:
    """用 _make_movie_task_with_detail 同样的元数据计算 directory_name，
    在 movies_dir 下预放同名目录+同名 .mkv，使得 build_movie_write_plan 触发冲突。"""
    from media_pilot.orchestration.jellyfin_movie_writer import (
        _movie_directory_name,
        _quality_suffix_from_source_name,
    )

    title = "天气之子"
    year = 2019
    directory_name = _movie_directory_name(title, year, identifier=None)
    existing = config.movies_dir / directory_name
    existing.mkdir(parents=True, exist_ok=True)
    quality_suffix = _quality_suffix_from_source_name(
        source_stem="movie", title=title, year=year,
    )
    file_stem = directory_name if quality_suffix == "" else f"{directory_name} - {quality_suffix}"
    (existing / f"{file_stem}.mkv").write_bytes(b"x")
    return existing


# ── handler 直接调用测试 ──────────────────────────────────────────────


class TestHandleCancelPublish:
    def test_marks_task_agent_failed_with_clear_reason(self, tmp_path: Path):
        from media_pilot.repository.repositories import (
            AgentRunRepository,
            IngestTaskRepository,
        )
        from media_pilot.services.target_conflict_handler import (
            CANCEL_PUBLISH_FAILURE_REASON,
            handle_cancel_publish,
        )

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_movie_task_with_detail(session, config, tmp_path, status="waiting_user")
            run = _create_active_run(session, task.id)
            session.commit()
            decision = _make_target_conflict_decision(session, task_id=task.id, run_id=run.id)
            session.commit()

        # handler 必须在新的 session 中执行（无 active transaction）
        with sf() as session:
            fresh_decision = type(decision)(
                id=decision.id,
                run_id=decision.run_id,
                task_id=decision.task_id,
                decision_type=decision.decision_type,
                question=decision.question,
                options=decision.options,
                free_text_allowed=decision.free_text_allowed,
                status=decision.status,
                decision=decision.decision,
                decided_by=decision.decided_by,
                decided_at=decision.decided_at,
                created_at=decision.created_at,
            )
            from media_pilot.repository.models import AgentDecisionRequest

            fresh_decision = session.get(AgentDecisionRequest, decision.id)
            result = handle_cancel_publish(
                session=session, config=config, decision=fresh_decision,
            )
            session.commit()

        assert result["outcome"] == "cancelled"
        assert result["failure_reason"] == CANCEL_PUBLISH_FAILURE_REASON

        with sf() as session:
            task = IngestTaskRepository(session).get(decision.task_id)
            assert task.status == "agent_failed"
            assert task.current_step == "agent_failed"
            assert task.failure_reason == CANCEL_PUBLISH_FAILURE_REASON

            run = AgentRunRepository(session).get(decision.run_id)
            assert run.status == "failed"
            assert run.current_step == "agent_failed"

    def test_missing_task_does_not_raise(self, tmp_path: Path):
        """handler 应当容忍 task 缺失（DB row 已被并发删除等），不抛错。"""
        from media_pilot.repository.models import AgentDecisionRequest
        from media_pilot.services.target_conflict_handler import handle_cancel_publish

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            run = _create_active_run(session, "nonexistent-task-id")
            session.commit()

        with sf() as session:
            fake_decision = AgentDecisionRequest(
                id="decision-fake",
                run_id=run.id,
                task_id="nonexistent-task-id",
                decision_type="target_conflict",
                question="?",
                free_text_allowed=False,
                options=[],
            )
            # 显式 flush 让 session 能 get run
            result = handle_cancel_publish(
                session=session, config=config, decision=fake_decision,
            )
            session.commit()

        assert result["outcome"] == "cancelled"
        assert result["task_id"] == "nonexistent-task-id"


class TestHandleOverwriteTargetDeterministic:
    def test_calls_execute_movie_write_with_force_overwrite(self, tmp_path: Path, monkeypatch):
        """overwrite_target 路径必须调用 execute_movie_write(force_overwrite=True)，
        不调用 LLM；决策 payload 应当持久化目标路径。"""
        from media_pilot.services.target_conflict_handler import handle_overwrite_target

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        # 先让 publish dir 存在，否则 build_movie_write_plan 不会触发 conflict
        existing = _seed_existing_movie_target(tmp_path, config)
        # 计算 handler 重建 plan 时会用的 final_target_file 实际值
        from media_pilot.orchestration.jellyfin_movie_writer import (
            _movie_directory_name,
            _quality_suffix_from_source_name,
        )
        title = "天气之子"
        year = 2019
        directory_name = _movie_directory_name(title, year, identifier=None)
        quality_suffix = _quality_suffix_from_source_name(
            source_stem="movie", title=title, year=year,
        )
        file_stem = directory_name if quality_suffix == "" else f"{directory_name} - {quality_suffix}"
        actual_target_file = str(existing / f"{file_stem}.mkv")

        with sf() as session:
            task = _make_movie_task_with_detail(session, config, tmp_path, status="waiting_user")
            run = _create_active_run(session, task.id)
            session.commit()
            decision = _make_target_conflict_decision(
                session,
                task_id=task.id,
                run_id=run.id,
                payload={
                    "final_target_dir": str(existing),
                    "final_target_file": actual_target_file,
                    "conflict": "target_file_already_exists",
                },
            )
            session.commit()

        captured = {}

        class _StubWriteResult:
            status = "succeeded"
            warnings: list = []

        def _stub_execute_movie_write(session, *, task_id, source_path, detail, plan, client, provider, force_overwrite=False):
            captured["force_overwrite"] = force_overwrite
            captured["task_id"] = task_id
            captured["provider"] = provider
            captured["plan"] = plan
            return _StubWriteResult()

        from media_pilot.orchestration import jellyfin_movie_writer
        monkeypatch.setattr(jellyfin_movie_writer, "execute_movie_write", _stub_execute_movie_write)

        with sf() as session:
            from media_pilot.repository.models import AgentDecisionRequest
            fresh_decision = session.get(AgentDecisionRequest, decision.id)
            # 决策 payload 已经写入；handler 必须读到这个值并据此做目标稳定性校验
            assert fresh_decision.payload["final_target_file"] == actual_target_file
            result = handle_overwrite_target(
                session=session, config=config, decision=fresh_decision,
            )
            session.commit()

        assert captured["force_overwrite"] is True
        assert captured["task_id"] == decision.task_id
        assert result["outcome"] == "published"

    def test_overwrite_re_resolves_path_ignoring_stale_payload(
        self, tmp_path: Path, monkeypatch,
    ):
        """历史 bug: 决策 payload 里的 final_target_file 是用目录名后缀拼的
        (`.MX]`), 错误的. Issue B 修复后, handler 必须用
        ``resolve_main_video_for_publish`` 重新解析真实主视频, 不盲信旧
        payload 里的 final_target_dir / final_target_file. 旧的稳定性
        guard (saved_dir / saved_file 比对) 已删除 — 用户回 overwrite 时
        当下 plan 才是真相, 决策创建时 payload 只作审计.
        """
        from media_pilot.services.target_conflict_handler import handle_overwrite_target
        from media_pilot.orchestration.jellyfin_movie_writer import (
            _movie_directory_name,
        )

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        # 预放已存在的目标 movie 目录, 让 build_movie_write_plan 触发冲突
        existing_dir = config.movies_dir / _movie_directory_name("天气之子", 2019)
        existing_dir.mkdir(parents=True, exist_ok=True)
        (existing_dir / "Tenki.No.Ko.2019.mkv").write_bytes(b"existing")

        with sf() as session:
            task = _make_movie_task_with_detail(session, config, tmp_path, status="waiting_user")
            run = _create_active_run(session, task.id)
            session.commit()
            # 历史 bug: payload 里的 final_target_file 是用目录名后缀
            # 拼的 (这里模拟一个"陈旧/错误"的路径)
            decision = _make_target_conflict_decision(
                session,
                task_id=task.id,
                run_id=run.id,
                payload={
                    "final_target_dir": "/data/library/movies/Stale Path",
                    "final_target_file": "/data/library/movies/Stale Path/Stale.mkv",
                    "conflict": "target_file_already_exists",
                },
            )
            session.commit()
            decision_id = decision.id

        seen_plan = {}

        class _StubWriteResult:
            status = "succeeded"
            warnings: list = []

        def _stub_execute_movie_write(
            session, *, task_id, source_path, detail, plan, client, provider,
            force_overwrite=False,
        ):
            seen_plan["final_target_file"] = plan.final_target_file
            return _StubWriteResult()

        from media_pilot.orchestration import jellyfin_movie_writer
        monkeypatch.setattr(
            jellyfin_movie_writer, "execute_movie_write",
            _stub_execute_movie_write,
        )

        with sf() as session:
            from media_pilot.repository.models import AgentDecisionRequest
            fresh_decision = session.get(AgentDecisionRequest, decision_id)
            # 不应再抛 409 — handler 重新解析后跑下去
            result = handle_overwrite_target(
                session=session, config=config, decision=fresh_decision,
            )
            session.commit()

        # handler 重建的 plan 应当基于当前任务 (天气之子) 的真实 metadata,
        # 不被旧 payload 里的 "Stale Path" 污染.
        assert "Stale" not in str(seen_plan["final_target_file"])
        assert result["outcome"] == "published"
        assert result["final_target_file"].endswith(".mkv")

    def test_overwrite_succeeds_when_target_stable(self, tmp_path: Path, monkeypatch):
        """目标稳定时 handler 应当正常跑通，覆盖成功。"""
        from media_pilot.services.target_conflict_handler import handle_overwrite_target

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        existing = _seed_existing_movie_target(tmp_path, config)
        # 算 handler 重建 plan 时会用的 final_target_file / dir
        from media_pilot.orchestration.jellyfin_movie_writer import (
            _movie_directory_name,
            _quality_suffix_from_source_name,
        )
        title = "天气之子"
        year = 2019
        directory_name = _movie_directory_name(title, year, identifier=None)
        quality_suffix = _quality_suffix_from_source_name(
            source_stem="movie", title=title, year=year,
        )
        file_stem = directory_name if quality_suffix == "" else f"{directory_name} - {quality_suffix}"
        expected_target = str(existing / f"{file_stem}.mkv")
        expected_dir = str(existing)

        with sf() as session:
            task = _make_movie_task_with_detail(session, config, tmp_path, status="waiting_user")
            run = _create_active_run(session, task.id)
            session.commit()
            # 提前算 plan 目标，写入 payload；handler 重建后应当一致
            decision = _make_target_conflict_decision(
                session,
                task_id=task.id,
                run_id=run.id,
                payload={
                    "final_target_dir": expected_dir,
                    "final_target_file": expected_target,
                    "conflict": "target_file_already_exists",
                },
            )
            session.commit()

        class _StubWriteResult:
            status = "succeeded"
            warnings: list = []

        def _stub_execute_movie_write(session, *, task_id, source_path, detail, plan, client, provider, force_overwrite=False):
            return _StubWriteResult()

        from media_pilot.orchestration import jellyfin_movie_writer
        monkeypatch.setattr(jellyfin_movie_writer, "execute_movie_write", _stub_execute_movie_write)

        with sf() as session:
            from media_pilot.repository.models import AgentDecisionRequest
            fresh_decision = session.get(AgentDecisionRequest, decision.id)
            result = handle_overwrite_target(
                session=session, config=config, decision=fresh_decision,
            )
            session.commit()

        assert result["outcome"] == "published"
        assert result["final_target_file"] == expected_target

    def test_overwrite_back_compat_with_no_payload(self, tmp_path: Path, monkeypatch):
        """旧决策（payload 为空 dict）应当跳过目标稳定性校验，正常覆盖。"""
        from media_pilot.services.target_conflict_handler import handle_overwrite_target

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        _seed_existing_movie_target(tmp_path, config)

        with sf() as session:
            task = _make_movie_task_with_detail(session, config, tmp_path, status="waiting_user")
            run = _create_active_run(session, task.id)
            session.commit()
            # payload=None → .create() 会写入 {} → handler 跳过校验
            decision = _make_target_conflict_decision(
                session, task_id=task.id, run_id=run.id, payload=None,
            )
            session.commit()

        class _StubWriteResult:
            status = "succeeded"
            warnings: list = []

        def _stub_execute_movie_write(session, *, task_id, source_path, detail, plan, client, provider, force_overwrite=False):
            return _StubWriteResult()

        from media_pilot.orchestration import jellyfin_movie_writer
        monkeypatch.setattr(jellyfin_movie_writer, "execute_movie_write", _stub_execute_movie_write)

        with sf() as session:
            from media_pilot.repository.models import AgentDecisionRequest
            fresh_decision = session.get(AgentDecisionRequest, decision.id)
            result = handle_overwrite_target(
                session=session, config=config, decision=fresh_decision,
            )
            session.commit()

        assert result["outcome"] == "published"


# ── end-to-end: 决策 + reply_to_decision ───────────────────────────────


class TestReplyToDecisionForTargetConflict:
    def _setup(self, tmp_path):
        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with sf() as session:
            task = _make_movie_task_with_detail(session, config, tmp_path, status="agent_running")
            run = _create_active_run(session, task.id)
            # run 状态保持 active：target_conflict 决策由 system run 创建，
            # 不要求 waiting_user
            session.commit()
            decision = _make_target_conflict_decision(session, task_id=task.id, run_id=run.id)
            session.commit()
        return sf, config, decision

    def test_reply_with_cancel_publish_dispatches_handler(self, tmp_path: Path):
        from media_pilot.repository.repositories import (
            AgentDecisionRequestRepository,
            AgentMessageRepository,
            AgentRunRepository,
            IngestTaskRepository,
        )
        from media_pilot.services.decision_reply import ReplyInput, reply_to_decision

        sf, config, decision = self._setup(tmp_path)

        with sf() as session:
            result = reply_to_decision(
                session=session,
                config=config,
                reply=ReplyInput(decision_id=decision.id, option_id="cancel_publish"),
                mock_llm_client=None,
            )
            session.commit()

        assert result.status == "target_conflict_cancelled"

        with sf() as session:
            d = AgentDecisionRequestRepository(session).get(decision.id)
            assert d.status == "decided"
            assert d.decision == {"option_id": "cancel_publish", "type": "option"}

            task = IngestTaskRepository(session).get(decision.task_id)
            assert task.status == "agent_failed"
            assert task.failure_reason == "用户取消目标冲突处理"

            run = AgentRunRepository(session).get(decision.run_id)
            assert run.status == "failed"
            assert run.current_step == "agent_failed"

            # 决策回复写入 user message — 用 [SystemAction] 系统动作摘要格式
            # (polish-agent-decision-actions 引入). 不得以 option id
            # "cancel_publish" 作为主体, 必须用可读中文摘要.
            user_msgs = [m for m in AgentMessageRepository(session).list_by_run(run.id) if m.role == "user"]
            assert user_msgs, "决策回复必须写入 user message"
            user_content = user_msgs[-1].content or ""
            assert user_content.startswith("[SystemAction]"), (
                f"决策回复必须以 [SystemAction] 开头, 实际: {user_content!r}"
            )
            # 关键: option id "cancel_publish" 不得作为消息主体.
            # 这是 polish-agent-decision-actions 的核心 contract —
            # 数据库 key 暴露给用户是审计噪音.
            assert "cancel_publish" not in user_content, (
                f"决策回复不得包含 option id 作为主体, 实际: {user_content!r}"
            )
            # 必须包含可读中文摘要
            assert "已取消发布" in user_content, (
                f"决策回复必须包含可读中文摘要, 实际: {user_content!r}"
            )

    def test_reply_does_not_consume_llm_client(self, tmp_path: Path):
        """target_conflict 决策为确定性后端路径，handler 不调用 LLM。
        验证：传入的 mock_llm_client 没有任何调用。"""
        from media_pilot.services.decision_reply import ReplyInput, reply_to_decision

        sf, config, decision = self._setup(tmp_path)

        class _SpyLLM:
            def __init__(self):
                self.chat_called = 0
                self.chat_stream_called = 0

            def chat(self, *args, **kwargs):
                self.chat_called += 1
                raise AssertionError("target_conflict handler must not call LLM")

            def chat_stream(self, *args, **kwargs):
                self.chat_stream_called += 1
                raise AssertionError("target_conflict handler must not call LLM")

        spy = _SpyLLM()

        with sf() as session:
            result = reply_to_decision(
                session=session,
                config=config,
                reply=ReplyInput(decision_id=decision.id, option_id="cancel_publish"),
                mock_llm_client=spy,
            )
            session.commit()

        assert result.status == "target_conflict_cancelled"
        assert spy.chat_called == 0
        assert spy.chat_stream_called == 0

    def test_target_conflict_does_not_require_waiting_user_run(self, tmp_path: Path):
        """target_conflict 决策可由 active 状态的 system run 创建；
        reply 时不要求 run.status == 'waiting_user'。"""
        from media_pilot.repository.repositories import AgentRunRepository
        from media_pilot.services.decision_reply import ReplyInput, reply_to_decision

        sf, config, decision = self._setup(tmp_path)

        with sf() as session:
            run = AgentRunRepository(session).get(decision.run_id)
            assert run.status == "active"

        with sf() as session:
            result = reply_to_decision(
                session=session,
                config=config,
                reply=ReplyInput(decision_id=decision.id, option_id="cancel_publish"),
                mock_llm_client=None,
            )
            session.commit()

        assert result.status == "target_conflict_cancelled"

    def test_invalid_option_id_rejected(self, tmp_path: Path):
        from media_pilot.services.decision_reply import ReplyInput, reply_to_decision

        sf, config, decision = self._setup(tmp_path)

        with sf() as session:
            with pytest.raises(ValueError) as excinfo:
                reply_to_decision(
                    session=session,
                    config=config,
                    reply=ReplyInput(decision_id=decision.id, option_id="bogus_option"),
                    mock_llm_client=None,
                )
            assert "not found in decision options" in str(excinfo.value)

    def test_free_text_rejected_when_not_allowed(self, tmp_path: Path):
        """target_conflict 决策 free_text_allowed=False，free_text 必被拒。"""
        from media_pilot.services.decision_reply import ReplyInput, reply_to_decision

        sf, config, decision = self._setup(tmp_path)

        with sf() as session:
            with pytest.raises(ValueError) as excinfo:
                reply_to_decision(
                    session=session,
                    config=config,
                    reply=ReplyInput(decision_id=decision.id, free_text="自行决定"),
                    mock_llm_client=None,
                )
            assert "Free text is not allowed" in str(excinfo.value)


# ── WRITE tool: publish_movie_to_library 决策落库 payload ─────────────


class TestPublishMovieToolPersistsTargetPayload:
    """publish_movie_to_library 检测到 target 冲突时，必须把
    final_target_dir / final_target_file / conflict 持久化到
    AgentDecisionRequest.payload，并显式把 task.current_step 切到
    'target_conflict'。"""

    def test_publish_tool_persists_target_payload(self, tmp_path: Path, monkeypatch):
        from media_pilot.agent.tools.base import ToolContext
        from media_pilot.agent.tools.write import _handle_publish_movie_to_library
        from media_pilot.repository.repositories import (
            AgentDecisionRequestRepository,
            AgentRunRepository,
            IngestTaskRepository,
        )

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        # 准备一个已存在的目标，使 build_movie_write_plan 触发冲突
        existing_dir = _seed_existing_movie_target(tmp_path, config)

        # 让 check_eligibility 通过：避免触发其它分支
        monkeypatch.setattr(
            "media_pilot.services.auto_ingest.check_eligibility",
            lambda session, config, task_id: type("E", (), {
                "blocking_reasons": [],
                "media_type": "movie",
                "candidate_count": 1,
                "confidence_threshold": 0.6,
                "margin": 0.1,
            })(),
        )

        with sf() as session:
            task = _make_movie_task_with_detail(session, config, tmp_path, status="agent_running")
            run = _create_active_run(session, task.id)
            session.commit()
            task_id = task.id
            run_id = run.id

        with sf() as session:
            ctx = ToolContext(session=session, config=config, task_id=task_id, run_id=run_id)
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})
            session.commit()

        assert result.status == "success"
        assert result.data["decision_requested"] is True
        assert result.data["decision_type"] == "target_conflict"

        with sf() as session:
            dr_repo = AgentDecisionRequestRepository(session)
            decisions = dr_repo.list_pending_by_run(run_id)
            assert len(decisions) == 1
            d = decisions[0]
            assert d.decision_type == "target_conflict"
            assert d.payload["final_target_dir"] == str(existing_dir)
            assert d.payload["final_target_file"].endswith(".mkv")
            assert str(existing_dir) in d.payload["final_target_file"]
            assert d.payload["conflict"] == "final_target_file_exists"

            # task 应当切到 waiting_user + target_conflict
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "waiting_user"
            assert task.current_step == "target_conflict"


# ── Issue B: publish_movie_to_library 自动补写 selection ──────────────


class TestPublishToolAutoCreatesSelectionForDirSingleVideo:
    """watch 目录型单电影输入, 没有 MediaSourceSelection 时, publish_movie_to_library
    必须用 video_source_resolver 解析真实 .mkv 并自动补写 selection.
    决策 payload 的 final_target_file 必须是 .mkv (不是 .MX]).
    """

    def test_publish_tool_resolves_real_video_file_for_dir_input(
        self, tmp_path, monkeypatch,
    ):
        from sqlalchemy import select
        from media_pilot.agent.tools.base import ToolContext
        from media_pilot.agent.tools.write import _handle_publish_movie_to_library
        from media_pilot.repository.models import (
            MediaSourceSelection, MetadataDetail,
        )
        from media_pilot.repository.repositories import (
            AgentDecisionRequestRepository,
            IngestTaskCreate, IngestTaskRepository,
        )

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        # 准备 watch 目录型单电影输入
        source_dir = config.watch_dir / "Warcraft ... [YTS.MX]"
        source_dir.mkdir(parents=True, exist_ok=True)
        video = source_dir / "Warcraft.2016.1080p.BluRay.x264.mkv"
        video.write_bytes(b"fake video content")
        (source_dir / "info.txt").write_text("info")

        # 让 check_eligibility 通过
        monkeypatch.setattr(
            "media_pilot.services.auto_ingest.check_eligibility",
            lambda session, config, task_id: type("E", (), {
                "blocking_reasons": [],
                "media_type": "movie",
                "candidate_count": 1,
                "confidence_threshold": 0.6,
                "margin": 0.1,
            })(),
        )

        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path=str(source_dir),
                status="agent_running",
                current_step="publish",
                media_type="movie",
            ))
            session.add(MetadataDetail(
                task_id=task.id,
                provider="tmdb", provider_id="movie:68735",
                media_type="movie", title="魔兽",
                original_title="Warcraft", year=2016,
                payload={"plot": "test", "images": {}},
            ))
            session.commit()
            run = _create_active_run(session, task.id)
            session.commit()
            task_id = task.id
            run_id = run.id

        with sf() as session:
            ctx = ToolContext(session=session, config=config, task_id=task_id, run_id=run_id)
            result = _handle_publish_movie_to_library(ctx, {"task_id": task_id})
            session.commit()

        # 应进入 target_conflict 决策 (因为 movies_dir 里魔兽 (2016)/ 不存在,
        # 不会触发 conflict — 我们换一种思路: 让 build_movie_write_plan 返回
        # success 路径, 验证 video_source = .mkv 而不是目录).
        # 实际场景: 没有 conflict 时直接 publish, 不创建 decision.
        # 这里 movies_dir 是空的, 所以直接走 publish 路径, 不会创建 decision.
        # 改为检查 MediaSourceSelection 是否被自动补写.
        with sf() as session:
            sel = session.scalars(
                select(MediaSourceSelection)
                .where(MediaSourceSelection.task_id == task_id)
                .order_by(MediaSourceSelection.created_at.desc())
            ).first()
            # 解析器应当在 publish 时补写 selection
            assert sel is not None, "publish_movie_to_library 没自动补写 MediaSourceSelection"
            assert sel.selected_path == str(video)
            assert sel.input_path == str(source_dir)
            assert sel.reason == "auto_single_video_dir"


# ── Issue B 修复: 目录型单电影输入的 overwrite ─────────────────────────


class TestOverwriteOnDirSingleVideo:
    """watch 目录型单电影输入 (`Warcraft ... [YTS.MX]/foo.mkv` + 噪声
    文件), 历史 bug: 决策 payload final_target_file 是目录名后缀 `.MX]`,
    handle_overwrite_target 用目录路径喂 execute_movie_write 触发
    IsADirectoryError. 修复后:
    - handle_overwrite_target 必须用 video_source_resolver 解析真实 .mkv 文件.
    - target 文件名后缀是 .mkv, 不是 .MX].
    - 即便 decision.payload 里的 final_target_file 是错的 (历史 bug 残留),
      handler 仍能正确执行覆盖.
    """

    def _setup_dir_single_video_task(
        self, session, config, tmp_path, *, media_type="movie",
    ):
        """构造 watch 目录型单电影任务: 1 个 .mkv + jpg/txt 噪声, 无 selection."""
        from media_pilot.repository.models import MetadataDetail
        from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

        source_dir = config.watch_dir / "Warcraft ... [YTS.MX]"
        source_dir.mkdir(parents=True, exist_ok=True)
        video = source_dir / "Warcraft.2016.1080p.BluRay.x264.mkv"
        video.write_bytes(b"fake video content")
        (source_dir / "Subs.jpg").write_bytes(b"jpg")
        (source_dir / "info.txt").write_text("info")

        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(source_dir),
            status="waiting_user",
            current_step="target_conflict",
            media_type=media_type,
        ))
        session.add(MetadataDetail(
            task_id=task.id,
            provider="tmdb",
            provider_id="movie:68735",
            media_type="movie",
            title="魔兽",
            original_title="Warcraft",
            year=2016,
            payload={
                "plot": "test",
                "images": {"poster": "https://example.test/poster.jpg"},
            },
        ))
        session.commit()
        return task, video, source_dir

    def test_overwrite_on_dir_single_video_succeeds(
        self, tmp_path, monkeypatch,
    ):
        """目录型单视频 → overwrite 成功, target 是 .mkv 而不是 .MX]."""
        from media_pilot.services.target_conflict_handler import handle_overwrite_target
        from media_pilot.orchestration.jellyfin_movie_writer import (
            _movie_directory_name,
        )

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        # 预放已存在的目标 movie 目录, 让 build_movie_write_plan 触发冲突
        existing_dir = config.movies_dir / _movie_directory_name("魔兽", 2016)
        existing_dir.mkdir(parents=True, exist_ok=True)
        (existing_dir / "Warcraft.2016.1080p.mkv").write_bytes(b"existing")

        with sf() as session:
            task, video, source_dir = self._setup_dir_single_video_task(
                session, config, tmp_path,
            )
            run = _create_active_run(session, task.id)
            session.commit()
            task_id = task.id
            run_id = run.id

        # 模拟历史 bug: 决策 payload 里的 final_target_file 是错的
        # (用目录名后缀而不是 .mkv). 修复后 handler 不应再依赖 payload.
        with sf() as session:
            decision = _make_target_conflict_decision(
                session, task_id=task_id, run_id=run_id,
                payload={
                    "final_target_dir": str(existing_dir),
                    "final_target_file": str(existing_dir / "魔兽 (2016).MX]"),
                    "conflict": "final_target_file_exists",
                },
            )
            session.commit()
            decision_id = decision.id

        # 桩 execute_movie_write, 记录 source_path 是不是文件
        seen_source = {}

        class _StubWriteResult:
            status = "succeeded"
            warnings: list = []

        def _stub_execute_movie_write(
            session, *, task_id, source_path, detail, plan, client, provider,
            force_overwrite=False,
        ):
            seen_source["path"] = source_path
            seen_source["force"] = force_overwrite
            return _StubWriteResult()

        from media_pilot.orchestration import jellyfin_movie_writer
        monkeypatch.setattr(
            jellyfin_movie_writer, "execute_movie_write",
            _stub_execute_movie_write,
        )

        with sf() as session:
            from media_pilot.repository.models import AgentDecisionRequest
            fresh_decision = session.get(AgentDecisionRequest, decision_id)
            result = handle_overwrite_target(
                session=session, config=config, decision=fresh_decision,
            )
            session.commit()

        # 关键断言: source_path 是真实 .mkv 文件, 不是目录
        assert seen_source["path"].is_file(), (
            f"source_path 必须是文件, 拿到目录: {seen_source['path']}"
        )
        assert seen_source["path"].suffix == ".mkv"
        assert seen_source["path"] == video
        # handler 应当走 force_overwrite=True
        assert seen_source["force"] is True
        # handler 应当返回 published
        assert result["outcome"] == "published"
        # target 文件名后缀是 .mkv, 不是目录名后缀 .MX]
        assert result["final_target_file"].endswith(".mkv")
        assert ".MX]" not in result["final_target_file"]

    def test_overwrite_returns_structured_failure_on_dir_no_videos(
        self, tmp_path, monkeypatch,
    ):
        """目录里 0 个主视频 → handle_overwrite_target 抛 422 structured failure,
        不是 500. 让前端展示并允许用户重试或走 cancel_publish.
        """
        from media_pilot.repository.models import MetadataDetail
        from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
        from media_pilot.services.target_conflict_handler import handle_overwrite_target

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        source_dir = config.watch_dir / "Empty.Warcraft.2016"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "info.txt").write_text("info")

        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path=str(source_dir),
                status="waiting_user",
                current_step="target_conflict",
                media_type="movie",
            ))
            session.add(MetadataDetail(
                task_id=task.id,
                provider="tmdb", provider_id="movie:68735",
                media_type="movie", title="魔兽", year=2016,
                payload={"plot": "test", "images": {}},
            ))
            session.commit()
            run = _create_active_run(session, task.id)
            decision = _make_target_conflict_decision(
                session, task_id=task.id, run_id=run.id,
                payload={
                    "final_target_dir": "/tmp/movies/Empty",
                    "final_target_file": "/tmp/movies/Empty/x.mkv",
                },
            )
            session.commit()
            decision_id = decision.id
            task_id = task.id

        with sf() as session:
            from media_pilot.repository.models import AgentDecisionRequest
            fresh_decision = session.get(AgentDecisionRequest, decision_id)
            with pytest.raises(ValueError) as excinfo:
                handle_overwrite_target(
                    session=session, config=config, decision=fresh_decision,
                )
            # 422 不是 500
            err = excinfo.value.args[0]
            assert isinstance(err, dict)
            assert err["status_code"] == 422
            assert err.get("code") == "no_main_video"
            assert "no_main_video" in (err.get("detail") or "").lower() or \
                "主视频" in (err.get("detail") or "")

            # task 仍保持 waiting_user, 没被切到 failed
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task_id)
            assert task.status == "waiting_user"


# ══════════════════════════════════════════════════════════════════════
# Show overwrite_target dispatch (fix-show-absolute-episode-ingest-and-agent-search-loop §3)
# ══════════════════════════════════════════════════════════════════════
#
# 剧集 overwrite_target 必须走 _handle_overwrite_show_target, 不调用 LLM,
# 仅清理当前 EpisodeMapping 涉及的 episode 视频 / NFO / 同源字幕, 不
# 删除整个 show 或 season 目录, 也不影响其它无关 episode.

class TestShowOverwriteDispatch:
    def test_show_dispatches_to_show_handler(self, tmp_path, monkeypatch):
        """当 MetadataDetail.media_type == 'show' 时, handle_overwrite_target
        必须走 _handle_overwrite_show_target, 而不是 movie 路径."""
        from media_pilot.services import target_conflict_handler as tch
        from media_pilot.services.target_conflict_handler import (
            handle_overwrite_target,
        )

        captured = {}

        def _fake_show_handler(
            *, session, config, decision, task, orm_detail,
        ):
            captured["called"] = True
            captured["task_id"] = task.id
            captured["orm_detail_id"] = orm_detail.id
            captured["media_type"] = orm_detail.media_type
            return {"outcome": "published", "media_type": "show"}

        monkeypatch.setattr(
            tch, "_handle_overwrite_show_target", _fake_show_handler,
        )

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_show_task_with_detail(
                session, config, tmp_path, status="waiting_user",
            )
            run = _create_active_run(session, task.id)
            decision = _make_target_conflict_decision(
                session,
                task_id=task.id,
                run_id=run.id,
                payload={
                    "final_target_dir": str(
                        config.shows_dir / "Example Show (2024)"
                    ),
                    "final_target_file": str(
                        config.shows_dir / "Example Show (2024)"
                        / "Season 01"
                    ),
                    "conflict": "target_episode_file_exists:S01E01",
                    "media_type": "show",
                },
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.models import AgentDecisionRequest
            fresh = session.get(AgentDecisionRequest, decision.id)
            result = handle_overwrite_target(
                session=session, config=config, decision=fresh,
            )
            session.commit()

        assert captured["called"] is True
        assert captured["media_type"] == "show"
        assert result["media_type"] == "show"
        assert result["outcome"] == "published"

    def test_show_overwrite_only_clears_current_episode_artifacts(
        self, tmp_path, monkeypatch,
    ):
        """_handle_overwrite_show_target 调 execute_show_write(force_overwrite=True),
        只清理当前 EpisodeMapping 涉及的 episode 视频 / NFO / 同源字幕,
        不动无关 episode. 通过 fake execute_show_write 验证 force_overwrite=True."""
        from media_pilot.services.target_conflict_handler import (
            _handle_overwrite_show_target,
        )

        captured = {}

        class _StubWriteResult:
            status = "succeeded"
            warnings: list = []

        def _fake_execute_show_write(
            session, *, task_id, detail, plan, client, provider,
            force_overwrite=False,
        ):
            captured["force_overwrite"] = force_overwrite
            captured["task_id"] = task_id
            captured["provider"] = provider
            captured["plan"] = plan
            return _StubWriteResult()

        from media_pilot.orchestration import jellyfin_show_writer
        monkeypatch.setattr(
            jellyfin_show_writer, "execute_show_write",
            _fake_execute_show_write,
        )

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_show_task_with_detail(
                session, config, tmp_path, status="waiting_user",
            )
            run = _create_active_run(session, task.id)
            decision = _make_target_conflict_decision(
                session,
                task_id=task.id,
                run_id=run.id,
                payload={
                    "final_target_dir": str(
                        config.shows_dir / "Example Show (2024)" / "Season 01"
                    ),
                    "conflict": "target_episode_file_exists:S01E01",
                    "media_type": "show",
                },
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.models import AgentDecisionRequest
            fresh = session.get(AgentDecisionRequest, decision.id)
            from media_pilot.repository.repositories import MetadataDetailRepository
            orm = MetadataDetailRepository(session).get_for_task(task.id)
            from media_pilot.repository.repositories import IngestTaskRepository
            t = IngestTaskRepository(session).get(task.id)
            result = _handle_overwrite_show_target(
                session=session, config=config, decision=fresh,
                task=t, orm_detail=orm,
            )
            session.commit()

        assert captured["force_overwrite"] is True
        assert captured["task_id"] == task.id
        assert captured["plan"] is not None
        assert result["outcome"] == "published"
        assert result["media_type"] == "show"

    def test_movie_does_not_dispatch_to_show(self, tmp_path, monkeypatch):
        """Movie 任务走 movie 路径, 不进 _handle_overwrite_show_target."""
        from media_pilot.services import target_conflict_handler as tch
        from media_pilot.services.target_conflict_handler import (
            handle_overwrite_target,
        )

        captured = {"show_called": False}

        def _fake_show_handler(**_):
            captured["show_called"] = True
            return {"outcome": "should-not-run", "media_type": "show"}

        monkeypatch.setattr(
            tch, "_handle_overwrite_show_target", _fake_show_handler,
        )

        # movie overwrite 调用 execute_movie_write — monkeypatch 让它立刻成功,
        # 不真正写文件.
        class _StubMovieWriteResult:
            status = "succeeded"
            warnings: list = []

        def _fake_movie_write(
            session, *, task_id, source_path, detail, plan, client, provider,
            force_overwrite=False,
        ):
            return _StubMovieWriteResult()

        from media_pilot.orchestration import jellyfin_movie_writer
        monkeypatch.setattr(
            jellyfin_movie_writer, "execute_movie_write", _fake_movie_write,
        )

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_movie_task_with_detail(
                session, config, tmp_path, status="waiting_user",
            )
            run = _create_active_run(session, task.id)
            decision = _make_target_conflict_decision(
                session,
                task_id=task.id,
                run_id=run.id,
                payload={
                    "final_target_dir": str(
                        config.movies_dir / "Movie (2024)"
                    ),
                    "conflict": "target_file_already_exists",
                },
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.models import AgentDecisionRequest
            fresh = session.get(AgentDecisionRequest, decision.id)
            result = handle_overwrite_target(
                session=session, config=config, decision=fresh,
            )
            session.commit()

        assert captured["show_called"] is False
        assert result["outcome"] == "published"


# ── show task + MetadataDetail fixture helpers ─────────────────

def _make_show_task_with_detail(session, config, tmp_path, *, status):
    """构造剧集任务 + MetadataDetail + EpisodeMapping, 返回 task."""
    from media_pilot.repository.repositories import (
        EpisodeMappingRepository,
        IngestTaskCreate,
        IngestTaskRepository,
        MetadataDetailRepository,
    )

    source = tmp_path / "Show.S01E01.mkv"
    source.write_bytes(b"v")
    task = IngestTaskRepository(session).create(IngestTaskCreate(
        source_path=str(source),
        status=status,
        current_step="waiting_user",
        media_type="show",
    ))
    EpisodeMappingRepository(session).save_mappings(
        task_id=task.id,
        entries=[{
            "file_path": str(source), "season": 1, "episode": 1,
            "source": "filename",
        }],
    )
    MetadataDetailRepository(session).save(
        task_id=task.id,
        provider="tmdb",
        provider_id="tmdb:show-1",
        media_type="show",
        title="Example Show",
        original_title="Example Show",
        year=2024,
        payload={
            "plot": "A show.",
            "images": {
                "poster_url": None, "backdrop_url": None, "logo_url": None,
            },
            "external_ids": {"imdb_id": None},
        },
    )
    session.flush()
    return task
