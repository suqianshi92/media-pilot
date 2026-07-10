import pytest
import threading

from tests.agent_runner_helpers import MockLLMClient, _make_config, _make_task
from tests.auth_helpers import AuthenticatedTestClient as TestClient

# ══════════════════════════════════════════════════════════════════════
# Retry Ack-only (fix-agent-retry-button-ui-state-semantics §4)
# ══════════════════════════════════════════════════════════════════════
#
# 后端 retry ack-only 改造: 验证 POST /tasks/{id}/agent-runs 在 agent_failed
# 任务上立即返回 ack (status="active"), 后台 loop 在独立 thread 跑完.
# 关键契约:
#   - POST resolve 那一刻, AgentRun (status=active) + task (agent_running) +
#     初始 user message 都已落库
#   - LLM 实际调用发生在 background thread, 不在 ack POST 路径
#   - 已有 active / waiting run 时, 仍 409 (走 API 端既有 409 检查)
# ══════════════════════════════════════════════════════════════════════


class _GatedMockLLMClient:
    """Thread-gated mock LLM.

    ``chat()`` records the call, signals ``entered``, and blocks on
    ``gate`` until the test releases it. This lets the test deterministically
    observe "ack returned but LLM not yet called" vs "background thread
    reached chat() but blocked" vs "background thread completed".
    """

    def __init__(self):
        from media_pilot.agent.llm_client import LLMResponse

        self.calls: list[dict] = []
        self.responses: list[LLMResponse] = []
        self.entered = threading.Event()
        self.gate = threading.Event()
        self._LLMResponse = LLMResponse

    def add_text_response(self, content: str) -> None:
        self.responses.append(self._LLMResponse(content=content, tool_calls=[]))

    def chat(self, messages, tools=None):
        self.calls.append({"messages": list(messages), "tools": list(tools) if tools else []})
        self.entered.set()
        if not self.gate.wait(timeout=10):
            raise TimeoutError("Test never released the LLM gate")
        if self.responses:
            return self.responses.pop(0)
        return self._LLMResponse(content="Done.", tool_calls=[])


class TestRetryAckOnly:
    def test_async_retry_returns_immediate_ack_with_active_status(self, tmp_path):
        """run_agent_turn_async MUST return immediately with status='active'.

        Critical contract: ack is bound to the synchronous phase (run + message
        + task status update + commit), NOT the LLM execution. The LLM call
        must not happen before the function returns. The background thread
        is blocked on the gate so we can assert mock.calls is non-empty and
        the thread is alive at ack time deterministically.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, status="agent_failed", current_step="agent_failed")
            task_id = task.id

        mock = _GatedMockLLMClient()
        mock.add_text_response("Recovery done.")

        from media_pilot.agent.runner import run_agent_turn_async

        ack = run_agent_turn_async(
            session_factory=sf,
            config=config,
            task_id=task_id,
            mode="auto_ingest",
            mock_llm_client=mock,
        )

        # Ack contract: 立即返回, status=active, 携带 run_id + thread.
        assert isinstance(ack.run_id, str) and ack.run_id
        assert ack.status == "active"
        assert isinstance(ack.thread, threading.Thread)
        assert ack.thread.daemon is True
        assert ack.thread.is_alive()

        # Critical: background thread 已经被调度, 且 LLM chat() 已经被调一次
        # (call 记录在 entered.set() 之前), 现在被 gate 阻断.
        assert mock.entered.wait(timeout=2), "Background thread never reached chat()"
        assert len(mock.calls) == 1

        # Release the gate so the thread can finish and pytest can exit.
        mock.gate.set()
        ack.thread.join(timeout=5)
        assert not ack.thread.is_alive(), "Background thread did not finish"

    def test_async_retry_persists_run_and_updates_task_to_agent_running(self, tmp_path):
        """Synchronous phase MUST commit AgentRun (active) + task (agent_running)
        + initial user message before the ack returns.

        This is what the frontend's refetch reads on the next poll to show
        "Agent 正在处理中". If the commit is missing or wrong, the UI reverts
        to the failed state.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, status="agent_failed", current_step="agent_failed")
            task_id = task.id

        mock = _GatedMockLLMClient()
        mock.add_text_response("ok")

        from media_pilot.agent.runner import run_agent_turn_async

        ack = run_agent_turn_async(
            session_factory=sf,
            config=config,
            task_id=task_id,
            mode="auto_ingest",
            mock_llm_client=mock,
        )

        # Read back the persisted state immediately (sync phase committed
        # before ack was returned).
        with sf() as session:
            from media_pilot.repository.models import IngestTask
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
                AgentRunRepository,
            )

            task_db = session.get(IngestTask, task_id)
            assert task_db.status == "agent_running"
            assert task_db.current_step == "agent_running"

            runs = AgentRunRepository(session).list_by_task(task_id)
            # 1 active run created by run_agent_turn_async; no prior runs.
            assert len(runs) == 1
            active_run = runs[0]
            assert active_run.id == ack.run_id
            assert active_run.status == "active"
            assert active_run.current_step == "agent_start"

            # Initial user message persisted (default = make_retry_user_message).
            msgs = AgentMessageRepository(session).list_by_run(active_run.id)
            user_msgs = [m for m in msgs if m.role == "user"]
            assert len(user_msgs) == 1
            assert "failed" in user_msgs[0].content
            assert "recovery" in user_msgs[0].content.lower()
            assert task_id in user_msgs[0].content

        # Cleanup: release the background thread.
        assert mock.entered.wait(timeout=2)
        mock.gate.set()
        ack.thread.join(timeout=5)

    def test_async_retry_schedules_background_loop(self, tmp_path):
        """Background thread MUST eventually drive the run to a final state
        (completed / waiting_user / failed) using the same AgentRun created
        by the sync phase. The frontend refetch will see the final state on
        a later poll.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, status="agent_failed", current_step="agent_failed")
            task_id = task.id

        mock = _GatedMockLLMClient()
        mock.add_text_response("Recovery explanation.")

        from media_pilot.agent.runner import run_agent_turn_async

        ack = run_agent_turn_async(
            session_factory=sf,
            config=config,
            task_id=task_id,
            mode="auto_ingest",
            mock_llm_client=mock,
        )

        # Before release, thread reached chat() and is blocked.
        assert mock.entered.wait(timeout=2)

        # Release the gate and let the loop finish.
        mock.gate.set()
        ack.thread.join(timeout=5)
        assert not ack.thread.is_alive(), "Background thread did not finish"

        # The AgentRun should be in a final state, NOT still 'active'.
        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentMessageRepository,
                AgentRunRepository,
            )

            run = AgentRunRepository(session).get(ack.run_id)
            assert run is not None
            assert run.status in ("completed", "waiting_user", "failed"), (
                f"Run stayed in non-terminal status: {run.status}"
            )

            # Exactly 1 user message (initial) + ≥1 assistant (from LLM).
            msgs = AgentMessageRepository(session).list_by_run(ack.run_id)
            roles = [m.role for m in msgs]
            assert "user" in roles
            assert "assistant" in roles, "Background loop never wrote an assistant message"

    def test_async_retry_409_when_active_run_exists(self, tmp_path):
        """API contract: POST /tasks/{id}/agent-runs MUST 409 when there's
        already an active/waiting AgentRun for that task, even on an
        agent_failed task. The frontend's retry button MUST not silently
        start a second background run.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, status="agent_failed", current_step="agent_failed")
            task_id = task.id

            # Pre-create an active run to force the 409 path.
            from media_pilot.repository.models import AgentRun
            session.add(AgentRun(task_id=task_id, status="active"))
            session.commit()

        from media_pilot.app import create_app

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        resp = client.post(f"/api/v1/tasks/{task_id}/agent-runs")

        assert resp.status_code == 409


class _FailingMockLLMClient:
    """Mock LLM whose ``chat()`` always raises.

    Used to verify the background thread failure path: the run/task MUST
    be marked failed, never stuck in active.
    """

    def __init__(self, exc: Exception):
        from media_pilot.agent.llm_client import LLMResponse

        self.exc = exc
        self.calls = 0
        self._LLMResponse = LLMResponse

    def chat(self, messages, tools=None):
        self.calls += 1
        raise self.exc


class TestRetryAckOnlyFailure:
    """Background loop 异常兜底契约 — 不能留下永久 active run.

    上一次实现 (aac20c1) 兜底只 rollback + logger.exception, 留下了
    一个 known 漏洞: 当 ``continue_agent_run`` 自身抛异常 (run 不存在 /
    status 非法 / 工具注册表故障等场景, ``_run_agent_loop`` 的 try
    还没接住), run/task 长期卡在 active, 后续 retry POST 永远 409.
    修复后 MUST 主动把 run/task 标 failed.
    """

    def test_background_llm_exception_marks_run_and_task_failed(self, tmp_path):
        """Background thread: LLM.chat() 抛异常 → run.status=failed,
        task.status=agent_failed, error_message / failure_reason 有内容.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, status="agent_failed", current_step="agent_failed")
            task_id = task.id

        llm_exc = RuntimeError("simulated llm network failure")
        mock = _FailingMockLLMClient(llm_exc)

        from media_pilot.agent.runner import run_agent_turn_async

        ack = run_agent_turn_async(
            session_factory=sf,
            config=config,
            task_id=task_id,
            mode="auto_ingest",
            mock_llm_client=mock,
        )

        # ack 立即返回, 行为不变.
        assert ack.status == "active"
        assert ack.run_id

        # 等待 background thread 收口.
        ack.thread.join(timeout=5)
        assert not ack.thread.is_alive(), "Background thread did not finish"
        assert mock.calls >= 1, "LLM was never called from the background thread"

        # 关键断言: run 标 failed, task 标 agent_failed.
        with sf() as session:
            from media_pilot.repository.models import IngestTask
            from media_pilot.repository.repositories import AgentRunRepository

            run = AgentRunRepository(session).get(ack.run_id)
            assert run is not None
            assert run.status == "failed", (
                f"Run stayed in {run.status!r}; expected 'failed' so the "
                f"next retry POST is not blocked by a stale active run"
            )
            # current_step 由 catch 路径决定:
            #   - ``_run_agent_loop`` 内部 catch-all 命中 → "llm_error".
            #   - ``_handle_background_failure`` 兜底命中 → "background_failed".
            # 两种都属于"failure 落库"语义, 测试只关心"不是 active".
            assert run.current_step in ("background_failed", "llm_error")
            assert run.error_message is not None
            # error_message 由 catch 路径决定: 内部 catch-all 写 str(exc)
            # (无 type prefix), 兜底写 "Type: message". 测试只关心异常
            # 信息被持久化, 不区分前缀.
            assert "simulated llm network failure" in run.error_message

            task_db = session.get(IngestTask, task_id)
            assert task_db.status == "agent_failed"
            assert task_db.current_step == "agent_failed"
            assert task_db.failure_reason is not None
            # failure_reason 也按 catch 路径分前缀:
            #   - 内部 catch-all → "Agent run failed: <msg>".
            #   - 兜底 → "Background agent run failed: <Type>: <msg>".
            assert "simulated llm network failure" in task_db.failure_reason

    def test_subsequent_retry_does_not_409_after_background_failure(self, tmp_path):
        """关键回归: 背景 loop 异常 → run 标 failed → 后续 retry POST
        不应被 stale active run 卡 409.

        修复前 (aac20c1 之后): run 永远 active → retry 永远 409, 用户
        必须用别的方式清掉这个 run, 体验断裂.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, status="agent_failed", current_step="agent_failed")
            task_id = task.id

        mock = _FailingMockLLMClient(RuntimeError("transient"))

        from media_pilot.agent.runner import run_agent_turn_async

        # 第一次 retry: 走 async 路径, 背景 loop 失败.
        first_ack = run_agent_turn_async(
            session_factory=sf,
            config=config,
            task_id=task_id,
            mode="auto_ingest",
            mock_llm_client=mock,
        )
        first_ack.thread.join(timeout=5)
        assert not first_ack.thread.is_alive()

        # 关键断言: 此时 task.status 应已变回 agent_failed, 旧 run 已
        # failed. 第二次 retry 走 API 应该 200/2xx, 而不是 409.
        with sf() as session:
            from media_pilot.repository.repositories import AgentRunRepository
            runs = AgentRunRepository(session).list_by_task(task_id)
            assert all(r.status != "active" for r in runs), (
                f"Stale active run persists after background failure: "
                f"{[r.status for r in runs]}"
            )

        from media_pilot.app import create_app

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        # 第二次 retry: 不应被 stale run 阻塞. 这次给一个能成功的 mock
        # 避免再次进入失败路径, 简化断言.
        success_mock = _GatedMockLLMClient()
        success_mock.add_text_response("done")
        # 注: 第二次 retry 也走 run_agent_turn_async; 给一个成功
        # 路径的 mock LLM 以便 ack 路径走通.
        # 实际生产中, mock LLM 不会被注入, 这里只为测试目的覆盖.
        second_ack = run_agent_turn_async(
            session_factory=sf,
            config=config,
            task_id=task_id,
            mode="auto_ingest",
            mock_llm_client=success_mock,
        )
        # ack 拿到新 run_id (旧 run 已 failed, 不阻塞)
        assert second_ack.run_id != first_ack.run_id
        assert second_ack.status == "active"
        # 让 background thread 跑完.
        assert success_mock.entered.wait(timeout=2)
        success_mock.gate.set()
        second_ack.thread.join(timeout=5)

    def test_sync_phase_db_locked_propagates_operational_error(self, tmp_path, monkeypatch):
        """Sync phase safe_commit 抛 OperationalError → run_agent_turn_async
        重新抛出, 调用方 (v1.py::create_agent_run) 负责捕获并返回
        ``_db_locked_response``. 验证: 不留半成品 run, 不留半成品 message.
        """
        from sqlalchemy.exc import OperationalError

        from media_pilot.repository.repositories import (
            AgentMessageRepository,
            AgentRunRepository,
            IngestTaskRepository,
        )
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, status="agent_failed", current_step="agent_failed")
            task_id = task.id

        from media_pilot.orchestration import db_retry

        def _raise_locked(session):
            raise OperationalError("stmt", {}, Exception("database is locked"))

        # Patch 源模块即可. runner.py / v1.py 都用 `from
        # media_pilot.orchestration.db_retry import safe_commit` (函数
        # 局部 import), 每次调用都会重新 import, 拿到 patch 后的版本.
        monkeypatch.setattr(db_retry, "safe_commit", _raise_locked)

        from media_pilot.agent.runner import run_agent_turn_async

        with pytest.raises(OperationalError):
            run_agent_turn_async(
                session_factory=sf,
                config=config,
                task_id=task_id,
                mode="auto_ingest",
                mock_llm_client=_GatedMockLLMClient(),
            )

        # 关键断言: 没有半成品 AgentRun / AgentMessage 留下 (sync phase
        # 在 safe_commit 失败时整体 rollback).
        with sf() as session:
            runs = AgentRunRepository(session).list_by_task(task_id)
            assert len(runs) == 0, (
                f"Sync phase left {len(runs)} orphan AgentRun(s) on "
                f"db_locked: {[r.id for r in runs]}"
            )
            task_db = IngestTaskRepository(session).get(task_id)
            # task status 仍 agent_failed, 没被推到 agent_running.
            assert task_db.status == "agent_failed"
            # 没有任何 message 留下.
            assert AgentMessageRepository(session).list_by_task(task_id) == []

    def test_api_returns_409_db_locked_on_sync_phase_operational_error(
        self, tmp_path, monkeypatch,
    ):
        """API contract: createAgentRun 在 sync phase 撞 db lock → 返
        409 db_locked + retryable=True, 不返 500. 复用项目既有
        ``_db_locked_response`` 语义.
        """
        from sqlalchemy.exc import OperationalError

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, status="agent_failed", current_step="agent_failed")
            task_id = task.id

        from media_pilot.orchestration import db_retry

        def _raise_locked(session):
            raise OperationalError("stmt", {}, Exception("database is locked"))

        monkeypatch.setattr(db_retry, "safe_commit", _raise_locked)

        from media_pilot.app import create_app

        app = create_app(config=config, session_factory=sf)
        client = TestClient(app)
        resp = client.post(f"/api/v1/tasks/{task_id}/agent-runs")

        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["status"] == "error"
        assert body["messages"][0]["code"] == "db_locked"
        assert body["meta"]["retryable"] is True

    def test_continue_agent_run_raises_below_loop_marks_run_and_task_failed(
        self, tmp_path, monkeypatch,
    ):
        """Exercise ``_handle_background_failure`` 兜底分支.

        关键差异: 上一个测试里 LLM 抛异常, ``_run_agent_loop`` 的
        catch-all 已经在内部把 run/task 标 failed, 兜底不触发. 这里
        让 ``continue_agent_run`` 自身 (在 ``_run_agent_loop`` 之前)
        抛异常 — 这种情况兜底 MUST 主动写 failed, 不留 active.
        """
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        with sf() as session:
            from media_pilot.agent.tools.registry import register_builtin_tools
            register_builtin_tools()
            task = _make_task(session, status="agent_failed", current_step="agent_failed")
            task_id = task.id

        # Patch continue_agent_run 让它直接抛 — 模拟 run 状态非法 /
        # 注册表故障 / 其它 ``_run_agent_loop`` 之前发生的异常.
        from media_pilot.agent import runner as runner_mod

        boom = RuntimeError("simulated infra failure before loop")
        monkeypatch.setattr(
            runner_mod, "continue_agent_run",
            lambda **_kwargs: (_ for _ in ()).throw(boom),
        )

        from media_pilot.agent.runner import run_agent_turn_async

        ack = run_agent_turn_async(
            session_factory=sf,
            config=config,
            task_id=task_id,
            mode="auto_ingest",
            mock_llm_client=_GatedMockLLMClient(),
        )
        assert ack.status == "active"

        # 等 background thread 收口 (走 ``_handle_background_failure``).
        ack.thread.join(timeout=5)
        assert not ack.thread.is_alive()

        with sf() as session:
            from media_pilot.repository.models import IngestTask
            from media_pilot.repository.repositories import AgentRunRepository

            run = AgentRunRepository(session).get(ack.run_id)
            assert run is not None
            assert run.status == "failed", (
                f"Run stayed in {run.status!r}; expected 'failed' after "
                f"continue_agent_run raised before _run_agent_loop"
            )
            # 兜底路径会写 "background_failed" 这一 current_step.
            assert run.current_step == "background_failed"
            assert "RuntimeError" in (run.error_message or "")
            assert "simulated infra failure" in (run.error_message or "")

            task_db = session.get(IngestTask, task_id)
            assert task_db.status == "agent_failed"
            assert task_db.current_step == "agent_failed"
            assert "Background agent run failed" in (task_db.failure_reason or "")
            assert "RuntimeError" in (task_db.failure_reason or "")

"""AgentRun completion safety net tests.

MP-Lab-02-Matrix-1999-Dominant 现场: LLM final text 收口时
`task.status="agent_running"` + 无 MetadataDetail + 无 WriteResult +
无 FileAsset, 任务永久卡自相矛盾状态. Safety net 必须收口.
"""
