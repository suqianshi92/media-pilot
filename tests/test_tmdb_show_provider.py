import httpx

from media_pilot.adapters.tmdb import TmdbMovieProvider


def test_tmdb_provider_gets_show_detail_with_string_origin_country() -> None:
    provider = make_provider(
        {
            route_key("/tv/65942", language="zh-CN", api_key="test-key"): {
                "id": 65942,
                "name": "Re：从零开始的异世界生活",
                "original_name": "Re:ゼロから始める異世界生活",
                "first_air_date": "2016-04-04",
                "overview": "异世界动画",
                "vote_average": 7.8,
                "genres": [{"id": 16, "name": "Animation"}],
                "origin_country": ["JP"],
                "production_companies": [{"name": "White Fox"}],
                "seasons": [
                    {"season_number": 1, "episode_count": 25},
                    {"season_number": 2, "episode_count": 25},
                    {"season_number": 3, "episode_count": 16},
                ],
            },
            route_key("/tv/65942/credits", api_key="test-key"): {
                "cast": [
                    {"id": 11, "name": "Yusuke Kobayashi", "character": "Subaru"}
                ],
                "crew": [],
            },
            route_key("/tv/65942/external_ids", api_key="test-key"): {
                "id": 65942,
                "imdb_id": "tt5607616",
            },
            route_key(
                "/tv/65942/images",
                include_image_language="zh-CN,en-US,null,en",
                api_key="test-key",
            ): {
                "posters": [{"file_path": "/poster.jpg", "iso_639_1": "zh-CN"}],
                "backdrops": [],
                "logos": [],
            },
        }
    )

    response = provider.get_show_details("show:65942", language_priority=["zh-CN", "en-US"])

    assert response.is_success is True
    assert response.value is not None
    detail = response.value
    assert detail.title == "Re：从零开始的异世界生活"
    assert detail.media_type == "show"
    assert detail.year == 2016
    assert detail.countries == ["JP"]
    assert detail.payload["raw"]["seasons"][2]["season_number"] == 3


def test_tmdb_provider_gets_show_detail_with_legacy_dict_origin_country() -> None:
    provider = make_provider(
        {
            route_key("/tv/1", language="zh-CN", api_key="test-key"): {
                "id": 1,
                "name": "Legacy Show",
                "original_name": "Legacy Show",
                "first_air_date": "2026-01-01",
                "overview": "Legacy country shape",
                "vote_average": 7.0,
                "genres": [],
                "origin_country": [{"iso_3166_1": "US"}],
                "production_companies": [],
            },
            route_key("/tv/1/credits", api_key="test-key"): {"cast": [], "crew": []},
            route_key("/tv/1/external_ids", api_key="test-key"): {
                "id": 1,
                "imdb_id": None,
            },
            route_key(
                "/tv/1/images",
                include_image_language="zh-CN,en-US,null,en",
                api_key="test-key",
            ): {"posters": [], "backdrops": [], "logos": []},
        }
    )

    response = provider.get_show_details("show:1", language_priority=["zh-CN", "en-US"])

    assert response.is_success is True
    assert response.value is not None
    assert response.value.countries == ["US"]


def make_provider(
    routes: dict[tuple[str, tuple[tuple[str, str], ...]], dict | httpx.Response],
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
    return TmdbMovieProvider(api_key="test-key", client=client)


def route_key(path: str, **params: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    return (path, tuple(sorted((key, value) for key, value in params.items())))
