"""卡住 Agent 恢复服务.

把"任务卡在 agent_running + active run + 无 pending decision"的场景转成
受控恢复: 旧 active run 显式标 failed, 启动新的 ack-only auto_ingest run
继续处理. 与普通 ``agent_failed`` 重试的关键差异:
- 旧 run 是被人为判定为"卡住", 不是运行期异常; 用 ``current_step =
  "stuck_recovered"`` + 固定 error_message 标识这是人工恢复, 不掩盖
  真实运行错误.
- 不复用旧的 run, 始终创建新 run.
- task.status 保持 ``agent_running``, 不切到 ``agent_failed`` — 用户
  期望的是"继续处理", 不是"重试失败任务".

原子性: 标旧 run failed + 创建新 run + 切 task 状态 全部塞进同一
session 的同一事务, 单次 ``safe_commit`` 落库. 防止"半恢复"残留 — 旧
run 已标 failed 但新 run 没创建成功的中间态. 失败时整个事务回滚, 旧 run
保留 active, task 保持原状.
"""

from __future__ import annotations

from media_pilot.config import AppConfig
from media_pilot.repository.repositories import (
    AgentDecisionRequestRepository,
    AgentRunRepository,
    IngestTaskRepository,
)
from media_pilot.orchestration import db_retry


_STUCK_RECOVERED_STEP = "stuck_recovered"
_STUCK_RECOVERED_ERROR = "Manually recovered stuck agent run"
_RETRY_INITIAL_STEP = "agent_running"


def _raise_conflict(detail: str) -> None:
    raise ValueError({"status_code": 409, "detail": detail})


def recover_stuck_agent_run(
    *,
    session_factory,
    config: AppConfig,
    task_id: str,
    mock_llm_client=None,
) -> dict:
    """卡住 Agent 恢复主入口. 返回 ``{"run_id": str, "status": "active"}``.

    校验链 (任何一步失败 → 抛 ValueError 带 status_code 409/404, 不创建
    新 run, 不修改旧 run/task):
    1. 任务存在 (否则 404)
    2. task.status == "agent_running" (否则 409)
    3. 任务不在 deleted / library_import_complete / agent_failed /
       completed 终态 (否则 409)
    4. 存在 active AgentRun (status in {"active", "waiting_user"})
    5. 不存在 pending AgentDecisionRequest

    成功路径 (单事务, safe_commit):
    1. 旧 active run.status = "failed", current_step = "stuck_recovered",
       error_message = "Manually recovered stuck agent run"
    2. task 写回 status = "agent_running", current_step = "agent_running"
    3. ``_create_run_in_session`` 在同一 session 内创建新 run + 初始
       user message (task 切到 agent_running 是幂等的, 无副作用)
    4. 一次 ``safe_commit`` 把 1+2+3 一起落库 — 失败时整个事务回滚,
       旧 run 保持 active, 没有新 run, 防止"半恢复"残留
    5. commit 成功后 ``_start_background_loop`` 在 daemon thread 里跑
       Agent loop, 立即返回新 run_id 给前端 ack.

    与 ``reply_to_decision`` 的 ``target_conflict_pending`` 终态类似, 该
    路径必须快速 ack. 实际 Agent loop 在后台 daemon thread 跑.
    """
    # ── 同步阶段: 校验 + 标旧 run failed + 创建新 run (单事务) ──
    with session_factory() as session:
        task_repo = IngestTaskRepository(session)
        run_repo = AgentRunRepository(session)
        dr_repo = AgentDecisionRequestRepository(session)

        task = task_repo.get(task_id)
        if task is None:
            raise ValueError({"status_code": 404, "detail": f"Task {task_id} not found"})

        if task.status != "agent_running":
            # waiting_user 是常见边界: 用户有 pending decision 没处理.
            # 给前端一个明确提示, 避免红 toast 报"非卡住"造成误判.
            if task.status == "waiting_user":
                _raise_conflict(
                    "Task is waiting for user decision; resolve pending "
                    "decisions before recovering the stuck agent run"
                )
            _raise_conflict(
                f"Task {task_id} is not stuck (status={task.status}); "
                "use Agent retry for failed tasks"
            )

        # 防御性终态守卫, 避免历史脏数据绕过 status==agent_running 校验
        if task.status in {"deleted", "library_import_complete", "agent_failed", "completed"}:
            _raise_conflict(f"Task {task_id} is in terminal state (status={task.status})")

        # 找 active run. 优先 active, 然后 waiting_user (fallback, 后面
        # 会再二次检查). 这里用 get_active_or_waiting_by_task 避免重复
        # 手写 SQL.
        active_run = run_repo.get_active_by_task(task_id)
        waiting_run = None
        if active_run is None:
            waiting_run = run_repo.get_active_or_waiting_by_task(task_id)
        if active_run is None and waiting_run is None:
            _raise_conflict(
                f"Task {task_id} has no active AgentRun to recover; "
                "use Agent retry for failed tasks"
            )

        # 实际 status='waiting_user' 的 run 阻卡: 用户应先处理 pending 决策.
        if active_run is None and waiting_run is not None:
            pending = dr_repo.list_pending_by_task(task_id)
            if pending:
                _raise_conflict(
                    f"Task {task_id} has pending decisions; "
                    "resolve them before recovering"
                )
            # waiting 但无 pending — 历史脏数据; 仍按"非卡住" 拒绝.
            _raise_conflict(
                f"Task {task_id} is waiting for user decision; "
                "resolve pending decisions before recovering"
            )

        # pending decision 二次防御: 即便 active run, 也得确认 task 层
        # 没有 pending. 实际生产里 waiting_user 跟 pending 是配对的,
        # 但 active + pending 也是边界情况 (race), 不应静默放行.
        pending_for_task = dr_repo.list_pending_by_task(task_id)
        if pending_for_task:
            _raise_conflict(
                f"Task {task_id} has pending decisions; "
                "resolve them before recovering"
            )

        # 旧 run 标 failed + stuck_recovered
        old_run = active_run
        run_repo.update_status(
            old_run,
            status="failed",
            current_step=_STUCK_RECOVERED_STEP,
            error_message=_STUCK_RECOVERED_ERROR,
        )

        # 显式写回 task 状态, 防止 helper 假定 task 已是 agent_running 后
        # 跳过这一步.
        task_repo.update_status(
            task, status="agent_running", current_step=_RETRY_INITIAL_STEP,
        )

        # 同步阶段 helper: 在当前 session 内创建新 run + 初始 user message
        # + 幂等切 task 状态. 不 commit, 由下面一次 safe_commit 统一落库 —
        # 这就是单事务原子性的关键: 旧 run failed 和新 run active 要么
        # 一起成功, 要么一起回滚.
        from media_pilot.agent.prompts import make_retry_user_message
        from media_pilot.agent.runner import _create_run_in_session
        try:
            new_run_id = _create_run_in_session(
                session=session,
                task_id=task_id,
                initial_message=make_retry_user_message(task_id),
            )
        except ValueError as exc:
            # race 兜底: _create_run_in_session 内部 task 突然不存在
            # (极端 race) 或 active run 突现. 转译为 409 结构化错误,
            # 不让 v1.py 兜底 500. session 退出 with 时自动 rollback.
            raise ValueError({
                "status_code": 409,
                "detail": f"new run creation failed: {exc}",
            }) from exc

        try:
            db_retry.safe_commit(session)
        except Exception as exc:
            # OperationalError / 其它数据库锁 — safe_commit 内部已 rollback
            # 整个事务. 旧 run 的 failed 写入也被撤销, 旧 run 保持 active,
            # 没有新 run. 让 caller (v1.py) 转译为 409 db_locked envelope.
            raise ValueError({"status_code": 409, "detail": f"db_locked: {exc}"})

    # ── 异步阶段: commit 成功后启动后台 daemon thread ──
    # 注意: _start_background_loop 必须在 commit 之后才能调. 一旦 commit
    # 失败, 上面 raise ValueError 冒泡, 永远到不了这里. 这条不可变性
    # 由 test_recovery_does_not_start_background_thread_on_failure 锁住.
    from media_pilot.agent.runner import _start_background_loop

    _start_background_loop(
        session_factory=session_factory,
        config=config,
        run_id=new_run_id,
        task_id=task_id,
        mode="auto_ingest",
        mock_llm_client=mock_llm_client,
    )
    return {"run_id": new_run_id, "status": "active"}
