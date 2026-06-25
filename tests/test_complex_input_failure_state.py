"""Issue 3: no_videos / unsafe_path / scan_failed 路径必须把 task 切到
agent_failed 终态, 同步 AgentRun.status, 不停留在 waiting_user /
agent_running 状态. 这保证前端时间线和 task_mapper 看到的 task.status
不会与 pending_decision_count=0 / failure 矛盾.

测试通过 _handle_prepare_complex_input_decision 直接构造 ToolContext
调用工具处理器, 不依赖 LLM.
"""

from __future__ import annotations

from pathlib import Path

import pytest


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


def _make_task(session, source_path: str | None, **kwargs):
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

    defaults = {
        "source_path": source_path or "/tmp/nonexistent.mkv",
        "status": "discovered",
        "current_step": "agent_start",
    }
    defaults.update(kwargs)
    task = IngestTaskRepository(session).create(IngestTaskCreate(**defaults))
    session.commit()
    return task


def _make_run(session, task_id: str, *, status: str = "active",
              current_step: str = "user_replied"):
    from media_pilot.repository.repositories import (
        AgentRunCreate,
        AgentRunRepository,
    )

    run = AgentRunRepository(session).create(
        AgentRunCreate(task_id=task_id, current_step=current_step),
    )
    if status != "active":
        AgentRunRepository(session).update_status(
            run, status=status, current_step=current_step,
        )
    session.commit()
    return run


# ── helper: 直接调用 complex_input 工具处理器 ──────────────────────


def _invoke_prepare_tool(*, session, config, task_id: str, run_id: str):
    from media_pilot.agent.tools.base import ToolContext
    from media_pilot.agent.tools.complex_input import (
        _handle_prepare_complex_input_decision,
    )

    ctx = ToolContext(
        session=session, config=config, task_id=task_id, run_id=run_id,
    )
    return _handle_prepare_complex_input_decision(ctx, {"task_id": task_id})


# ── 路径解析失败 (scan_failed) → task agent_failed ─────────────────


class TestScanFailedTransitionsToAgentFailed:
    def test_nonexistent_path_marks_task_agent_failed(self, tmp_path: Path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        # task.source_path 指向一个不存在的路径
        missing = str(tmp_path / "missing_dir" / "video.mkv")

        with sf() as session:
            task = _make_task(session, missing)
            run = _make_run(session, task.id, status="active")
            result = _invoke_prepare_tool(
                session=session, config=config,
                task_id=task.id, run_id=run.id,
            )
            session.commit()
            assert result.status == "failure"
            assert result.data.get("reason") == "source_path_not_found"

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                IngestTaskRepository,
            )
            task = IngestTaskRepository(session).get(task.id)
            assert task.status == "agent_failed"
            assert task.current_step == "agent_failed"
            assert task.failure_reason == "source_path_not_found"
            run = AgentRunRepository(session).get(run.id)
            # run 也切到 failed
            assert run.status == "failed"
            assert run.current_step == "agent_failed"


# ── 越界路径 (unsafe_path) → task agent_failed ─────────────────────


class TestUnsafePathTransitionsToAgentFailed:
    def test_path_outside_safe_roots_marks_task_agent_failed(
        self, tmp_path: Path,
    ):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        # 受控根之外的路径 — 不在 downloads / watch / workspace 内
        outside = tmp_path / "outside_unsafe"
        outside.mkdir()
        target = outside / "movie.mkv"
        target.write_bytes(b"x")

        with sf() as session:
            task = _make_task(session, str(target))
            run = _make_run(session, task.id, status="active")
            result = _invoke_prepare_tool(
                session=session, config=config,
                task_id=task.id, run_id=run.id,
            )
            session.commit()
            assert result.status == "failure"
            assert result.data.get("reason") == "source_path_outside_safe_roots"

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                IngestTaskRepository,
            )
            task = IngestTaskRepository(session).get(task.id)
            assert task.status == "agent_failed"
            assert task.current_step == "agent_failed"
            assert task.failure_reason == "source_path_outside_safe_roots"
            run = AgentRunRepository(session).get(run.id)
            assert run.status == "failed"
            assert run.current_step == "agent_failed"


# ── 无视频 (no_videos) → task agent_failed ──────────────────────────


class TestNoVideosTransitionsToAgentFailed:
    def test_directory_without_videos_marks_task_agent_failed(
        self, tmp_path: Path,
    ):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        # 只有字幕 / NFO, 没有视频
        (config.downloads_dir / "movie.srt").write_text("...")

        with sf() as session:
            task = _make_task(session, str(config.downloads_dir))
            run = _make_run(session, task.id, status="active")
            result = _invoke_prepare_tool(
                session=session, config=config,
                task_id=task.id, run_id=run.id,
            )
            session.commit()
            assert result.status == "failure"
            assert result.data.get("reason") == "no_video_files_found"

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                IngestTaskRepository,
            )
            task = IngestTaskRepository(session).get(task.id)
            assert task.status == "agent_failed"
            assert task.current_step == "agent_failed"
            assert task.failure_reason == "no_video_files_found"
            run = AgentRunRepository(session).get(run.id)
            assert run.status == "failed"
            assert run.current_step == "agent_failed"


# ── 成功路径 (ready) 不动 task.status ──────────────────────────────


class TestReadyPathDoesNotTransitionTask:
    def test_single_file_ready_keeps_task_in_place(self, tmp_path: Path):
        """单文件 ready 是成功路径, 不应被错误地切到 agent_failed."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")

        with sf() as session:
            task = _make_task(session, str(source))
            run = _make_run(session, task.id, status="active")
            result = _invoke_prepare_tool(
                session=session, config=config,
                task_id=task.id, run_id=run.id,
            )
            session.commit()
            assert result.status == "success"
            assert result.data.get("ready") is True

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task = IngestTaskRepository(session).get(task.id)
            # 任务保持原状态, 不被切到 agent_failed
            assert task.status == "discovered"
            assert task.failure_reason is None


# ── Issue 1: review_user_note_already_consumed 必须切 agent_failed, 不得
#    创建无法回复的新 review 决策. 同时校验: 任务进入 agent_failed,
#    run 切 failed, 没有新的 pending decision, 也没有遗留无法回复的
#    AgentDecisionRequest.


class TestReviewUserNoteAlreadyConsumed:
    def _seed_selection_with_user_note(
        self, session, *, task_id: str, input_path: str, user_note: str,
    ):
        from media_pilot.repository.repositories import (
            MediaSourceSelectionRepository,
        )
        MediaSourceSelectionRepository(session).save(
            task_id=task_id,
            input_path=input_path,
            selected_path=None,
            confidence=1.0,
            reason="user_decision:review_complex_input",
            payload={
                "selection_source": "user_decision",
                "decision_type": "review_complex_input",
                "user_note": user_note,
            },
        )

    def test_consumed_user_note_does_not_create_new_decision(
        self, tmp_path: Path,
    ):
        """用户已在上一轮 reply_complex_input, MediaSourceSelection 含
        user_note → 本轮 prepare_complex_input_decision 返回 unsupported
        但 tool handler 不得创建新 AgentDecisionRequest."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        bdmv_root = config.downloads_dir / "BDMV_MOVIE"
        bdmv_root.mkdir()
        (bdmv_root / "BDMV").mkdir()

        with sf() as session:
            task = _make_task(session, str(bdmv_root))
            run = _make_run(session, task.id, status="active")
            # 模拟用户已通过 reply_to_decision 写入 user_note
            self._seed_selection_with_user_note(
                session, task_id=task.id, input_path=str(bdmv_root),
                user_note="请把 BDMV 文件夹当成蓝光原盘处理",
            )
            session.commit()
            result = _invoke_prepare_tool(
                session=session, config=config,
                task_id=task.id, run_id=run.id,
            )
            session.commit()
            assert result.status == "failure"
            assert result.data.get("reason") == "complex_input_review_unsupported"

        # 关键断言: 没有创建新的 pending AgentDecisionRequest
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                AgentRunRepository,
                IngestTaskRepository,
            )
            pending = AgentDecisionRequestRepository(session).list_pending_by_task(
                task.id,
            )
            assert pending == [], (
                f"review_user_note_already_consumed 不应再创建 "
                f"AgentDecisionRequest, 实际: {[p.decision_type for p in pending]}"
            )
            # task 切到 agent_failed, failure_reason 写明
            task = IngestTaskRepository(session).get(task.id)
            assert task.status == "agent_failed"
            assert task.current_step == "agent_failed"
            assert task.failure_reason == "complex_input_review_unsupported"
            # run 切到 failed
            run = AgentRunRepository(session).get(run.id)
            assert run.status == "failed"
            assert run.current_step == "agent_failed"
            assert run.error_message == "complex_input_review_unsupported"
