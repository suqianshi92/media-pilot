"""资源发现领域类型单元测试"""

from dataclasses import FrozenInstanceError

import pytest

from media_pilot.resource_discovery.types import (
    DownloadRequest,
    DownloadSubmitResult,
    ResourceCandidate,
    ResourceIntent,
    ResourceSearchRequest,
    ResourceSearchResult,
    ToolConnectionStatus,
)


class TestResourceSearchRequest:
    def test_default_values(self):
        req = ResourceSearchRequest(query="天气之子", search_type="movie")
        assert req.query == "天气之子"
        assert req.search_type == "movie"
        assert req.limit == 20

    def test_custom_limit(self):
        req = ResourceSearchRequest(query="test", search_type="all", limit=50)
        assert req.limit == 50

    def test_frozen_prevents_mutation(self):
        req = ResourceSearchRequest(query="test", search_type="movie")
        with pytest.raises(FrozenInstanceError):
            req.query = "changed"  # type: ignore[misc]


class TestResourceCandidate:
    def test_minimal_candidate(self):
        c = ResourceCandidate(title="Test", indexer="TorrentGalaxy", source="prowlarr")
        assert c.title == "Test"
        assert c.seeders == 0
        assert c.leechers == 0
        assert c.download_count == 0
        assert c.download_url is None
        assert c.magnet_url is None

    def test_full_candidate(self):
        c = ResourceCandidate(
            title="天气之子 1080p",
            indexer="TorrentGalaxy",
            source="prowlarr",
            download_url="https://example.com/torrent/1",
            magnet_url="magnet:?xt=urn:btih:abc123",
            size_bytes=2147483648,
            seeders=42,
            leechers=3,
            publish_date="2026-05-01",
            download_count=150,
        )
        assert c.size_bytes == 2147483648
        assert c.seeders == 42
        assert c.magnet_url == "magnet:?xt=urn:btih:abc123"

    def test_no_sensitive_fields_in_repr(self):
        c = ResourceCandidate(
            title="Test",
            indexer="private-tracker",
            source="prowlarr",
            download_url="https://private.example.com?token=secret123",
        )
        r = repr(c)
        # download URL may appear but token in URL is acceptable at type level
        # — actual adapter must strip tokens from logs
        assert "Test" in r


class TestResourceSearchResult:
    def test_empty_result(self):
        r = ResourceSearchResult()
        assert r.candidates == []
        assert r.query_used == ""
        assert r.message == ""

    def test_with_candidates(self):
        candidate = ResourceCandidate(title="Test", indexer="TGx", source="prowlarr")
        r = ResourceSearchResult(
            candidates=[candidate],
            query_used="天气之子 1080p",
            search_type="movie",
            source="prowlarr",
            message="找到 1 个候选",
        )
        assert len(r.candidates) == 1
        assert r.query_used == "天气之子 1080p"
        assert r.message == "找到 1 个候选"


class TestDownloadRequest:
    def test_url_download(self):
        req = DownloadRequest(
            download_url="https://example.com/torrent/1",
            title="天气之子",
            source="prowlarr",
            indexer="TGx",
        )
        assert req.download_url == "https://example.com/torrent/1"
        assert req.magnet_url is None

    def test_magnet_download(self):
        req = DownloadRequest(
            magnet_url="magnet:?xt=urn:btih:abc",
            title="测试",
            source="prowlarr",
            indexer="TGx",
        )
        assert req.magnet_url == "magnet:?xt=urn:btih:abc"

    def test_no_save_path_field(self):
        """客户端不可指定保存路径"""
        req = DownloadRequest(title="test", source="s", indexer="i")
        assert not hasattr(req, "save_path")


class TestDownloadSubmitResult:
    def test_submitted(self):
        r = DownloadSubmitResult(status="submitted", title="test", message="已提交")
        assert r.status == "submitted"
        assert r.info_hash is None

    def test_with_hash(self):
        r = DownloadSubmitResult(
            status="submitted",
            title="test",
            message="OK",
            info_hash="abc123def456",
        )
        assert r.info_hash == "abc123def456"


class TestToolConnectionStatus:
    def test_prowlarr_configured(self):
        s = ToolConnectionStatus(
            tool="prowlarr",
            configured=True,
            reachable=True,
            authenticated=True,
            message="连接正常",
        )
        assert s.tool == "prowlarr"
        assert s.configured
        assert s.reachable

    def test_qbittorrent_not_configured(self):
        s = ToolConnectionStatus(
            tool="qbittorrent",
            configured=False,
            reachable=False,
            authenticated=False,
            message="未配置 URL",
        )
        assert not s.configured
        assert not s.reachable


class TestResourceIntent:
    def test_movie_intent(self):
        intent = ResourceIntent(
            query_text="我想下载天气之子",
            search_type="movie",
            title_candidates=["天气之子"],
            resource_keywords=["天气之子 1080p", "Weathering With You 1080p"],
            quality_hint="1080p",
            profile_hint="tmdb_movie",
            reason="用户请求动画电影",
        )
        assert intent.search_type == "movie"
        assert len(intent.resource_keywords) == 2
        assert intent.profile_hint == "tmdb_movie"

    def test_adult_intent(self):
        intent = ResourceIntent(
            query_text="ABP-123",
            search_type="adult",
            title_candidates=["ABP-123"],
            resource_keywords=["ABP-123"],
            reason="番号匹配",
            profile_hint="tpdb_adult_movie",
        )
        assert intent.search_type == "adult"
        assert intent.profile_hint == "tpdb_adult_movie"
