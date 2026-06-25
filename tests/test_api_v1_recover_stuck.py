"""``POST /api/v1/tasks/{task_id}/agent-runs/recover-stuck`` API 合同测试.

锁定: 卡住 Agent 恢复 HTTP 端点契约 — 路径、状态码、envelope 形状、
校验链. 与 ``POST /api/v1/tasks/{task_id}/agent-runs`` 普通 retry 端点
严格分离, 共享同一 service 层但不暴露对方的内部逻辑.

红色: 当前 v1.py 没有 ``recover-stuck`` 端点, 这些测试必失败.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from media_pilot.app import create_app
from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.repositories import (
    AgentDecisionRequestCreate,
    AgentDecisionRequestRepository,
    AgentRunCreate,
    AgentRunRepository,
    IngestTaskCreate,
    IngestTaskRepository,
)


# ── helpers ─────────────────────────────────────────────────────────


def _make_config(database_dir: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"),
        shows_dir=Path("/media/library/shows"),
        database_dir=database_dir,
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        tmdb_api_key="test-tmdb-key",
    )


def _make_session_factory(tmp_path: Path):
    config = _make_config(tmp_path)
    initialize_database(config)
    return create_session_factory(config)


def _make_task(session, *, status: str = "agent_running"):
    task = IngestTaskRepository(session).create(IngestTaskCreate(
        source_path="/tmp/source.mkv",
        status=status,
        media_type="movie",
    ))
    session.commit()
    return task


def _make_run(session, *, task_id: str, status: str = "active", current_step: str = "llm_streaming"):
    run = AgentRunRepository(session).create(AgentRunCreate(
        task_id=task_id, current_step=current_step,
    ))
    AgentRunRepository(session).update_status(run, status=status, current_step=current_step)
    session.commit()
    return run


def _make_pending_decision(session, *, run_id: str, task_id: str):
    dr = AgentDecisionRequestRepository(session).create(AgentDecisionRequestCreate(
        run_id=run_id, task_id=task_id, decision_type="select_metadata_candidate",
        question="请选择候选", free_text_allowed=False, options=[],
    ))
    session.commit()
    return dr


def _stub_recover_helpers(monkeypatch):
    """monkeypatch recover_stuck_agent_run 实际依赖的 runner 内部 helper.

    关键: ``recover_stuck_agent_run`` 不再调 ``run_agent_turn_async`` — 它
    拆成两段: ``_create_run_in_session`` (在 caller session 内创建 run 行
    + 落 message) + ``_start_background_loop`` (启动 daemon thread). 单元
    测试不应真启动后台 LLM loop, 所以 stub ``_start_background_loop`` 让它
    啥也不做; ``_create_run_in_session`` 仍用生产实现以保证 service 真的
    写出新 run 行可被断言.
    """
    from media_pilot.agent import runner as runner_module

    def _no_background(**kwargs):
        return None

    monkeypatch.setattr(
        runner_module, "_start_background_loop", _no_background,
    )


# ── tests ───────────────────────────────────────────────────────────


def test_api_v1_recover_stuck_happy_path_returns_new_run_id(
    tmp_path: Path, monkeypatch,
) -> None:
    """agent_running + active run + 无 pending → 200 success + 新 run_id."""
    _stub_recover_helpers(monkeypatch)

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        task = _make_task(session)
        old_run = _make_run(session, task_id=task.id)

    app = create_app()
    app.state.session_factory = sf
    app.state.config = _make_config(tmp_path)
    client = TestClient(app)

    resp = client.post(f"/api/v1/tasks/{task.id}/agent-runs/recover-stuck")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # envelope.success
    assert body["status"] == "success", body
    assert body["data"]["status"] == "active"
    assert body["data"]["run_id"], "必须返回新 run_id"
    assert body["data"]["run_id"] != old_run.id, (
        f"必须创建新 run, 不应复用旧 {old_run.id}"
    )
    # message 是 machine-readable token, 不暴露 run_id
    msg = body["messages"][0]
    assert msg["level"] == "info"
    assert msg["text"] == "active"
    assert "Agent run" not in msg["text"]
    assert not re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        msg["text"], re.IGNORECASE,
    ), f"message 不得暴露 UUID, got: {msg['text']!r}"

    # 旧 run 标 failed + stuck_recovered
    with sf() as session:
        old = AgentRunRepository(session).get(old_run.id)
        assert old is not None
        assert old.status == "failed"
        assert old.current_step == "stuck_recovered"
        assert old.error_message

        # task 仍 agent_running
        task_row = IngestTaskRepository(session).get(task.id)
        assert task_row.status == "agent_running"


def test_api_v1_recover_stuck_pending_decision_returns_409(
    tmp_path: Path, monkeypatch,
) -> None:
    """存在 pending decision → 409, 不创建新 run, 旧 run 保持 active."""
    _stub_recover_helpers(monkeypatch)

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        task = _make_task(session)
        old_run = _make_run(session, task_id=task.id)
        _make_pending_decision(session, run_id=old_run.id, task_id=task.id)

    app = create_app()
    app.state.session_factory = sf
    app.state.config = _make_config(tmp_path)
    client = TestClient(app)

    resp = client.post(f"/api/v1/tasks/{task.id}/agent-runs/recover-stuck")
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["status"] == "error"
    # 错误信息必须提示先处理决策
    detail = body["messages"][0]["text"]
    assert "decision" in detail.lower() or "决策" in detail or "pending" in detail.lower()

    # 旧 run 保持 active, 旧 pending 保持 pending
    with sf() as session:
        old = AgentRunRepository(session).get(old_run.id)
        assert old.status == "active"
        pendings = AgentDecisionRequestRepository(session).list_pending_by_task(task.id)
        assert len(pendings) == 1
        assert pendings[0].status == "pending"


def test_api_v1_recover_stuck_waiting_user_returns_409(
    tmp_path: Path, monkeypatch,
) -> None:
    """active run.status='waiting_user' → 409, 提示先处理决策."""
    _stub_recover_helpers(monkeypatch)

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        task = _make_task(session)
        _make_run(session, task_id=task.id, status="waiting_user")

    app = create_app()
    app.state.session_factory = sf
    app.state.config = _make_config(tmp_path)
    client = TestClient(app)

    resp = client.post(f"/api/v1/tasks/{task.id}/agent-runs/recover-stuck")
    assert resp.status_code == 409, resp.text


def test_api_v1_recover_stuck_no_active_run_returns_409(
    tmp_path: Path, monkeypatch,
) -> None:
    """任务没有 active / waiting run → 409."""
    _stub_recover_helpers(monkeypatch)

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        task = _make_task(session)
        _make_run(session, task_id=task.id, status="completed")

    app = create_app()
    app.state.session_factory = sf
    app.state.config = _make_config(tmp_path)
    client = TestClient(app)

    resp = client.post(f"/api/v1/tasks/{task.id}/agent-runs/recover-stuck")
    assert resp.status_code == 409, resp.text


def test_api_v1_recover_stuck_terminal_status_returns_409(
    tmp_path: Path, monkeypatch,
) -> None:
    """任务处于 deleted / library_import_complete / agent_failed / completed → 409."""
    _stub_recover_helpers(monkeypatch)

    for bad_status in ("deleted", "library_import_complete", "agent_failed", "completed"):
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, status=bad_status)
            _make_run(session, task_id=task.id)

        app = create_app()
        app.state.session_factory = sf
        app.state.config = _make_config(tmp_path)
        client = TestClient(app)

        resp = client.post(f"/api/v1/tasks/{task.id}/agent-runs/recover-stuck")
        assert resp.status_code == 409, (
            f"task.status={bad_status} 应拒绝 (409), got {resp.status_code}: {resp.text}"
        )


def test_api_v1_recover_stuck_task_not_found_returns_404(
    tmp_path: Path, monkeypatch,
) -> None:
    """任务不存在 → 404."""
    _stub_recover_helpers(monkeypatch)

    sf = _make_session_factory(tmp_path)
    app = create_app()
    app.state.session_factory = sf
    app.state.config = _make_config(tmp_path)
    client = TestClient(app)

    resp = client.post("/api/v1/tasks/nonexistent-id/agent-runs/recover-stuck")
    assert resp.status_code == 404, resp.text


def test_api_v1_recover_stuck_db_locked_returns_409_with_retryable(
    tmp_path: Path, monkeypatch,
) -> None:
    """同步阶段数据库锁冲突 → 409 + meta.retryable=True (与项目统一风格)."""
    from sqlalchemy.exc import OperationalError
    from media_pilot.orchestration import db_retry

    _stub_recover_helpers(monkeypatch)

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        task = _make_task(session)
        _make_run(session, task_id=task.id)

    # 让 safe_commit 抛 OperationalError — 模拟锁竞争.
    def _raise_lock(session):
        raise OperationalError("simulated lock", None, None)
    monkeypatch.setattr(db_retry, "safe_commit", _raise_lock)

    app = create_app()
    app.state.session_factory = sf
    app.state.config = _make_config(tmp_path)
    client = TestClient(app)

    resp = client.post(f"/api/v1/tasks/{task.id}/agent-runs/recover-stuck")
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["messages"][0]["code"] == "db_locked"
    assert body["meta"].get("retryable") is True


def test_api_v1_recover_stuck_does_not_collide_with_normal_create_agent_run(
    tmp_path: Path, monkeypatch,
) -> None:
    """恢复端点与普通 createAgentRun 端点路径不同, 不共享同一 route."""
    _stub_recover_helpers(monkeypatch)

    sf = _make_session_factory(tmp_path)
    app = create_app()
    app.state.session_factory = sf
    app.state.config = _make_config(tmp_path)
    client = TestClient(app)

    # 路径必须包含 "recover-stuck", 与 POST /agent-runs 区分
    with sf() as session:
        task = _make_task(session)
        _make_run(session, task_id=task.id)

    resp = client.post(f"/api/v1/tasks/{task.id}/agent-runs/recover-stuck")
    assert resp.status_code in (200, 409), resp.text
    # 同时, 同一个 active run 阻止普通 createAgentRun 走 happy path
    resp_normal = client.post(f"/api/v1/tasks/{task.id}/agent-runs", json={})
    assert resp_normal.status_code == 409, (
        f"普通 createAgentRun 在 active run 时应 409, got {resp_normal.status_code}"
    )


def test_api_v1_recover_stuck_db_locked_does_not_corrupt_old_run(
    tmp_path: Path, monkeypatch,
) -> None:
    """db_locked 路径必须保证单事务回滚: 旧 run 保持 active.

    这是修复 d5f3d96 半恢复风险的核心契约. safe_commit 抛
    OperationalError → 整个事务回滚 → 旧 run 不被标 failed, 没有新 run.
    """
    from sqlalchemy.exc import OperationalError
    from media_pilot.orchestration import db_retry

    _stub_recover_helpers(monkeypatch)

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        task = _make_task(session)
        old_run = _make_run(session, task_id=task.id)

    def _raise_lock(session):
        raise OperationalError("simulated lock", None, None)
    monkeypatch.setattr(db_retry, "safe_commit", _raise_lock)

    app = create_app()
    app.state.session_factory = sf
    app.state.config = _make_config(tmp_path)
    client = TestClient(app)

    resp = client.post(f"/api/v1/tasks/{task.id}/agent-runs/recover-stuck")
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["messages"][0]["code"] == "db_locked"
    assert body["meta"].get("retryable") is True

    # 关键: 旧 run 必须保持 active, 没有任何半成品状态
    with sf() as session:
        old = AgentRunRepository(session).get(old_run.id)
        assert old.status == "active", (
            f"db_locked 后旧 run 必须保持 active, got {old.status}"
        )
        assert old.error_message is None
        assert old.current_step == "llm_streaming"

        # 没有新 run
        all_runs = AgentRunRepository(session).list_by_task(task.id)
        assert len(all_runs) == 1


def test_api_v1_recover_stuck_new_run_value_error_returns_409(
    tmp_path: Path, monkeypatch,
) -> None:
    """新 run 同步阶段 ValueError → 409 结构化 (不是 500).

    修复 d5f3d96 半恢复风险: 之前 ValueError 会冒泡到 v1.py, 落到 except
    分支取不到 status_code, 默认 500. 现在服务层必须捕获并转译.
    """
    from media_pilot.agent import runner as runner_module

    def _raise_value(*args, **kwargs):
        raise ValueError("simulated new run race condition")

    monkeypatch.setattr(
        runner_module, "_create_run_in_session", _raise_value,
    )

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        task = _make_task(session)
        old_run = _make_run(session, task_id=task.id)

    app = create_app()
    app.state.session_factory = sf
    app.state.config = _make_config(tmp_path)
    client = TestClient(app)

    resp = client.post(f"/api/v1/tasks/{task.id}/agent-runs/recover-stuck")
    # 关键: 必须是 409 (不是 500). 这是"不能 500"的硬约束.
    assert resp.status_code == 409, (
        f"ValueError 必须转译 409, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["status"] == "error"

    # 旧 run 保持 active
    with sf() as session:
        old = AgentRunRepository(session).get(old_run.id)
        assert old.status == "active"
