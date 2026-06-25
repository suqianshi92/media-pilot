from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class MetadataProviderError:
    provider: str
    code: str
    message: str
    retryable: bool
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MetadataProviderResponse[T]:
    value: T | None = None
    error: MetadataProviderError | None = None

    @property
    def is_success(self) -> bool:
        return self.error is None and self.value is not None


@dataclass(frozen=True)
class MetadataCandidate:
    provider: str
    provider_id: str
    title: str
    original_title: str | None
    year: int | None
    media_type: str
    overview: str | None
    poster_url: str | None
    confidence: float
    match_reason: str
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MetadataPerson:
    provider: str
    provider_id: str | None
    name: str
    role: str | None
    profile_url: str | None
    image_url: str | None
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MetadataCredits:
    directors: list[MetadataPerson] = field(default_factory=list)
    actors: list[MetadataPerson] = field(default_factory=list)
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MetadataExternalIds:
    imdb_id: str | None
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MetadataImages:
    poster_url: str | None
    backdrop_url: str | None
    logo_url: str | None
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MetadataDetail:
    provider: str
    provider_id: str
    media_type: str
    title: str
    original_title: str | None
    year: int | None
    plot: str | None
    runtime_minutes: int | None
    premiered: str | None
    rating: float | None
    genres: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    studios: list[str] = field(default_factory=list)
    credits: MetadataCredits = field(default_factory=MetadataCredits)
    external_ids: MetadataExternalIds = field(default_factory=lambda: MetadataExternalIds(None))
    images: MetadataImages = field(default_factory=lambda: MetadataImages(None, None, None))
    payload: dict = field(default_factory=dict)


class MetadataProvider(Protocol):
    def search_movie(
        self,
        keyword: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse[list[MetadataCandidate]]:
        """Search normalized movie candidates without leaking provider-specific fields."""

    def search_show(
        self,
        keyword: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse[list[MetadataCandidate]]:
        """Search normalized show candidates for TV series."""

    def get_movie_details(
        self,
        provider_id: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse[MetadataDetail]:
        """Return normalized movie details for downstream NFO and image planning."""

    def get_show_details(
        self,
        provider_id: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse[MetadataDetail]:
        """Return normalized show details for TV series."""

    def get_movie_credits(self, provider_id: str) -> MetadataProviderResponse[MetadataCredits]:
        """Return normalized directors and actors."""

    def get_show_credits(self, provider_id: str) -> MetadataProviderResponse[MetadataCredits]:
        """Return normalized show credits."""

    def get_movie_external_ids(
        self,
        provider_id: str,
    ) -> MetadataProviderResponse[MetadataExternalIds]:
        """Return normalized external ids such as imdb id."""

    def get_show_external_ids(
        self,
        provider_id: str,
    ) -> MetadataProviderResponse[MetadataExternalIds]:
        """Return normalized external ids for show."""

    def get_movie_images(
        self,
        provider_id: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse[MetadataImages]:
        """Return normalized image references for poster, backdrop, and logo."""

    def get_show_images(
        self,
        provider_id: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse[MetadataImages]:
        """Return normalized image references for show."""
