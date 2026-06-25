"""Tests for complex input decision reply handlers.

Section 6.2: 决策回复测试覆盖主视频选择、字幕选择、自由文本复核和续跑 Agent.
These tests focus on the *handler* layer (handle_select_primary_video /
handle_select_subtitles / handle_review_complex_input) and verify the
MediaSourceSelection writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


def _make_task_with_input(session, source_path: str, **kwargs):
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

    defaults = {
        "source_path": source_path,
        "status": "discovered",
        "current_step": "agent_start",
    }
    defaults.update(kwargs)
    task = IngestTaskRepository(session).create(IngestTaskCreate(**defaults))
    session.commit()
    return task


def _make_run(session, task_id: str, *, status: str = "waiting_user",
              current_step: str | None = None):
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


def _make_decision(session, *, run_id: str, task_id: str, decision_type: str,
                   question: str, options: list[dict], free_text_allowed: bool = False):
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
    )

    dr = AgentDecisionRequestRepository(session).create(
        AgentDecisionRequestCreate(
            run_id=run_id,
            task_id=task_id,
            decision_type=decision_type,
            question=question,
            options=options,
            free_text_allowed=free_text_allowed,
        ),
    )
    session.commit()
    return dr


@dataclass
class _DecisionShim:
    """Minimal stand-in for AgentDecisionRequest ORM model, for handler direct calls."""
    id: str
    task_id: str
    run_id: str
    decision_type: str
    options: list[dict] = field(default_factory=list)
    decision: dict | None = None
    option_id: str | None = None


def _shim_from(decision, *, option_id: str | None = None, free_text: str | None = None):
    decision_payload: dict | None = None
    if option_id is not None:
        decision_payload = {"option_id": option_id, "type": "option"}
    elif free_text is not None:
        decision_payload = {"free_text": free_text, "type": "free_text"}
    return _DecisionShim(
        id=decision.id, task_id=decision.task_id, run_id=decision.run_id,
        decision_type=decision.decision_type,
        options=decision.options,
        decision=decision_payload,
        option_id=option_id,
    )


# ── handle_select_primary_video ────────────────────────────────────


class TestHandleSelectPrimaryVideo:
    def test_writes_media_source_selection_with_user_decision_marker(
        self, tmp_path: Path,
    ):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")

        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task_with_input(session, str(source))
            run = _make_run(session, task.id, status="waiting_user")
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_primary_video",
                question="选择主视频",
                options=[
                    {
                        "id": "video_0",
                        "label": "Example.Movie.2026.mkv",
                        "description": "...",
                        "payload": {"path": str(source), "name": source.name,
                                    "size_bytes": source.stat().st_size},
                    },
                ],
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.complex_input_reply import (
                handle_select_primary_video,
            )
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                MediaSourceSelectionRepository,
            )

            decision = AgentDecisionRequestRepository(session).get(decision_id)
            shim = _shim_from(decision, option_id="video_0")
            result = handle_select_primary_video(
                session=session, config=config, decision=shim,
            )
            session.commit()

            sel = MediaSourceSelectionRepository(session).get_for_task(task.id)
            assert result.status == "recorded"
            assert sel is not None
            assert sel.selected_path == str(source)
            assert sel.input_path == str(source)
            assert sel.reason == "user_decision:select_primary_video"
            assert sel.payload["selection_source"] == "user_decision"
            assert sel.payload["decision_id"] == decision.id
            assert sel.payload["decision_type"] == "select_primary_video"

    def test_rejects_path_outside_input_node(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")

        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)

        # Fake an "outside" path that doesn't actually exist on disk
        outside_path = str(tmp_path / "elsewhere" / "evil.mkv")

        with sf() as session:
            task = _make_task_with_input(session, str(source))
            run = _make_run(session, task.id, status="waiting_user")
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_primary_video",
                question="...",
                options=[
                    {
                        "id": "video_0",
                        "label": "evil.mkv",
                        "description": "...",
                        "payload": {"path": outside_path, "name": "evil.mkv",
                                    "size_bytes": 0},
                    },
                ],
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.complex_input_reply import (
                handle_select_primary_video,
            )
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
            )

            decision = AgentDecisionRequestRepository(session).get(decision_id)
            shim = _shim_from(decision, option_id="video_0")
            result = handle_select_primary_video(
                session=session, config=config, decision=shim,
            )
            assert result.status == "failed"
            assert "outside" in result.reason or "input" in result.reason

    def test_preserves_existing_selected_subtitles(self, tmp_path: Path):
        """主视频选择不应清空已存在的 selected_subtitles."""
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")
        sub = config.downloads_dir / "Example.Movie.2026.srt"
        sub.write_text("...")

        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task_with_input(session, str(source))
            run = _make_run(session, task.id, status="waiting_user")

            # Pre-existing selection with subtitles
            from media_pilot.repository.repositories import (
                MediaSourceSelectionRepository,
            )
            pre_existing = MediaSourceSelectionRepository(session).save(
                task_id=task.id,
                input_path=str(source),
                selected_path=None,
                confidence=None,
                reason="user_decision:select_subtitles",
                payload={
                    "selection_source": "user_decision",
                    "selected_subtitles": [str(sub)],
                },
            )
            pre_existing_id = pre_existing.id

            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_primary_video",
                question="...",
                options=[
                    {
                        "id": "video_0",
                        "label": source.name,
                        "description": "...",
                        "payload": {"path": str(source), "name": source.name,
                                    "size_bytes": source.stat().st_size},
                    },
                ],
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.complex_input_reply import (
                handle_select_primary_video,
            )
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                MediaSourceSelectionRepository,
            )

            decision = AgentDecisionRequestRepository(session).get(decision_id)
            shim = _shim_from(decision, option_id="video_0")
            result = handle_select_primary_video(
                session=session, config=config, decision=shim,
            )
            assert result.status == "recorded"
            new_sel = MediaSourceSelectionRepository(session).get_for_task(task.id)
            assert new_sel.id != pre_existing_id
            # 沿用既有字幕
            assert new_sel.payload.get("selected_subtitles") == [str(sub)]


# ── handle_select_subtitles ────────────────────────────────────────


class TestHandleSelectSubtitles:
    def test_records_selected_subtitles_payload(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")
        sub = config.downloads_dir / "random.srt"
        sub.write_text("...")

        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task_with_input(session, str(source))
            run = _make_run(session, task.id, status="waiting_user")
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_subtitles",
                question="...",
                options=[
                    {
                        "id": "subtitle_0",
                        "label": "random.srt",
                        "description": "...",
                        "payload": {"path": str(sub), "name": "random.srt",
                                    "size_bytes": 3},
                    },
                    {
                        "id": "no_subtitles",
                        "label": "不带入字幕",
                        "description": "...",
                        "payload": {"selected_subtitles": []},
                    },
                ],
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.complex_input_reply import (
                handle_select_subtitles,
            )
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                MediaSourceSelectionRepository,
            )

            decision = AgentDecisionRequestRepository(session).get(decision_id)
            shim = _shim_from(decision, option_id="subtitle_0")
            result = handle_select_subtitles(
                session=session, config=config, decision=shim,
            )
            assert result.status == "recorded"
            sel = MediaSourceSelectionRepository(session).get_for_task(task.id)
            assert sel.reason == "user_decision:select_subtitles"
            assert sel.payload["selected_subtitles"] == [str(sub)]
            assert sel.payload["selection_source"] == "user_decision"

    def test_no_subtitles_option_clears_selection(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")

        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task_with_input(session, str(source))
            run = _make_run(session, task.id, status="waiting_user")
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_subtitles",
                question="...",
                options=[
                    {
                        "id": "no_subtitles",
                        "label": "不带入字幕",
                        "description": "...",
                        "payload": {"selected_subtitles": []},
                    },
                ],
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.complex_input_reply import (
                handle_select_subtitles,
            )
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                MediaSourceSelectionRepository,
            )

            decision = AgentDecisionRequestRepository(session).get(decision_id)
            shim = _shim_from(decision, option_id="no_subtitles")
            result = handle_select_subtitles(
                session=session, config=config, decision=shim,
            )
            assert result.status == "recorded"
            sel = MediaSourceSelectionRepository(session).get_for_task(task.id)
            assert sel.payload["selected_subtitles"] == []

    def test_subtitle_outside_input_node_fails(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")
        evil_sub = str(tmp_path / "elsewhere" / "evil.srt")

        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task_with_input(session, str(source))
            run = _make_run(session, task.id, status="waiting_user")
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_subtitles",
                question="...",
                options=[
                    {
                        "id": "subtitle_0",
                        "label": "evil.srt",
                        "description": "...",
                        "payload": {"path": evil_sub, "name": "evil.srt",
                                    "size_bytes": 0},
                    },
                ],
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.complex_input_reply import (
                handle_select_subtitles,
            )
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
            )

            decision = AgentDecisionRequestRepository(session).get(decision_id)
            shim = _shim_from(decision, option_id="subtitle_0")
            result = handle_select_subtitles(
                session=session, config=config, decision=shim,
            )
            assert result.status == "failed"
            assert "outside" in result.reason or "input" in result.reason


# ── handle_review_complex_input ────────────────────────────────────


class TestHandleReviewComplexInput:
    def test_records_user_note_payload(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")

        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task_with_input(session, str(source))
            run = _make_run(session, task.id, status="waiting_user")
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="review_complex_input",
                question="...",
                options=[],
                free_text_allowed=True,
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.complex_input_reply import (
                handle_review_complex_input,
            )
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                MediaSourceSelectionRepository,
            )

            decision = AgentDecisionRequestRepository(session).get(decision_id)
            shim = _shim_from(
                decision, free_text="请按电影处理这个目录",
            )
            result = handle_review_complex_input(
                session=session, config=config, decision=shim,
            )
            assert result.status == "recorded"
            sel = MediaSourceSelectionRepository(session).get_for_task(task.id)
            assert sel.reason == "user_decision:review_complex_input"
            assert sel.payload["user_note"] == "请按电影处理这个目录"

    def test_missing_free_text_fails(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")

        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task_with_input(session, str(source))
            run = _make_run(session, task.id, status="waiting_user")
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="review_complex_input",
                question="...",
                options=[],
                free_text_allowed=True,
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.complex_input_reply import (
                handle_review_complex_input,
            )
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
            )

            decision = AgentDecisionRequestRepository(session).get(decision_id)
            shim = _shim_from(decision)  # no option_id, no free_text
            result = handle_review_complex_input(
                session=session, config=config, decision=shim,
            )
            assert result.status == "failed"
            assert "free_text" in result.reason


# ── 边界: decision reply 自身 (reply_to_decision) 行为 ─────────────


class TestReplyToDecisionComplexInput:
    """reply_to_decision 接到 complex input 决策时:
    1. 不抛 409
    2. 调用正确的 handler
    3. 续跑 AgentRun
    """

    def test_select_primary_video_reply_continues_agent_run(
        self, tmp_path: Path, monkeypatch,
    ):
        from media_pilot.agent.runner import AgentRunResult

        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")

        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task_with_input(session, str(source))
            run = _make_run(session, task.id, status="waiting_user")
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_primary_video",
                question="...",
                options=[
                    {
                        "id": "video_0",
                        "label": source.name,
                        "description": "...",
                        "payload": {"path": str(source), "name": source.name,
                                    "size_bytes": source.stat().st_size},
                    },
                ],
            )
            decision_id = dr.id
            task_id = task.id
            run_id = run.id

        # Stub continue_agent_run to avoid LLM calls
        def _stub_continue(*args, **kwargs):
            return AgentRunResult(
                run_id=run_id,
                status="completed",
                message_count=0,
                tool_call_count=0,
            )

        monkeypatch.setattr(
            "media_pilot.agent.runner.continue_agent_run",
            _stub_continue,
        )

        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput, reply_to_decision,
            )
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                IngestTaskRepository,
                MediaSourceSelectionRepository,
            )

            result = reply_to_decision(
                session=session, config=config,
                reply=ReplyInput(decision_id=decision_id, option_id="video_0"),
            )
            session.commit()
            assert isinstance(result, AgentRunResult)

            # Run 切到 active (续跑由 stub 立即结束为 completed)
            run = AgentRunRepository(session).get(run_id)
            assert run.status in ("active", "completed")
            assert run.current_step in ("user_replied", "completed")

            # task 切到 agent_running
            task = IngestTaskRepository(session).get(task_id)
            assert task.status in ("agent_running", "completed")
            assert task.current_step in ("user_replied", "completed")

            # MediaSourceSelection 写入了
            sel = MediaSourceSelectionRepository(session).get_for_task(task_id)
            assert sel is not None
            assert sel.selected_path == str(source)

    def test_run_status_must_be_waiting_user_unless_complex(
        self, tmp_path: Path, monkeypatch,
    ):
        """非 complex input 决策 + run.status=active → 应该 409.
        complex input 决策 (select_primary_video / select_subtitles /
        review_complex_input) 现在也走同样的 run.status 等待用户检查.
        旁路只留给 post_revoke / target_conflict / source_cleanup 这类
        确定性后端路径. 参见 TestComplexInputRequiresWaitingUser.
        """
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")

        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task_with_input(session, str(source))
            run = _make_run(session, task.id, status="active", current_step="chat_only")
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="metadata_selection",
                question="...",
                options=[
                    {"id": "opt_a", "label": "A", "description": "",
                     "payload": {}},
                ],
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput, reply_to_decision,
            )

            with pytest.raises(ValueError) as exc:
                reply_to_decision(
                    session=session, config=config,
                    reply=ReplyInput(decision_id=decision_id, option_id="opt_a"),
                )
            assert "409" in str(exc.value)


# ── Issue 2: complex input 决策在 run 非 waiting_user 时必须 409 ───


class TestComplexInputRequiresWaitingUser:
    """`select_primary_video` / `select_subtitles` / `review_complex_input`
    不再作为确定性旁路放行, 必须在 run.status == "waiting_user" 时才能被
    回复. 任何 run.status == "active" / "completed" 等状态时回复 → 409.
    """

    def _setup_active_decision(self, tmp_path, decision_type):
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source = config.downloads_dir / "Example.Movie.2026.mkv"
        source.write_bytes(b"video")

        from tests.test_api_v1 import _make_session_factory
        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task_with_input(session, str(source))
            run = _make_run(session, task.id, status="active", current_step="chat_only")
            if decision_type == "review_complex_input":
                dr = _make_decision(
                    session, run_id=run.id, task_id=task.id,
                    decision_type=decision_type,
                    question="...",
                    options=[],
                    free_text_allowed=True,
                )
            else:
                dr = _make_decision(
                    session, run_id=run.id, task_id=task.id,
                    decision_type=decision_type,
                    question="...",
                    options=[
                        {
                            "id": "opt_0",
                            "label": "...",
                            "description": "...",
                            "payload": {"path": str(source), "name": source.name,
                                        "size_bytes": source.stat().st_size},
                        },
                    ],
                )
            decision_id = dr.id
            return sf, config, decision_id

    def test_select_primary_video_active_run_returns_409(self, tmp_path: Path):
        sf, config, decision_id = self._setup_active_decision(
            tmp_path, "select_primary_video",
        )
        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput, reply_to_decision,
            )
            with pytest.raises(ValueError) as exc:
                reply_to_decision(
                    session=session, config=config,
                    reply=ReplyInput(decision_id=decision_id, option_id="opt_0"),
                )
            assert "409" in str(exc.value)
            assert "waiting for user" in str(exc.value)

    def test_select_subtitles_active_run_returns_409(self, tmp_path: Path):
        sf, config, decision_id = self._setup_active_decision(
            tmp_path, "select_subtitles",
        )
        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput, reply_to_decision,
            )
            with pytest.raises(ValueError) as exc:
                reply_to_decision(
                    session=session, config=config,
                    reply=ReplyInput(decision_id=decision_id, option_id="opt_0"),
                )
            assert "409" in str(exc.value)

    def test_review_complex_input_active_run_returns_409(self, tmp_path: Path):
        sf, config, decision_id = self._setup_active_decision(
            tmp_path, "review_complex_input",
        )
        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput, reply_to_decision,
            )
            with pytest.raises(ValueError) as exc:
                reply_to_decision(
                    session=session, config=config,
                    reply=ReplyInput(decision_id=decision_id,
                                     free_text="用户说明"),
                )
            assert "409" in str(exc.value)
