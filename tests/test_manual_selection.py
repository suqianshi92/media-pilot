"""人工辅助元数据选择服务测试

覆盖：
1. 目标冲突 → 创建 AgentDecisionRequest(decision_type="target_conflict")，
   task.status / run.status 切到 waiting_user，current_step 切到 target_conflict；
   AgentStatusSummary.run_status == "waiting_user" 且 pending_decision_count == 1。
2. 安全门禁阻塞 + 任务无 active/waiting AgentRun → 自动创建 system run，
   再创建 decision 并把 run 切到 waiting_user；返回的 decision_id 必非空；
   reply_to_decision(option_id="retry") 不再 409。
3. 安全门禁阻塞 + 任务已有 active AgentRun → decision 挂在该 run 上；
   run.status 由 active 切到 waiting_user。
4. 候选选择和详情获取失败的早退路径仍然只返回 saved。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from media_pilot.config import AppConfig
from media_pilot.repository.database import (
    create_session_factory,
    initialize_database,
)
from media_pilot.repository.models import (
    AgentDecisionRequest,
    AgentRun,
    IngestTask,
    MediaCandidate,
)
from media_pilot.repository.repositories import (
    IngestTaskCreate,
    IngestTaskRepository,
)


# ── helpers ──────────────────────────────────────────────────────────────


def _make_config(tmp_path: Path) -> AppConfig:
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        downloads_dir=downloads,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    return config


def _make_task(
    session_factory,
    *,
    source_path: str,
    status: str = "agent_failed",
) -> str:
    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=source_path,
            status=status,
            media_type="movie",
        ))
        task_id = task.id
        session.commit()
    return task_id


def _seed_metadata_detail(
    session_factory,
    task_id: str,
    *,
    title: str = "Test Movie",
    year: int | None = 2026,
    provider: str = "tmdb",
    provider_id: str = "movie:123",
) -> None:
    from media_pilot.repository.models import MetadataDetail
    from media_pilot.repository.repositories import MetadataDetailRepository

    with session_factory() as session:
        MetadataDetailRepository(session).upsert(
            task_id=task_id,
            provider=provider,
            provider_id=provider_id,
            media_type="movie",
            payload={
                "title": title,
                "year": year,
                "media_type": "movie",
                "provider_id": provider_id,
                "provider": provider,
            },
        )
        session.commit()


def _stub_ok_eligibility_and_images(monkeypatch, auto_ingest) -> None:
    class _OkEligibility:
        eligible = True
        media_type = "movie"
        candidate_count = 1
        confidence_threshold = 0.8
        margin = 0.0
        blocking_reasons: list[str] = []
        warnings: list[str] = []

    monkeypatch.setattr(
        auto_ingest,
        "check_eligibility",
        lambda *, session, config, task_id: _OkEligibility(),
    )
    monkeypatch.setattr(
        "media_pilot.orchestration.jellyfin_movie_writer._download_image",
        lambda client, url, path: b"poster",
    )


# ── fixtures for stubbing detail-fetch eligibility ───────────────────────


class _StubAdvisor:
    """Stub for testing target_conflict / blocked paths without LLM."""

    pass


@pytest.fixture
def stub_dependencies(monkeypatch):
    """Stub out auto_ingest dependencies so we can drive eligibility without LLM."""
    from media_pilot.services import auto_ingest
    from media_pilot.services import manual_selection as ms

    def _noop_persist(*, session, task_id, provider_name, provider_id, media_type,
                      title, year=None, original_title=None, confidence=None):
        from media_pilot.repository.repositories import MediaCandidateRepository
        from media_pilot.services.auto_ingest import (
            PersistSelectionResult as Result,
        )

        # Persist a candidate with confidence=1.0 as the manual selection service does
        repo = MediaCandidateRepository(session)
        # Use a low-level call matching persist_metadata_selection
        candidate = repo.add_candidate(
            task_id=task_id,
            source=provider_name,
            media_type=media_type,
            title=title,
            original_title=original_title or title,
            year=year,
            external_id=provider_id,
            confidence=confidence or 1.0,
            reason="manual_selection",
            payload={
                "title": title,
                "original_title": original_title or title,
                "year": year,
                "media_type": media_type,
                "external_id": provider_id,
            },
        )
        return Result(status="success", summary="ok", candidate_id=candidate.id)

    def _success_detail(*, session, config, task_id, provider_name, provider_id, media_type):
        from media_pilot.repository.repositories import MetadataDetailRepository
        from media_pilot.services.auto_ingest import (
            FetchAndSaveDetailResult as Result,
        )
        # 真正落库 MetadataDetail, 让 _quick_publish 能读到
        MetadataDetailRepository(session).save(
            task_id=task_id,
            provider=provider_name,
            provider_id=provider_id,
            media_type=media_type,
            title="Test Movie",
            original_title="Test Movie",
            year=2026,
            payload={
                "title": "Test Movie",
                "original_title": "Test Movie",
                "year": 2026,
                "media_type": "movie",
                "provider_id": provider_id,
                "provider": provider_name,
            },
        )
        return Result(
            status="success",
            provider=provider_name,
            provider_id=provider_id,
            title="Test Movie",
            year=2026,
            summary="ok",
        )

    monkeypatch.setattr(auto_ingest, "persist_metadata_selection", _noop_persist)
    monkeypatch.setattr(auto_ingest, "fetch_and_save_metadata_detail", _success_detail)


# ── 1. target_conflict path ────────────────────────────────────────────


def test_manual_select_target_conflict_creates_decision_and_waits_user(
    tmp_path: Path, monkeypatch, stub_dependencies
) -> None:
    """目标冲突 → 创建 target_conflict decision + task 转 waiting_user/target_conflict。"""
    from media_pilot.services import auto_ingest
    from media_pilot.services.manual_selection import submit_manual_selection

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    # 在 movies_dir 预放冲突目标，确保 _quick_publish 走到 target_conflict
    movies_dir = config.movies_dir
    target_dir = movies_dir / "Test Movie (2026)"
    target_file = target_dir / "Test Movie (2026).mkv"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file.write_bytes(b"existing content")

    downloads = config.downloads_dir
    source_path = downloads / "source.mkv"
    source_path.write_bytes(b"new content")

    task_id = _make_task(session_factory, source_path=str(source_path))

    # 提供一个无阻塞的 eligibility（门禁通过 + candidate 已存在）
    from dataclasses import dataclass
    from media_pilot.services.auto_ingest import EligibilityResult

    @dataclass
    class _OkEligibility:
        eligible: bool = True
        media_type: str = "movie"
        candidate_count: int = 1
        confidence_threshold: float = 0.8
        margin: float = 0.0
        blocking_reasons: list[str] = None
        warnings: list[str] = None

        def __post_init__(self) -> None:
            if self.blocking_reasons is None:
                self.blocking_reasons = []
            if self.warnings is None:
                self.warnings = []

    def _ok_eligibility(*, session, config, task_id):
        # 没有 candidates 在 DB 也能跑通（quick_publish 内部不依赖 candidate 计数）
        return _OkEligibility()

    monkeypatch.setattr(auto_ingest, "check_eligibility", _ok_eligibility)

    # 任务尚无 AgentRun → _ensure_manual_select_run 应自动创建 system run
    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:123",
            title="Test Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    # 关键断言
    assert result.status == "waiting_user", f"expected waiting_user, got {result.status}: {result.summary}"
    assert result.decision_id is not None, "target_conflict 路径必须创建 decision"
    assert "目标" in result.summary

    # DB 状态: task = waiting_user / current_step=target_conflict, decision = pending
    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "waiting_user", f"task.status={task.status}"
        assert task.current_step == "target_conflict", f"task.current_step={task.current_step}"

        decision = session.get(AgentDecisionRequest, result.decision_id)
        assert decision is not None
        assert decision.decision_type == "target_conflict"
        assert decision.status == "pending"
        assert decision.payload.get("final_target_file") == str(target_file)
        assert decision.payload.get("conflict") is not None
        assert decision.payload.get("source") == "manual_selection"

        # system run 应被自动创建并切到 waiting_user, current_step=target_conflict
        run = session.get(AgentRun, decision.run_id)
        assert run is not None
        assert run.task_id == task_id
        assert run.status == "waiting_user", f"run.status={run.status}"
        assert run.current_step == "target_conflict", f"run.current_step={run.current_step}"

    # AgentStatusSummary 必须反映 run_status=waiting_user 与 pending_decision_count=1
    from media_pilot.api.task_mapper import _build_agent_status_index

    with session_factory() as session:
        summary_index = _build_agent_status_index(session, [task_id])
        summary = summary_index[task_id]
        assert summary.run_status == "waiting_user", f"summary.run_status={summary.run_status}"
        assert summary.pending_decision_count == 1, (
            f"summary.pending_decision_count={summary.pending_decision_count}"
        )

    # 清理
    shutil.rmtree(movies_dir / ".media-pilot-staging", ignore_errors=True)


# ── 2. blocked path with no existing run ───────────────────────────────


def test_manual_select_blocked_with_no_existing_run_creates_system_run_and_decision(
    tmp_path: Path, monkeypatch, stub_dependencies
) -> None:
    """阻塞 + 无 active run → 自动建 system run + decision_id 非空。"""
    from media_pilot.services import auto_ingest
    from media_pilot.services.manual_selection import submit_manual_selection

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_path = config.downloads_dir / "source.mkv"
    source_path.write_bytes(b"any content")
    task_id = _make_task(session_factory, source_path=str(source_path))

    # eligibility 返回阻塞原因（"multiple_videos" 之类的安全门禁）
    from media_pilot.services.auto_ingest import EligibilityResult

    def _blocked_eligibility(*, session, config, task_id):
        return EligibilityResult(
            eligible=False,
            blocking_reasons=["multiple_video_files_not_supported"],
            media_type="movie",
        )

    monkeypatch.setattr(auto_ingest, "check_eligibility", _blocked_eligibility)

    # 任务尚无 run
    with session_factory() as session:
        existing_runs = session.scalars(
            select(AgentRun).where(AgentRun.task_id == task_id)
        ).all()
        assert existing_runs == []

        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:123",
            title="Test Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    # 决策必非空
    assert result.status == "waiting_user"
    assert result.decision_id is not None, (
        "blocked 路径即使无 active run, 也必须创建 decision (run 应被自动建)"
    )
    assert "multiple_video_files_not_supported" in result.summary

    # DB 校验
    with session_factory() as session:
        decision = session.get(AgentDecisionRequest, result.decision_id)
        assert decision is not None
        assert decision.decision_type == "manual_selection_blocked"
        assert decision.status == "pending"

        # system run 应被自动创建并切到 waiting_user, current_step=manual_selection_blocked
        run = session.get(AgentRun, decision.run_id)
        assert run is not None
        assert run.task_id == task_id
        assert run.status == "waiting_user", f"run.status={run.status}"
        assert run.current_step == "manual_selection_blocked", (
            f"run.current_step={run.current_step}"
        )

        # task 状态联动
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "waiting_user"
        assert task.current_step == "manual_selection_blocked", (
            f"task.current_step={task.current_step}"
        )

    # reply_to_decision(option_id="retry") 不应再 409 — run 已是 waiting_user
    from media_pilot.services.decision_reply import ReplyInput, reply_to_decision

    with session_factory() as session:
        try:
            reply_to_decision(
                session=session,
                config=config,
                reply=ReplyInput(
                    decision_id=result.decision_id,
                    option_id="retry",
                ),
            )
            session.commit()
        except ValueError as exc:
            payload = exc.args[0] if exc.args else None
            if isinstance(payload, dict) and payload.get("status_code") == 409:
                pytest.fail(
                    f"reply_to_decision 不应再 409, 但 run 守卫失败: {payload}"
                )
            raise


# ── 3. blocked path with existing run ──────────────────────────────────


def test_manual_select_blocked_with_existing_run_attaches_decision(
    tmp_path: Path, monkeypatch, stub_dependencies
) -> None:
    """阻塞 + 已有 active run → decision 挂到现有 run 上。"""
    from media_pilot.repository.repositories import AgentRunCreate, AgentRunRepository
    from media_pilot.services import auto_ingest
    from media_pilot.services.manual_selection import submit_manual_selection

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_path = config.downloads_dir / "source.mkv"
    source_path.write_bytes(b"any content")
    task_id = _make_task(session_factory, source_path=str(source_path))

    # 预先创建 active run
    with session_factory() as session:
        run_repo = AgentRunRepository(session)
        existing_run = run_repo.create(AgentRunCreate(
            task_id=task_id,
            current_step="agent_start",
        ))
        existing_run_id = existing_run.id
        session.commit()

    # eligibility 返回阻塞原因
    from media_pilot.services.auto_ingest import EligibilityResult

    def _blocked_eligibility(*, session, config, task_id):
        return EligibilityResult(
            eligible=False,
            blocking_reasons=["iso_image_not_supported"],
            media_type="movie",
        )

    monkeypatch.setattr(auto_ingest, "check_eligibility", _blocked_eligibility)

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:123",
            title="Test Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    assert result.status == "waiting_user"
    assert result.decision_id is not None

    with session_factory() as session:
        decision = session.get(AgentDecisionRequest, result.decision_id)
        assert decision is not None
        # decision 必须挂在已有 run 上, 不应新建 run
        assert decision.run_id == existing_run_id
        # 且不应该再额外创建其他 run
        all_runs = session.scalars(
            select(AgentRun).where(AgentRun.task_id == task_id)
        ).all()
        assert len(all_runs) == 1
        assert all_runs[0].id == existing_run_id
        # 复用已有 active run 时, run 状态必须被切到 waiting_user
        assert all_runs[0].status == "waiting_user", (
            f"existing run.status={all_runs[0].status}, 应已切到 waiting_user"
        )
        assert all_runs[0].current_step == "manual_selection_blocked", (
            f"existing run.current_step={all_runs[0].current_step}"
        )

        # task 状态联动
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "waiting_user"
        assert task.current_step == "manual_selection_blocked"


# ── 4b. existing active run transitions to waiting_user on blocked ─────


def test_manual_select_existing_active_run_transitions_to_waiting_user(
    tmp_path: Path, monkeypatch, stub_dependencies
) -> None:
    """预先存在 status="active" 的 run, 触发 blocked 路径后, 该 run 状态
    必须从 active 切到 waiting_user, current_step 从 agent_start
    切到 manual_selection_blocked。"""
    from media_pilot.repository.repositories import AgentRunCreate, AgentRunRepository
    from media_pilot.services import auto_ingest
    from media_pilot.services.auto_ingest import EligibilityResult
    from media_pilot.services.manual_selection import submit_manual_selection

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_path = config.downloads_dir / "source.mkv"
    source_path.write_bytes(b"any content")
    task_id = _make_task(session_factory, source_path=str(source_path))

    # 预先创建 active run
    with session_factory() as session:
        run_repo = AgentRunRepository(session)
        existing_run = run_repo.create(AgentRunCreate(
            task_id=task_id,
            current_step="agent_start",
        ))
        existing_run_id = existing_run.id
        session.commit()
    # 确认初始 run 状态
    with session_factory() as session:
        initial = session.get(AgentRun, existing_run_id)
        assert initial.status == "active"
        assert initial.current_step == "agent_start"

    # 触发阻塞路径
    def _blocked_eligibility(*, session, config, task_id):
        return EligibilityResult(
            eligible=False,
            blocking_reasons=["multiple_video_files_not_supported"],
            media_type="movie",
        )

    monkeypatch.setattr(auto_ingest, "check_eligibility", _blocked_eligibility)

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:123",
            title="Test Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    assert result.status == "waiting_user"
    assert result.decision_id is not None

    # 关键断言: 同一个 run 的 status 从 active → waiting_user,
    # current_step 从 agent_start → manual_selection_blocked
    with session_factory() as session:
        run = session.get(AgentRun, existing_run_id)
        assert run is not None
        assert run.status == "waiting_user", (
            f"run.status={run.status}, expected waiting_user"
        )
        assert run.current_step == "manual_selection_blocked", (
            f"run.current_step={run.current_step}"
        )

        # AgentStatusSummary 也应反映该状态
        from media_pilot.api.task_mapper import _build_agent_status_index

        summary_index = _build_agent_status_index(session, [task_id])
        summary = summary_index[task_id]
        assert summary.run_status == "waiting_user", (
            f"summary.run_status={summary.run_status}"
        )
        assert summary.pending_decision_count == 1, (
            f"summary.pending_decision_count={summary.pending_decision_count}"
        )


# ── 4c. manual_selection_blocked cancel = 确定性取消, 不调 LLM ─────


def test_manual_selection_blocked_cancel_deterministic_no_llm(
    tmp_path: Path, monkeypatch, stub_dependencies
) -> None:
    """manual_selection_blocked + cancel 必须走确定性 handler:
    - 任务和 run 都进入 failed, failure_reason 写明用户取消。
    - 不会调用 continue_agent_run, 不发起 LLM 请求。
    - 返回 AgentRunResult.status == "manual_selection_cancelled"。
    """
    from media_pilot.services import auto_ingest
    from media_pilot.services.auto_ingest import EligibilityResult
    from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
    from media_pilot.services.manual_selection import (
        MANUAL_SELECTION_CANCEL_FAILURE_REASON,
        submit_manual_selection,
    )

    # Stub continue_agent_run 以便断言它没被调用
    from media_pilot.agent import runner as runner_mod

    llm_call_count = {"n": 0}

    def _fake_continue_agent_run(**kwargs):
        llm_call_count["n"] += 1
        from media_pilot.agent.runner import AgentRunResult
        return AgentRunResult(
            run_id=kwargs.get("run_id", ""),
            status="completed",
            message_count=1,
            tool_call_count=0,
        )

    monkeypatch.setattr(runner_mod, "continue_agent_run", _fake_continue_agent_run)

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_path = config.downloads_dir / "source.mkv"
    source_path.write_bytes(b"any content")
    task_id = _make_task(session_factory, source_path=str(source_path))

    def _blocked_eligibility(*, session, config, task_id):
        return EligibilityResult(
            eligible=False,
            blocking_reasons=["multiple_video_files_not_supported"],
            media_type="movie",
        )

    monkeypatch.setattr(auto_ingest, "check_eligibility", _blocked_eligibility)

    # 创建 pending decision (run 切到 waiting_user)
    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:123",
            title="Test Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    assert result.status == "waiting_user"
    assert result.decision_id is not None
    decision_id = result.decision_id

    # 走 cancel → 确定性取消
    with session_factory() as session:
        run_result = reply_to_decision(
            session=session,
            config=config,
            reply=ReplyInput(decision_id=decision_id, option_id="cancel"),
        )
        session.commit()

    # 关键: 返回结果与 continue_agent_run 一致 → 不会调 LLM
    assert llm_call_count["n"] == 0, (
        f"cancel 必须确定性, 不应调 continue_agent_run, 但被调用了 {llm_call_count['n']} 次"
    )
    assert run_result.status == "manual_selection_cancelled", (
        f"run_result.status={run_result.status}"
    )

    # DB 终态
    with session_factory() as session:
        decision = session.get(AgentDecisionRequest, decision_id)
        assert decision is not None
        assert decision.status != "pending", "decision 必须已被决定"
        assert decision.decision == {"option_id": "cancel", "type": "option"}

        run = session.get(AgentRun, decision.run_id)
        assert run is not None
        assert run.status == "failed", f"run.status={run.status}"
        assert run.current_step == "agent_failed"
        assert run.error_message == MANUAL_SELECTION_CANCEL_FAILURE_REASON

        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "agent_failed", f"task.status={task.status}"
        assert task.current_step == "agent_failed"
        assert task.failure_reason == MANUAL_SELECTION_CANCEL_FAILURE_REASON


# ── 4d. manual_selection_blocked retry 仍继续普通 Agent 续跑 ─────


def test_manual_selection_blocked_retry_continues_normal_agent_path(
    tmp_path: Path, monkeypatch, stub_dependencies
) -> None:
    """manual_selection_blocked + retry 必须走普通 Agent 续跑:
    - 不会被 cancel handler 截断, 落到 continue_agent_run。
    - 任务和 run 切到 active / agent_running, 不进 agent_failed。
    """
    from media_pilot.services import auto_ingest
    from media_pilot.services.auto_ingest import EligibilityResult
    from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
    from media_pilot.services.manual_selection import submit_manual_selection

    from media_pilot.agent import runner as runner_mod
    from media_pilot.agent.runner import AgentRunResult

    continue_called = {"n": 0, "run_id": ""}

    def _fake_continue_agent_run(**kwargs):
        continue_called["n"] += 1
        continue_called["run_id"] = kwargs.get("run_id", "")
        # 模拟 continue 成功, 把 task/run 切到 active/agent_running
        from media_pilot.repository.repositories import (
            AgentRunRepository,
            IngestTaskRepository,
        )

        session = kwargs["session"]
        run_repo = AgentRunRepository(session)
        run = run_repo.get(kwargs.get("run_id", ""))
        if run is not None:
            run_repo.update_status(
                run, status="active", current_step="user_replied",
            )
        task_repo = IngestTaskRepository(session)
        task = task_repo.get(kwargs.get("task_id", "")) or run.task_id if run else None
        return AgentRunResult(
            run_id=continue_called["run_id"],
            status="agent_completed",
            message_count=1,
            tool_call_count=0,
        )

    monkeypatch.setattr(runner_mod, "continue_agent_run", _fake_continue_agent_run)

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_path = config.downloads_dir / "source.mkv"
    source_path.write_bytes(b"any content")
    task_id = _make_task(session_factory, source_path=str(source_path))

    def _blocked_eligibility(*, session, config, task_id):
        return EligibilityResult(
            eligible=False,
            blocking_reasons=["multiple_video_files_not_supported"],
            media_type="movie",
        )

    monkeypatch.setattr(auto_ingest, "check_eligibility", _blocked_eligibility)

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:123",
            title="Test Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    decision_id = result.decision_id

    with session_factory() as session:
        run_result = reply_to_decision(
            session=session,
            config=config,
            reply=ReplyInput(decision_id=decision_id, option_id="retry"),
        )
        session.commit()

    # retry 必须走到 continue_agent_run, 不会被 cancel handler 截断
    assert continue_called["n"] == 1, (
        f"retry 必须调用 continue_agent_run 1 次, 实际 {continue_called['n']}"
    )
    assert continue_called["run_id"], "continue_agent_run 必须拿到 run_id"
    assert run_result.status == "agent_completed", (
        f"run_result.status={run_result.status}"
    )

    # DB 终态: run 是 active (continue_agent_run 内部维护), task 不应是 agent_failed
    with session_factory() as session:
        decision = session.get(AgentDecisionRequest, decision_id)
        assert decision is not None
        assert decision.decision == {"option_id": "retry", "type": "option"}

        run = session.get(AgentRun, decision.run_id)
        assert run is not None
        assert run.status != "failed", (
            f"retry 路径下 run.status={run.status}, 不应为 failed"
        )

        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status != "agent_failed", (
            f"retry 路径下 task.status={task.status}, 不应为 agent_failed"
        )


# ── 4. early-return path on candidate persist failure ──────────────────


def test_manual_select_returns_saved_when_candidate_persist_fails(
    tmp_path: Path, monkeypatch, stub_dependencies
) -> None:
    """persist_metadata_selection 失败时只返回 saved, 不创建 decision / run。"""
    from media_pilot.services import auto_ingest
    from media_pilot.services.manual_selection import submit_manual_selection

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_path = config.downloads_dir / "source.mkv"
    source_path.write_bytes(b"any content")
    task_id = _make_task(session_factory, source_path=str(source_path))

    def _fail_persist(*, session, task_id, **kwargs):
        from media_pilot.services.auto_ingest import (
            PersistSelectionResult as Result,
        )
        return Result(status="failure", summary="cannot persist", candidate_id=None)

    monkeypatch.setattr(auto_ingest, "persist_metadata_selection", _fail_persist)

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:123",
            title="Test Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    assert result.status == "saved"
    assert result.decision_id is None
    assert "cannot persist" in result.summary

    # 不应创建 run / decision
    with session_factory() as session:
        runs = session.scalars(
            select(AgentRun).where(AgentRun.task_id == task_id)
        ).all()
        assert runs == []


def test_manual_select_rejects_agent_running_task(
    tmp_path: Path, stub_dependencies
) -> None:
    """agent_running 状态下后端也必须拒绝人工改元数据。"""
    from media_pilot.services.manual_selection import submit_manual_selection

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_path = config.downloads_dir / "source.mkv"
    source_path.write_bytes(b"any content")
    task_id = _make_task(
        session_factory, source_path=str(source_path), status="agent_running",
    )

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:123",
            title="Test Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    assert result.status == "rejected"
    assert "Agent" in result.summary

    with session_factory() as session:
        candidates = session.scalars(
            select(MediaCandidate).where(MediaCandidate.task_id == task_id)
        ).all()
        assert candidates == []


def test_manual_select_supersedes_pending_decisions_before_publishing(
    tmp_path: Path, monkeypatch, stub_dependencies
) -> None:
    """人工改元数据会废弃旧 pending 决策，避免 stale 卡片继续可回复。"""
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
        AgentRunCreate,
        AgentRunRepository,
    )
    from media_pilot.services import manual_selection as ms
    from media_pilot.services.manual_selection import submit_manual_selection

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_path = config.downloads_dir / "source.mkv"
    source_path.write_bytes(b"any content")
    task_id = _make_task(
        session_factory, source_path=str(source_path), status="waiting_user",
    )

    with session_factory() as session:
        run = AgentRunRepository(session).create(AgentRunCreate(
            task_id=task_id, current_step="select_metadata_candidate",
        ))
        decision = AgentDecisionRequestRepository(session).create(
            AgentDecisionRequestCreate(
                run_id=run.id,
                task_id=task_id,
                decision_type="select_metadata_candidate",
                question="old",
                options=[{"id": "old", "label": "Old"}],
            )
        )
        decision_id = decision.id
        session.commit()

    def _fake_publish(session, config, task_id):
        task = session.get(IngestTask, task_id)
        assert task is not None
        task.status = "library_import_complete"
        task.current_step = "library_import_complete"
        session.flush()
        return ms._PublishOutcome(kind="published")

    monkeypatch.setattr(ms, "_quick_publish", _fake_publish)

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:123",
            title="Correct Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    assert result.status == "published", result.summary

    with session_factory() as session:
        decision = session.get(AgentDecisionRequest, decision_id)
        assert decision is not None
        assert decision.status == "superseded"
        assert decision.decision == {
            "type": "system",
            "reason": "manual_metadata_selection_override",
        }
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "library_import_complete"
        assert task.current_step == "library_import_complete"
        assert task.title == "Test Movie"
        assert task.year == 2026
        assert task.media_type == "movie"
        run = session.scalars(select(AgentRun).where(AgentRun.task_id == task_id)).one()
        assert run.status == "completed"
        assert run.current_step == "manual_metadata_published"
        assert run.error_message is None


def test_manual_select_completed_task_revokes_before_republish(
    tmp_path: Path, monkeypatch, stub_dependencies
) -> None:
    """已入库任务人工改元数据时，必须先撤销旧发布再重新发布。"""
    from media_pilot.orchestration.revoke_publish import RevokePublishResult
    from media_pilot.services import manual_selection as ms
    from media_pilot.services.manual_selection import submit_manual_selection

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_path = config.downloads_dir / "source.mkv"
    source_path.write_bytes(b"any content")
    task_id = _make_task(
        session_factory,
        source_path=str(source_path),
        status="library_import_complete",
    )
    with session_factory() as session:
        from media_pilot.repository.models import MediaSourceSelection, WriteResult

        session.add(MediaSourceSelection(
            task_id=task_id,
            input_path=str(source_path),
            selected_path=str(source_path),
            payload={},
        ))
        session.add(WriteResult(
            task_id=task_id,
            status="succeeded",
            payload={"target_dir": str(config.movies_dir / "Old Movie (2025)")},
        ))
        session.commit()

    calls: list[str] = []

    def _fake_revoke(session, *, task_id, skip_post_revoke_decision=False, existing_run_id=None):
        calls.append(f"revoke:{skip_post_revoke_decision}")
        task = session.get(IngestTask, task_id)
        assert task is not None
        task.status = "processing"
        task.current_step = "post_revoke_reingest"
        session.flush()
        return RevokePublishResult(
            status="completed",
            outcome="ok",
            decision_id=None,
        )

    def _fake_publish(session, config, task_id):
        calls.append("publish")
        return ms._PublishOutcome(kind="published")

    monkeypatch.setattr(
        "media_pilot.orchestration.revoke_publish.execute_revoke_publish",
        _fake_revoke,
    )
    monkeypatch.setattr(ms, "_quick_publish", _fake_publish)

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:456",
            title="Correct Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    assert result.status == "published", result.summary
    assert calls == ["revoke:True", "publish"]


def test_manual_select_completed_task_with_missing_source_republishes_from_library_output(
    tmp_path: Path, monkeypatch, stub_dependencies,
) -> None:
    """已发布任务源文件被清理后, 人工改元数据应复用已发布主视频而不是删除任务。"""
    from media_pilot.repository.models import (
        FileAsset,
        MediaSourceSelection,
        WritePlan,
        WriteResult,
    )
    from media_pilot.services import auto_ingest
    from media_pilot.services.manual_selection import submit_manual_selection

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    missing_source = config.downloads_dir / "already-trashed.mkv"
    old_publish_dir = config.movies_dir / "Wrong Movie (2024)"
    old_publish_dir.mkdir(parents=True)
    old_video = old_publish_dir / "Wrong Movie (2024).mkv"
    old_video.write_bytes(b"published movie")

    task_id = _make_task(
        session_factory,
        source_path=str(missing_source),
        status="library_import_complete",
    )

    with session_factory() as session:
        session.add(MediaSourceSelection(
            task_id=task_id,
            input_path=str(missing_source),
            selected_path=str(missing_source),
            confidence=1.0,
            reason="source_was_cleaned",
            payload={"selection_source": "original_source"},
        ))
        session.add(WritePlan(
            task_id=task_id,
            target_dir=str(old_publish_dir),
            target_file=str(old_video),
            nfo_path=str(old_publish_dir / "Wrong Movie (2024).nfo"),
            payload={},
        ))
        session.add(WriteResult(
            task_id=task_id,
            status="succeeded",
            payload={
                "target_dir": str(old_publish_dir),
                "target_file": str(old_video),
            },
        ))
        session.add(FileAsset(
            task_id=task_id,
            role="library_video",
            path=str(old_video),
            size_bytes=old_video.stat().st_size,
        ))
        session.commit()

    from dataclasses import dataclass

    @dataclass
    class _OkEligibility:
        eligible: bool = True
        media_type: str = "movie"
        candidate_count: int = 1
        confidence_threshold: float = 0.8
        margin: float = 0.0
        blocking_reasons: list[str] = None
        warnings: list[str] = None

        def __post_init__(self) -> None:
            if self.blocking_reasons is None:
                self.blocking_reasons = []
            if self.warnings is None:
                self.warnings = []

    monkeypatch.setattr(
        auto_ingest,
        "check_eligibility",
        lambda *, session, config, task_id: _OkEligibility(),
    )
    monkeypatch.setattr(
        "media_pilot.orchestration.jellyfin_movie_writer._download_image",
        lambda client, url, path: b"poster",
    )

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:456",
            title="Correct Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    assert result.status == "published", result.summary
    assert not old_publish_dir.exists()

    new_publish_dir = config.movies_dir / "Test Movie (2026)"
    assert new_publish_dir.exists()
    assert (new_publish_dir / "Test Movie (2026).mkv").read_bytes() == b"published movie"
    assert not (
        config.movies_dir / ".media-pilot-staging" / task_id / "republish-source"
    ).exists()

    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "library_import_complete"
        assert task.current_step == "library_import_complete"
        assert task.title == "Test Movie"
        assert task.year == 2026

        plans = session.scalars(
            select(WritePlan).where(WritePlan.task_id == task_id)
        ).all()
        assert len(plans) == 1
        assert plans[0].target_dir.endswith("Test Movie (2026)")

        assets = session.scalars(
            select(FileAsset).where(FileAsset.task_id == task_id)
        ).all()
        assert any(asset.role == "library_video" for asset in assets)


def test_manual_select_completed_bdmv_task_revokes_without_deleting_task(
    tmp_path: Path, monkeypatch, stub_dependencies,
) -> None:
    """已发布 BDMV 电影手动改元数据时, 应保留任务并复用原 BDMV 源重发。"""
    from media_pilot.repository.models import MediaSourceSelection, WritePlan, WriteResult
    from media_pilot.services import auto_ingest
    from media_pilot.services.manual_selection import submit_manual_selection

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_dir = config.downloads_dir / "Old BDMV"
    (source_dir / "BDMV").mkdir(parents=True)
    (source_dir / "BDMV" / "index.bdmv").write_text("index")
    (source_dir / "BDMV" / "STREAM").mkdir()
    (source_dir / "BDMV" / "STREAM" / "00001.m2ts").write_bytes(b"movie")

    old_publish_dir = config.movies_dir / "Wrong Movie (2024)"
    (old_publish_dir / "BDMV").mkdir(parents=True)
    (old_publish_dir / "BDMV" / "index.bdmv").write_text("old")

    task_id = _make_task(
        session_factory,
        source_path=str(source_dir),
        status="library_import_complete",
    )
    with session_factory() as session:
        session.add(MediaSourceSelection(
            task_id=task_id,
            input_path=str(source_dir),
            selected_path=None,
            confidence=1.0,
            reason="auto_bdmv_movie_dir",
            payload={"source_kind": "bdmv", "bdmv_dir": str(source_dir / "BDMV")},
        ))
        session.add(WritePlan(
            task_id=task_id,
            target_dir=str(old_publish_dir),
            target_file=str(old_publish_dir / "BDMV" / "index.bdmv"),
            nfo_path=str(old_publish_dir / "BDMV" / "index.nfo"),
            payload={"source_kind": "bdmv"},
        ))
        session.add(WriteResult(
            task_id=task_id,
            status="succeeded",
            payload={"target_dir": str(old_publish_dir), "source_kind": "bdmv"},
        ))
        session.commit()

    _stub_ok_eligibility_and_images(monkeypatch, auto_ingest)

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:456",
            title="Correct Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    assert result.status == "published", result.summary
    assert not old_publish_dir.exists()
    assert (config.movies_dir / "Test Movie (2026)" / "BDMV" / "index.bdmv").exists()

    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "library_import_complete"
        assert task.source_path == str(source_dir)


def test_manual_select_completed_bdmv_task_with_missing_source_republishes_from_library_output(
    tmp_path: Path, monkeypatch, stub_dependencies,
) -> None:
    """原 BDMV 源已清理时, 手动改元数据应从已发布 BDMV 临时复制后重发。"""
    from media_pilot.repository.models import (
        FileAsset,
        MediaSourceSelection,
        WritePlan,
        WriteResult,
    )
    from media_pilot.services import auto_ingest
    from media_pilot.services.manual_selection import submit_manual_selection

    config = _make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    missing_source = config.downloads_dir / "Missing BDMV"
    old_publish_dir = config.movies_dir / "Wrong Movie (2024)"
    (old_publish_dir / "BDMV").mkdir(parents=True)
    (old_publish_dir / "BDMV" / "index.bdmv").write_text("old")
    (old_publish_dir / "BDMV" / "STREAM").mkdir()
    (old_publish_dir / "BDMV" / "STREAM" / "00001.m2ts").write_bytes(b"movie")
    (old_publish_dir / "CERTIFICATE").mkdir()
    (old_publish_dir / "CERTIFICATE" / "id.bdmv").write_text("cert")

    task_id = _make_task(
        session_factory,
        source_path=str(missing_source),
        status="library_import_complete",
    )
    with session_factory() as session:
        session.add(MediaSourceSelection(
            task_id=task_id,
            input_path=str(missing_source),
            selected_path=None,
            confidence=1.0,
            reason="source_was_cleaned",
            payload={"source_kind": "bdmv", "bdmv_dir": str(missing_source / "BDMV")},
        ))
        session.add(WritePlan(
            task_id=task_id,
            target_dir=str(old_publish_dir),
            target_file=str(old_publish_dir / "BDMV" / "index.bdmv"),
            nfo_path=str(old_publish_dir / "BDMV" / "index.nfo"),
            payload={"source_kind": "bdmv"},
        ))
        session.add(WriteResult(
            task_id=task_id,
            status="succeeded",
            payload={"target_dir": str(old_publish_dir), "source_kind": "bdmv"},
        ))
        session.add(FileAsset(
            task_id=task_id,
            role="library_bdmv",
            path=str(old_publish_dir / "BDMV"),
            size_bytes=None,
        ))
        session.commit()

    _stub_ok_eligibility_and_images(monkeypatch, auto_ingest)

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:456",
            title="Correct Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    assert result.status == "published", result.summary
    assert not old_publish_dir.exists()
    assert (config.movies_dir / "Test Movie (2026)" / "BDMV" / "index.bdmv").exists()
    assert (config.movies_dir / "Test Movie (2026)" / "CERTIFICATE" / "id.bdmv").exists()
    assert not (
        config.movies_dir / ".media-pilot-staging" / task_id / "republish-source"
    ).exists()

    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "library_import_complete"
        assert task.source_path.endswith("/republish-source")


def test_manual_select_publish_success_applies_trash_cleanup_policy(
    tmp_path: Path, monkeypatch, stub_dependencies
) -> None:
    """手动选择确定性发布成功后，也必须执行 source_cleanup_policy=trash。"""
    from media_pilot.repository.models import OperationRecord, WriteResult
    from media_pilot.services import manual_selection as ms
    from media_pilot.services.app_settings import AppSettings, AppSettingsService
    from media_pilot.services.manual_selection import submit_manual_selection

    from dataclasses import replace

    config = replace(_make_config(tmp_path), trash_dir=tmp_path / "trash")
    config.trash_dir.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)
    AppSettingsService(session_factory).save(AppSettings(source_cleanup_policy="trash"))

    source_path = config.downloads_dir / "source.mkv"
    source_path.write_bytes(b"any content")
    task_id = _make_task(
        session_factory,
        source_path=str(source_path),
        status="waiting_user",
    )

    def _fake_publish(session, config, task_id):
        task = session.get(IngestTask, task_id)
        assert task is not None
        task.status = "library_import_complete"
        task.current_step = "library_import_complete"
        session.add(WriteResult(
            task_id=task_id,
            status="succeeded",
            payload={"target_file": "dummy"},
        ))
        session.flush()
        return ms._PublishOutcome(kind="published")

    monkeypatch.setattr(ms, "_quick_publish", _fake_publish)

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:456",
            title="Correct Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    assert result.status == "published"
    assert not source_path.exists()
    trashed = config.trash_dir / "source.mkv"
    assert trashed.exists()

    with session_factory() as session:
        ops = list(session.scalars(
            select(OperationRecord).where(OperationRecord.task_id == task_id)
        ))
        assert any(op.operation_type == "source_input_trashed" for op in ops)
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "library_import_complete"
