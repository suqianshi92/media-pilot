"""Prowlarr Adapter 单元测试 — 使用 httpx mock，不依赖真实 Prowlarr"""

from __future__ import annotations

import pytest

from media_pilot.config.settings import AppConfig
from media_pilot.resource_discovery.prowlarr_adapter import ProwlarrAdapter
from media_pilot.resource_discovery.types import (
    ResourceSearchRequest,
)

# ── 辅助：构造最小 AppConfig ──

def _make_config(*, prowlarr_url: str, api_key: str) -> AppConfig:
    from pathlib import Path

    return AppConfig(
        downloads_dir=Path("/tmp/test-dl"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp/test-ws"),
        movies_dir=Path("/tmp/test-movies"),
        shows_dir=Path("/tmp/test-shows"),
        database_dir=Path("/tmp/test-db"),
        prowlarr_url=prowlarr_url,
        prowlarr_api_key=api_key,
    )


# ── test_connection ──


class TestConnectionProbe:
    def test_not_configured_when_url_empty(self):
        cfg = _make_config(prowlarr_url="", api_key="")
        adapter = ProwlarrAdapter(cfg)
        status = adapter.test_connection()
        assert status.tool == "prowlarr"
        assert not status.configured
        assert "未配置 URL" in status.message

    def test_not_configured_when_api_key_empty(self):
        cfg = _make_config(prowlarr_url="http://prowlarr:9696", api_key="")
        adapter = ProwlarrAdapter(cfg)
        status = adapter.test_connection()
        assert not status.configured


class TestConnectionWithHttpxMock:
    """test_connection 使用 httpx mock"""

    @pytest.fixture
    def mock_client(self, httpx_mock):
        return httpx_mock

    def test_auth_failure_401(self, mock_client):
        mock_client.add_response(
            url="http://prowlarr:9696/api/v1/system/status",
            status_code=401,
        )
        cfg = _make_config(prowlarr_url="http://prowlarr:9696", api_key="key-401")
        adapter = ProwlarrAdapter(cfg)
        status = adapter.test_connection()
        assert status.configured
        assert status.authenticated is False
        assert "401" in status.message or "认证" in status.message

    def test_connection_success(self, mock_client):
        mock_client.add_response(
            url="http://prowlarr:9696/api/v1/system/status",
            json={"version": "1.0"},
        )
        cfg = _make_config(prowlarr_url="http://prowlarr:9696", api_key="key-ok")
        adapter = ProwlarrAdapter(cfg)
        status = adapter.test_connection()
        assert status.configured
        assert status.reachable
        assert status.authenticated
        assert status.message == "连接正常"

    def test_connection_timeout(self, mock_client):
        import httpx

        mock_client.add_exception(
            url="http://prowlarr:9696/api/v1/system/status",
            exception=httpx.TimeoutException("timeout"),
        )
        cfg = _make_config(prowlarr_url="http://prowlarr:9696", api_key="key-t")
        adapter = ProwlarrAdapter(cfg)
        status = adapter.test_connection()
        assert status.configured
        assert status.reachable is False
        assert "超时" in status.message or "timeout" in status.message.lower()


# ── search ──


class TestSearchCategoryMapping:
    """验证搜索类型 → Prowlarr categories 映射"""

    @pytest.fixture
    def mock_client(self, httpx_mock):
        return httpx_mock

    def test_movie_category_2000(self, mock_client):
        mock_client.add_response(
            url="http://prowlarr:9696/api/v1/search?query=%E5%A4%A9%E6%B0%94%E4%B9%8B%E5%AD%90&type=search&categories=2000&limit=20",
            json=[],
        )
        cfg = _make_config(prowlarr_url="http://prowlarr:9696", api_key="key")
        adapter = ProwlarrAdapter(cfg)
        result = adapter.search(ResourceSearchRequest(query="天气之子", search_type="movie"))
        assert result.search_type == "movie"

    def test_adult_category_6000(self, mock_client):
        mock_client.add_response(
            url="http://prowlarr:9696/api/v1/search?query=ABP-123&type=search&categories=6000&limit=20",
            json=[],
        )
        cfg = _make_config(prowlarr_url="http://prowlarr:9696", api_key="key")
        adapter = ProwlarrAdapter(cfg)
        result = adapter.search(ResourceSearchRequest(query="ABP-123", search_type="adult"))
        assert result.search_type == "adult"

    def test_all_categories_2000_6000(self, mock_client):
        mock_client.add_response(
            url="http://prowlarr:9696/api/v1/search?query=test&type=search&categories=2000&categories=6000&limit=20",
            json=[],
        )
        cfg = _make_config(prowlarr_url="http://prowlarr:9696", api_key="key")
        adapter = ProwlarrAdapter(cfg)
        result = adapter.search(ResourceSearchRequest(query="test", search_type="all"))
        assert result.source == "prowlarr"


class TestSearchResultNormalization:
    """验证 Prowlarr 返回 → ResourceCandidate 规范化"""

    @pytest.fixture
    def mock_client(self, httpx_mock):
        return httpx_mock

    _PROWLARR_ITEM = {
        "title": "[TGx] Weathering With You 2019 1080p BluRay x264",
        "indexer": "TorrentGalaxy",
        "downloadUrl": "https://torrentgalaxy.to/torrent/example.torrent",
        "magnetUrl": "magnet:?xt=urn:btih:abc123def456",
        "size": 2147483648,
        "seeders": 42,
        "leechers": 3,
        "publishDate": "2026-05-01T12:00:00Z",
        "grabs": 150,
    }

    def test_normalizes_full_item(self, mock_client):
        mock_client.add_response(
            url="http://prowlarr:9696/api/v1/search?query=%E5%A4%A9%E6%B0%94%E4%B9%8B%E5%AD%90&type=search&categories=2000&limit=20",
            json=[self._PROWLARR_ITEM],
        )
        cfg = _make_config(prowlarr_url="http://prowlarr:9696", api_key="key")
        adapter = ProwlarrAdapter(cfg)
        result = adapter.search(ResourceSearchRequest(query="天气之子", search_type="movie"))
        assert len(result.candidates) == 1
        c = result.candidates[0]
        assert c.title == "[TGx] Weathering With You 2019 1080p BluRay x264"
        assert c.indexer == "TorrentGalaxy"
        assert c.source == "prowlarr"
        assert c.download_url == "https://torrentgalaxy.to/torrent/example.torrent"
        assert c.magnet_url == "magnet:?xt=urn:btih:abc123def456"
        assert c.size_bytes == 2147483648
        assert c.seeders == 42
        assert c.leechers == 3
        assert c.publish_date == "2026-05-01T12:00:00Z"
        assert c.download_count == 150

    def test_normalizes_item_with_missing_fields(self, mock_client):
        item = {"title": "Minimal Release", "indexer": "MinTracker"}
        mock_client.add_response(
            url="http://prowlarr:9696/api/v1/search?query=minimal&type=search&categories=2000&limit=20",
            json=[item],
        )
        cfg = _make_config(prowlarr_url="http://prowlarr:9696", api_key="key")
        adapter = ProwlarrAdapter(cfg)
        result = adapter.search(ResourceSearchRequest(query="minimal", search_type="movie"))
        assert len(result.candidates) == 1
        c = result.candidates[0]
        assert c.title == "Minimal Release"
        assert c.indexer == "MinTracker"
        assert c.download_url is None
        assert c.size_bytes is None
        assert c.seeders == 0



class TestSearchErrorHandling:
    """验证 Prowlarr 异常处理"""

    @pytest.fixture
    def mock_client(self, httpx_mock):
        return httpx_mock

    def test_empty_result(self, mock_client):
        mock_client.add_response(
            url="http://prowlarr:9696/api/v1/search?query=nobody&type=search&categories=2000&limit=20",
            json=[],
        )
        cfg = _make_config(prowlarr_url="http://prowlarr:9696", api_key="key")
        adapter = ProwlarrAdapter(cfg)
        result = adapter.search(ResourceSearchRequest(query="nobody", search_type="movie"))
        assert result.candidates == []
        assert "未找到" in result.message

    def test_non_2xx_response(self, mock_client):
        mock_client.add_response(
            url="http://prowlarr:9696/api/v1/search?query=fail&type=search&categories=2000&limit=20",
            status_code=502,
        )
        cfg = _make_config(prowlarr_url="http://prowlarr:9696", api_key="key")
        adapter = ProwlarrAdapter(cfg)
        result = adapter.search(ResourceSearchRequest(query="fail", search_type="movie"))
        assert result.candidates == []
        assert "502" in result.message or "失败" in result.message or "Prowlarr" in result.message

    def test_timeout(self, mock_client):
        import httpx

        mock_client.add_exception(
            url="http://prowlarr:9696/api/v1/search?query=slow&type=search&categories=2000&limit=20",
            exception=httpx.TimeoutException("timeout"),
        )
        cfg = _make_config(prowlarr_url="http://prowlarr:9696", api_key="key")
        adapter = ProwlarrAdapter(cfg)
        result = adapter.search(ResourceSearchRequest(query="slow", search_type="movie"))
        assert result.candidates == []
        assert "超时" in result.message or "timeout" in result.message.lower()

    def test_not_configured_returns_empty_result(self):
        cfg = _make_config(prowlarr_url="", api_key="")
        adapter = ProwlarrAdapter(cfg)
        result = adapter.search(ResourceSearchRequest(query="test", search_type="movie"))
        assert result.candidates == []
        assert "未配置" in result.message
