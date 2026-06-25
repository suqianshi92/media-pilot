"""IngestTaskRepository.list 排序契约 — RED 守卫.

排序必须在分页前由 SQL 完成, 不得依赖 Python 后处理. 优先级契约:

- 1: waiting_user
- 2: agent_running / processing / queued / downloading / awaiting_sync / waiting_stable
- 3: agent_failed / failed / sync_failed
- 4: library_import_complete / completed
- 5: 其它 (兜底)

同优先级内: updated_at desc → created_at desc → id 兜底.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository


def _make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"),
        shows_dir=Path("/media/library/shows"),
        database_dir=tmp_path,
    )


def _make_session_factory(tmp_path: Path):
    config = _make_config(tmp_path)
    initialize_database(config)
    return create_session_factory(config)


def test_repository_list_priority_order_not_created_at(tmp_path: Path) -> None:
    """四种状态混合, 排序必须按 priority 分组, 不得退化为 created_at desc.

    关键: 创建顺序故意与 priority 顺序不一致, 避免老实现巧合命中.
    创建顺序: library(p4) → active(p2) → failed(p3) → waiting(p1)
    老 (created_at desc): waiting → failed → active → library
    新 (priority 1→4):    waiting → active → failed → library
    """

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/library.mkv",
            status="library_import_complete",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/active.mkv",
            status="agent_running",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/failed.mkv",
            status="agent_failed",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/waiting.mkv",
            status="waiting_user",
        ))
        session.commit()

    with sf() as session:
        repo = IngestTaskRepository(session)
        rows = repo.list()

    statuses = [r.status for r in rows]
    assert statuses == [
        "waiting_user",
        "agent_running",
        "agent_failed",
        "library_import_complete",
    ], f"priority order broken: {statuses}"


def test_repository_list_within_priority_updated_at_desc(tmp_path: Path) -> None:
    """同 priority 组内, updated_at 较新的必须靠前, 不退化为 created_at.

    关键: 让两条任务的 updated_at 顺序与 created_at 顺序相反, 才能区分.
    - first.mkv: 先创建 (created_at=base), 但 updated_at=base+20h (最晚)
    - second.mkv: 后创建 (created_at=base+10h), 但 updated_at=base+5h (较早)

    按 created_at desc: second → first
    按 updated_at desc: first → second
    """

    base = datetime(2026, 1, 1, tzinfo=UTC)
    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        first = repo.create(IngestTaskCreate(
            source_path="/media/downloads/first.mkv",
            status="waiting_user",
        ))
        first.created_at = base
        first.updated_at = base + timedelta(hours=20)
        session.flush()
        second = repo.create(IngestTaskCreate(
            source_path="/media/downloads/second.mkv",
            status="waiting_user",
        ))
        second.created_at = base + timedelta(hours=10)
        second.updated_at = base + timedelta(hours=5)
        session.commit()

    with sf() as session:
        repo = IngestTaskRepository(session)
        rows = repo.list()

    paths = [r.source_path for r in rows]
    # first.mkv updated_at 较新 → 排前
    assert paths == ["/media/downloads/first.mkv", "/media/downloads/second.mkv"], (
        f"同 priority 内 updated_at desc 失败: {paths}"
    )


def test_repository_list_unknown_status_falls_to_priority_5(tmp_path: Path) -> None:
    """未在 TASK_STATUS_PRIORITY 注册的 status 走兜底组, 排在所有显式 priority 之后."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        # 先建 priority 1, 再建未知 status (兜底 priority 5)
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/waiting.mkv",
            status="waiting_user",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/mystery.mkv",
            status="some_weird_state",
        ))
        session.commit()

    with sf() as session:
        repo = IngestTaskRepository(session)
        rows = repo.list()

    statuses = [r.status for r in rows]
    # 兜底组 (priority 5) 必须在 priority 1 之后
    assert statuses == ["waiting_user", "some_weird_state"], statuses


def test_repository_list_processing_in_priority_2(tmp_path: Path) -> None:
    """processing / queued / downloading / awaiting_sync / waiting_stable 都属 priority 2."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        # priority 1 (waiting_user) 必须排在 priority 2 (processing) 之前
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/processing.mkv",
            status="processing",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/waiting.mkv",
            status="waiting_user",
        ))
        session.commit()

    with sf() as session:
        repo = IngestTaskRepository(session)
        rows = repo.list()

    statuses = [r.status for r in rows]
    assert statuses == ["waiting_user", "processing"], statuses


# ── SQL 分页契约 ──


def test_repository_count_no_filter(tmp_path: Path) -> None:
    """无 status filter 时, count() 返回全部 ingest task 行数.

    必须（MUST）走 SQL `COUNT(*)`, 不得依赖全量 list 后 len().
    """

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        for status in ("waiting_user", "agent_running", "agent_failed",
                       "library_import_complete", "discovered"):
            repo.create(IngestTaskCreate(
                source_path=f"/media/downloads/{status}.mkv",
                status=status,
            ))
        session.commit()

    with sf() as session:
        repo = IngestTaskRepository(session)
        total = repo.count()

    assert total == 5, f"count() 应返回全量 5 条, 实际 {total}"


def test_repository_count_with_status_filter(tmp_path: Path) -> None:
    """带 status filter 时, count() 只统计匹配行, 不得忽略 filter."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        for _ in range(3):
            repo.create(IngestTaskCreate(
                source_path="/media/downloads/agent_failed_1.mkv",
                status="agent_failed",
            ))
        for _ in range(2):
            repo.create(IngestTaskCreate(
                source_path="/media/downloads/agent_running_1.mkv",
                status="agent_running",
            ))
        session.commit()

    with sf() as session:
        repo = IngestTaskRepository(session)
        failed_total = repo.count(status="agent_failed")
        running_total = repo.count(status="agent_running")

    assert failed_total == 3
    assert running_total == 2


def test_repository_list_page_respects_attention_priority_and_limit(tmp_path: Path) -> None:
    """list_page() 必须复用 attention priority 排序, 且只返回 [offset:offset+limit].

    关键: 创建顺序故意与 priority 顺序不一致. 创建顺序:
    library(p4) → active(p2) → failed(p3) → waiting(p1) → discovered(兜底 p5)
    按 priority 1→5 排序应是: waiting → active → failed → library → discovered.
    """

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/library.mkv",
            status="library_import_complete",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/active.mkv",
            status="agent_running",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/failed.mkv",
            status="agent_failed",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/waiting.mkv",
            status="waiting_user",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/mystery.mkv",
            status="discovered",
        ))
        session.commit()

    with sf() as session:
        repo = IngestTaskRepository(session)
        # limit=2, offset=0 → waiting + active
        page1 = repo.list_page(status=None, limit=2, offset=0)
        page2 = repo.list_page(status=None, limit=2, offset=2)
        page3 = repo.list_page(status=None, limit=2, offset=4)

    paths1 = [r.source_path for r in page1]
    paths2 = [r.source_path for r in page2]
    paths3 = [r.source_path for r in page3]
    assert paths1 == ["/media/downloads/waiting.mkv", "/media/downloads/active.mkv"], (
        f"page1 priority + limit 失败: {paths1}"
    )
    assert paths2 == ["/media/downloads/failed.mkv", "/media/downloads/library.mkv"], (
        f"page2 priority 续接失败: {paths2}"
    )
    assert paths3 == ["/media/downloads/mystery.mkv"], f"page3 末页失败: {paths3}"


def test_repository_list_page_with_status_filter(tmp_path: Path) -> None:
    """list_page 带 status filter 时, 分页结果只来自过滤集合."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        # 4 条 agent_failed, 1 条 agent_running
        for i in range(4):
            repo.create(IngestTaskCreate(
                source_path=f"/media/downloads/agent_failed_{i}.mkv",
                status="agent_failed",
            ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/agent_running.mkv",
            status="agent_running",
        ))
        session.commit()

    with sf() as session:
        repo = IngestTaskRepository(session)
        page1 = repo.list_page(status="agent_failed", limit=2, offset=0)
        page2 = repo.list_page(status="agent_failed", limit=2, offset=2)

    paths1 = [r.source_path for r in page1]
    paths2 = [r.source_path for r in page2]
    assert len(paths1) == 2
    assert len(paths2) == 2
    # 全部都是 agent_failed, 不得混入 agent_running
    for p in paths1 + paths2:
        assert "agent_failed" in p, f"filter 泄漏: {p}"


def test_repository_list_page_stable_across_pages_within_priority(tmp_path: Path) -> None:
    """同 priority 同 updated_at 的任务, 跨页必须按 id asc 稳定排序.

    关键: 让 3 条 waiting_user 任务的 updated_at / created_at 完全相同,
    只靠 id 兜底. 否则 page 边界会出现重复或跳项.
    """

    base = datetime(2026, 1, 1, tzinfo=UTC)
    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        ids: list[str] = []
        for i in range(3):
            task = repo.create(IngestTaskCreate(
                source_path=f"/media/downloads/waiting_{i}.mkv",
                status="waiting_user",
            ))
            task.updated_at = base
            task.created_at = base
            ids.append(task.id)
        session.commit()

    with sf() as session:
        repo = IngestTaskRepository(session)
        # limit=1 让边界严格落在每条任务上
        p1 = repo.list_page(status=None, limit=1, offset=0)
        p2 = repo.list_page(status=None, limit=1, offset=1)
        p3 = repo.list_page(status=None, limit=1, offset=2)

    # id asc 兜底: page 顺序应按 id 字典序升序 (UUID 是 String(36) 列,
    # SQL ORDER BY id ASC 走字符串字典序). 3 条任务跨页必须无重复无跳项.
    sorted_ids = sorted(ids)
    assert [r.id for r in p1] == [sorted_ids[0]]
    assert [r.id for r in p2] == [sorted_ids[1]]
    assert [r.id for r in p3] == [sorted_ids[2]]
