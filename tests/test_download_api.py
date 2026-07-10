"""DownloadTask API DTO + mapper + 端点测试"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.api.task_dtos import DownloadTaskSummary
from media_pilot.repository.database import Base
from media_pilot.repository.repositories import (
    DownloadTaskCreate,
    DownloadTaskRepository,
    IngestTaskCreate,
    IngestTaskRepository,
)
from tests.auth_helpers import AuthenticatedTestClient as TestClient


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as s:
        yield s


class TestDownloadTaskSummaryDto:
    def test_minimal_fields(self) -> None:
        dto = DownloadTaskSummary(
            id="dt-1",
            title="Test Movie",
            source="prowlarr",
            save_path="/dl/test",
            progress=0.0,
            seeders=0,
            leechers=0,
            status="submitted",
            created_at="2025-01-01T00:00:00Z",  # type: ignore[arg-type]
            updated_at="2025-01-01T00:00:00Z",  # type: ignore[arg-type]
        )
        assert dto.id == "dt-1"
        assert dto.title == "Test Movie"
        assert dto.qb_hash is None
        assert dto.ingest_task_id is None


class TestDownloadTaskMapper:
    """map_download_task_to_summary + map_to_task_summaries 集成"""

    def test_maps_basic_download_task(self, session: Session) -> None:
        from media_pilot.api.task_mapper import map_download_task_to_summary

        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Weathering With You",
                source="prowlarr",
                save_path="/dl/test",
                qb_hash="abc123",
            )
        )
        session.commit()

        dto = map_download_task_to_summary(task)
        assert dto.id == task.id
        assert dto.title == "Weathering With You"
        assert dto.source == "prowlarr"
        assert dto.qb_hash == "abc123"
        assert dto.status == "submitted"
        assert dto.progress == 0.0
        assert dto.seeders == 0
        assert dto.ingest_task_id is None

    def test_maps_downloading_task(self, session: Session) -> None:
        from media_pilot.api.task_mapper import map_download_task_to_summary

        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Active Download",
                source="prowlarr",
                save_path="/dl/active",
                qb_hash="def456",
            )
        )
        repo.update_sync_status(
            task,
            progress=0.72,
            download_speed_bytes_per_second=5242880,
            upload_speed_bytes_per_second=102400,
            seeders=15,
            leechers=3,
            connections=42,
            qb_state="downloading",
            qb_name="Active.Download.mkv",
            content_path="/dl/active/Active.Download.mkv",
            status="downloading",
        )
        session.commit()

        dto = map_download_task_to_summary(task)
        assert dto.status == "downloading"
        assert dto.progress == 0.72
        assert dto.download_speed_bytes_per_second == 5242880
        assert dto.seeders == 15
        assert dto.connections == 42
        assert dto.qb_state == "downloading"

    def test_maps_completed_with_ingest_task(self, session: Session) -> None:
        from media_pilot.api.task_mapper import map_download_task_to_summary

        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Done Movie",
                source="prowlarr",
                save_path="/dl/done",
                qb_hash="ghi789",
            )
        )
        repo.update_sync_status(task, status="completed")
        repo.bind_ingest_task(task, "ingest-linked")
        session.commit()

        dto = map_download_task_to_summary(task)
        assert dto.status == "completed"
        assert dto.ingest_task_id == "ingest-linked"

    def test_ingest_task_summary_includes_download_info(self, session: Session) -> None:
        """有 source_download_task_id 的 IngestTask → TaskSummary 包含 download_task"""
        from media_pilot.api.task_mapper import map_to_task_summaries

        # 创建下载任务
        dl_repo = DownloadTaskRepository(session)
        dl_task = dl_repo.create(
            DownloadTaskCreate(
                title="Linked Movie",
                source="prowlarr",
                save_path="/dl/linked",
                qb_hash="lnk456",
            )
        )
        dl_repo.update_sync_status(
            dl_task,
            progress=1.0,
            qb_state="uploading",
            status="completed",
        )

        # 创建关联入库任务
        ingest_repo = IngestTaskRepository(session)
        ingest_task = ingest_repo.create(
            IngestTaskCreate(
                source_path="/dl/linked/Linked.Movie.mkv",
                status="discovered",
                current_step="download_scan",
                source_download_task_id=dl_task.id,
            )
        )
        dl_repo.bind_ingest_task(dl_task, ingest_task.id)
        session.commit()

        summaries = map_to_task_summaries(session, [ingest_task])
        assert len(summaries) == 1
        ts = summaries[0]
        assert ts.download_task is not None
        assert ts.download_task.id == dl_task.id
        assert ts.download_task.title == "Linked Movie"
        assert ts.download_task.status == "completed"
        assert ts.download_task.progress == 1.0
        assert ts.download_task.ingest_task_id == ingest_task.id

    def test_ingest_task_without_download_link(self, session: Session) -> None:
        """无 source_download_task_id → download_task 为 None"""
        from media_pilot.api.task_mapper import map_to_task_summaries

        ingest_repo = IngestTaskRepository(session)
        task = ingest_repo.create(
            IngestTaskCreate(
                source_path="/dl/standalone.mkv",
                status="discovered",
                current_step="download_scan",
            )
        )
        session.commit()

        summaries = map_to_task_summaries(session, [task])
        assert len(summaries) == 1
        assert summaries[0].download_task is None


# ── 下载端点 download-only 边界与 resume 不乐观 ──


class TestDownloadEndpoints:
    """GET/POST /downloads/{id} 端点的合同行为"""

    @staticmethod
    def _make_config(database_dir: Path) -> AppConfig:
        from media_pilot.config import AppConfig
        return AppConfig(
            downloads_dir=Path("/media/downloads"),
            watch_dir=Path("/media/watch"),
            workspace_dir=Path("/media/workspace"),
            movies_dir=Path("/media/library/movies"),
            shows_dir=Path("/media/library/shows"),
            database_dir=database_dir,
        )

    @staticmethod
    def _make_session_factory(tmp_path: Path):
        from media_pilot.repository.database import create_session_factory, initialize_database
        config = TestDownloadEndpoints._make_config(tmp_path)
        initialize_database(config)
        return create_session_factory(config)

    def test_detail_rejects_linked_ingest_task(self, tmp_path: Path) -> None:
        from media_pilot.app import create_app

        config = self._make_config(tmp_path)
        sf = self._make_session_factory(tmp_path)
        with sf() as s:
            dl_repo = DownloadTaskRepository(s)
            dl = dl_repo.create(DownloadTaskCreate(
                title="Linked DL", source="prowlarr", save_path="/dl/test",
            ))
            dl_repo.bind_ingest_task(dl, "ingest-linked")
            s.commit()
            dl_id = dl.id

        client = TestClient(create_app(config=config, session_factory=sf))
        resp = client.get(f"/api/v1/downloads/{dl_id}")
        assert resp.status_code == 409

    def test_pause_rejects_linked_ingest_task(self, tmp_path: Path) -> None:
        from media_pilot.app import create_app

        config = self._make_config(tmp_path)
        sf = self._make_session_factory(tmp_path)
        with sf() as s:
            dl_repo = DownloadTaskRepository(s)
            dl = dl_repo.create(DownloadTaskCreate(
                title="Linked DL", source="prowlarr", save_path="/dl/test",
                qb_hash="abc123",
            ))
            dl_repo.bind_ingest_task(dl, "ingest-linked")
            s.commit()
            dl_id = dl.id

        client = TestClient(create_app(config=config, session_factory=sf))
        resp = client.post(f"/api/v1/downloads/{dl_id}/pause")
        assert resp.status_code == 409

    def test_resume_rejects_linked_ingest_task(self, tmp_path: Path) -> None:
        from media_pilot.app import create_app

        config = self._make_config(tmp_path)
        sf = self._make_session_factory(tmp_path)
        with sf() as s:
            dl_repo = DownloadTaskRepository(s)
            dl = dl_repo.create(DownloadTaskCreate(
                title="Linked DL", source="prowlarr", save_path="/dl/test",
                qb_hash="abc123",
            ))
            dl_repo.bind_ingest_task(dl, "ingest-linked")
            s.commit()
            dl_id = dl.id

        client = TestClient(create_app(config=config, session_factory=sf))
        resp = client.post(f"/api/v1/downloads/{dl_id}/resume")
        assert resp.status_code == 409

    def test_refresh_rejects_linked_ingest_task(self, tmp_path: Path) -> None:
        from media_pilot.app import create_app

        config = self._make_config(tmp_path)
        sf = self._make_session_factory(tmp_path)
        with sf() as s:
            dl_repo = DownloadTaskRepository(s)
            dl = dl_repo.create(DownloadTaskCreate(
                title="Linked DL", source="prowlarr", save_path="/dl/test",
            ))
            dl_repo.bind_ingest_task(dl, "ingest-linked")
            s.commit()
            dl_id = dl.id

        client = TestClient(create_app(config=config, session_factory=sf))
        resp = client.post(f"/api/v1/downloads/{dl_id}/refresh")
        assert resp.status_code == 409

    def test_resume_sets_awaiting_sync_not_downloading(self, tmp_path: Path, monkeypatch) -> None:
        """resume 成功后状态应为 awaiting_sync，不乐观伪造 downloading"""
        from media_pilot.app import create_app
        from media_pilot.resource_discovery.qbittorrent_adapter import QBittorrentAdapter

        config = self._make_config(tmp_path)
        sf = self._make_session_factory(tmp_path)
        with sf() as s:
            dl_repo = DownloadTaskRepository(s)
            dl = dl_repo.create(DownloadTaskCreate(
                title="Paused DL", source="prowlarr", save_path="/dl/test",
                qb_hash="abc123",
            ))
            dl_repo.update_sync_status(dl, status="paused")
            s.commit()
            dl_id = dl.id

        # mock qB adapter 让 resume 成功
        def mock_resume(self, qb_hash):
            return True

        monkeypatch.setattr(QBittorrentAdapter, "resume_torrent", mock_resume)

        client = TestClient(create_app(config=config, session_factory=sf))
        resp = client.post(f"/api/v1/downloads/{dl_id}/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["status"] == "awaiting_sync"

        # 验证 DB 中状态不是 downloading
        from media_pilot.repository.models import DownloadTask as DT
        with sf() as s:
            dl2 = s.get(DT, dl_id)
            assert dl2 is not None
            assert dl2.status == "awaiting_sync"

    def test_pause_sets_paused_status(self, tmp_path: Path, monkeypatch) -> None:
        from media_pilot.app import create_app
        from media_pilot.resource_discovery.qbittorrent_adapter import QBittorrentAdapter

        config = self._make_config(tmp_path)
        sf = self._make_session_factory(tmp_path)
        with sf() as s:
            dl_repo = DownloadTaskRepository(s)
            dl = dl_repo.create(DownloadTaskCreate(
                title="Active DL", source="prowlarr", save_path="/dl/test",
                qb_hash="abc123",
            ))
            dl_repo.update_sync_status(dl, status="downloading")
            s.commit()
            dl_id = dl.id

        def mock_pause(self, qb_hash):
            return True

        monkeypatch.setattr(QBittorrentAdapter, "pause_torrent", mock_pause)

        client = TestClient(create_app(config=config, session_factory=sf))
        resp = client.post(f"/api/v1/downloads/{dl_id}/pause")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["status"] == "paused"

    def test_detail_allows_download_only(self, tmp_path: Path) -> None:
        """无 ingest_task_id 的 download-only 流程可以正常查看详情"""
        from media_pilot.app import create_app

        sf = self._make_session_factory(tmp_path)
        with sf() as s:
            dl_repo = DownloadTaskRepository(s)
            dl = dl_repo.create(DownloadTaskCreate(
                title="Download Only", source="prowlarr", save_path="/dl/test",
                qb_hash="abc123",
            ))
            dl_repo.update_sync_status(dl, progress=0.5, status="downloading")
            s.commit()
            dl_id = dl.id

        client = TestClient(create_app(session_factory=sf))
        resp = client.get(f"/api/v1/downloads/{dl_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["id"] == dl_id
        assert data["data"]["title"] == "Download Only"
        assert data["data"]["status"] == "downloading"

    def test_detail_returns_not_found(self, tmp_path: Path) -> None:
        from media_pilot.app import create_app

        sf = self._make_session_factory(tmp_path)
        client = TestClient(create_app(session_factory=sf))
        resp = client.get("/api/v1/downloads/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert data["messages"][0]["code"] == "not_found"
