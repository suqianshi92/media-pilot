"""DB commit 失败兜底 — 单一职责: 把 OperationalError 翻译为可重试信号.

背景: 后台 processor / Agent 长事务 / API 删除等路径会争用 SQLite
写锁, 即便已有 busy_timeout=5s + SQLAlchemy timeout=30s, commit
阶段仍可能抛出 `sqlite3.OperationalError: database is locked`.

早期版本 commit_with_retry 试图在 rollback 后再次 commit 重放业务
变更 — 但 SQLAlchemy rollback 已经撤销了当前事务内的所有
delete/update, 第二次 commit 实际上没东西可写, 造成"API 返 200
但 DB 实际没删"的伪成功. 这是非常严重的语义错误.

新 KISS 语义:
1. safe_commit 尝试 commit 一次.
2. 失败 (任何 SQLAlchemy OperationalError, 不仅是 locked) → rollback
   释放事务, 重新抛出原异常.
3. 调用方必须自己 try/except OperationalError, 转 409 + retryable
   envelope 或让后台下一轮重试. rollback 已由 safe_commit 完成,
   调用方不要再 rollback.

不要再写"伪重试" — 业务变更重试只能在"完整 operation closure"内
(在新的 session / 新事务里重新跑整个业务), 而不是在同一个事务里
rollback 后再 commit.
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def safe_commit(session: Session) -> None:
    """Commit 当前事务, 失败时 rollback + 重新抛 OperationalError.

    单一职责: 不重试, 不掩盖错误. 调用方拿到 OperationalError 后
    应该:
    - API 层 → 返回 409 ApiEnvelope(code="db_locked", meta.retryable=True).
    - 后台 processor / worker → 跳过本轮, 下一轮自然重试 (任务状态未变).
    - AgentRun → 返回 db_locked 状态, Agent 框架不重试但 task 保留.

    故意不区分 "locked" vs 其它 OperationalError — 两者对调用方语义
    相同 (都是"现在写不进去, 稍后重试"), 区分反而容易掩盖 disk full
    / corruption 等真实故障.
    """
    try:
        session.commit()
        return
    except OperationalError as exc:
        logger.warning("safe_commit 遇 OperationalError, rollback 后冒泡: %s", exc)
        try:
            session.rollback()
        except Exception:
            logger.exception("rollback 失败, session 状态可能不一致")
        raise
