"""safe_commit 测试 — 验证 OperationalError 兜底与 rollback 闭环.

背景: 后台 processor / Agent 长事务 / API 删除等路径会争用 SQLite
写锁. safe_commit 单一职责: 失败时 rollback + 重新抛 OperationalError,
不重试 (重试必须在"完整 operation closure"内, 不能在同事务 rollback
后假装重放). 早期版本 commit_with_retry 走"rollback 后再 commit"
伪重试, 实际是"API 返 200 但 DB 没改"的伪成功, 已废弃.

调用方必须自己 try/except OperationalError — API 层转 409
ApiEnvelope(code=db_locked, meta.retryable=True), 后台 processor
跳过本轮让下一轮自然重试.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError


def _make_session():
    """构造一个 in-memory SQLite Session, 通过 monkeypatch 替换 commit()."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from media_pilot.repository.database import Base

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return SessionLocal()


def test_safe_commit_succeeds_first_try():
    """无锁时 safe_commit 应当一次成功, 不重试."""
    from media_pilot.orchestration.db_retry import safe_commit

    session = _make_session()
    call_count = {"n": 0}
    original_commit = session.commit

    def _spy_commit():
        call_count["n"] += 1
        return original_commit()

    session.commit = _spy_commit  # type: ignore[method-assign]
    safe_commit(session)
    assert call_count["n"] == 1


def test_safe_commit_rolls_back_and_raises_on_locked():
    """commit 抛 locked → safe_commit 必须 rollback 释放事务, 然后冒泡.
    不允许在同事务内再 commit 一次假装重放 (伪成功)."""
    from media_pilot.orchestration.db_retry import safe_commit

    session = _make_session()
    call_count = {"n": 0}
    rollback_count = {"n": 0}

    def _always_locked():
        call_count["n"] += 1
        raise OperationalError("stmt", {}, Exception("database is locked"))

    def _spy_rollback():
        rollback_count["n"] += 1
        return None

    session.commit = _always_locked  # type: ignore[method-assign]
    session.rollback = _spy_rollback  # type: ignore[method-assign]

    with pytest.raises(OperationalError, match="locked"):
        safe_commit(session)
    # 关键约束: commit 只调 1 次, 不重试.
    assert call_count["n"] == 1
    # rollback 恰好 1 次, 释放锁让下一轮后台能继续.
    assert rollback_count["n"] == 1


def test_safe_commit_does_not_retry_on_any_operational_error():
    """所有 OperationalError (locked / disk full / corruption) 一律
    rollback + 冒泡, 不区分. 区分容易掩盖 disk full / corruption
    等真实故障."""
    from media_pilot.orchestration.db_retry import safe_commit

    session = _make_session()
    call_count = {"n": 0}

    def _disk_full():
        call_count["n"] += 1
        raise OperationalError("stmt", {}, Exception("database or disk is full"))

    session.commit = _disk_full  # type: ignore[method-assign]

    with pytest.raises(OperationalError, match="disk is full"):
        safe_commit(session)
    assert call_count["n"] == 1


def test_safe_commit_preserves_business_state_on_failure():
    """safe_commit 失败时必须 rollback 撤销业务变更 — 这是核心合约.
    早期 commit_with_retry 在 rollback 后再 commit, 实际没东西可写,
    造成"API 返成功但 DB 实际没变"伪成功. 新合约必须用真实 mutation
    验证 rollback 撤销了 delete."""
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from media_pilot.orchestration.db_retry import safe_commit
    from media_pilot.repository.database import Base
    from media_pilot.repository.models import DownloadTask
    from media_pilot.repository.repositories import (
        DownloadTaskCreate,
        DownloadTaskRepository,
    )

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    # Step 1: create the record with a healthy session, then commit.
    with SessionLocal() as session:
        task = DownloadTaskRepository(session).create(DownloadTaskCreate(
            title="Should Survive Locked Commit",
            source="prowlarr",
            save_path="/tmp/test-downloads",
        ))
        session.commit()
        task_id = task.id

    # Step 2: open a fresh session, queue a delete, then call safe_commit
    # while commit() is rigged to raise OperationalError. Verify the
    # record survives (rollback undid the staged delete).
    with SessionLocal() as session:
        dl = session.get(DownloadTask, task_id)
        assert dl is not None  # precondition

        real_commit = session.commit

        def _locked_commit():
            raise OperationalError("stmt", {}, Exception("database is locked"))

        session.commit = _locked_commit  # type: ignore[method-assign]
        session.delete(dl)
        with pytest.raises(OperationalError, match="locked"):
            safe_commit(session)
        # restore for cleanup
        session.commit = real_commit  # type: ignore[method-assign]

    # Step 3: re-open a fresh session, the record must still exist.
    with SessionLocal() as session:
        survivor = session.get(DownloadTask, task_id)
        assert survivor is not None, (
            "safe_commit failure must rollback the staged delete — "
            "early commit_with_retry was a silent-failure vector"
        )
        assert survivor.title == "Should Survive Locked Commit"
