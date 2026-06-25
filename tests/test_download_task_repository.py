"""DownloadTask 模型与 Repository 单元测试"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.repository.database import Base
from media_pilot.repository.repositories import DownloadTaskCreate, DownloadTaskRepository


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as s:
        yield s


class TestDownloadTaskModel:
    def test_create_minimal(self, session: Session) -> None:
        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Test Movie 1080p",
                source="prowlarr",
                save_path="/data/downloads/test",
                indexer="TestIndexer",
            )
        )
        assert task.id is not None
        assert task.title == "Test Movie 1080p"
        assert task.source == "prowlarr"
        assert task.save_path == "/data/downloads/test"
        assert task.status == "submitted"
        assert task.progress == 0.0
        assert task.seeders == 0
        assert task.ingest_task_id is None

    def test_create_with_metadata_preselection(self, session: Session) -> None:
        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Test Movie",
                source="prowlarr",
                save_path="/data/downloads/test",
                preselected_metadata_profile="tmdb_movie",
                preselected_metadata_provider="tmdb",
                preselected_metadata_external_id="12345",
            )
        )
        assert task.preselected_metadata_profile == "tmdb_movie"
        assert task.preselected_metadata_provider == "tmdb"
        assert task.preselected_metadata_external_id == "12345"


class TestDownloadTaskRepository:
    def test_get_by_id(self, session: Session) -> None:
        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Get Test", source="prowlarr", save_path="/tmp/dl"
            )
        )
        found = repo.get(task.id)
        assert found is not None
        assert found.title == "Get Test"

    def test_get_by_qb_hash(self, session: Session) -> None:
        repo = DownloadTaskRepository(session)
        repo.create(
            DownloadTaskCreate(
                title="Hash Test",
                source="prowlarr",
                save_path="/tmp/dl",
                qb_hash="abc123def456",
            )
        )
        found = repo.get_by_qb_hash("abc123def456")
        assert found is not None
        assert found.title == "Hash Test"

        not_found = repo.get_by_qb_hash("nonexistent")
        assert not_found is None

    def test_list_non_terminal(self, session: Session) -> None:
        repo = DownloadTaskRepository(session)
        repo.create(
            DownloadTaskCreate(
                title="Downloading", source="prowlarr", save_path="/tmp/a"
            )
        )
        repo.create(
            DownloadTaskCreate(
                title="Submitted", source="prowlarr", save_path="/tmp/b",
                status="submitted",
            )
        )
        # Create a completed one (should not appear)
        repo.create(
            DownloadTaskCreate(
                title="Completed", source="prowlarr", save_path="/tmp/c",
                status="completed",
            )
        )
        # Create a failed one (should not appear)
        repo.create(
            DownloadTaskCreate(
                title="Failed", source="prowlarr", save_path="/tmp/e",
                status="failed",
            )
        )

        non_terminal = repo.list_non_terminal()
        titles = {t.title for t in non_terminal}
        assert "Downloading" in titles
        assert "Submitted" in titles
        assert "Completed" not in titles
        assert "Failed" not in titles

    def test_sync_failed_is_not_terminal(self, session: Session) -> None:
        """sync_failed 是可恢复状态，必须出现在非终态列表中以继续同步。"""
        repo = DownloadTaskRepository(session)
        repo.create(DownloadTaskCreate(
            title="SyncFailedRecoverable", source="prowlarr", save_path="/tmp/x",
            status="sync_failed",
        ))
        session.commit()

        non_terminal = repo.list_non_terminal()
        titles = {t.title for t in non_terminal}
        assert "SyncFailedRecoverable" in titles, (
            "sync_failed 应出现在非终态列表中，以便后续同步周期继续接管"
        )

    def test_awaiting_sync_is_not_terminal(self, session: Session) -> None:
        """awaiting_sync 不是终态，应继续参与同步补齐 hash。"""
        repo = DownloadTaskRepository(session)
        repo.create(DownloadTaskCreate(
            title="Awaiting", source="prowlarr", save_path="/tmp/x",
            status="awaiting_sync",
        ))
        session.commit()

        non_terminal = repo.list_non_terminal()
        titles = {t.title for t in non_terminal}
        assert "Awaiting" in titles, (
            "awaiting_sync 应出现在非终态列表中，以便后续同步补齐 hash"
        )

    def test_update_sync_status_partial(self, session: Session) -> None:
        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Sync Test", source="prowlarr", save_path="/tmp/dl"
            )
        )
        updated = repo.update_sync_status(
            task,
            progress=0.75,
            download_speed_bytes_per_second=1024000,
            seeders=12,
            qb_state="downloading",
        )
        assert updated.progress == 0.75
        assert updated.download_speed_bytes_per_second == 1024000
        assert updated.seeders == 12
        assert updated.qb_state == "downloading"
        # Unchanged fields stay
        assert updated.status == "submitted"

    def test_update_sync_status_hash_and_path(self, session: Session) -> None:
        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Hash Fill", source="prowlarr", save_path="/tmp/dl"
            )
        )
        updated = repo.update_sync_status(
            task,
            qb_hash="hash789",
            qb_name="Test.Torrent.Name",
            content_path="/data/downloads/Test.Movie.1080p",
            status="downloading",
        )
        assert updated.qb_hash == "hash789"
        assert updated.qb_name == "Test.Torrent.Name"
        assert updated.content_path == "/data/downloads/Test.Movie.1080p"
        assert updated.status == "downloading"

    def test_update_sync_status_error(self, session: Session) -> None:
        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Fail Test", source="prowlarr", save_path="/tmp/dl"
            )
        )
        updated = repo.update_sync_status(
            task,
            status="failed",
            error_message="qBittorrent unreachable",
        )
        assert updated.status == "failed"
        assert updated.error_message == "qBittorrent unreachable"

    def test_bind_ingest_task(self, session: Session) -> None:
        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Bind Test", source="prowlarr", save_path="/tmp/dl"
            )
        )
        assert task.ingest_task_id is None
        updated = repo.bind_ingest_task(task, "ingest-123")
        assert updated.ingest_task_id == "ingest-123"

    def test_created_at_set(self, session: Session) -> None:
        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Time Test", source="prowlarr", save_path="/tmp/dl"
            )
        )
        assert task.created_at is not None
        assert task.updated_at is not None



class TestListOccupiedPaths:
    """list_occupied_paths — 只占用具体内容路径，不占用 save_path 根目录"""

    def test_only_includes_content_paths(self, session: Session) -> None:
        """save_path 是统一的下载根目录，不应出现在 occupied 中；
        只有 torrent 顶层内容路径才被视为占用。"""
        repo = DownloadTaskRepository(session)
        repo.create(DownloadTaskCreate(
            title="Downloading", source="prowlarr", save_path="/data/downloads",
            status="submitted",
        ))
        task = repo.list_non_terminal()[0]
        repo.update_sync_status(
            task,
            content_path="/data/downloads/Movie.2024",
            status="downloading",
        )
        session.commit()

        occupied = repo.list_occupied_paths()
        from pathlib import Path
        # save_path 根目录不得占用
        assert Path("/data/downloads") not in occupied
        # 具体内容路径占用
        assert Path("/data/downloads/Movie.2024") in occupied

    def test_save_path_root_does_not_block_external(self, session: Session) -> None:
        """即使有活跃下载，外部输入（如 PikPak/手动拷贝）不应被阻塞。"""
        repo = DownloadTaskRepository(session)
        repo.create(DownloadTaskCreate(
            title="Active DL", source="prowlarr", save_path="/data/downloads",
            status="submitted", qb_hash="abc123",
        ))
        session.commit()

        occupied = repo.list_occupied_paths()
        from pathlib import Path
        # 根目录本身不在 occupied 中
        assert Path("/data/downloads") not in occupied
        # 外部文件路径不被占用
        assert Path("/data/downloads/external_file.mkv") not in occupied

    def test_content_path_directory_occupied(self, session: Session) -> None:
        """目录类型 torrent 的 content_path 目录被占用。"""
        repo = DownloadTaskRepository(session)
        repo.create(DownloadTaskCreate(
            title="TV Show", source="prowlarr", save_path="/data/downloads",
            status="downloading", qb_hash="hash2",
        ))
        task = repo.list_non_terminal()[0]
        repo.update_sync_status(
            task,
            content_path="/data/downloads/TV.Show.S01",
            status="downloading",
        )
        session.commit()

        occupied = repo.list_occupied_paths()
        from pathlib import Path
        assert Path("/data/downloads/TV.Show.S01") in occupied

    def test_excludes_terminal_tasks(self, session: Session) -> None:
        repo = DownloadTaskRepository(session)
        repo.create(DownloadTaskCreate(
            title="Completed", source="prowlarr", save_path="/dl/done",
            status="completed",
        ))
        repo.create(DownloadTaskCreate(
            title="Failed", source="prowlarr", save_path="/dl/fail",
            status="failed",
        ))
        session.commit()

        occupied = repo.list_occupied_paths()
        assert len(occupied) == 0

    def test_includes_sync_failed_tasks(self, session: Session) -> None:
        """sync_failed 不再是终态，下载任务仍在磁盘占用空间，应保护路径"""
        repo = DownloadTaskRepository(session)
        repo.create(DownloadTaskCreate(
            title="SyncFailed", source="prowlarr", save_path="/dl/dead",
            status="sync_failed",
        ))
        session.commit()

        occupied = repo.list_occupied_paths()
        # sync_failed 任务有 content_path 时仍占用路径
        # content_path 为 None 时通过 save_path + title 预估
        assert len(occupied) > 0

    def test_reserved_path_when_no_content_path(self, session: Session) -> None:
        """content_path 为空时，用 save_path + title 预估占用路径"""
        repo = DownloadTaskRepository(session)
        repo.create(DownloadTaskCreate(
            title="Weathering.With.You.2019", source="prowlarr",
            save_path="/data/downloads", status="submitted",
        ))
        session.commit()

        occupied = repo.list_occupied_paths()
        from pathlib import Path
        # 目录级预留（覆盖目录 torrent）
        assert Path("/data/downloads/Weathering.With.You.2019") in occupied
        # 全量媒体扩展名预留（覆盖单文件 torrent）
        assert Path("/data/downloads/Weathering.With.You.2019.mkv") in occupied
        assert Path("/data/downloads/Weathering.With.You.2019.mp4") in occupied
        assert Path("/data/downloads/Weathering.With.You.2019.ts") in occupied
        assert Path("/data/downloads/Weathering.With.You.2019.m2ts") in occupied

    def test_no_reserved_path_for_terminal(self, session: Session) -> None:
        """终态任务即使无 content_path 也不占用"""
        repo = DownloadTaskRepository(session)
        repo.create(DownloadTaskCreate(
            title="Completed.No.Path", source="prowlarr",
            save_path="/data/downloads", status="completed",
        ))
        session.commit()

        occupied = repo.list_occupied_paths()
        assert len(occupied) == 0
