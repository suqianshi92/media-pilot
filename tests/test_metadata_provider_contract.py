from media_pilot.adapters.metadata import (
    MetadataCandidate,
    MetadataCredits,
    MetadataDetail,
    MetadataExternalIds,
    MetadataImages,
    MetadataProvider,
    MetadataProviderError,
    MetadataProviderResponse,
)


def test_metadata_provider_contract_is_provider_agnostic() -> None:
    provider_type = MetadataProvider

    assert hasattr(provider_type, "search_movie")
    assert hasattr(provider_type, "get_movie_details")
    assert hasattr(provider_type, "get_movie_credits")
    assert hasattr(provider_type, "get_movie_external_ids")
    assert hasattr(provider_type, "get_movie_images")


def test_metadata_provider_response_supports_success_and_failure() -> None:
    success = MetadataProviderResponse(value=[MetadataCandidate(
        provider="fake",
        provider_id="movie:example:2026",
        title="Example Movie",
        original_title="Example Movie",
        year=2026,
        media_type="movie",
        overview="Fake overview",
        poster_url="https://example.test/poster.jpg",
        confidence=0.9,
        match_reason="exact match",
    )])
    failure = MetadataProviderResponse[list[MetadataCandidate]](
        error=MetadataProviderError(
            provider="fake",
            code="rate_limited",
            message="slow down",
            retryable=True,
        )
    )

    assert success.is_success is True
    assert failure.is_success is False
    assert failure.error is not None
    assert failure.error.retryable is True


def test_metadata_detail_structure_covers_writer_inputs() -> None:
    detail = MetadataDetail(
        provider="fake",
        provider_id="movie:example:2026",
        media_type="movie",
        title="Example Movie",
        original_title="Example Movie",
        year=2026,
        plot="Fake plot",
        runtime_minutes=120,
        premiered="2026-01-01",
        rating=7.5,
        genres=["Drama"],
        countries=["CN"],
        studios=["Media Pilot Studio"],
        credits=MetadataCredits(),
        external_ids=MetadataExternalIds(imdb_id="tt2026000"),
        images=MetadataImages(
            poster_url="https://example.test/poster.jpg",
            backdrop_url="https://example.test/backdrop.jpg",
            logo_url="https://example.test/logo.png",
        ),
    )

    assert detail.media_type == "movie"
    assert detail.runtime_minutes == 120
    assert detail.external_ids.imdb_id == "tt2026000"
    assert detail.images.poster_url is not None
