"""``recover_stuck_agent_run`` 服务函数测试.

锁定: 卡住 Agent 恢复 = 任务处于 ``agent_running`` + 存在 active AgentRun
+ 无 pending AgentDecisionRequest 时, 用户显式将旧 active run 标 failed,
启动新的 ack-only auto_ingest run, 返回新 run_id 和 ``status="active"``.

红色: 当前 ``media_pilot.services`` 没有 ``recover_stuck_agent_run`` 函数,
这些测试必失败.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


# ── helpers ─────────────────────────────────────────────────────────


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


def _make_session_factory(tmp_path: Path):
    from media_pilot.repository.database import create_session_factory, initialize_database
    config = _make_config(tmp_path)
    initialize_database(config)
    return create_session_factory(config)


def _make_task(session, *, source_path: str = "/tmp/source.mkv", status: str = "agent_running"):
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
    task = IngestTaskRepository(session).create(IngestTaskCreate(
        source_path=source_path,
        status=status,
        media_type="movie",
    ))
    session.commit()
    return task


def _make_active_run(session, *, task_id: str, status: str = "active", current_step: str = "llm_streaming"):
    from media_pilot.repository.repositories import AgentRunCreate, AgentRunRepository
    run = AgentRunRepository(session).create(AgentRunCreate(
        task_id=task_id, current_step=current_step,
    ))
    AgentRunRepository(session).update_status(run, status=status, current_step=current_step)
    session.commit()
    return run


def _make_pending_decision(session, *, run_id: str, task_id: str, decision_type: str = "select_metadata_candidate"):
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
    )
    dr = AgentDecisionRequestRepository(session).create(AgentDecisionRequestCreate(
        run_id=run_id, task_id=task_id, decision_type=decision_type,
        question="请选择候选", free_text_allowed=False, options=[],
    ))
    session.commit()
    return dr


# ── tests ───────────────────────────────────────────────────────────


class TestRecoverStuckAgentRun:
    """卡住 Agent 恢复契约."""

    def test_happy_path_marks_old_run_failed_and_creates_new_active_run(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """agent_running + active run + 无 pending decision → 成功恢复."""
        from media_pilot.services.recover_stuck_agent_run import (
            recover_stuck_agent_run,
        )
        from media_pilot.repository.repositories import (
            AgentRunCreate, AgentRunRepository, AgentMessageCreate,
            AgentMessageRepository, IngestTaskRepository,
        )
        from media_pilot.agent import runner as runner_module

        # stub _create_run_in_session: 在 caller session 内创建新 run 行
        # (与生产实现语义一致: 写 run + 写 message + 不 commit). 关键
        # contract 是: 必须把 run 行加入 caller session 的 pending
        # transaction, 由 service 函数 commit 统一落库.
        def _fake_create_run_in_session(*, session, task_id, initial_message=None):
            run = AgentRunRepository(session).create(AgentRunCreate(
                task_id=task_id, current_step="agent_start",
            ))
            AgentMessageRepository(session).create(AgentMessageCreate(
                run_id=run.id, role="user", content=initial_message or "",
            ))
            return run.id

        # stub _start_background_loop: 不真启动后台线程. 返回 None
        # (生产返回 Thread 对象, 但 service 不消费返回值).
        def _fake_start_background_loop(**kwargs):
            return None

        monkeypatch.setattr(
            runner_module, "_create_run_in_session", _fake_create_run_in_session,
        )
        monkeypatch.setattr(
            runner_module, "_start_background_loop", _fake_start_background_loop,
        )

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, source_path="/tmp/movie.mkv")
            old_run = _make_active_run(session, task_id=task.id)

        config = _make_config(tmp_path)
        result = recover_stuck_agent_run(
            session_factory=sf, config=config, task_id=task.id,
        )

        assert result["status"] == "active"
        assert result["run_id"] != old_run.id, (
            f"必须返回新 run_id, 不应复用旧 active run {old_run.id}"
        )

        # 旧 run 标 failed + current_step=stuck_recovered
        with sf() as session:
            old = AgentRunRepository(session).get(old_run.id)
            assert old is not None
            assert old.status == "failed", (
                f"旧 active run 必须标 failed, got status={old.status}"
            )
            assert old.current_step == "stuck_recovered", (
                f"旧 run current_step 必须标识 stuck_recovered, got {old.current_step}"
            )
            assert old.error_message, (
                "旧 run 必须有 error_message 标识为人工恢复"
            )

            # 任务保持 agent_running
            task_row = IngestTaskRepository(session).get(task.id)
            assert task_row is not None
            assert task_row.status == "agent_running"

    def test_pending_decision_blocks_recovery(self, tmp_path: Path) -> None:
        """有 pending decision → 抛 ValueError 409, 不得创建新 run, 不得动旧 pending."""
        from media_pilot.services.recover_stuck_agent_run import (
            recover_stuck_agent_run,
        )
        from media_pilot.repository.repositories import (
            AgentDecisionRequestRepository, AgentRunRepository,
        )

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, source_path="/tmp/movie.mkv")
            old_run = _make_active_run(session, task_id=task.id)
            pending = _make_pending_decision(session, run_id=old_run.id, task_id=task.id)

        config = _make_config(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            recover_stuck_agent_run(
                session_factory=sf, config=config, task_id=task.id,
            )
        err = exc_info.value
        # ValueError 用 dict 表示 status_code + detail
        assert isinstance(err.args[0], dict), (
            f"recover_stuck 必须抛 ValueError + dict(status_code, detail), got: {err!r}"
        )
        assert err.args[0]["status_code"] == 409
        assert "pending" in err.args[0]["detail"].lower() or "决策" in err.args[0]["detail"]

        # 旧 run 保持 active, pending 保持 pending
        with sf() as session:
            old = AgentRunRepository(session).get(old_run.id)
            assert old.status == "active"
            p = AgentDecisionRequestRepository(session).get(pending.id)
            assert p.status == "pending"

    def test_waiting_user_run_blocks_recovery(self, tmp_path: Path) -> None:
        """active run 实际 status='waiting_user' → 409 拒绝, 提示先处理决策."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            _make_active_run(session, task_id=task.id, status="waiting_user")

        from media_pilot.services.recover_stuck_agent_run import (
            recover_stuck_agent_run,
        )
        config = _make_config(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            recover_stuck_agent_run(
                session_factory=sf, config=config, task_id=task.id,
            )
        err = exc_info.value
        assert err.args[0]["status_code"] == 409

    def test_no_active_run_blocks_recovery(self, tmp_path: Path) -> None:
        """任务没有 active / waiting run → 409 拒绝."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            # 创建一个 completed run (不是 active)
            _make_active_run(session, task_id=task.id, status="completed")

        from media_pilot.services.recover_stuck_agent_run import (
            recover_stuck_agent_run,
        )
        config = _make_config(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            recover_stuck_agent_run(
                session_factory=sf, config=config, task_id=task.id,
            )
        assert exc_info.value.args[0]["status_code"] == 409

    def test_non_agent_running_status_blocks_recovery(self, tmp_path: Path) -> None:
        """任务处于 agent_failed / library_import_complete / deleted / completed → 拒绝."""
        from media_pilot.services.recover_stuck_agent_run import (
            recover_stuck_agent_run,
        )
        for bad_status in ("agent_failed", "library_import_complete", "deleted", "completed"):
            sf = _make_session_factory(tmp_path)
            with sf() as session:
                task = _make_task(
                    session, source_path=f"/tmp/{bad_status}.mkv", status=bad_status,
                )
            config = _make_config(tmp_path)
            with pytest.raises(ValueError) as exc_info:
                recover_stuck_agent_run(
                    session_factory=sf, config=config, task_id=task.id,
                )
            err = exc_info.value
            assert err.args[0]["status_code"] == 409, (
                f"task.status={bad_status} 应拒绝 (409), got: {err!r}"
            )

    def test_task_not_found_raises_404(self, tmp_path: Path) -> None:
        """任务不存在 → 抛 404."""
        from media_pilot.services.recover_stuck_agent_run import (
            recover_stuck_agent_run,
        )
        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            recover_stuck_agent_run(
                session_factory=sf, config=config, task_id="nonexistent-id",
            )
        assert exc_info.value.args[0]["status_code"] == 404

    def test_old_run_stays_active_when_safe_commit_raises_db_locked(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """safe_commit 抛 OperationalError → 409 db_locked, 旧 run 保持 active.

        单事务原子性: 旧 run 标 failed + 新 run 创建 + task agent_running
        都在同一事务. safe_commit 失败 → 整个事务回滚, 旧 run 不会被标
        failed, 也不会有"半恢复"残留. 这是修复 d5f3d96 半恢复风险的核心
        契约.
        """
        from sqlalchemy.exc import OperationalError
        from media_pilot.orchestration import db_retry

        def _raise_lock(session):
            raise OperationalError("simulated commit lock", None, None)

        monkeypatch.setattr(db_retry, "safe_commit", _raise_lock)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, source_path="/tmp/movie.mkv")
            old_run = _make_active_run(session, task_id=task.id)

        from media_pilot.services.recover_stuck_agent_run import (
            recover_stuck_agent_run,
        )
        from media_pilot.repository.repositories import AgentRunRepository

        config = _make_config(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            recover_stuck_agent_run(
                session_factory=sf, config=config, task_id=task.id,
            )
        err = exc_info.value
        assert err.args[0]["status_code"] == 409
        assert "db_locked" in err.args[0]["detail"]

        # 关键: rollback 之后, 旧 run 必须保持 active, 没有任何半成品状态.
        with sf() as session:
            old = AgentRunRepository(session).get(old_run.id)
            assert old is not None
            assert old.status == "active", (
                f"rollback 必须撤销旧 run 标 failed, got status={old.status}"
            )
            assert old.error_message is None, (
                f"rollback 必须撤销旧 run error_message, got {old.error_message!r}"
            )
            assert old.current_step == "llm_streaming", (
                f"rollback 必须撤销旧 run current_step, got {old.current_step!r}"
            )

            # 整个 task 只能有原来那一个 run, 没有创建出新 run
            runs = AgentRunRepository(session).list_by_task(task.id)
            assert len(runs) == 1, (
                f"rollback 后必须没有新 run, got {len(runs)} runs"
            )

    def test_old_run_stays_active_when_new_run_sync_raises_value_error(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """新 run 同步阶段抛 ValueError → 409 结构化错误, 旧 run 保持 active.

        ValueError 来源: 极端 race (e.g. _create_run_in_session 内部发现
        task 突然不存在, 或 active run 突现). 服务必须捕获并转译为 409
        + detail, 不能让 v1.py 兜底成 500.
        """
        from media_pilot.agent import runner as runner_module

        def _raise_value(*args, **kwargs):
            raise ValueError("simulated new run race: task vanished")

        monkeypatch.setattr(
            runner_module, "_create_run_in_session", _raise_value,
        )

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, source_path="/tmp/movie.mkv")
            old_run = _make_active_run(session, task_id=task.id)

        from media_pilot.services.recover_stuck_agent_run import (
            recover_stuck_agent_run,
        )
        from media_pilot.repository.repositories import AgentRunRepository

        config = _make_config(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            recover_stuck_agent_run(
                session_factory=sf, config=config, task_id=task.id,
            )
        err = exc_info.value
        # 必须是结构化 409, 不能让 v1.py 兜底 500
        assert isinstance(err.args[0], dict), (
            f"必须抛结构化 ValueError, got: {err!r}"
        )
        assert err.args[0]["status_code"] == 409, (
            f"新 run 失败必须 409 (不是 500), got: {err!r}"
        )
        assert "vanished" in err.args[0]["detail"] or "race" in err.args[0]["detail"].lower()

        # 旧 run 保持 active (rollback 起效)
        with sf() as session:
            old = AgentRunRepository(session).get(old_run.id)
            assert old.status == "active", (
                f"旧 run 必须保持 active, got {old.status}"
            )
            assert old.error_message is None

            # 不会有新 run
            runs = AgentRunRepository(session).list_by_task(task.id)
            assert len(runs) == 1

    def test_recovery_does_not_start_background_thread_on_failure(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """safe_commit 失败时, _start_background_loop 必须未被调用.

        单事务语义的延伸: 事务没成功 commit, 就不应该启动后台线程. 避免
        "新 run 在 DB 里但没有线程跑" 的孤儿状态.
        """
        from sqlalchemy.exc import OperationalError
        from media_pilot.orchestration import db_retry
        from media_pilot.agent import runner as runner_module

        def _raise_lock(session):
            raise OperationalError("simulated lock", None, None)
        monkeypatch.setattr(db_retry, "safe_commit", _raise_lock)

        def _explode_background(*args, **kwargs):
            raise AssertionError(
                "_start_background_loop 在 safe_commit 失败时绝不能被调用"
            )
        monkeypatch.setattr(
            runner_module, "_start_background_loop", _explode_background,
        )

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, source_path="/tmp/movie.mkv")
            _make_active_run(session, task_id=task.id)

        from media_pilot.services.recover_stuck_agent_run import (
            recover_stuck_agent_run,
        )
        config = _make_config(tmp_path)
        with pytest.raises(ValueError):
            recover_stuck_agent_run(
                session_factory=sf, config=config, task_id=task.id,
            )
