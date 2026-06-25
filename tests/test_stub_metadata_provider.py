from tests.stubs import StubMetadataProvider


def test_stub_metadata_provider_returns_normalized_movie_candidate() -> None:
    provider = StubMetadataProvider()

    response = provider.search_movie("Example Movie 2026", language_priority=["zh-CN", "en-US"])

    assert response.is_success is True
    assert response.value is not None
    candidate = response.value[0]
    assert candidate.provider == "stub_metadata"
    assert candidate.provider_id == "movie:example-movie:2026"
    assert candidate.title == "Example Movie"
    assert candidate.year == 2026
    assert candidate.media_type == "movie"
    assert candidate.poster_url is not None


def test_stub_metadata_provider_returns_normalized_movie_detail_bundle() -> None:
    provider = StubMetadataProvider()

    response = provider.get_movie_details(
        "movie:example-movie:2026",
        language_priority=["zh-CN", "en-US"],
    )

    assert response.is_success is True
    assert response.value is not None
    detail = response.value
    assert detail.provider == "stub_metadata"
    assert detail.title == "Example Movie"
    assert detail.year == 2026
    assert detail.credits.directors[0].name == "Stub Director"
    assert detail.external_ids.imdb_id == "tt2026"
    assert detail.images.logo_url is not None


def test_stub_metadata_provider_exposes_not_found_error_shape() -> None:
    provider = StubMetadataProvider()

    response = provider.get_movie_images("movie:missing", language_priority=["zh-CN"])

    assert response.is_success is False
    assert response.error is not None
    assert response.error.provider == "stub_metadata"
    assert response.error.code == "not_found"
    assert response.error.retryable is False
