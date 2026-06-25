"""未发布流程删除功能测试"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.repository.database import Base
from media_pilot.repository.models import (
    DownloadTask,
    IngestTask,
    MediaCandidate,
    WriteResult,
)
from media_pilot.repository.repositories import (
    DownloadTaskCreate,
    DownloadTaskRepository,
    IngestTaskCreate,
    IngestTaskRepository,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as s:
        yield s


@pytest.fixture
def app_config():
    from media_pilot.config.settings import AppConfig

    return AppConfig(
        database_dir=Path("/tmp/test-database"),
        downloads_dir=Path("/tmp/test-downloads"),
        watch_dir=Path("/tmp/test-watch"),
        movies_dir=Path("/tmp/test-movies"),
        shows_dir=Path("/tmp/test-shows"),
        workspace_dir=Path("/tmp/test-workspace"),
        trash_dir=Path("/tmp/test-trash"),
        qbittorrent_url="http://localhost:8080",
        qbittorrent_username="admin",
        qbittorrent_password="adminadmin",
        qbittorrent_save_path="/tmp/test-downloads",
    )


# ── 路径守卫测试 ──


class TestPathGuard:
    def test_allows_path_within_allowed_root(self, tmp_path):
        from media_pilot.orchestration.delete_unpublished import _is_safe_to_delete

        root = tmp_path / "downloads"
        root.mkdir()
        target = root / "some-movie.mkv"
        target.write_text("test")

        assert _is_safe_to_delete(target, [root]) is True

    def test_denies_root_directory_itself(self, tmp_path):
        from media_pilot.orchestration.delete_unpublished import _is_safe_to_delete

        root = tmp_path / "downloads"
        root.mkdir()

        assert _is_safe_to_delete(root, [root]) is False

    def test_denies_path_outside_allowed_roots(self, tmp_path):
        from media_pilot.orchestration.delete_unpublished import _is_safe_to_delete

        root = tmp_path / "downloads"
        root.mkdir()
        other = tmp_path / "outside" / "file.txt"
        other.parent.mkdir(parents=True)
        other.write_text("test")

        assert _is_safe_to_delete(other, [root]) is False

    def test_allows_nonexistent_path_within_root(self, tmp_path):
        from media_pilot.orchestration.delete_unpublished import _is_safe_to_delete

        root = tmp_path / "downloads"
        root.mkdir()
        target = root / "nonexistent-file.mkv"

        assert _is_safe_to_delete(target, [root]) is True

    def test_allows_paths_in_second_root(self, tmp_path):
        from media_pilot.orchestration.delete_unpublished import _is_safe_to_delete

        root1 = tmp_path / "downloads"
        root2 = tmp_path / "watch"
        root1.mkdir()
        root2.mkdir()
        target = root2 / "video.mkv"
        target.write_text("test")

        assert _is_safe_to_delete(target, [root1, root2]) is True


# ── 删除 download-only 流程测试 ──


class TestDeleteDownloadOnly:
    def test_returns_false_for_nonexistent_download(
        self, session: Session, app_config
    ):
        from media_pilot.orchestration.delete_unpublished import delete_download_only

        result = delete_download_only(session, "nonexistent-id", app_config)
        assert result.deleted is False
        assert result.task_id == "nonexistent-id"

    def test_deletes_download_without_qb_hash(
        self, session: Session, app_config, monkeypatch
    ):
        from media_pilot.orchestration.delete_unpublished import delete_download_only

        # 创建下载任务（无 qb_hash）
        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Test Movie",
                source="prowlarr",
                save_path="/tmp/test-downloads",
            )
        )
        session.commit()
        task_id = task.id

        result = delete_download_only(session, task_id, app_config)
        assert result.deleted is True
        assert result.qb_deleted is None  # 无 qb_hash，无需 qB 删除
        assert result.qb_error is None

        # 确认数据库记录已删除
        assert session.get(DownloadTask, task_id) is None

    def test_continues_local_cleanup_when_qb_delete_fails(
        self, session: Session, app_config, monkeypatch
    ):
        from media_pilot.orchestration.delete_unpublished import delete_download_only

        monkeypatch.setattr(
            "media_pilot.resource_discovery.qbittorrent_adapter.QBittorrentAdapter",
            lambda cfg: _FakeQBAdapter(delete_state="error"),
        )

        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Test Movie",
                source="prowlarr",
                save_path="/tmp/test-downloads",
                qb_hash="abc123",
            )
        )
        session.commit()
        task_id = task.id

        result = delete_download_only(session, task_id, app_config)
        assert result.deleted is True  # 本地清理继续
        assert result.qb_deleted is False
        assert result.qb_error == "qBittorrent 删除失败，本地清理继续"

    def test_continues_local_cleanup_when_qb_returns_not_found(
        self, session: Session, app_config, monkeypatch
    ):
        """qB 404 (already gone) 应视作幂等成功, 让"qB 已删但 DB 残留"的
        半完成状态可以靠重试收敛. 这次删除应当成功, 且 qb_deleted=True.
        """
        from media_pilot.orchestration.delete_unpublished import delete_download_only

        monkeypatch.setattr(
            "media_pilot.resource_discovery.qbittorrent_adapter.QBittorrentAdapter",
            lambda cfg: _FakeQBAdapter(delete_state="not_found"),
        )

        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Test Movie",
                source="prowlarr",
                save_path="/tmp/test-downloads",
                qb_hash="abc123",
            )
        )
        session.commit()
        task_id = task.id

        result = delete_download_only(session, task_id, app_config)
        assert result.deleted is True
        assert result.qb_deleted is True  # 404 视作幂等成功
        assert result.qb_error is None  # 没有错误

    def test_locked_commit_propagates_operational_error_and_preserves_record(
        self, session: Session, app_config, monkeypatch
    ):
        """commit 抛 locked → safe_commit 必须 rollback + 冒泡, 业务
        记录 (download) 必须仍然存在. 早期 commit_with_retry "rollback
        后再 commit 假装成功" 会在 rollback 后第二次 commit 没东西可写,
        造成"API 返 200 但 DB 实际没删"的伪成功. 此测试必须断言 DB
        最终状态, 不允许伪成功."""
        from media_pilot.orchestration.delete_unpublished import delete_download_only
        from sqlalchemy.exc import OperationalError

        repo = DownloadTaskRepository(session)
        task = repo.create(
            DownloadTaskCreate(
                title="Locked Commit Survivor",
                source="prowlarr",
                save_path="/tmp/test-downloads",
            )
        )
        session.commit()
        task_id = task.id

        class _LockedSession:
            """commit 永远抛 locked, rollback 走真实 session 让 staged
            delete 被撤销. 这就是 "API 返 success 但 DB 没变" 失败模式
            的关键: rollback 必须在 commit 失败时真正发生, 而不是被
            fake 吞掉."""

            def __init__(self, real):
                self._real = real
                self.commit_calls = 0

            def commit(self):
                self.commit_calls += 1
                raise OperationalError(
                    "stmt", {}, Exception("database is locked"),
                )

            def rollback(self):
                return self._real.rollback()

            def __getattr__(self, name):
                return getattr(self._real, name)

        locked = _LockedSession(session)
        with pytest.raises(OperationalError, match="locked"):
            delete_download_only(locked, task_id, app_config)

        # commit 只调 1 次, 不重试.
        assert locked.commit_calls == 1

        # 关键断言: rollback 撤销了 staged delete, 记录必须仍然存在.
        # 早期 commit_with_retry 版本会漏掉这个断言, 静默放行伪成功.
        survivor = session.get(DownloadTask, task_id)
        assert survivor is not None, (
            "delete_download_only 在 commit locked 时必须 rollback 撤销 delete, "
            "否则 API 会返 success 但 DB 实际没删, 留下孤儿下载任务"
        )
        assert survivor.title == "Locked Commit Survivor"

    def test_locked_commit_during_ingest_task_delete_preserves_record(
        self, session: Session, app_config, monkeypatch
    ):
        """同上的 IngestTask 路径: commit locked → rollback + 冒泡,
        task 仍然存在, 业务数据 (MediaCandidate / WriteResult) 仍在.
        旧 commit_with_retry 漏测这条路径."""
        from media_pilot.orchestration.delete_unpublished import delete_ingest_task
        from sqlalchemy.exc import OperationalError

        monkeypatch.setattr(
            "media_pilot.resource_discovery.qbittorrent_adapter.QBittorrentAdapter",
            lambda cfg: _FakeQBAdapter(delete_result=True),
        )

        repo = IngestTaskRepository(session)
        task = repo.create(
            IngestTaskCreate(
                source_path="/tmp/test-downloads/movie.mkv",
                status="discovered",
                current_step="download_scan",
            )
        )
        session.add(MediaCandidate(
            task_id=task.id, source="tmdb", external_id="movie:1", title="T",
        ))
        session.add(WriteResult(task_id=task.id, status="pending"))
        session.commit()
        task_id = task.id

        class _LockedSession:
            def __init__(self, real):
                self._real = real
                self.commit_calls = 0

            def commit(self):
                self.commit_calls += 1
                raise OperationalError(
                    "stmt", {}, Exception("database is locked"),
                )

            def rollback(self):
                return self._real.rollback()

            def __getattr__(self, name):
                return getattr(self._real, name)

        locked = _LockedSession(session)
        with pytest.raises(OperationalError, match="locked"):
            delete_ingest_task(locked, task_id, app_config)

        assert locked.commit_calls == 1
        # 关键断言: rollback 撤销了所有 staged cascade delete.
        assert session.get(IngestTask, task_id) is not None, (
            "delete_ingest_task 在 commit locked 时必须保留 IngestTask, "
            "否则 409 重试后用户会拿到 404"
        )
        # 关联业务数据也必须保留.
        from sqlalchemy import select
        candidates = list(session.scalars(
            select(MediaCandidate).where(MediaCandidate.task_id == task_id)
        ))
        assert len(candidates) == 1
        write_results = list(session.scalars(
            select(WriteResult).where(WriteResult.task_id == task_id)
        ))
        assert len(write_results) == 1


class _FakeQBAdapter:
    """3 态 fake: delete_state ∈ {"deleted", "not_found", "error"}.

    兼容旧调用 ``_FakeQBAdapter(delete_result=True)`` (True → "deleted",
    False → "error"). 旧测试 / 旧 fix 没传新 key 的不会破.
    """

    def __init__(self, delete_state: str = "deleted", *, delete_result=None):
        if delete_result is not None:
            # 旧 bool 约定: True → "deleted", False → "error".
            delete_state = "deleted" if delete_result else "error"
        if delete_state not in ("deleted", "not_found", "error"):
            raise ValueError(f"invalid delete_state: {delete_state}")
        self._delete_state = delete_state

    def delete_torrent(self, qb_hash, *, delete_files=True):
        return self._delete_state


# ── 删除入库任务测试 ──


class TestDeleteIngestTask:
    def test_returns_false_for_nonexistent_task(
        self, session: Session, app_config
    ):
        from media_pilot.orchestration.delete_unpublished import delete_ingest_task

        result = delete_ingest_task(session, "nonexistent-id", app_config)
        assert result.deleted is False

    def test_denies_delete_for_published_task(
        self, session: Session, app_config
    ):
        from media_pilot.orchestration.delete_unpublished import delete_ingest_task

        repo = IngestTaskRepository(session)
        task = repo.create(
            IngestTaskCreate(
                source_path="/tmp/test-downloads/movie.mkv",
                status="library_import_complete",
                current_step="library_import_complete",
            )
        )
        session.commit()

        result = delete_ingest_task(session, task.id, app_config)
        assert result.deleted is False

    def test_denies_delete_for_completed_task(
        self, session: Session, app_config
    ):
        """兼容完成态 completed 也视为已发布，不允许删除"""
        from media_pilot.orchestration.delete_unpublished import delete_ingest_task

        repo = IngestTaskRepository(session)
        task = repo.create(
            IngestTaskCreate(
                source_path="/tmp/test-downloads/movie.mkv",
                status="completed",
                current_step="completed",
            )
        )
        session.commit()

        result = delete_ingest_task(session, task.id, app_config)
        assert result.deleted is False

    def test_deletes_ingest_task_with_cascade(
        self, session: Session, app_config, monkeypatch
    ):
        from media_pilot.orchestration.delete_unpublished import delete_ingest_task

        monkeypatch.setattr(
            "media_pilot.resource_discovery.qbittorrent_adapter.QBittorrentAdapter",
            lambda cfg: _FakeQBAdapter(delete_result=True),
        )

        # 创建入库任务及关联数据
        repo = IngestTaskRepository(session)
        task = repo.create(
            IngestTaskCreate(
                source_path="/tmp/test-downloads/movie.mkv",
                status="discovered",
                current_step="download_scan",
            )
        )
        session.commit()
        task_id = task.id

        # 添加关联数据
        candidate = MediaCandidate(
            task_id=task_id,
            source="tmdb",
            external_id="movie:123",
            title="Test",
        )
        session.add(candidate)

        write_result = WriteResult(
            task_id=task_id,
            status="pending",
        )
        session.add(write_result)
        session.commit()

        result = delete_ingest_task(session, task_id, app_config)
        assert result.deleted is True

        # 确认主任务已删除
        assert session.get(IngestTask, task_id) is None

        # 确认关联数据已级联删除
        assert session.get(MediaCandidate, candidate.id) is None
        assert session.get(WriteResult, write_result.id) is None

    def test_deletes_associated_download_task(
        self, session: Session, app_config, monkeypatch
    ):
        from media_pilot.orchestration.delete_unpublished import delete_ingest_task

        monkeypatch.setattr(
            "media_pilot.resource_discovery.qbittorrent_adapter.QBittorrentAdapter",
            lambda cfg: _FakeQBAdapter(delete_result=True),
        )

        # 创建下载任务
        dl_repo = DownloadTaskRepository(session)
        dl_task = dl_repo.create(
            DownloadTaskCreate(
                title="Linked Download",
                source="prowlarr",
                save_path="/tmp/test-downloads",
                qb_hash="linked123",
            )
        )

        # 创建关联的入库任务
        repo = IngestTaskRepository(session)
        task = repo.create(
            IngestTaskCreate(
                source_path="/tmp/test-downloads/video.mkv",
                status="discovered",
                current_step="download_scan",
                source_download_task_id=dl_task.id,
            )
        )
        dl_repo.bind_ingest_task(dl_task, task.id)
        session.commit()

        result = delete_ingest_task(session, task.id, app_config)
        assert result.deleted is True
        assert result.qb_deleted is True

        # 两个任务都应删除
        assert session.get(IngestTask, task.id) is None
        assert session.get(DownloadTask, dl_task.id) is None
