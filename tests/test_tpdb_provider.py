"""TPDB Provider Adapter 测试 — 使用 httpx.MockTransport 模拟官方 API"""

from pathlib import Path

import httpx
import pytest

from media_pilot.config import AppConfig


class TestTpdbProviderConstruction:
    """TPDB 环境配置和启动校验"""

    def test_from_config_requires_api_key(self):
        config = AppConfig(
            downloads_dir=Path("/tmp"),
            watch_dir=Path("/tmp/watch"),
            workspace_dir=Path("/tmp"),
            movies_dir=Path("/tmp"),
            shows_dir=Path("/tmp"),
            database_dir=Path("/tmp"),
            tpdb_api_key=None,
        )
        from media_pilot.adapters.tpdb import TpdbAdultProvider
        with pytest.raises(ValueError, match="tpdb_api_key"):
            TpdbAdultProvider.from_config(config)

    def test_from_config_creates_with_api_key(self):
        config = AppConfig(
            downloads_dir=Path("/tmp"),
            watch_dir=Path("/tmp/watch"),
            workspace_dir=Path("/tmp"),
            movies_dir=Path("/tmp"),
            shows_dir=Path("/tmp"),
            database_dir=Path("/tmp"),
            tpdb_api_key="test-key",
        )
        from media_pilot.adapters.tpdb import TpdbAdultProvider
        provider = TpdbAdultProvider.from_config(config)
        assert provider is not None

    def test_factory_creates_tpdb_by_name(self):
        config = AppConfig(
            downloads_dir=Path("/tmp"),
            watch_dir=Path("/tmp/watch"),
            workspace_dir=Path("/tmp"),
            movies_dir=Path("/tmp"),
            shows_dir=Path("/tmp"),
            database_dir=Path("/tmp"),
            tpdb_api_key="test-key",
        )
        from media_pilot.adapters.factory import create_metadata_provider_by_name
        provider = create_metadata_provider_by_name(config, "tpdb")
        assert provider is not None


class TestTpdbImageMapping:
    """TPDB 图片 URL 映射 — 官方 API poster 结构"""

    def test_poster_url_from_item(self):
        from media_pilot.adapters.tpdb import _candidate_poster_url
        item = {
            "images": {
                "poster": {
                    "full": "https://example.com/poster.jpg",
                    "thumb": "https://example.com/thumb.jpg",
                }
            }
        }
        url = _candidate_poster_url(item)
        assert url == "https://example.com/poster.jpg"

    def test_poster_url_from_large_field(self):
        from media_pilot.adapters.tpdb import _candidate_poster_url
        item = {
            "images": {
                "poster": {
                    "large": "https://example.com/large.jpg",
                }
            }
        }
        url = _candidate_poster_url(item)
        assert url == "https://example.com/large.jpg"

    def test_poster_url_prepends_base(self):
        from media_pilot.adapters.tpdb import _candidate_poster_url
        item = {
            "images": {
                "poster": "images/poster.jpg",
            }
        }
        url = _candidate_poster_url(item)
        assert url is not None
        assert url.startswith("http")


class TestTpdbDetailMapping:
    """TPDB 详情归一化 — 官方 /jav/{uuid} 响应格式"""

    def test_detail_extracts_id_and_title(self):
        from media_pilot.adapters.tpdb import _jav_data_to_detail
        data = {
            "id": "abc-123-uuid",
            "external_id": "ABCD-123",
            "title": "Test Movie",
            "date": "2024-03-15",
        }
        detail = _jav_data_to_detail(data)
        assert detail.provider_id == "jav/abc-123-uuid"
        assert detail.title == "Test Movie"
        assert detail.year == 2024
        assert detail.original_title == "ABCD-123"

    def test_detail_extracts_optional_fields(self):
        from media_pilot.adapters.tpdb import _jav_data_to_detail
        data = {
            "id": "uuid-456",
            "title": "Another Movie",
            "description": "A test plot",
            "rating": 4.5,
            "date": "2023-06-01",
            "tags": [{"name": "Tag1"}, {"name": "Tag2"}],
            "site": {"name": "Test Studio", "logo": "logo.png"},
            "images": {
                "poster": {"full": "https://example.com/poster.jpg"},
            },
        }
        detail = _jav_data_to_detail(data)
        assert detail.plot == "A test plot"
        assert detail.rating == 4.5
        assert detail.premiered == "2023-06-01"
        assert detail.genres == ["Tag1", "Tag2"]
        assert detail.studios == ["Test Studio"]


class TestTpdbProviderErrors:
    """TPDB 请求失败时返回标准 MetadataProviderError"""

    def test_search_404_returns_provider_error(self):
        from media_pilot.adapters.tpdb import TpdbAdultProvider

        def handler(request):
            return httpx.Response(404, request=request)

        provider = TpdbAdultProvider(api_key="test-key")
        provider._client = httpx.Client(  # noqa: SLF001
            base_url="https://api.theporndb.net",
            transport=httpx.MockTransport(handler),
        )

        response = provider.search_movie("MVSD-682", language_priority=["zh-CN"])

        assert response.value is None
        assert response.error is not None
        assert response.error.provider == "tpdb"
        assert response.error.retryable is False


class TestTpdbConfidenceModel:
    """JAV 置信度模型"""

    def test_exact_match_external_id(self):
        from media_pilot.adapters.tpdb import _jav_confidence
        confidence, reason = _jav_confidence("MVSD-682", "MVSD-682", "Some Title")
        assert confidence == 0.95
        assert reason == "番号精确匹配"

    def test_match_in_title(self):
        from media_pilot.adapters.tpdb import _jav_confidence
        confidence, reason = _jav_confidence(
            "MVSD-682", "OTHER-999", "MVSD-682 Special Edition"
        )
        assert confidence == 0.90
        assert reason == "标题包含番号"

    def test_match_without_dashes(self):
        from media_pilot.adapters.tpdb import _jav_confidence
        confidence, reason = _jav_confidence("MVSD682", "MVSD-682", "Some Title")
        assert confidence == 0.88
        assert reason == "番号匹配（忽略横杠）"

    def test_same_prefix_different_number(self):
        from media_pilot.adapters.tpdb import _jav_confidence
        confidence, reason = _jav_confidence("MVSD-682", "MVSD-082", "Some Title")
        assert confidence == 0.45
        assert reason == "番号前缀相同但编号不同"

    def test_fuzzy_match(self):
        from media_pilot.adapters.tpdb import _jav_confidence
        confidence, _reason = _jav_confidence("MVSD-682", "MVSD", "Some Title")
        assert confidence <= 0.30

    def test_normalize_code(self):
        from media_pilot.adapters.tpdb import _normalize_code
        assert _normalize_code("mvsd-682") == "MVSD-682"
        assert _normalize_code("MVSD 682") == "MVSD682"
        assert _normalize_code("MVSD–682") == "MVSD-682"  # em-dash → hyphen


class TestTpdbProviderIdParsing:
    """provider_id 解析"""

    def test_parse_jav_provider_id(self):
        from media_pilot.adapters.tpdb import _parse_provider_id
        result = _parse_provider_id("jav/abc-123-uuid")
        assert result == ("jav", "abc-123-uuid")

    def test_parse_bare_uuid_falls_back_to_jav(self):
        from media_pilot.adapters.tpdb import _parse_provider_id
        result = _parse_provider_id("bare-uuid-456")
        assert result == ("jav", "bare-uuid-456")

    def test_parse_empty_returns_none(self):
        from media_pilot.adapters.tpdb import _parse_provider_id
        assert _parse_provider_id("") is None
        assert _parse_provider_id(None) is None


# ======================================================================
# MockTransport 集成测试 — 使用模拟 TPDB 官方 API 响应
# ======================================================================

_JAV_SEARCH_RESPONSE = {
    "data": [
        {
            "id": "abc-123-uuid",
            "external_id": "MVSD-682",
            "title": "MVSD-682 Special Edition",
            "description": "A test description",
            "date": "2024-03-15",
            "rating": 4.5,
            "tags": [{"name": "Tag1"}, {"name": "Tag2"}],
            "site": {"name": "Test Studio", "logo": "logo.png"},
            "performers": [{"name": "Performer A", "images": {"face": "face.jpg"}}],
            "directors": [{"name": "Director X"}],
            "images": {
                "poster": {"full": "https://example.com/poster.jpg"},
                "background": {"large": "https://example.com/bg.jpg"},
            },
            "sku": "SKU-001",
            "url": "https://example.com/scene",
            "type": "jav",
        },
    ],
}

_JAV_EMPTY_RESPONSE = {"data": []}


def _make_mock_transport(status_code=200, response_json=None):
    """创建 MockTransport 返回指定响应"""
    if response_json is None:
        response_json = _JAV_EMPTY_RESPONSE

    def handler(request):
        return httpx.Response(status_code, json=response_json, request=request)

    return httpx.MockTransport(handler)


def _mock_provider(transport):
    from media_pilot.adapters.tpdb import TpdbAdultProvider
    provider = TpdbAdultProvider(api_key="test-key")
    provider._client = httpx.Client(  # noqa: SLF001
        base_url="https://api.theporndb.net",
        transport=transport,
    )
    return provider


class TestTpdbMockTransportSearch:
    """8.1-8.3: /jav 搜索 MockTransport 测试"""

    def test_jav_search_q_param_success(self):
        """8.1: /jav?q=<keyword> 搜索成功"""
        transport = _make_mock_transport(response_json=_JAV_SEARCH_RESPONSE)
        provider = _mock_provider(transport)
        response = provider.search("MVSD-682")
        assert response.is_success
        assert len(response.value) == 1
        candidate = response.value[0]
        assert candidate.provider == "tpdb"
        assert candidate.provider_id == "jav/abc-123-uuid"
        assert candidate.title == "MVSD-682 Special Edition"
        assert candidate.original_title == "MVSD-682"
        assert candidate.year == 2024
        assert candidate.confidence == 0.95

    def test_jav_search_empty_fallback_to_parse(self):
        """8.2: q= 空结果后降级到 parse="""
        call_count = [0]

        def handler(request):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: q= returns empty
                return httpx.Response(200, json=_JAV_EMPTY_RESPONSE, request=request)
            # Second call: parse= returns results
            return httpx.Response(200, json=_JAV_SEARCH_RESPONSE, request=request)

        provider = _mock_provider(httpx.MockTransport(handler))
        response = provider.search("MVSD-682")
        assert call_count[0] == 2
        assert response.is_success
        assert len(response.value) == 1

    def test_jav_search_empty_result_is_not_error(self):
        """8.3: /jav 返回 200 空数组不是错误"""
        transport = _make_mock_transport(response_json=_JAV_EMPTY_RESPONSE)
        provider = _mock_provider(transport)
        response = provider.search("NONEXIST-999")
        assert response.error is None
        assert response.value == []


class TestTpdbMockTransportDetail:
    """8.4-8.5: /jav/{uuid} 详情 MockTransport 测试"""

    _DETAIL_RESPONSE = {
        "data": {
            "id": "abc-123-uuid",
            "external_id": "MVSD-682",
            "title": "Test JAV Movie",
            "description": "Full plot description",
            "date": "2024-03-15",
            "rating": 4.2,
            "tags": [{"name": "TagA"}, {"name": "TagB"}],
            "site": {"name": "Studio X"},
            "performers": [{"name": "Actor 1"}],
            "directors": [],
            "images": {
                "poster": {"full": "https://example.com/poster.jpg"},
            },
        },
    }

    def test_jav_detail_success(self):
        """8.4: /jav/{uuid} 详情成功"""
        def handler(request):
            return httpx.Response(200, json=self._DETAIL_RESPONSE, request=request)

        provider = _mock_provider(httpx.MockTransport(handler))
        response = provider.get_details("jav/abc-123-uuid")
        assert response.is_success
        detail = response.value
        assert detail.provider_id == "jav/abc-123-uuid"
        assert detail.title == "Test JAV Movie"
        assert detail.original_title == "MVSD-682"
        assert detail.plot == "Full plot description"
        assert detail.rating == 4.2
        assert detail.genres == ["TagA", "TagB"]
        assert detail.studios == ["Studio X"]
        assert detail.premiered == "2024-03-15"

    def test_jav_detail_404_is_error(self):
        """8.5: /jav/{uuid} 404 返回 provider error"""
        def handler(request):
            return httpx.Response(404, request=request)

        provider = _mock_provider(httpx.MockTransport(handler))
        response = provider.get_details("jav/nonexistent-uuid")
        assert response.value is None
        assert response.error is not None
        assert response.error.provider == "tpdb"

    def test_jav_detail_scenes_is_unsupported(self):
        """8.11: scenes/<uuid> 返回不支持错误"""
        from media_pilot.adapters.tpdb import TpdbAdultProvider
        provider = TpdbAdultProvider(api_key="test-key")
        response = provider.get_details("scenes/some-uuid")
        assert response.value is None
        assert response.error is not None
        assert response.error.code == "unsupported_type"

    def test_jav_detail_movies_is_unsupported(self):
        """8.11: movies/<uuid> 返回不支持错误"""
        from media_pilot.adapters.tpdb import TpdbAdultProvider
        provider = TpdbAdultProvider(api_key="test-key")
        response = provider.get_details("movies/some-uuid")
        assert response.value is None
        assert response.error is not None
        assert response.error.code == "unsupported_type"


class TestTpdbMockTransportPing:
    """8.6-8.7: /auth/user 连通性探测 MockTransport 测试"""

    def test_auth_user_200_success(self):
        """8.6: /auth/user 200 返回正常"""
        def handler(request):
            return httpx.Response(200, json={"data": {}}, request=request)

        provider = _mock_provider(httpx.MockTransport(handler))
        ok, message, latency_ms = provider.ping()
        assert ok is True
        assert "正常" in message
        assert latency_ms is not None

    def test_auth_user_401_failure(self):
        """8.7: /auth/user 401 token 无效"""
        def handler(request):
            return httpx.Response(401, request=request)

        provider = _mock_provider(httpx.MockTransport(handler))
        ok, message, latency_ms = provider.ping()
        assert ok is False
        assert "token" in message.lower()

    def test_auth_user_403_failure(self):
        """8.7: /auth/user 403 权限不足"""
        def handler(request):
            return httpx.Response(403, request=request)

        provider = _mock_provider(httpx.MockTransport(handler))
        ok, message, latency_ms = provider.ping()
        assert ok is False
        assert "token" in message.lower()
