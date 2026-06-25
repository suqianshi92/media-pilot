import httpx

from media_pilot.adapters.tmdb import TmdbMovieProvider
from media_pilot.config import AppConfig


def test_tmdb_provider_builds_from_config_and_requires_api_key(tmp_path) -> None:
    config = AppConfig(
downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="secret",
    )

    provider = TmdbMovieProvider.from_config(config)

    assert provider.api_key == "secret"
    assert provider.language_priority == ("zh-CN", "en-US")


def test_tmdb_provider_search_normalizes_candidates_and_confidence() -> None:
    provider = make_provider(
        {
            route_key(
                "/search/movie",
                query="Example Movie 2026",
                language="zh-CN",
                api_key="test-key",
            ): {
                "results": [
                    {
                        "id": 123,
                        "title": "Example Movie",
                        "original_title": "Example Movie",
                        "release_date": "2026-02-10",
                        "overview": "Primary result",
                        "poster_path": "/poster-a.jpg",
                    },
                    {
                        "id": 456,
                        "title": "Example Movie Returns",
                        "original_title": "Example Movie Returns",
                        "release_date": "2025-02-10",
                        "overview": "Secondary result",
                        "poster_path": "/poster-b.jpg",
                    },
                ]
            }
        }
    )

    response = provider.search_movie("Example Movie 2026", language_priority=["zh-CN", "en-US"])

    assert response.is_success is True
    assert response.value is not None
    assert [candidate.provider_id for candidate in response.value] == ["movie:123", "movie:456"]
    assert response.value[0].confidence > response.value[1].confidence
    assert response.value[0].match_reason == "title_exact,year_match,rank_1"
    assert response.value[0].poster_url == "https://image.tmdb.org/t/p/w780/poster-a.jpg"


def test_tmdb_provider_gets_movie_detail_with_language_fallback_and_related_data() -> None:
    provider = make_provider(
        {
            route_key("/movie/123", language="zh-CN", api_key="test-key"): {
                "id": 123,
                "title": "示例电影",
                "original_title": "Example Movie",
                "release_date": "2026-02-10",
                "overview": "",
                "runtime": 126,
                "vote_average": 7.8,
                "genres": [{"id": 18, "name": "Drama"}],
                "production_countries": [{"iso_3166_1": "CN"}],
                "production_companies": [{"name": "Media Pilot"}],
            },
            route_key("/movie/123", language="en-US", api_key="test-key"): {
                "id": 123,
                "title": "Example Movie",
                "original_title": "Example Movie",
                "release_date": "2026-02-10",
                "overview": "English fallback plot",
                "runtime": 126,
                "vote_average": 7.8,
                "genres": [{"id": 18, "name": "Drama"}],
                "production_countries": [{"iso_3166_1": "CN"}],
                "production_companies": [{"name": "Media Pilot"}],
            },
            route_key("/movie/123/credits", api_key="test-key"): {
                "cast": [
                    {
                        "id": 11,
                        "name": "Lead Actor",
                        "character": "Hero",
                        "profile_path": "/lead.jpg",
                    }
                ],
                "crew": [
                    {
                        "id": 22,
                        "name": "Director Name",
                        "job": "Director",
                        "profile_path": "/director.jpg",
                    }
                ],
            },
            route_key("/movie/123/external_ids", api_key="test-key"): {
                "id": 123,
                "imdb_id": "tt1234567",
            },
            route_key(
                "/movie/123/images",
                include_image_language="zh-CN,en-US,null,en",
                api_key="test-key",
            ): {
                "posters": [
                    {"file_path": "/poster-en.jpg", "iso_639_1": "en"},
                    {"file_path": "/poster-zh.jpg", "iso_639_1": "zh-CN"},
                ],
                "backdrops": [
                    {"file_path": "/backdrop-null.jpg", "iso_639_1": None},
                ],
                "logos": [
                    {"file_path": "/logo-en.png", "iso_639_1": "en"},
                ],
            },
        }
    )

    response = provider.get_movie_details("movie:123", language_priority=["zh-CN", "en-US"])

    assert response.is_success is True
    assert response.value is not None
    detail = response.value
    assert detail.title == "示例电影"
    assert detail.plot == "English fallback plot"
    assert detail.payload["field_sources"] == {"title": "zh-CN", "overview": "en-US"}
    assert detail.credits.directors[0].name == "Director Name"
    assert detail.credits.actors[0].role == "Hero"
    assert detail.external_ids.imdb_id == "tt1234567"
    assert detail.images.poster_url == "https://image.tmdb.org/t/p/w780/poster-zh.jpg"
    assert detail.images.backdrop_url == "https://image.tmdb.org/t/p/w1280/backdrop-null.jpg"
    assert detail.images.logo_url == "https://image.tmdb.org/t/p/w500/logo-en.png"


def test_tmdb_provider_returns_optional_image_warnings_without_blocking() -> None:
    provider = make_provider(
        {
            route_key(
                "/movie/123/images",
                include_image_language="zh-CN,en-US,null,en",
                api_key="test-key",
            ): {
                "posters": [{"file_path": "/poster.jpg", "iso_639_1": "zh-CN"}],
                "backdrops": [],
                "logos": [],
            }
        }
    )

    response = provider.get_movie_images("movie:123", language_priority=["zh-CN", "en-US"])

    assert response.is_success is True
    assert response.value is not None
    assert response.value.poster_url == "https://image.tmdb.org/t/p/w780/poster.jpg"
    assert response.value.backdrop_url is None
    assert response.value.logo_url is None
    assert response.value.payload["warnings"] == ["missing_backdrop", "missing_logo"]


def test_tmdb_provider_reports_retryable_http_errors() -> None:
    provider = make_provider(
        {
            route_key(
                "/search/movie",
                query="Broken",
                language="zh-CN",
                api_key="test-key",
            ): httpx.Response(
                503,
                json={"status_message": "upstream unavailable"},
            )
        }
    )

    response = provider.search_movie("Broken", language_priority=["zh-CN", "en-US"])

    assert response.is_success is False
    assert response.error is not None
    assert response.error.code == "http_503"
    assert response.error.retryable is True
    assert response.error.payload["status_code"] == 503


def test_tmdb_provider_emits_request_logs_for_success_and_failure() -> None:
    calls: list[dict] = []
    provider = make_provider(
        {
            route_key(
                "/search/movie",
                query="Example Movie 2026",
                language="zh-CN",
                api_key="test-key",
            ): {
                "results": [
                    {
                        "id": 123,
                        "title": "Example Movie",
                        "original_title": "Example Movie",
                        "release_date": "2026-02-10",
                        "overview": "Primary result",
                        "poster_path": "/poster-a.jpg",
                    }
                ]
            },
            route_key(
                "/search/movie",
                query="Broken",
                language="zh-CN",
                api_key="test-key",
            ): httpx.Response(
                503,
                json={"status_message": "upstream unavailable"},
            ),
        },
        call_logger=calls.append,
    )

    success = provider.search_movie("Example Movie 2026", language_priority=["zh-CN", "en-US"])
    failure = provider.search_movie("Broken", language_priority=["zh-CN", "en-US"])

    assert success.is_success is True
    assert failure.is_success is False
    assert calls == [
        {
            "provider": "tmdb",
            "path": "/search/movie",
            "params": {"query": "Example Movie 2026", "language": "zh-CN"},
            "status": "succeeded",
            "status_code": 200,
            "response_summary": {"result_count": 1},
            "error_message": None,
        },
        {
            "provider": "tmdb",
            "path": "/search/movie",
            "params": {"query": "Broken", "language": "zh-CN"},
            "status": "failed",
            "status_code": 503,
            "response_summary": {"status_message": "upstream unavailable"},
            "error_message": "tmdb request failed with status 503",
        },
    ]


def make_provider(
    routes: dict[tuple[str, tuple[tuple[str, str], ...]], dict | httpx.Response],
    *,
    call_logger=None,
) -> TmdbMovieProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/3")
        key = route_key(path, **dict(request.url.params))
        payload = routes.get(key)
        if payload is None:
            return httpx.Response(404, json={"missing_route": key})
        if isinstance(payload, httpx.Response):
            return payload
        return httpx.Response(200, json=payload)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.themoviedb.org/3",
    )
    return TmdbMovieProvider(
        api_key="test-key",
        client=client,
        call_logger=call_logger,
    )


def route_key(path: str, **params: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    return (path, tuple(sorted((key, value) for key, value in params.items())))


# ── provider_id 归一化: 裸数字 / "movie:<id>" / "show:<id>" 兼容 ─────


class TestTmdbProviderIdNormalization:
    """Warcraft 现场: LLM 用裸数字 ``"68735"`` 调
    ``fetch_metadata_draft`` 失败, 改 ``"movie:68735"`` 才成功. 修复后
    两种形式都该被后端接受, 内部统一用前缀形式派发 / 持久化.

    TPDB 不动 (走不同 protocol, 不会撞这条 parser)."""

    def test_unit_helper_accepts_bare_movie_id(self) -> None:
        from media_pilot.adapters.tmdb import _tmdb_movie_id

        assert _tmdb_movie_id("68735") == 68735

    def test_unit_helper_accepts_prefixed_movie_id(self) -> None:
        from media_pilot.adapters.tmdb import _tmdb_movie_id

        assert _tmdb_movie_id("movie:68735") == 68735

    def test_unit_helper_accepts_bare_show_id(self) -> None:
        from media_pilot.adapters.tmdb import _tmdb_show_id

        assert _tmdb_show_id("123") == 123

    def test_unit_helper_accepts_prefixed_show_id(self) -> None:
        from media_pilot.adapters.tmdb import _tmdb_show_id

        assert _tmdb_show_id("show:123") == 123

    def test_unit_helper_rejects_non_numeric_id(self) -> None:
        from media_pilot.adapters.tmdb import _tmdb_movie_id

        assert _tmdb_movie_id("movie:abc") is None

    def test_unit_helper_rejects_floating_point_id(self) -> None:
        from media_pilot.adapters.tmdb import _tmdb_movie_id

        assert _tmdb_movie_id("687.35") is None
        assert _tmdb_movie_id("movie:687.35") is None

    def test_unit_helper_rejects_wrong_prefix(self) -> None:
        from media_pilot.adapters.tmdb import _tmdb_movie_id

        # ``tv:`` 不是 TMDB 合法前缀, 必须拒绝 (走 invalid_provider_id)
        assert _tmdb_movie_id("tv:123") is None

    def test_get_movie_details_accepts_bare_id(self) -> None:
        """Bare 数字 provider_id 走 ``get_movie_details`` 应被归一化为
        ``movie:<id>`` 后再派发到 ``/movie/<id>`` 路由."""
        provider = make_provider({
            route_key("/movie/68735", language="zh-CN", api_key="test-key"): {
                "id": 68735,
                "title": "Warcraft",
                "original_title": "Warcraft",
                "release_date": "2016-06-10",
                "overview": "...",
                "runtime": 123,
                "vote_average": 7.2,
                "genres": [],
                "production_countries": [],
                "production_companies": [],
            },
            route_key("/movie/68735", language="en-US", api_key="test-key"): {
                "id": 68735,
                "title": "Warcraft",
                "original_title": "Warcraft",
                "release_date": "2016-06-10",
                "overview": "...",
                "runtime": 123,
                "vote_average": 7.2,
                "genres": [],
                "production_countries": [],
                "production_companies": [],
            },
            route_key(
                "/movie/68735/credits", api_key="test-key",
            ): {"id": 68735, "cast": [], "crew": []},
            route_key(
                "/movie/68735/external_ids", api_key="test-key",
            ): {"id": 68735, "imdb_id": "tt0803096"},
            route_key(
                "/movie/68735/images",
                include_image_language="zh-CN,en-US,null,en",
                api_key="test-key",
            ): {"id": 68735, "posters": [], "backdrops": [], "logos": []},
        })

        response = provider.get_movie_details(
            "68735", language_priority=["zh-CN", "en-US"],
        )
        assert response.is_success is True
        assert response.value is not None
        # Adapter 接受 bare 数字, response 仍能成功填充 detail 字段
        # (provider_id 字段保留调用方传入的形式; 归一化到前缀形式由
        # ``fetch_metadata_draft`` 入口负责, 见
        # ``TestFetchMetadataDraftTmdbNormalization``).
        assert response.value.title == "Warcraft"
        assert response.value.year == 2016

    def test_get_movie_details_accepts_prefixed_id(self) -> None:
        """``movie:<id>`` 仍正常, 与 bare 数字行为一致 (不双前缀)."""
        provider = make_provider({
            route_key("/movie/68735", language="zh-CN", api_key="test-key"): {
                "id": 68735,
                "title": "Warcraft",
                "original_title": "Warcraft",
                "release_date": "2016-06-10",
                "overview": "...",
                "runtime": 123,
                "vote_average": 7.2,
                "genres": [],
                "production_countries": [],
                "production_companies": [],
            },
            route_key(
                "/movie/68735/credits", api_key="test-key",
            ): {"id": 68735, "cast": [], "crew": []},
            route_key(
                "/movie/68735/external_ids", api_key="test-key",
            ): {"id": 68735, "imdb_id": "tt0803096"},
            route_key(
                "/movie/68735/images",
                include_image_language="zh-CN,en-US,null,en",
                api_key="test-key",
            ): {"id": 68735, "posters": [], "backdrops": [], "logos": []},
        })

        response = provider.get_movie_details(
            "movie:68735", language_priority=["zh-CN", "en-US"],
        )
        assert response.is_success is True
        assert response.value is not None
        assert response.value.provider_id == "movie:68735"

    def test_get_movie_details_rejects_non_numeric_id(self) -> None:
        provider = make_provider({})
        response = provider.get_movie_details(
            "movie:abc", language_priority=["zh-CN"],
        )
        assert response.is_success is False
        assert response.error is not None
        assert response.error.code == "invalid_provider_id"

    def test_get_movie_details_rejects_floating_point_id(self) -> None:
        provider = make_provider({})
        response = provider.get_movie_details(
            "687.35", language_priority=["zh-CN"],
        )
        assert response.is_success is False
        assert response.error is not None
        assert response.error.code == "invalid_provider_id"
