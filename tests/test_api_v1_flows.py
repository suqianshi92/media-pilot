"""/api/v1/flows 端点契约 — RED 守卫.

媒体获取流程列表 read-model. 后端必须聚合:

- 关联 `DownloadTask.ingest_task_id` 的 ingest flow
- 未关联的 download-only `DownloadTask` flow
- watch/import `IngestTask` (无 download_task) flow

排序: attention priority 1 (waiting_user) → 2 (processing-adjacent) → 3 (failed)
→ 4 (done) → 5 (unknown). 同优先级内 updated_at desc → created_at desc → id asc.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.auth_helpers import AuthenticatedTestClient as TestClient

from media_pilot.app import create_app
from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import DownloadTask
from media_pilot.repository.repositories import (
    DownloadTaskCreate,
    DownloadTaskRepository,
    IngestTaskCreate,
    IngestTaskRepository,
)


def _make_config(database_dir: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"),
        shows_dir=Path("/media/library/shows"),
        database_dir=database_dir,
    )


def _make_session_factory(tmp_path: Path):
    config = _make_config(tmp_path)
    initialize_database(config)
    return create_session_factory(config)


# ── 1. DTO 契约 (Task 1.1) ──


def test_flows_endpoint_returns_ingest_flow_with_linked_download(tmp_path: Path) -> None:
    """linked ingest flow: 单条 flow 含 ingest_task_id + download_task_id, route_target=task_detail."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        dl = DownloadTaskRepository(session).create(
            DownloadTaskCreate(
                title="movie.mkv",
                source="prowlarr",
                save_path="/media/downloads/movie.mkv",
                status="downloading",
            )
        )
        ingest = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/movie.mkv",
                status="processing",
                source_download_task_id=dl.id,
            )
        )
        session.commit()

    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/flows")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["data"]["items"]
    assert len(items) == 1
    flow = items[0]
    assert flow["ingest_task_id"] == ingest.id
    assert flow["download_task_id"] is not None
    assert flow["route_target"] == "task_detail"
    assert flow["status_summary"] is not None
    assert flow["status_summary"]["status"] == "processing"
    assert flow["flow_type"] == "managed_download"


def test_flows_endpoint_returns_watch_import_ingest_without_download(tmp_path: Path) -> None:
    """watch/import ingest flow: 无 download_task, download_task_id 必须为空."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/watch/external.mkv",
                status="discovered",
            )
        )
        session.commit()

    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/flows")
    body = resp.json()
    items = body["data"]["items"]
    assert len(items) == 1
    flow = items[0]
    assert flow["ingest_task_id"] is not None
    assert flow["download_task_id"] is None
    assert flow["route_target"] == "task_detail"


def test_flows_endpoint_returns_download_only_flow(tmp_path: Path) -> None:
    """download-only flow: ingest_task_id 空, route_target=download_detail."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        DownloadTaskRepository(session).create(
            DownloadTaskCreate(
                title="orphan.mkv",
                source="prowlarr",
                save_path="/media/downloads/orphan.mkv",
                status="downloading",
            )
        )
        session.commit()

    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/flows")
    body = resp.json()
    items = body["data"]["items"]
    assert len(items) == 1
    flow = items[0]
    assert flow["ingest_task_id"] is None
    assert flow["download_task_id"] is not None
    assert flow["route_target"] == "download_detail"
    assert flow["download_task"] is not None


def test_flows_endpoint_dedupes_linked_download_task(tmp_path: Path) -> None:
    """去重: 同一 DownloadTask.ingest_task_id 已有 IngestTask 时, 不得再返回 download-only flow.

    这是 design Decision 3 的核心: 一个媒体获取流程对应一个主对象 (IngestTask),
    download-only 是 download_task 没有 ingest_task_id 时的兜底.
    """

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        linked = DownloadTaskRepository(session).create(
            DownloadTaskCreate(
                title="linked.mkv",
                source="prowlarr",
                save_path="/media/downloads/linked.mkv",
                status="downloading",
            )
        )
        ingest = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/linked.mkv",
                status="processing",
                source_download_task_id=linked.id,
            )
        )
        # 同时建一个孤儿 download, 用来证明 download-only 路径仍能返回
        DownloadTaskRepository(session).create(
            DownloadTaskCreate(
                title="orphan.mkv",
                source="prowlarr",
                save_path="/media/downloads/orphan.mkv",
                status="downloading",
            )
        )
        session.commit()

    client = TestClient(create_app(session_factory=sf))
    body = client.get("/api/v1/flows").json()
    items = body["data"]["items"]
    # 2 条 flow: linked ingest + download-only orphan. 不得出现 3 条 (linked download 重复).
    assert len(items) == 2
    ingest_flow = next(f for f in items if f["ingest_task_id"] == ingest.id)
    assert ingest_flow["route_target"] == "task_detail"
    orphan_flow = next(f for f in items if f["ingest_task_id"] is None)
    assert orphan_flow["route_target"] == "download_detail"
    # 关键: linked download_task 不得再作为独立 download-only flow 出现
    download_ids = [f["download_task_id"] for f in items if f["download_task_id"]]
    assert len(download_ids) == 2  # linked dl + orphan dl


# ── 2. 排序/筛选/分页 (Task 2.1-2.7) ──


def test_flows_endpoint_sorts_mixed_by_attention_priority(tmp_path: Path) -> None:
    """混合 flow 必须按 attention priority 排序: waiting → processing → failed → done."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        # p1: waiting ingest
        IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/waiting.mkv",
                status="waiting_user",
            )
        )
        # p2: processing download-only
        DownloadTaskRepository(session).create(
            DownloadTaskCreate(
                title="downloading.mkv",
                source="prowlarr",
                save_path="/media/downloads/dl.mkv",
                status="downloading",
            )
        )
        # p3: failed download-only
        DownloadTaskRepository(session).create(
            DownloadTaskCreate(
                title="sync_failed.mkv",
                source="prowlarr",
                save_path="/media/downloads/sync.mkv",
                status="sync_failed",
            )
        )
        # p4: done ingest
        IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/done.mkv",
                status="library_import_complete",
            )
        )
        session.commit()

    client = TestClient(create_app(session_factory=sf))
    body = client.get("/api/v1/flows").json()
    items = body["data"]["items"]
    statuses = []
    for f in items:
        if f.get("status_summary"):
            statuses.append(f["status_summary"]["status"])
        elif f.get("download_task"):
            statuses.append(f["download_task"]["status"])
    assert statuses == ["waiting_user", "downloading", "sync_failed", "library_import_complete"], (
        f"attention priority 排序失败: {statuses}"
    )


def test_flows_endpoint_filter_processing_includes_download_only_adjacent(tmp_path: Path) -> None:
    """filter=processing 必须同时返回 ingest processing 与 download-only downloading/awaiting_sync/paused."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        # p2 范围内: ingest processing, download-only downloading, awaiting_sync, paused
        IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/processing.mkv",
                status="processing",
            )
        )
        DownloadTaskRepository(session).create(
            DownloadTaskCreate(
                title="dl.mkv",
                source="prowlarr",
                save_path="/media/downloads/dl.mkv",
                status="downloading",
            )
        )
        DownloadTaskRepository(session).create(
            DownloadTaskCreate(
                title="await.mkv",
                source="prowlarr",
                save_path="/media/downloads/await.mkv",
                status="awaiting_sync",
            )
        )
        DownloadTaskRepository(session).create(
            DownloadTaskCreate(
                title="paused.mkv",
                source="prowlarr",
                save_path="/media/downloads/paused.mkv",
                status="paused",
            )
        )
        # p1: waiting ingest (不得出现在 processing filter)
        IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/waiting.mkv",
                status="waiting_user",
            )
        )
        # p4: done ingest (不得出现在 processing filter)
        IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/done.mkv",
                status="library_import_complete",
            )
        )
        session.commit()

    client = TestClient(create_app(session_factory=sf))
    body = client.get("/api/v1/flows?filter=processing").json()
    items = body["data"]["items"]
    # 4 条 processing-adjacent: ingest processing + 3 个 download-only
    assert len(items) == 4
    # 全部 4 条必须属于 processing-adjacent 状态集
    for f in items:
        if f.get("status_summary"):
            st = f["status_summary"]["status"]
        elif f.get("download_task"):
            st = f["download_task"]["status"]
        else:
            st = None
        assert st in ("processing", "downloading", "awaiting_sync", "paused"), (
            f"filter=processing 泄漏非 processing-adjacent: {st}"
        )
    assert body["meta"]["total"] == 4


def test_flows_endpoint_pagination_total_and_slice(tmp_path: Path) -> None:
    """分页: meta.total 是筛选后总数, data.items 只含当前页."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        for i in range(5):
            DownloadTaskRepository(session).create(
                DownloadTaskCreate(
                    title=f"orphan_{i}.mkv",
                    source="prowlarr",
                    save_path=f"/media/downloads/orphan_{i}.mkv",
                    status="downloading",
                )
            )
        session.commit()

    client = TestClient(create_app(session_factory=sf))
    p1 = client.get("/api/v1/flows?page=1&page_size=2").json()
    p3 = client.get("/api/v1/flows?page=3&page_size=2").json()
    assert p1["meta"]["total"] == 5
    assert len(p1["data"]["items"]) == 2
    assert p3["meta"]["total"] == 5
    assert len(p3["data"]["items"]) == 1


def test_flows_endpoint_includes_full_download_only_set(tmp_path: Path) -> None:
    """/flows 是统一列表接口, 不得复用 /downloads 的"非终态 + 最近 50"
    截断. 创建 60 个 download-only 终态记录, meta.total 必须 = 60,
    后续页 (page=3 page_size=20) 必须返回第 41-60 条.
    """

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        # 30 个 completed, 30 个 failed — 都是 download-only (无 ingest_task_id).
        # 故意把 updated_at 倒序递增, 验证分页与 attention priority 都生效.
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(60):
            status = "completed" if i < 30 else "failed"
            dl = DownloadTaskRepository(session).create(
                DownloadTaskCreate(
                    title=f"orphan_{i:02d}.mkv",
                    source="prowlarr",
                    save_path=f"/media/downloads/orphan_{i:02d}.mkv",
                    status=status,
                )
            )
            # 直接改 updated_at, 模拟历史积压.
            session.execute(
                DownloadTask.__table__.update()
                .where(DownloadTask.id == dl.id)
                .values(updated_at=base_time + timedelta(hours=i))
            )
        session.commit()

    client = TestClient(create_app(session_factory=sf))
    # meta.total 必须包含全部 60 条, 不得只返回前 50.
    body = client.get("/api/v1/flows?page=1&page_size=20").json()
    assert body["meta"]["total"] == 60
    assert len(body["data"]["items"]) == 20

    # 第 3 页 (page=3, page_size=20) 必须返回第 41-60 条 download-only flow.
    p3 = client.get("/api/v1/flows?page=3&page_size=20").json()
    assert p3["meta"]["total"] == 60
    assert len(p3["data"]["items"]) == 20
    # 全部 page=3 记录必须是 download-only flow (route_target=download_detail).
    for f in p3["data"]["items"]:
        assert f["route_target"] == "download_detail"
        assert f["ingest_task_id"] is None
        assert f["download_task_id"] is not None


def test_flows_endpoint_unknown_filter_returns_error(tmp_path: Path) -> None:
    """未知 filter 必须返回错误 envelope, 不得退化为全量查询."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        DownloadTaskRepository(session).create(
            DownloadTaskCreate(
                title="x.mkv",
                source="prowlarr",
                save_path="/media/downloads/x.mkv",
                status="downloading",
            )
        )
        session.commit()

    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/flows?filter=definitely_not_a_filter")
    body = resp.json()
    assert body["status"] == "error"
    assert body["messages"][0]["code"] == "unknown_filter"
    # 关键: total 不得退化为 1 (即 1 个 download-only flow)
    assert body["meta"] == {} or body["meta"].get("total") in (None, 0)


# ── 3. route_target 路由字段 (Task 1.1, 1.5) ──


def test_flows_endpoint_ingest_flow_id_uses_ingest_prefix(tmp_path: Path) -> None:
    """flow id 必须用 ingest:<id> / download:<id> 前缀, 避免 ID 碰撞."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/i.mkv",
                status="discovered",
            )
        )
        DownloadTaskRepository(session).create(
            DownloadTaskCreate(
                title="d.mkv",
                source="prowlarr",
                save_path="/media/downloads/d.mkv",
                status="downloading",
            )
        )
        session.commit()

    client = TestClient(create_app(session_factory=sf))
    body = client.get("/api/v1/flows").json()
    ids = [f["id"] for f in body["data"]["items"]]
    assert any(i.startswith("ingest:") for i in ids)
    assert any(i.startswith("download:") for i in ids)
    # 不得有纯 UUID 形式
    for i in ids:
        assert ":" in i, f"flow id 必须带前缀: {i}"
