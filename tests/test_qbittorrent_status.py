"""qBittorrent 状态读取与完成判定测试"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from media_pilot.config.settings import AppConfig
from media_pilot.resource_discovery.qbittorrent_adapter import QBittorrentAdapter
from media_pilot.resource_discovery.types import (
    QBTorrentInfo,
    is_qb_torrent_completed,
)

# ── 辅助 ──


def _make_config(**kwargs) -> AppConfig:
    from pathlib import Path

    defaults = {
        "downloads_dir": Path("/tmp/test-dl"),
        "watch_dir": Path("/tmp/test-watch"),
        "workspace_dir": Path("/tmp/test-ws"),
        "movies_dir": Path("/tmp/test-movies"),
        "shows_dir": Path("/tmp/test-shows"),
        "database_dir": Path("/tmp/test-db"),
        "qbittorrent_url": "http://qbittorrent:8080",
        "qbittorrent_username": "admin",
        "qbittorrent_password": "pass",
        "qbittorrent_save_path": "/data/downloads",
        "qbittorrent_category": "media-pilot",
    }
    defaults.update(kwargs)
    return AppConfig(**defaults)


def _qbtorrent_json(hashes: list[str], state="downloading", progress=0.5) -> list[dict]:
    return [
        {
            "hash": h,
            "name": f"Test.Torrent.{h[:6]}",
            "save_path": "/data/downloads",
            "content_path": f"/data/downloads/Test.Movie.{h[:6]}",
            "progress": progress,
            "size": 1073741824,
            "dlspeed": 1024000,
            "upspeed": 512000,
            "num_seeds": 10,
            "num_leechs": 2,
            "num_complete": 5,
            "connections": 8,
            "state": state,
        }
        for h in hashes
    ]


def _torrent_info_url(hashes: list[str]) -> str:
    if not hashes:
        return "http://qbittorrent:8080/api/v2/torrents/info"
    encoded = quote("|".join(hashes), safe="")
    return f"http://qbittorrent:8080/api/v2/torrents/info?hashes={encoded}"


# ── DTO 与完成判定 ──


class TestQBTorrentInfo:
    def test_basic_fields(self):
        info = QBTorrentInfo(
            hash="abc123",
            name="Test Movie",
            save_path="/data/downloads",
        )
        assert info.hash == "abc123"
        assert info.name == "Test Movie"


class TestIsQBTorrentCompleted:
    @pytest.mark.parametrize("state", [
        "uploading", "stalledUP", "pausedUP", "queuedUP", "forcedUP",
    ])
    def test_completion_states(self, state):
        info = QBTorrentInfo(
            hash="abc", name="t", save_path="/d", progress=1.0, state=state
        )
        assert is_qb_torrent_completed(info)

    def test_downloading_not_completed(self):
        info = QBTorrentInfo(
            hash="abc", name="t", save_path="/d", progress=0.8, state="downloading"
        )
        assert not is_qb_torrent_completed(info)

    def test_checkingUP_not_completed(self):
        info = QBTorrentInfo(
            hash="abc", name="t", save_path="/d", progress=1.0, state="checkingUP"
        )
        assert not is_qb_torrent_completed(info)

    def test_100pct_but_downloading_not_completed(self):
        info = QBTorrentInfo(
            hash="abc", name="t", save_path="/d", progress=1.0, state="downloading"
        )
        assert not is_qb_torrent_completed(info)


# ── Adapter get_torrent_info ──


class TestGetTorrentInfo:
    @pytest.fixture
    def mock_client(self, httpx_mock):
        return httpx_mock

    @pytest.fixture
    def adapter(self):
        return QBittorrentAdapter(_make_config())

    def test_get_single_torrent(self, mock_client, adapter):
        hash_val = "a1b2c3d4e5f6"
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=test_sid_123"},
        )
        mock_client.add_response(
            method="GET",
            url=_torrent_info_url([hash_val]),
            json=_qbtorrent_json([hash_val], state="uploading", progress=1.0),
        )

        result = adapter.get_torrent_info([hash_val])
        assert len(result) == 1
        assert result[0].hash == hash_val
        assert result[0].progress == 1.0
        assert result[0].state == "uploading"

    def test_get_multiple_torrents(self, mock_client, adapter):
        hashes = ["aaa111", "bbb222", "ccc333"]
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=test_sid"},
        )
        mock_client.add_response(
            method="GET",
            url=_torrent_info_url(hashes),
            json=[
                *_qbtorrent_json(["aaa111"], state="downloading", progress=0.3),
                *_qbtorrent_json(["bbb222"], state="uploading", progress=1.0),
                *_qbtorrent_json(["ccc333"], state="stalledUP", progress=1.0),
            ],
        )

        result = adapter.get_torrent_info(hashes)
        assert len(result) == 3
        states = {r.hash: r.state for r in result}
        assert states["aaa111"] == "downloading"
        assert states["bbb222"] == "uploading"
        assert states["ccc333"] == "stalledUP"

    def test_empty_hashes_returns_empty(self, mock_client, adapter):
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=test_sid"},
        )
        mock_client.add_response(
            method="GET",
            url=_torrent_info_url([]),
            json=[],
        )

        result = adapter.get_torrent_info([])
        assert result == []

    def test_not_configured_returns_empty(self):
        cfg = _make_config(qbittorrent_url="")
        adapter = QBittorrentAdapter(cfg)
        result = adapter.get_torrent_info(["abc"])
        assert result == []

    def test_login_failure_returns_empty(self, mock_client, adapter):
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            status_code=403,
        )

        result = adapter.get_torrent_info(["abc"])
        assert result == []

    def test_api_error_returns_empty(self, mock_client, adapter):
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=test_sid"},
        )
        mock_client.add_response(
            method="GET",
            url=_torrent_info_url(["abc"]),
            status_code=500,
        )

        result = adapter.get_torrent_info(["abc"])
        assert result == []

    def test_sid_cookie_qbt_v5(self, mock_client, adapter):
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "QBT_SID_8080=test_sid_v5"},
        )
        mock_client.add_response(
            method="GET",
            url=_torrent_info_url(["abc123"]),
            json=_qbtorrent_json(["abc123"]),
        )

        result = adapter.get_torrent_info(["abc123"])
        assert len(result) == 1
        assert result[0].hash == "abc123"

        # 验证 cookie 名称被正确沿用
        info_req = mock_client.get_requests(method="GET")[-1]
        assert "QBT_SID_8080=test_sid_v5" in info_req.headers.get("cookie", "")
