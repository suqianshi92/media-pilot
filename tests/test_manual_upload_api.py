"""手动上传 API 测试 — parse 和 submit 端点"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from tests.auth_helpers import AuthenticatedTestClient as TestClient

from media_pilot.config.settings import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.repositories import DownloadTaskRepository


def _make_config(**overrides) -> AppConfig:
    kwargs = dict(
        downloads_dir=Path("/tmp/test-dl"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp/test-ws"),
        movies_dir=Path("/tmp/test-movies"),
        shows_dir=Path("/tmp/test-shows"),
        database_dir=Path("/tmp/test-db-mu"),
        qbittorrent_url="http://qbittorrent:8080",
        qbittorrent_username="admin",
        qbittorrent_password="pass",
        qbittorrent_save_path="/data/downloads",
        qbittorrent_category="media-pilot",
    )
    kwargs.update(overrides)
    return AppConfig(**kwargs)


def _make_minimal_torrent_bytes() -> bytes:
    """构造包含外层 dict 的最小 torrent"""
    info = b"4:infod4:name8:test.mkv6:lengthi1048576e12:piece lengthi262144e6:pieces20:aaaaaaaaaaaaaaaaaaaae"
    return b"d" + info + b"e"


# ── parse ──


class TestParseEndpoint:
    @pytest.fixture
    def client(self) -> TestClient:
        from media_pilot.app import create_app

        config = _make_config()
        initialize_database(config)
        session_factory = create_session_factory(config)
        app = create_app(config=config, session_factory=session_factory)
        return TestClient(app)

    def test_parse_magnet_lines(self, client: TestClient):
        magnets = "magnet:?xt=urn:btih:abc123&dn=TestMovie\nmagnet:?xt=urn:btih:def456&dn=Another"
        resp = client.post("/api/v1/manual-upload/parse", data={"magnets": magnets})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert len(body["data"]["items"]) == 2
        assert body["data"]["items"][0]["kind"] == "magnet"
        assert body["data"]["items"][0]["display_name"] == "TestMovie"
        assert body["data"]["items"][0]["source_index"] == 0
        assert body["data"]["items"][1]["display_name"] == "Another"
        assert body["data"]["items"][1]["source_index"] == 1

    def test_parse_invalid_magnet_reported_as_error(self, client: TestClient):
        magnets = "not-a-magnet\nmagnet:?xt=urn:btih:abc123&dn=Good"
        resp = client.post("/api/v1/manual-upload/parse", data={"magnets": magnets})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert len(body["data"]["items"]) == 1
        assert len(body["data"]["errors"]) == 1

    def test_parse_torrent_file(self, client: TestClient):
        torrent_data = _make_minimal_torrent_bytes()
        resp = client.post(
            "/api/v1/manual-upload/parse",
            files=[("torrents", ("test.torrent", io.BytesIO(torrent_data), "application/x-bittorrent"))],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert len(body["data"]["items"]) == 1
        assert body["data"]["items"][0]["kind"] == "torrent"
        assert body["data"]["items"][0]["source_index"] == 0
        assert body["data"]["items"][0]["display_name"] == "test.mkv"
        assert body["data"]["items"][0]["size_bytes"] == 1048576

    def test_parse_mixed_magnet_and_torrent(self, client: TestClient):
        torrent_data = _make_minimal_torrent_bytes()
        magnets = "magnet:?xt=urn:btih:abc123&dn=MovieOne"
        resp = client.post(
            "/api/v1/manual-upload/parse",
            data={"magnets": magnets},
            files=[("torrents", ("test.torrent", io.BytesIO(torrent_data), "application/x-bittorrent"))],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["meta"]["total"] == 2

    def test_parse_torrent_key_uses_file_index_not_cumulative_count(self, client: TestClient):
        """混合输入时 torrent key 始终对应原始文件索引，不受前面 magnet 影响"""
        torrent_data = _make_minimal_torrent_bytes()
        # 先 2 个 magnet，再 1 个 torrent
        magnets = "magnet:?xt=urn:btih:aaa111&dn=M1\nmagnet:?xt=urn:btih:bbb222&dn=M2"
        files = [("torrents", ("movie.torrent", io.BytesIO(torrent_data), "application/x-bittorrent"))]
        resp = client.post(
            "/api/v1/manual-upload/parse",
            data={"magnets": magnets},
            files=files,
        )
        assert resp.status_code == 200
        body = resp.json()
        items = body["data"]["items"]
        torrent_items = [it for it in items if it["kind"] == "torrent"]
        assert len(torrent_items) == 1
        # key 应为 torrent-0（文件数组索引），而非 torrent-2（受 magnet 累计影响）
        assert torrent_items[0]["key"] == "torrent-0"

    def test_parse_magnet_key_uses_line_index(self, client: TestClient):
        """magnet key 中的数字为磁力链接行号索引"""
        magnets = "magnet:?xt=urn:btih:aaa111&dn=First\nmagnet:?xt=urn:btih:bbb222&dn=Second"
        resp = client.post("/api/v1/manual-upload/parse", data={"magnets": magnets})
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        magnet_keys = [it["key"] for it in items if it["kind"] == "magnet"]
        assert magnet_keys == ["magnet-0", "magnet-1"]

    def test_parse_consecutive_calls_yield_consistent_source_indices(self, client: TestClient):
        """两次独立 parse 调用都返回正确的 source_index，不互相干扰"""
        # 第一批
        resp1 = client.post(
            "/api/v1/manual-upload/parse",
            data={"magnets": "magnet:?xt=urn:btih:aaa111&dn=Batch1"},
        )
        assert resp1.status_code == 200
        batch1 = resp1.json()
        assert batch1["data"]["items"][0]["source_index"] == 0

        # 第二批 — source_index 应从 0 重新编号（批次内索引）
        resp2 = client.post(
            "/api/v1/manual-upload/parse",
            data={"magnets": "magnet:?xt=urn:btih:bbb222&dn=Batch2"},
        )
        assert resp2.status_code == 200
        batch2 = resp2.json()
        assert batch2["data"]["items"][0]["source_index"] == 0
        # 两批的 key 格式相同（都是 magnet-0）— frontend 应用 source_index 生成会话唯一 key
        assert batch2["data"]["items"][0]["key"] == "magnet-0"

    def test_parse_empty_torrent_file_reported_as_error(self, client: TestClient):
        resp = client.post(
            "/api/v1/manual-upload/parse",
            files=[("torrents", ("empty.torrent", io.BytesIO(b""), "application/x-bittorrent"))],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]["items"]) == 0
        assert len(body["data"]["errors"]) == 1

    def test_parse_rejects_more_than_5_combined_inputs(self, client: TestClient):
        """torrent + magnet 合并计数超过 5 时直接返回错误"""
        torrent_data = _make_minimal_torrent_bytes()
        files = [
            ("torrents", (f"test{i}.torrent", io.BytesIO(torrent_data), "application/x-bittorrent"))
            for i in range(3)
        ]
        magnets = "\n".join([
            "magnet:?xt=urn:btih:abc111&dn=M1",
            "magnet:?xt=urn:btih:abc222&dn=M2",
            "magnet:?xt=urn:btih:abc333&dn=M3",
        ])
        resp = client.post(
            "/api/v1/manual-upload/parse",
            files=files,
            data={"magnets": magnets},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"
        assert body["messages"][0]["code"] == "too_many_items"
        assert len(body["data"]["items"]) == 0

    def test_parse_allows_exactly_5_combined_inputs(self, client: TestClient):
        """torrent + magnet 合计正好 5 个时通过"""
        torrent_data = _make_minimal_torrent_bytes()
        files = [
            ("torrents", (f"test{i}.torrent", io.BytesIO(torrent_data), "application/x-bittorrent"))
            for i in range(2)
        ]
        magnets = "\n".join([
            "magnet:?xt=urn:btih:abc111&dn=M1",
            "magnet:?xt=urn:btih:abc222&dn=M2",
            "magnet:?xt=urn:btih:abc333&dn=M3",
        ])
        resp = client.post(
            "/api/v1/manual-upload/parse",
            files=files,
            data={"magnets": magnets},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert len(body["data"]["items"]) == 5


# ── submit ──


class TestSubmitEndpoint:
    @pytest.fixture
    def client(self, httpx_mock) -> TestClient:
        from media_pilot.app import create_app

        config = _make_config()
        initialize_database(config)
        session_factory = create_session_factory(config)
        app = create_app(config=config, session_factory=session_factory)
        return TestClient(app)

    def test_submit_with_qb_failure(self, client: TestClient, httpx_mock):
        # Mock qB login to fail
        httpx_mock.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            status_code=403,
        )
        body = {
            "items": [
                {
                    "key": "magnet-0",
                    "kind": "magnet",
                    "magnet_uri": "magnet:?xt=urn:btih:abc123&dn=Test",
                    "display_name": "Test",
                }
            ]
        }
        resp = client.post("/api/v1/manual-upload/submit", json=body)
        assert resp.status_code == 200
        rbody = resp.json()
        assert rbody["data"]["results"][0]["success"] is False

    def test_submit_with_qb_success(self, client: TestClient, httpx_mock):
        # Mock qB login success + add download
        httpx_mock.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=test123"},
        )
        httpx_mock.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/torrents/add",
            status_code=200,
        )
        body = {
            "items": [
                {
                    "key": "magnet-0",
                    "kind": "magnet",
                    "magnet_uri": "magnet:?xt=urn:btih:abc123&dn=Test",
                    "display_name": "Test",
                }
            ]
        }
        resp = client.post("/api/v1/manual-upload/submit", json=body)
        assert resp.status_code == 200
        rbody = resp.json()
        assert rbody["data"]["results"][0]["success"] is True
        assert rbody["status"] == "success"
        task_id = rbody["data"]["results"][0]["download_task_id"]
        current_user_id = client.get("/api/v1/auth/me").json()["data"]["user"]["id"]
        with client.app.state.session_factory() as session:
            task = DownloadTaskRepository(session).get(task_id)
        assert task is not None
        assert task.owner_user_id == current_user_id
        assert task.is_adult is False

    def test_submit_empty_items_rejected(self, client: TestClient):
        resp = client.post("/api/v1/manual-upload/submit", json={"items": []})
        assert resp.status_code == 422

    def test_submit_validates_preselection_fields(self, client: TestClient, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=test123"},
        )
        httpx_mock.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/torrents/add",
            status_code=200,
        )
        body = {
            "items": [
                {
                    "key": "magnet-0",
                    "kind": "magnet",
                    "magnet_uri": "magnet:?xt=urn:btih:abc123&dn=Test",
                    "display_name": "Test",
                    "preselected_profile": "tpdb_adult_movie",
                    "preselected_provider": "tpdb",
                    "preselected_external_id": "adult:1234567",
                }
            ]
        }
        resp = client.post("/api/v1/manual-upload/submit", json=body)
        assert resp.status_code == 200
        rbody = resp.json()
        assert rbody["data"]["results"][0]["success"] is True
        task_id = rbody["data"]["results"][0]["download_task_id"]
        with client.app.state.session_factory() as session:
            task = DownloadTaskRepository(session).get(task_id)
        assert task is not None
        assert task.is_adult is True
