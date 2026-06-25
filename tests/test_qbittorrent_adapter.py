"""qBittorrent Adapter 单元测试 — 使用 httpx mock，不依赖真实 qBittorrent"""

from __future__ import annotations

import pytest

from media_pilot.config.settings import AppConfig
from media_pilot.resource_discovery.qbittorrent_adapter import QBittorrentAdapter
from media_pilot.resource_discovery.types import DownloadRequest

# ── 辅助：构造最小 AppConfig ──


def _make_config(
    *,
    url: str = "http://qbittorrent:8080",
    username: str = "admin",
    password: str = "pass",
    save_path: str = "/data/downloads",
    category: str = "media-pilot",
) -> AppConfig:
    from pathlib import Path

    return AppConfig(
        downloads_dir=Path("/tmp/test-dl"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp/test-ws"),
        movies_dir=Path("/tmp/test-movies"),
        shows_dir=Path("/tmp/test-shows"),
        database_dir=Path("/tmp/test-db"),
        qbittorrent_url=url,
        qbittorrent_username=username,
        qbittorrent_password=password,
        qbittorrent_save_path=save_path,
        qbittorrent_category=category,
    )


# ── test_connection ──


class TestConnection:
    def test_not_configured_when_url_empty(self):
        cfg = _make_config(url="")
        adapter = QBittorrentAdapter(cfg)
        status = adapter.test_connection()
        assert not status.configured
        assert "未配置" in status.message


class TestConnectionWithMock:
    @pytest.fixture
    def mock_client(self, httpx_mock):
        return httpx_mock

    def test_connection_success(self, mock_client):
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=test123"},
        )
        mock_client.add_response(
            method="GET",
            url="http://qbittorrent:8080/api/v2/app/version",
            text="v4.6.0",
        )

        cfg = _make_config()
        adapter = QBittorrentAdapter(cfg)
        status = adapter.test_connection()
        assert status.configured
        assert status.reachable
        assert status.authenticated
        assert status.message == "连接正常"


    def test_connection_success_with_qbt_sid_cookie(self, mock_client):
        """qBittorrent 5.x/linuxserver 可能返回 QBT_SID_<port> cookie"""
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "QBT_SID_8080=test-qbt-sid"},
        )
        mock_client.add_response(
            method="GET",
            url="http://qbittorrent:8080/api/v2/app/version",
            text="v5.2.0",
        )

        cfg = _make_config()
        adapter = QBittorrentAdapter(cfg)
        status = adapter.test_connection()

        assert status.configured
        assert status.reachable
        assert status.authenticated

        version_req = mock_client.get_requests(method="GET")[-1]
        assert "QBT_SID_8080=test-qbt-sid" in version_req.headers.get("cookie", "")

    def test_login_failure_403(self, mock_client):
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            status_code=403,
            text="Forbidden",
        )

        cfg = _make_config()
        adapter = QBittorrentAdapter(cfg)
        status = adapter.test_connection()
        assert status.configured
        assert status.reachable
        assert not status.authenticated
        assert "认证" in status.message or "403" in status.message

    def test_connection_timeout(self, mock_client):
        import httpx

        mock_client.add_exception(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            exception=httpx.TimeoutException("timeout"),
        )

        cfg = _make_config()
        adapter = QBittorrentAdapter(cfg)
        status = adapter.test_connection()
        assert status.configured
        assert not status.reachable
        assert "超时" in status.message or "timeout" in status.message.lower()


# ── add_download ──


class TestAddDownload:
    @pytest.fixture
    def mock_client(self, httpx_mock):
        return httpx_mock

    def test_add_magnet_success(self, mock_client):
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=test789"},
        )
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/torrents/add",
            text="Ok.",
        )

        cfg = _make_config()
        adapter = QBittorrentAdapter(cfg)
        result = adapter.add_download(
            DownloadRequest(
                magnet_url="magnet:?xt=urn:btih:abc123",
                title="测试资源",
                source="prowlarr",
                indexer="TGx",
            )
        )
        assert result.status == "submitted"
        assert result.title == "测试资源"
        assert "已提交" in result.message


    def test_add_magnet_success_with_qbt_sid_cookie(self, mock_client):
        """提交下载时沿用 qBittorrent 返回的 QBT_SID_<port> cookie 名"""
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "QBT_SID_8080=download-qbt-sid"},
        )
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/torrents/add",
            text="Ok.",
        )

        cfg = _make_config()
        adapter = QBittorrentAdapter(cfg)
        result = adapter.add_download(
            DownloadRequest(
                magnet_url="magnet:?xt=urn:btih:abc123",
                title="测试资源",
                source="prowlarr",
                indexer="TGx",
            )
        )

        assert result.status == "submitted"
        add_req = [
            r for r in mock_client.get_requests(method="POST")
            if "/torrents/add" in str(r.url)
        ][0]
        assert "QBT_SID_8080=download-qbt-sid" in add_req.headers.get("cookie", "")

    def test_add_url_success(self, mock_client):
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=url123"},
        )
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/torrents/add",
            text="Ok.",
        )

        cfg = _make_config()
        adapter = QBittorrentAdapter(cfg)
        result = adapter.add_download(
            DownloadRequest(
                download_url="https://torrent.example.com/file.torrent",
                title="电影",
                source="prowlarr",
                indexer="TGx",
            )
        )
        assert result.status == "submitted"

    def test_download_failure_returns_failed_status(self, mock_client):
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=fail1"},
        )
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/torrents/add",
            status_code=500,
            text="Internal Server Error",
        )

        cfg = _make_config()
        adapter = QBittorrentAdapter(cfg)
        result = adapter.add_download(
            DownloadRequest(
                magnet_url="magnet:?xt=urn:btih:bad",
                title="失败资源",
                source="prowlarr",
                indexer="TGx",
            )
        )
        assert result.status == "failed"
        assert "500" in result.message or "失败" in result.message

    def test_no_urls_returns_failed(self):
        cfg = _make_config()
        adapter = QBittorrentAdapter(cfg)
        result = adapter.add_download(
            DownloadRequest(title="无链接", source="prowlarr", indexer="TGx")
        )
        assert result.status == "failed"
        assert "无可下载" in result.message or "下载链接" in result.message

    def test_not_configured_returns_failed(self):
        cfg = _make_config(url="")
        adapter = QBittorrentAdapter(cfg)
        result = adapter.add_download(
            DownloadRequest(
                magnet_url="magnet:?xt=urn:btih:abc", title="test", source="p", indexer="i"
            )
        )
        assert result.status == "failed"
        assert "未配置" in result.message

    def test_save_path_never_from_client(self, mock_client):
        """客户端不可指定保存路径 — savepath 来自后端配置"""
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=path"},
        )
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/torrents/add",
            text="Ok.",
        )

        cfg = _make_config(save_path="/data/downloads/custom")
        adapter = QBittorrentAdapter(cfg)
        adapter.add_download(
            DownloadRequest(
                magnet_url="magnet:?xt=urn:btih:xyz",
                title="path test",
                source="p",
                indexer="i",
            )
        )

        # 验证 httpx_mock 收到的请求中包含 savepath（URL-encoded）
        from urllib.parse import unquote

        reqs = mock_client.get_requests(method="POST")
        add_reqs = [r for r in reqs if "/torrents/add" in str(r.url)]
        assert len(add_reqs) > 0
        content = add_reqs[0].content.decode()
        decoded = unquote(content)
        assert "/data/downloads/custom" in decoded

    def test_category_in_request(self, mock_client):
        """验证默认 category 传入请求"""
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=cat"},
        )
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/torrents/add",
            text="Ok.",
        )

        cfg = _make_config(category="media-pilot")
        adapter = QBittorrentAdapter(cfg)
        adapter.add_download(
            DownloadRequest(
                magnet_url="magnet:?xt=urn:btih:cat",
                title="category test",
                source="p",
                indexer="i",
            )
        )
        reqs = mock_client.get_requests(method="POST")
        add_reqs = [r for r in reqs if "/torrents/add" in str(r.url)]
        assert len(add_reqs) > 0
        content = add_reqs[0].content.decode()
        assert "media-pilot" in content

    def test_add_download_with_tag(self, mock_client):
        """验证下载关联标签传入 torrents/add 请求"""
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=tag"},
        )
        mock_client.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/torrents/add",
            text="Ok.",
        )

        cfg = _make_config()
        adapter = QBittorrentAdapter(cfg)
        adapter.add_download(
            DownloadRequest(
                magnet_url="magnet:?xt=urn:btih:tag",
                title="tag test",
                source="p",
                indexer="i",
            ),
            tag="media-pilot:dt-001",
        )
        reqs = mock_client.get_requests(method="POST")
        add_reqs = [r for r in reqs if "/torrents/add" in str(r.url)]
        assert len(add_reqs) > 0
        content = add_reqs[0].content.decode()
        assert "media-pilot%3Adt-001" in content  # : 被 URL-encode 为 %3A
