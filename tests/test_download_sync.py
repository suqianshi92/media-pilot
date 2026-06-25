"""DownloadSyncService 单元测试 — 用 mock QBittorrentAdapter + in-memory SQLite"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.config.settings import AppConfig
from media_pilot.repository.database import Base
from media_pilot.repository.repositories import DownloadTaskCreate, DownloadTaskRepository
from media_pilot.resource_discovery.types import QBTorrentInfo

# ── stub adapter ──

class StubQBittorrentAdapter:
    """可配置返回值的 mock adapter，不发起真实 HTTP 请求"""

    def __init__(self) -> None:
        self._infos: dict[str, list[QBTorrentInfo]] = {}
        self._raise_on_call: Exception | None = None
        self.calls: list[list[str]] = []
        self._all_torrents: list[QBTorrentInfo] = []
        self._tagged_torrents: dict[str, list[QBTorrentInfo]] = {}

    def set_info(self, qb_hash: str, info: QBTorrentInfo) -> None:
        self._infos[qb_hash] = [info]

    def set_infos(self, qb_hash: str, infos: list[QBTorrentInfo]) -> None:
        self._infos[qb_hash] = infos

    def set_all_torrents(self, infos: list[QBTorrentInfo]) -> None:
        """配置 get_torrent_info([]) 返回的全部 torrent 列表（hash 补齐用）"""
        self._all_torrents = list(infos)

    def set_tagged_torrents(self, tag: str, infos: list[QBTorrentInfo]) -> None:
        """配置按标签回查的返回结果"""
        self._tagged_torrents[tag] = list(infos)

    def raise_on_call(self, exc: Exception) -> None:
        self._raise_on_call = exc

    def get_torrent_info(
        self, hashes: list[str], *, tag: str | None = None
    ) -> list[QBTorrentInfo]:
        self.calls.append(list(hashes))
        if self._raise_on_call is not None:
            raise self._raise_on_call
        # 标签查询：有 tag 时只返回匹配的，不匹配返回空
        if tag is not None:
            return list(self._tagged_torrents.get(tag, []))
        if not hashes:
            return list(self._all_torrents)
        result: list[QBTorrentInfo] = []
        for h in hashes:
            result.extend(self._infos.get(h, []))
        return result


# ── helpers ──

def _make_config() -> AppConfig:
    from pathlib import Path
    return AppConfig(
        downloads_dir=Path("/tmp/dl"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp/ws"),
        movies_dir=Path("/tmp/movies"),
        shows_dir=Path("/tmp/shows"),
        database_dir=Path("/tmp/db"),
        qbittorrent_url="http://qb:8080",
        qbittorrent_username="admin",
        qbittorrent_password="pass",
        qbittorrent_save_path="/data/downloads",
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
    )


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as s:
        yield s


def _create_task(repo: DownloadTaskRepository, **kwargs) -> str:
    defaults: dict = {
        "title": "Test.Movie.1080p",
        "source": "prowlarr",
        "save_path": "/data/downloads",
    }
    defaults.update(kwargs)
    task = repo.create(DownloadTaskCreate(**defaults))
    return task.id


# ── tests ──

class TestDownloadSyncService:
    """覆盖正常同步、hash 补齐、hash 不存在、API 失败、无 hash、跳过终态"""

    def test_sync_updates_progress_and_state(self, session: Session) -> None:
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="abc123", status="submitted")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        stub.set_info("abc123", QBTorrentInfo(
            hash="abc123",
            name="Test.Movie.1080p.mkv",
            save_path="/data/downloads",
            content_path="/data/downloads/Test.Movie.1080p.mkv",
            progress=0.65,
            dlspeed=5242880,
            upspeed=102400,
            num_seeds=15,
            num_leechs=3,
            connections=42,
            state="downloading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

        result = svc.sync_once(SessionLocal)
        assert result.synced == 1
        assert result.failed == 0
        assert result.skipped == 0

        # 刷新后验证
        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.progress == 0.65
        assert task.download_speed_bytes_per_second == 5242880
        assert task.upload_speed_bytes_per_second == 102400
        assert task.seeders == 15
        assert task.leechers == 3
        assert task.connections == 42
        assert task.qb_state == "downloading"
        assert task.qb_name == "Test.Movie.1080p.mkv"
        assert task.content_path == "/data/downloads/Test.Movie.1080p.mkv"
        assert task.status == "downloading"

    def test_sync_hash_fill_in_content_path(self, session: Session) -> None:
        """hash 补齐：qb_name 和 content_path 首次从 qB 获取"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash789", status="submitted")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        stub.set_info("hash789", QBTorrentInfo(
            hash="hash789",
            name="My.Movie.2024.2160p.mkv",
            save_path="/data/downloads",
            content_path="/data/downloads/My.Movie.2024.2160p",
            progress=0.1,
            dlspeed=1024000,
            state="downloading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        svc.sync_once(SessionLocal)

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.qb_name == "My.Movie.2024.2160p.mkv"
        assert task.content_path == "/data/downloads/My.Movie.2024.2160p"

    def test_qb_hash_not_found_marks_sync_failed(self, session: Session) -> None:
        """qB 中找不到 hash → 标记 sync_failed"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="deadbeef", status="submitted")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        # 不设置任何 info → get_torrent_info 返回空列表

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

        result = svc.sync_once(SessionLocal)
        assert result.failed == 1
        assert result.synced == 0

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.status == "sync_failed"
        assert "找不到 hash" in (task.error_message or "")

    def test_api_failure_marks_sync_failed(self, session: Session) -> None:
        """API 调用异常 → 标记 sync_failed"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="abc456", status="submitted")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        stub.raise_on_call(ConnectionError("connection refused"))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

        result = svc.sync_once(SessionLocal)
        assert result.failed == 1

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.status == "sync_failed"
        assert "API" in (task.error_message or "")

    def test_no_qb_hash_backfill_fails_when_no_match(self, session: Session) -> None:
        """无 qb_hash 的任务 → 尝试 backfill 失败 → 跳过，不标记为终态"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash=None, status="submitted")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        # 不配置任何 torrent → backfill 失败

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

        result = svc.sync_once(SessionLocal)
        assert result.skipped == 1
        assert result.synced == 0
        assert result.failed == 0

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        # 标记为 awaiting_sync（非终态，下次同步会重试 backfill）
        assert task.status == "awaiting_sync"

    def test_hash_backfill_by_name(self, session: Session) -> None:
        """hash 补齐：通过名称匹配从 qB 回查并补齐 hash，然后继续正常同步"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(
            repo, qb_hash=None, status="submitted",
            title="Test.Movie.1080p",
        )
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        # 模拟 qB 中有匹配的 torrent
        stub.set_all_torrents([
            QBTorrentInfo(
                hash="backfilled_hash_001",
                name="Test.Movie.1080p",
                save_path="/data/downloads",
                content_path="/data/downloads/Test.Movie.1080p",
                progress=0.45,
                dlspeed=1024000,
                upspeed=0,
                num_seeds=10,
                num_leechs=2,
                num_complete=5,
                connections=8,
                state="downloading",
            ),
        ])
        # 也要配置按 hash 查询的返回值（backfill 后继续同步）
        stub.set_info("backfilled_hash_001", QBTorrentInfo(
            hash="backfilled_hash_001",
            name="Test.Movie.1080p",
            save_path="/data/downloads",
            content_path="/data/downloads/Test.Movie.1080p",
            progress=0.45,
            dlspeed=1024000,
            upspeed=0,
            num_seeds=10,
            num_leechs=2,
            num_complete=5,
            connections=8,
            state="downloading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

        result = svc.sync_once(SessionLocal)
        assert result.synced == 1
        assert result.skipped == 0

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.qb_hash == "backfilled_hash_001"
        assert task.status == "downloading"
        assert task.progress == 0.45

    def test_hash_backfill_normalized_match(self, session: Session) -> None:
        """标准化匹配：忽略大小写、分隔符、扩展名差异"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        # task 标题是空格分隔的普通格式
        tid = _create_task(repo, qb_hash=None, status="submitted",
                           title="Weathering With You 2019 1080p")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        # qB 里是点分隔、带扩展名、大小写混合的格式
        stub.set_all_torrents([QBTorrentInfo(
            hash="hash_normalized",
            name="Weathering.With.You.2019.1080p.BluRay.x264.mkv",
            save_path="/data/downloads",
            content_path="/data/downloads/Weathering.With.You.2019.1080p.mkv",
            progress=0.3, dlspeed=1024, upspeed=0,
            num_seeds=5, num_leechs=1, num_complete=2,
            connections=3, state="downloading",
        )])
        stub.set_info("hash_normalized", QBTorrentInfo(
            hash="hash_normalized",
            name="Weathering.With.You.2019.1080p.BluRay.x264.mkv",
            save_path="/data/downloads",
            content_path="/data/downloads/Weathering.With.You.2019.1080p.mkv",
            progress=0.3, dlspeed=1024, upspeed=0,
            num_seeds=5, num_leechs=1, num_complete=2,
            connections=3, state="downloading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        result = svc.sync_once(SessionLocal)
        assert result.synced == 1

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.qb_hash == "hash_normalized"

    def test_hash_backfill_no_false_normalized_match(self, session: Session) -> None:
        """标准化后不匹配的不应误配"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash=None, status="submitted",
                           title="Totally Different Title")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        stub.set_all_torrents([QBTorrentInfo(
            hash="hash_other",
            name="Something.Else.2020.mkv",
            save_path="/data/downloads",
            content_path="/data/downloads/Something.Else.2020.mkv",
            progress=0.1, dlspeed=0, upspeed=0,
            num_seeds=0, num_leechs=0, num_complete=0,
            connections=0, state="downloading",
        )])

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        result = svc.sync_once(SessionLocal)
        assert result.skipped == 1
        assert result.synced == 0

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.qb_hash is None

    def test_skips_terminal_tasks(self, session: Session) -> None:
        """终态任务（completed/failed）不会被同步"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        _create_task(repo, title="Done", qb_hash="h1", status="completed")
        _create_task(repo, title="Failed", qb_hash="h2", status="failed")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

        result = svc.sync_once(SessionLocal)
        # 终态任务不在 list_non_terminal 中，因此 stub 也不应被调用
        assert result.synced == 0
        assert result.failed == 0
        assert result.skipped == 0
        assert len(stub.calls) == 0

    def test_sync_failed_task_is_retried_on_next_sync(self, session: Session) -> None:
        """sync_failed 任务继续参与同步循环，qB 恢复后自动回到 downloading 并清空旧错误"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash_was_dead", status="sync_failed")
        # 手动设置旧错误信息
        task = repo.get(tid)
        repo.update_sync_status(task, error_message="qBittorrent 不可达")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        # 模拟 qB 已恢复
        stub.set_info("hash_was_dead", QBTorrentInfo(
            hash="hash_was_dead",
            name="Recovered.Movie.mkv",
            save_path="/data/downloads",
            content_path="/data/downloads/Recovered.Movie.mkv",
            progress=0.45,
            dlspeed=1024000,
            upspeed=0,
            num_seeds=5,
            num_leechs=1,
            num_complete=2,
            connections=3,
            state="downloading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        result = svc.sync_once(SessionLocal)

        assert result.synced == 1
        assert result.failed == 0

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.status == "downloading"
        assert task.progress == 0.45
        assert task.error_message is None

    def test_hash_backfill_multimatch_no_exact_stays_awaiting_sync(
        self, session: Session,
    ) -> None:
        """多命中但无精确匹配 → 保守保留 awaiting_sync，不误绑。

        qB 中同时存在 REPACK 和 DIRECTOR'S CUT 版本时，标准化后
        任务标题都是子串匹配。无法确定哪个是正确的绑定目标，
        应保持 awaiting_sync 等待下一轮重试。
        """
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(
            repo, qb_hash=None, status="submitted",
            title="Movie.2024.1080p",
        )
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        # 多个候选都匹配但无精确匹配
        stub.set_all_torrents([
            QBTorrentInfo(
                hash="hash_repack",
                name="Movie.2024.1080p.REPACK.mkv",
                save_path="/data/downloads",
                content_path="/data/downloads/Movie.2024.1080p.REPACK.mkv",
                progress=0.5, dlspeed=1024, upspeed=0,
                num_seeds=5, num_leechs=1, num_complete=2,
                connections=3, state="downloading",
            ),
            QBTorrentInfo(
                hash="hash_dc",
                name="Movie.2024.1080p.DIRECTORS.CUT.mkv",
                save_path="/data/downloads",
                content_path="/data/downloads/Movie.2024.1080p.DIRECTORS.CUT.mkv",
                progress=0.3, dlspeed=512, upspeed=0,
                num_seeds=2, num_leechs=0, num_complete=1,
                connections=1, state="downloading",
            ),
        ])

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

        result = svc.sync_once(SessionLocal)
        # 多命中无精确匹配 → 跳过，不应绑定
        assert result.skipped == 1
        assert result.synced == 0
        assert result.failed == 0

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.qb_hash is None
        assert task.status == "awaiting_sync"

    def test_hash_backfill_multimatch_exact_preferred(
        self, session: Session,
    ) -> None:
        """多命中但有一个精确匹配 → 绑定精确命中的那个。

        当 qB 中同时存在标准版、REPACK、DIRECTOR'S CUT 时，
        标准化后与任务标题完全一致的 torrent 应被优先选中。
        """
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(
            repo, qb_hash=None, status="submitted",
            title="Movie.2024.1080p",
        )
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        # 三个候选：一个精确匹配 + 两个子串匹配
        stub.set_all_torrents([
            QBTorrentInfo(
                hash="hash_repack",
                name="Movie.2024.1080p.REPACK.mkv",
                save_path="/data/downloads",
                content_path="/data/downloads/Movie.2024.1080p.REPACK.mkv",
                progress=0.5, dlspeed=1024, upspeed=0,
                num_seeds=5, num_leechs=1, num_complete=2,
                connections=3, state="downloading",
            ),
            QBTorrentInfo(
                hash="hash_exact",
                name="Movie.2024.1080p",
                save_path="/data/downloads",
                content_path="/data/downloads/Movie.2024.1080p",
                progress=0.8, dlspeed=2048, upspeed=0,
                num_seeds=12, num_leechs=3, num_complete=10,
                connections=8, state="downloading",
            ),
            QBTorrentInfo(
                hash="hash_dc",
                name="Movie.2024.1080p.DIRECTORS.CUT.mkv",
                save_path="/data/downloads",
                content_path="/data/downloads/Movie.2024.1080p.DIRECTORS.CUT.mkv",
                progress=0.3, dlspeed=512, upspeed=0,
                num_seeds=2, num_leechs=0, num_complete=1,
                connections=1, state="downloading",
            ),
        ])
        stub.set_info("hash_exact", QBTorrentInfo(
            hash="hash_exact",
            name="Movie.2024.1080p",
            save_path="/data/downloads",
            content_path="/data/downloads/Movie.2024.1080p",
            progress=0.8, dlspeed=2048, upspeed=0,
            num_seeds=12, num_leechs=3, num_complete=10,
            connections=8, state="downloading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

        result = svc.sync_once(SessionLocal)
        assert result.synced == 1
        assert result.skipped == 0

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.qb_hash == "hash_exact"
        assert task.status == "downloading"

    def test_sync_success_clears_stale_error_message(self, session: Session) -> None:
        """正常同步成功后清空旧的 error_message。"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash_stale_err", status="downloading")
        task = repo.get(tid)
        repo.update_sync_status(task, error_message="上次同步失败: 找不到 hash")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        stub.set_info("hash_stale_err", QBTorrentInfo(
            hash="hash_stale_err",
            name="Normal.Movie.mkv",
            save_path="/data/downloads",
            content_path="/data/downloads/Normal.Movie.mkv",
            progress=0.5,
            dlspeed=1024000,
            upspeed=0,
            num_seeds=5,
            num_leechs=1,
            num_complete=2,
            connections=3,
            state="downloading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        result = svc.sync_once(SessionLocal)

        assert result.synced == 1
        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.status == "downloading"
        assert task.error_message is None

    # ── 4.5: 标签回查测试 ──

    def test_hash_backfill_by_tag(self, session: Session) -> None:
        """标签回查优先：提交时写入的标签在后续同步中匹配成功"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        task = _create_task(repo, qb_hash=None, status="submitting",
                            title="Some.Movie.2024")
        tid = task  # _create_task returns task.id
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        tag = f"media-pilot:{tid}"
        stub.set_tagged_torrents(tag, [
            QBTorrentInfo(
                hash="hash_by_tag",
                name="Some.Movie.2024.mkv",
                save_path="/data/downloads",
                progress=0.3,
                state="downloading",
            )
        ])
        # backfill 完成后继续按 hash 同步
        stub.set_info("hash_by_tag", QBTorrentInfo(
            hash="hash_by_tag",
            name="Some.Movie.2024.mkv",
            save_path="/data/downloads",
            progress=0.3,
            state="downloading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        result = svc.sync_once(SessionLocal)

        assert result.synced == 1
        session.refresh(repo.get(tid))
        task_after = repo.get(tid)
        assert task_after is not None
        assert task_after.qb_hash == "hash_by_tag"
        assert task_after.status == "downloading"

    def test_hash_backfill_tag_miss_falls_back_to_title(
        self, session: Session,
    ) -> None:
        """标签未匹配时降级到标题匹配"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        task = _create_task(
            repo, qb_hash=None, status="submitting",
            title="Weathering.With.You.2019",
        )
        tid = task  # _create_task returns task.id
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        # 标签回查无结果 → 降级到 all_torrents 标题匹配
        stub.set_all_torrents([
            QBTorrentInfo(
                hash="hash_fallback",
                name="Weathering.With.You.2019.1080p.BluRay.x264.mkv",
                save_path="/data/downloads",
                progress=0.0,
                state="downloading",
            )
        ])
        stub.set_info("hash_fallback", QBTorrentInfo(
            hash="hash_fallback",
            name="Weathering.With.You.2019.1080p.BluRay.x264.mkv",
            save_path="/data/downloads",
            progress=0.0,
            state="downloading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        result = svc.sync_once(SessionLocal)

        assert result.synced == 1
        session.refresh(repo.get(tid))
        task_after = repo.get(tid)
        assert task_after is not None
        assert task_after.qb_hash == "hash_fallback"
        assert task_after.status == "downloading"


# ── Worker 集成 ──


class TestWorkerSyncDownloads:
    """Worker.sync_downloads 将同步服务接入轮询"""

    def test_worker_sync_downloads_returns_result(self, session: Session) -> None:
        """Worker.sync_downloads 返回 DownloadSyncResult"""
        from media_pilot.worker import Worker

        repo = DownloadTaskRepository(session)
        _create_task(repo, qb_hash="abc111", status="submitted")
        session.commit()

        config = _make_config()

        import media_pilot.services.download_sync as ds_module
        stub = StubQBittorrentAdapter()
        stub.set_info("abc111", QBTorrentInfo(
            hash="abc111",
            name="test.mkv",
            save_path="/data/downloads",
            progress=0.5,
            state="downloading",
        ))

        original = ds_module.QBittorrentAdapter
        ds_module.QBittorrentAdapter = lambda cfg: stub  # type: ignore[assignment]
        try:
            SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
            worker = Worker(config)
            result = worker.sync_downloads(SessionLocal)
        finally:
            ds_module.QBittorrentAdapter = original

        assert result.synced == 1

    def test_worker_disabled_when_no_config(self) -> None:
        """无 config 时 Worker.sync_downloads 返回空结果"""
        from media_pilot.worker import Worker

        worker = Worker()
        result = worker.sync_downloads(None)  # type: ignore[arg-type]
        assert result.synced == 0
        assert result.failed == 0
        assert result.skipped == 0


# ── 下载完成转入库 ──


class TestDownloadCompletionToIngest:
    """下载完成 → 创建入库任务 + 幂等保护 + 路径校验"""

    def test_single_file_complete_creates_ingest(self, session: Session) -> None:
        """单文件完成 → 创建 IngestTask，绑定 download_task"""
        from sqlalchemy import select

        from media_pilot.repository.models import IngestTask
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash666", status="downloading",
                           title="Test.Movie.2024.mkv")
        session.commit()

        config = _make_config()
        # 确保目录存在并创建实体文件
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        movie_file = config.downloads_dir / "Test.Movie.2024.mkv"
        movie_file.write_bytes(b"movie")
        stub = StubQBittorrentAdapter()
        stub.set_info("hash666", QBTorrentInfo(
            hash="hash666",
            name="Test.Movie.2024.mkv",
            save_path="/data/downloads",
            content_path=str(movie_file),
            progress=1.0,
            dlspeed=0,
            state="uploading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        _ = svc.sync_once(SessionLocal)

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.status == "completed"
        assert task.ingest_task_id is not None

        ingest = session.scalars(
            select(IngestTask).where(IngestTask.id == task.ingest_task_id)
        ).first()
        assert ingest is not None
        assert ingest.source_path == str(config.downloads_dir / "Test.Movie.2024.mkv")
        assert ingest.source_download_task_id == tid
        assert ingest.status == "discovered"
        assert ingest.current_step == "download_scan"

    def test_directory_complete_creates_ingest(self, session: Session) -> None:
        """目录下载完成 → 创建 IngestTask，交给现有入库链路"""
        from sqlalchemy import select

        from media_pilot.repository.models import IngestTask
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash_dir", status="downloading",
                           title="TV.Show.S01.1080p")
        session.commit()

        config = _make_config()
        # 确保目录存在并创建实体目录
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        tv_dir = config.downloads_dir / "TV.Show.S01.1080p"
        tv_dir.mkdir(parents=True, exist_ok=True)
        (tv_dir / "S01E01.mkv").write_bytes(b"episode")
        stub = StubQBittorrentAdapter()
        stub.set_info("hash_dir", QBTorrentInfo(
            hash="hash_dir",
            name="TV.Show.S01.1080p",
            save_path="/data/downloads",
            content_path=str(tv_dir),
            progress=1.0,
            dlspeed=0,
            state="uploading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        _ = svc.sync_once(SessionLocal)

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.status == "completed"
        assert task.ingest_task_id is not None

        ingest = session.scalars(
            select(IngestTask).where(IngestTask.id == task.ingest_task_id)
        ).first()
        assert ingest is not None
        assert ingest.source_path == str(config.downloads_dir / "TV.Show.S01.1080p")
        assert ingest.source_download_task_id == tid

    def test_incomplete_no_ingest(self, session: Session) -> None:
        """下载未完成的不转入库"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash_incomplete", status="downloading",
                           title="Still.Downloading.mkv")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        stub.set_info("hash_incomplete", QBTorrentInfo(
            hash="hash_incomplete",
            name="Still.Downloading.mkv",
            save_path="/data/downloads",
            content_path=str(config.downloads_dir / "Still.Downloading.mkv"),
            progress=0.5,
            dlspeed=1024000,
            state="downloading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        _ = svc.sync_once(SessionLocal)

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.status == "downloading"
        assert task.ingest_task_id is None

    def test_complete_no_content_path_no_ingest(self, session: Session) -> None:
        """content_path 为空的不转入库"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash_no_path", status="downloading",
                           title="NoPath.mkv")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        stub.set_info("hash_no_path", QBTorrentInfo(
            hash="hash_no_path",
            name="NoPath.mkv",
            save_path="/data/downloads",
            content_path="",
            progress=1.0,
            dlspeed=0,
            state="uploading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        _ = svc.sync_once(SessionLocal)

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.status == "downloading"
        assert task.ingest_task_id is None

    def test_complete_path_outside_downloads_no_ingest(self, session: Session) -> None:
        """content_path 在 downloads_dir 外的不转入库"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash_outside", status="downloading",
                           title="Outside.mkv")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        stub.set_info("hash_outside", QBTorrentInfo(
            hash="hash_outside",
            name="Outside.mkv",
            save_path="/data/downloads",
            content_path="/etc/passwd",
            progress=1.0,
            dlspeed=0,
            state="uploading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        _ = svc.sync_once(SessionLocal)

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.ingest_task_id is None

    def test_idempotent_ingest(self, session: Session) -> None:
        """已完成绑定的任务不会再创建第二个 IngestTask"""
        from sqlalchemy import select

        from media_pilot.repository.models import IngestTask
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash_idem", status="downloading",
                           title="Idem.Movie.mkv")
        session.commit()

        config = _make_config()
        # 确保目录存在并创建实体文件
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        idem_file = config.downloads_dir / "Idem.Movie.mkv"
        idem_file.write_bytes(b"movie")
        stub = StubQBittorrentAdapter()
        stub.set_info("hash_idem", QBTorrentInfo(
            hash="hash_idem",
            name="Idem.Movie.mkv",
            save_path="/data/downloads",
            content_path=str(idem_file),
            progress=1.0,
            dlspeed=0,
            state="uploading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

        # 第一次同步：创建入库任务
        svc.sync_once(SessionLocal)
        # 第二次同步：幂等
        svc.sync_once(SessionLocal)
        # 第三次同步：幂等
        svc.sync_once(SessionLocal)

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.status == "completed"
        assert task.ingest_task_id is not None

        # 只有一个 IngestTask
        ingest_tasks = session.scalars(
            select(IngestTask).where(
                IngestTask.source_download_task_id == tid
            )
        ).all()
        assert len(ingest_tasks) == 1

    def test_complete_path_not_accessible_waits(self, session: Session) -> None:
        """下载完成但路径暂不可访问 → 等待转入入库，不回退到扫描器"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash_no_file", status="downloading",
                           title="Missing.File.mkv")
        session.commit()

        config = _make_config()
        # 路径在 downloads_dir 内但文件不存在
        missing_path = str(config.downloads_dir / "Missing.File.mkv")
        stub = StubQBittorrentAdapter()
        stub.set_info("hash_no_file", QBTorrentInfo(
            hash="hash_no_file",
            name="Missing.File.mkv",
            save_path="/data/downloads",
            content_path=missing_path,
            progress=1.0,
            state="uploading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        result = svc.sync_once(SessionLocal)

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        # 不应创建入库任务 — 路径不可访问
        assert task.ingest_task_id is None
        # 应进入等待状态而非完成
        assert task.status in ("waiting_ingest", "completed_pending_ingest")
        # 不应被标记为 sync_failed
        assert task.status != "sync_failed"
        assert result.ingested == 0


# ── retry_sync_one 手动重试 ──


class TestRetrySyncOne:
    """retry_sync_one：单任务手动重试同步"""

    def test_sync_failed_recovers_on_retry(self, session: Session) -> None:
        """sync_failed 任务手动重试同步，qB 恢复后回到 downloading 并清空错误"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash_retry", status="sync_failed")
        task = repo.get(tid)
        repo.update_sync_status(task, error_message="之前找不到 hash")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        stub.set_info("hash_retry", QBTorrentInfo(
            hash="hash_retry",
            name="Retry.Movie.mkv",
            save_path="/data/downloads",
            content_path="/data/downloads/Retry.Movie.mkv",
            progress=0.3,
            dlspeed=512000,
            upspeed=0,
            num_seeds=3,
            num_leechs=1,
            num_complete=1,
            connections=2,
            state="downloading",
        ))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        result = svc.retry_sync_one(SessionLocal, tid)

        assert result.synced == 1
        assert result.failed == 0

        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.status == "downloading"
        assert task.progress == 0.3
        assert task.error_message is None

    def test_retry_skips_task_with_ingest(self, session: Session) -> None:
        """已关联入库任务的下载不暴露重试入口"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash_linked", status="sync_failed")
        task = repo.get(tid)
        repo.bind_ingest_task(task, "ingest-999")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        result = svc.retry_sync_one(SessionLocal, tid)

        assert result.skipped == 1
        assert result.synced == 0

    def test_retry_nonexistent_task_skipped(self, session: Session) -> None:
        """不存在的任务 ID 返回 skipped"""
        from media_pilot.services.download_sync import DownloadSyncService

        config = _make_config()
        stub = StubQBittorrentAdapter()

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        result = svc.retry_sync_one(SessionLocal, "nonexistent-id")

        assert result.skipped == 1

    def test_retry_api_failure_marks_sync_failed_again(
        self, session: Session,
    ) -> None:
        """手动重试时 qB 仍不可达 → 保持 sync_failed"""
        from media_pilot.services.download_sync import DownloadSyncService

        repo = DownloadTaskRepository(session)
        tid = _create_task(repo, qb_hash="hash_still_dead", status="sync_failed")
        session.commit()

        config = _make_config()
        stub = StubQBittorrentAdapter()
        stub.raise_on_call(ConnectionError("still unreachable"))

        svc = DownloadSyncService(config, adapter=stub)
        SessionLocal = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        result = svc.retry_sync_one(SessionLocal, tid)

        assert result.failed == 1
        session.refresh(repo.get(tid))
        task = repo.get(tid)
        assert task is not None
        assert task.status == "sync_failed"
