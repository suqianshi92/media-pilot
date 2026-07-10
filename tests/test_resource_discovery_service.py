"""资源发现服务单元测试 — 编排 LLM 意图解析 + Prowlarr 搜索 + qBittorrent 下载"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from media_pilot.config.settings import AppConfig
from media_pilot.resource_discovery.types import (
    DownloadSubmitResult,
    ResourceCandidate,
    ResourceIntent,
    ResourceSearchResult,
)


def _make_config(*, llm_configured: bool = True) -> AppConfig:
    from pathlib import Path

    return AppConfig(
        downloads_dir=Path("/tmp/test-dl"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp/test-ws"),
        movies_dir=Path("/tmp/test-movies"),
        shows_dir=Path("/tmp/test-shows"),
        database_dir=Path("/tmp/test-db"),
        prowlarr_url="http://prowlarr:9696" if llm_configured else "",
        prowlarr_api_key="test-key" if llm_configured else "",
        qbittorrent_url="http://qbittorrent:8080" if llm_configured else "",
        qbittorrent_username="admin",
        qbittorrent_password="pass",
        llm_api_key="key" if llm_configured else None,
        llm_base_url="https://api.openai.com/v1" if llm_configured else None,
        llm_model="gpt-4" if llm_configured else None,
    )


# ── 搜索服务 ──


class TestResourceDiscoveryService:
    def test_llm_not_configured_returns_error(self):
        """LLM 未配置时返回错误，不调用 Prowlarr"""
        from media_pilot.services.resource_discovery import search_resources

        cfg = _make_config(llm_configured=False)
        result = search_resources("天气之子", cfg)

        assert result["status"] == "error"
        assert "LLM" in result.get("message", "")

    @patch("media_pilot.services.resource_discovery.ResourceIntentParser")
    @patch("media_pilot.services.resource_discovery.ProwlarrAdapter")
    def test_llm_directly_called_for_simple_title(self, mock_prowlarr, mock_parser_cls):
        """即使是简单片名也必须调用 LLM"""
        from media_pilot.services.resource_discovery import search_resources

        mock_parser = MagicMock()
        mock_parser.parse.return_value = ResourceIntent(
            query_text="天气之子",
            search_type="movie",
            title_candidates=["天气之子"],
            resource_keywords=["天气之子 1080p"],
            resource_search_keywords=["天气之子 1080p"],
            reason="电影关键词",
        )
        mock_parser_cls.return_value = mock_parser

        mock_adapter = MagicMock()
        mock_adapter.search.return_value = ResourceSearchResult(
            candidates=[],
            source="prowlarr",
            message="未找到",
        )
        mock_prowlarr.return_value = mock_adapter

        cfg = _make_config()
        result = search_resources("天气之子", cfg)

        mock_parser.parse.assert_called_once_with(
            "天气之子", preferred_language="zh", enabled_profiles=None,
        )
        assert "intent" in result["data"]

    @patch("media_pilot.services.resource_discovery.ResourceIntentParser")
    @patch("media_pilot.services.resource_discovery.ProwlarrAdapter")
    def test_search_with_candidates(self, mock_prowlarr, mock_parser_cls):
        """LLM 解析成功 + Prowlarr 返回候选"""
        from media_pilot.services.resource_discovery import search_resources

        mock_parser = MagicMock()
        mock_parser.parse.return_value = ResourceIntent(
            query_text="天气之子 1080p",
            search_type="movie",
            title_candidates=["天气之子"],
            resource_keywords=["天气之子 1080p"],
            quality_hint="1080p",
            profile_hint="tmdb_movie",
            reason="用户请求动画电影",
        )
        mock_parser_cls.return_value = mock_parser

        candidate = ResourceCandidate(
            title="[TGx] Weathering With You 2019 1080p",
            indexer="TGx",
            source="prowlarr",
            download_url="https://example.com/1.torrent",
            seeders=100,
        )
        mock_adapter = MagicMock()
        mock_adapter.search.return_value = ResourceSearchResult(
            candidates=[candidate],
            source="prowlarr",
            query_used="天气之子 1080p",
            search_type="movie",
            message="找到 1 个候选",
        )
        mock_prowlarr.return_value = mock_adapter

        cfg = _make_config()
        result = search_resources("天气之子 1080p", cfg)

        assert result["status"] == "success"
        assert len(result["data"]["candidates"]) == 1
        assert result["data"]["candidates"][0]["title"] == "[TGx] Weathering With You 2019 1080p"
        # 验证 LLM 摘要字段存在
        assert "intent" in result["data"]
        # 安全：候选不得泄露下载凭证
        for c in result["data"]["candidates"]:
            assert "download_url" not in c, f"download_url 泄露: {c.get('download_url')}"
            assert "magnet_url" not in c, f"magnet_url 泄露: {c.get('magnet_url')}"
        intent = result["data"]["intent"]
        assert intent["search_type"] == "movie"
        assert intent["reason"] == "用户请求动画电影"


    @patch("media_pilot.services.resource_discovery.ResourceIntentParser")
    @patch("media_pilot.services.resource_discovery.ProwlarrAdapter")
    def test_keyword_fallback_when_first_empty(self, mock_prowlarr, mock_parser_cls):
        """LLM 返回多个关键词时，第一个无结果则回退到第二个"""
        from media_pilot.services.resource_discovery import search_resources

        mock_parser = MagicMock()
        mock_parser.parse.return_value = ResourceIntent(
            query_text="天气之子 Weathering With You",
            search_type="movie",
            title_candidates=["天气之子"],
            resource_keywords=["天气之子 1080p", "Weathering With You"],
            reason="中英文关键词",
        )
        mock_parser_cls.return_value = mock_parser

        mock_adapter = MagicMock()

        def search_side_effect(req):
            if req.query == "天气之子 1080p":
                return ResourceSearchResult(
                    candidates=[], source="prowlarr",
                    query_used=req.query, search_type=req.search_type,
                    message="未找到",
                )
            else:
                candidate = ResourceCandidate(
                    title="[TGx] Weathering With You 2019", indexer="TGx",
                    source="prowlarr", download_url="https://x.com/t.torrent", seeders=42,
                )
                return ResourceSearchResult(
                    candidates=[candidate], source="prowlarr",
                    query_used=req.query, search_type=req.search_type,
                    message="找到 1 个候选",
                )

        mock_adapter.search.side_effect = search_side_effect
        mock_prowlarr.return_value = mock_adapter

        cfg = _make_config()
        result = search_resources("天气之子 Weathering With You", cfg)

        assert result["status"] == "success"
        assert len(result["data"]["candidates"]) == 1
        assert result["data"]["candidates"][0]["title"] == "[TGx] Weathering With You 2019"

    @patch("media_pilot.services.resource_discovery.ResourceIntentParser")
    @patch("media_pilot.services.resource_discovery.ProwlarrAdapter")
    def test_search_empty_results(self, mock_prowlarr, mock_parser_cls):
        """Prowlarr 返回空结果"""
        from media_pilot.services.resource_discovery import search_resources

        mock_parser = MagicMock()
        mock_parser.parse.return_value = ResourceIntent(
            query_text="不存在的电影",
            search_type="movie",
            title_candidates=["不存在的电影"],
            resource_keywords=["不存在的电影"],
            reason="无",
        )
        mock_parser_cls.return_value = mock_parser

        mock_adapter = MagicMock()
        mock_adapter.search.return_value = ResourceSearchResult(
            candidates=[],
            source="prowlarr",
            message='未找到与 "不存在的电影" 相关的资源',
        )
        mock_prowlarr.return_value = mock_adapter

        cfg = _make_config()
        result = search_resources("不存在的电影", cfg)

        assert result["status"] == "success"
        assert result["data"]["candidates"] == []

    @patch("media_pilot.services.resource_discovery.ResourceIntentParser")
    def test_llm_parse_error_returns_error(self, mock_parser_cls):
        """LLM 解析失败时返回错误"""
        from media_pilot.resource_discovery.intent_parser import IntentParseError
        from media_pilot.services.resource_discovery import search_resources

        mock_parser = MagicMock()
        mock_parser.parse.side_effect = IntentParseError("LLM 挂了")
        mock_parser_cls.return_value = mock_parser

        cfg = _make_config()
        result = search_resources("test", cfg)

        assert result["status"] == "error"
        assert "LLM" in result.get("message", "")

    @patch("media_pilot.services.resource_discovery.ResourceIntentParser")
    @patch("media_pilot.services.resource_discovery.ProwlarrAdapter")
    def test_prowlarr_not_configured_returns_error(self, mock_prowlarr, mock_parser_cls):
        """Prowlarr 未配置时返回 error，不伪装成功"""
        from media_pilot.services.resource_discovery import search_resources

        mock_parser = MagicMock()
        mock_parser.parse.return_value = ResourceIntent(
            query_text="test",
            search_type="movie",
            title_candidates=["test"],
            resource_keywords=["test"],
            reason="test",
        )
        mock_parser_cls.return_value = mock_parser

        mock_adapter = MagicMock()
        mock_adapter.search.return_value = ResourceSearchResult(
            candidates=[],
            source="prowlarr",
            message="Prowlarr 未配置",
            error_code="not_configured",
        )
        mock_prowlarr.return_value = mock_adapter

        cfg = _make_config()
        result = search_resources("test", cfg)

        assert result["status"] == "error"
        assert "Prowlarr 未配置" in result.get("message", "")


    @patch("media_pilot.services.resource_discovery.ResourceIntentParser")
    @patch("media_pilot.services.resource_discovery.ProwlarrAdapter")
    def test_prowlarr_timeout_returns_error(self, mock_prowlarr, mock_parser_cls):
        """Prowlarr 超时返回 error"""
        from media_pilot.services.resource_discovery import search_resources

        mock_parser = MagicMock()
        mock_parser.parse.return_value = ResourceIntent(
            query_text="test", search_type="movie",
            title_candidates=["test"], resource_keywords=["test"], reason="test",
        )
        mock_parser_cls.return_value = mock_parser

        mock_adapter = MagicMock()
        mock_adapter.search.return_value = ResourceSearchResult(
            candidates=[], source="prowlarr",
            message="Prowlarr 搜索超时（15.0s）",
            error_code="timeout",
        )
        mock_prowlarr.return_value = mock_adapter

        cfg = _make_config()
        result = search_resources("test", cfg)
        assert result["status"] == "error"
        assert "超时" in result.get("message", "")

    @patch("media_pilot.services.resource_discovery.ResourceIntentParser")
    @patch("media_pilot.services.resource_discovery.ProwlarrAdapter")
    def test_prowlarr_http_error_returns_error(self, mock_prowlarr, mock_parser_cls):
        """Prowlarr HTTP 失败返回 error"""
        from media_pilot.services.resource_discovery import search_resources

        mock_parser = MagicMock()
        mock_parser.parse.return_value = ResourceIntent(
            query_text="test", search_type="movie",
            title_candidates=["test"], resource_keywords=["test"], reason="test",
        )
        mock_parser_cls.return_value = mock_parser

        mock_adapter = MagicMock()
        mock_adapter.search.return_value = ResourceSearchResult(
            candidates=[], source="prowlarr",
            message="Prowlarr 搜索失败（HTTP 502）",
            error_code="http_error",
        )
        mock_prowlarr.return_value = mock_adapter

        cfg = _make_config()
        result = search_resources("test", cfg)
        assert result["status"] == "error"
        assert "502" in result.get("message", "")


    @patch("media_pilot.services.resource_discovery.ResourceIntentParser")
    @patch("media_pilot.services.resource_discovery.ProwlarrAdapter")
    def test_response_no_sensitive_leak(self, mock_prowlarr, mock_parser_cls):
        """搜索响应 JSON 不含 apikey/token/passkey 等敏感片段"""
        import json

        from media_pilot.services.resource_discovery import search_resources

        mock_parser = MagicMock()
        mock_parser.parse.return_value = ResourceIntent(
            query_text="test", search_type="movie",
            title_candidates=["test"], resource_keywords=["test"], reason="t",
        )
        mock_parser_cls.return_value = mock_parser

        candidate = ResourceCandidate(
            title="Test Movie", indexer="TGx", source="prowlarr",
            download_url="https://prowlarr.local/1/download/abc?apikey=secret123&token=xyz",
            magnet_url="magnet:?xt=urn:btih:abc&passkey=hidden",
            seeders=10,
        )
        mock_adapter = MagicMock()
        mock_adapter.search.return_value = ResourceSearchResult(
            candidates=[candidate], source="prowlarr",
            query_used="test", search_type="movie", message="ok",
        )
        mock_prowlarr.return_value = mock_adapter

        cfg = _make_config()
        result = search_resources("test", cfg)
        raw = json.dumps(result, ensure_ascii=False)

        forbidden = ["apikey", "api_key", "passkey", "token=xyz", "secret123", "hidden"]
        for pattern in forbidden:
            assert pattern not in raw.lower(), f"响应泄露敏感片段: {pattern}"

# ── 下载服务 ──

class TestDownloadService:
    def test_token_expired_returns_error(self):
        """token 过期返回错误"""
        import time

        from media_pilot.services.resource_discovery import (
            _CANDIDATE_CACHE,
            _CANDIDATE_TTL_SECONDS,
            _store_candidate,
            submit_download,
        )

        candidate = ResourceCandidate(
            title="过期候选", indexer="TGx", source="prowlarr",
            download_url="https://example.com/t.torrent", seeders=1,
        )
        token = _store_candidate(candidate)
        # 篡改创建时间使其过期
        _CANDIDATE_CACHE[token]["created_at"] = time.time() - _CANDIDATE_TTL_SECONDS - 1

        cfg = _make_config()
        result = submit_download(cfg, candidate_token=token)
        assert result["status"] == "error"
        assert "已过期" in result.get("message", "")

        # cleanup
        _CANDIDATE_CACHE.pop(token, None)

    def test_token_not_found_returns_error(self):
        """不存在的 token 返回错误"""
        from media_pilot.services.resource_discovery import submit_download

        cfg = _make_config()
        result = submit_download(cfg, candidate_token="nonexistent_token")
        assert result["status"] == "error"
        assert "已过期" in result.get("message", "")

    @patch("media_pilot.services.resource_discovery.QBittorrentAdapter")
    def test_valid_token_submit_success(self, mock_qb_cls):
        """有效 token 提交下载成功"""
        from media_pilot.services.resource_discovery import _store_candidate, submit_download

        candidate = ResourceCandidate(
            title="天气之子", indexer="TGx", source="prowlarr",
            magnet_url="magnet:?xt=urn:btih:abc", seeders=100,
        )
        token = _store_candidate(candidate)

        mock_adapter = MagicMock()
        mock_adapter.add_download.return_value = DownloadSubmitResult(
            status="submitted", title="天气之子", source="prowlarr",
            message='已提交到 qBittorrent："天气之子"',
        )
        mock_qb_cls.return_value = mock_adapter

        cfg = _make_config()
        result = submit_download(cfg, candidate_token=token)
        assert result["status"] == "success"
        assert "已提交" in result.get("message", "")
        # 验证 qBittorrent 收到的下载请求包含正确凭证
        call_args = mock_adapter.add_download.call_args[0][0]
        assert call_args.magnet_url == "magnet:?xt=urn:btih:abc"
        assert call_args.title == "天气之子"

    @patch("media_pilot.services.resource_discovery.QBittorrentAdapter")
    def test_valid_token_submit_failure(self, mock_qb_cls):
        """有效 token 但 qBittorrent 提交失败"""
        from media_pilot.services.resource_discovery import _store_candidate, submit_download

        candidate = ResourceCandidate(
            title="失败资源", indexer="TGx", source="prowlarr",
            download_url="https://bad.torrent", seeders=1,
        )
        token = _store_candidate(candidate)

        mock_adapter = MagicMock()
        mock_adapter.add_download.return_value = DownloadSubmitResult(
            status="failed", title="失败资源", source="prowlarr",
            message="下载提交失败（HTTP 500）",
        )
        mock_qb_cls.return_value = mock_adapter

        cfg = _make_config()
        result = submit_download(cfg, candidate_token=token)
        assert result["status"] == "error"
        assert "500" in result.get("message", "")

    def test_not_configured_returns_error(self):
        """qBittorrent 未配置时返回错误"""
        from media_pilot.services.resource_discovery import _store_candidate, submit_download

        candidate = ResourceCandidate(
            title="test", indexer="i", source="prowlarr",
            download_url="https://example.com/1.torrent", seeders=1,
        )
        token = _store_candidate(candidate)

        cfg = _make_config(llm_configured=False)
        result = submit_download(cfg, candidate_token=token)
        assert result["status"] == "error"
        assert "未配置" in result.get("message", "")

    def test_candidate_no_download_urls_returns_error(self):
        """候选无下载凭证返回错误"""
        from media_pilot.services.resource_discovery import _store_candidate, submit_download

        candidate = ResourceCandidate(
            title="无链接", indexer="TGx", source="prowlarr",
            download_url=None, magnet_url=None, seeders=1,
        )
        token = _store_candidate(candidate)

        cfg = _make_config()
        result = submit_download(cfg, candidate_token=token)
        assert result["status"] == "error"
        assert "无可下载" in result.get("message", "")

    @patch("media_pilot.services.resource_discovery.QBittorrentAdapter")
    def test_submit_creates_download_task(self, mock_qb_cls):
        """下载成功后创建持久化 DownloadTask 并返回 ID"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from media_pilot.repository.database import Base
        from media_pilot.repository.repositories import DownloadTaskRepository
        from media_pilot.services.resource_discovery import _store_candidate, submit_download

        # 用内存 SQLite 创建 session factory
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine, expire_on_commit=False)

        candidate = ResourceCandidate(
            title="测试资源", indexer="TGx", source="prowlarr",
            download_url="https://example.com/t.torrent", seeders=10,
        )
        token = _store_candidate(
            candidate,
            intent_context={"search_type": "adult"},
        )

        mock_adapter = MagicMock()
        mock_adapter.add_download.return_value = DownloadSubmitResult(
            status="submitted", title="测试资源", source="prowlarr",
            message="已提交", info_hash="deadbeef1234",
        )
        mock_qb_cls.return_value = mock_adapter

        cfg = _make_config()
        # 默认 _make_config 未设 qbittorrent_save_path，使用 AppConfig 默认值
        result = submit_download(
            cfg,
            candidate_token=token,
            session_factory=sf,
            owner_user_id="user-1",
        )

        assert result["status"] == "success"
        assert "download_task_id" in result["data"]
        task_id = result["data"]["download_task_id"]
        assert task_id is not None

        # 验证 DB 中存在
        with sf() as s:
            repo = DownloadTaskRepository(s)
            task = repo.get(task_id)
            assert task is not None
            assert task.title == "测试资源"
            assert task.source == "prowlarr"
            assert task.indexer == "TGx"
            assert task.qb_hash == "deadbeef1234"
            assert task.status == "submitted"
            assert task.owner_user_id == "user-1"
            assert task.is_adult is True

    @patch("media_pilot.services.resource_discovery.QBittorrentAdapter")
    def test_submit_failure_does_not_create_task(self, mock_qb_cls):
        """qB 提交失败时不创建下载任务"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from media_pilot.repository.database import Base
        from media_pilot.repository.repositories import DownloadTaskRepository
        from media_pilot.services.resource_discovery import _store_candidate, submit_download

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine, expire_on_commit=False)

        candidate = ResourceCandidate(
            title="失败测试", indexer="TGx", source="prowlarr",
            download_url="https://bad.torrent", seeders=1,
        )
        token = _store_candidate(candidate)

        mock_adapter = MagicMock()
        mock_adapter.add_download.return_value = DownloadSubmitResult(
            status="failed", title="失败测试", source="prowlarr",
            message="HTTP 500",
        )
        mock_qb_cls.return_value = mock_adapter

        cfg = _make_config()
        result = submit_download(cfg, candidate_token=token, session_factory=sf)

        assert result["status"] == "error"
        # DB 中无任务
        with sf() as s:
            repo = DownloadTaskRepository(s)
            tasks = repo.list_non_terminal()
            assert len(tasks) == 0

    @patch("media_pilot.services.resource_discovery.QBittorrentAdapter")
    def test_submit_hash_not_available_creates_task_anyway(self, mock_qb_cls):
        """qB 接受但 hash 暂不可用时仍创建 submitted 状态任务"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from media_pilot.repository.database import Base
        from media_pilot.repository.repositories import DownloadTaskRepository
        from media_pilot.services.resource_discovery import _store_candidate, submit_download

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine, expire_on_commit=False)

        candidate = ResourceCandidate(
            title="Hash 暂缺", indexer="TGx", source="prowlarr",
            magnet_url="magnet:?xt=urn:btih:xyz", seeders=5,
        )
        token = _store_candidate(candidate)

        mock_adapter = MagicMock()
        mock_adapter.add_download.return_value = DownloadSubmitResult(
            status="submitted", title="Hash 暂缺", source="prowlarr",
            message="已提交", info_hash=None,  # hash 不可用
        )
        mock_qb_cls.return_value = mock_adapter

        cfg = _make_config()
        result = submit_download(cfg, candidate_token=token, session_factory=sf)

        assert result["status"] == "success"
        task_id = result["data"].get("download_task_id")
        assert task_id is not None

        with sf() as s:
            repo = DownloadTaskRepository(s)
            task = repo.get(task_id)
            assert task is not None
            assert task.status == "submitted"
            assert task.qb_hash is None  # 待后续同步补齐


# ── 连通性 ──


class TestConnectivityProbes:
    def test_probe_prowlarr_not_configured(self):
        from media_pilot.services.resource_discovery import probe_prowlarr

        cfg = _make_config(llm_configured=False)
        status = probe_prowlarr(cfg)
        assert status["provider"] == "prowlarr"
        assert status["status"] == "not_configured"

    def test_probe_qbittorrent_not_configured(self):
        from media_pilot.services.resource_discovery import probe_qbittorrent

        cfg = _make_config(llm_configured=False)
        status = probe_qbittorrent(cfg)
        assert status["provider"] == "qbittorrent"
        assert status["status"] == "not_configured"
