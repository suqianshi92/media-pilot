"""测试 stub：可控 AI 和 metadata 行为。

从旧 fake adapter 迁移而来，供测试专用。
"""

import re
from pathlib import Path

from media_pilot.adapters.ai import (
    AiParseRequest,
    AiParseResult,
    AiSearchKeywordRequest,
    AiSearchKeywordResult,
    MediaType,
)
from media_pilot.adapters.metadata import (
    MetadataCandidate,
    MetadataCredits,
    MetadataDetail,
    MetadataExternalIds,
    MetadataImages,
    MetadataPerson,
    MetadataProviderError,
    MetadataProviderResponse,
)

SEASON_EPISODE_PATTERN = re.compile(
    r"(?:s(?P<sxx>\d{1,2})e(?P<exx>\d{1,2})|"
    r"(?P<xseason>\d{1,2})x(?P<xepisode>\d{1,2}))",
    re.IGNORECASE,
)
YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")
RESOLUTION_PATTERN = re.compile(r"\b(?:480p|720p|1080p|2160p|4k)\b", re.IGNORECASE)


class StubAiFilenameParser:
    def parse_filename(self, request: AiParseRequest) -> AiParseResult:
        filename = Path(request.filename).name
        stem = Path(filename).stem
        resolution = _find_resolution(stem)
        release_group = _find_release_group(stem)
        show_match = SEASON_EPISODE_PATTERN.search(stem)

        if show_match is not None:
            season = show_match.group("sxx") or show_match.group("xseason")
            episode = show_match.group("exx") or show_match.group("xepisode")
            title = _clean_title(stem[: show_match.start()])
            return AiParseResult(
                media_type=MediaType.SHOW,
                title=title,
                original_title=filename,
                year=None,
                season=int(season),
                episode=int(episode),
                resolution=resolution,
                release_group=release_group,
                language=None,
                confidence=0.88,
                reason="stub parser found season and episode markers in filename",
            )

        year_match = YEAR_PATTERN.search(stem)
        if year_match is not None:
            title = _clean_title(stem[: year_match.start()])
            return AiParseResult(
                media_type=MediaType.MOVIE,
                title=title,
                original_title=filename,
                year=int(year_match.group(0)),
                season=None,
                episode=None,
                resolution=resolution,
                release_group=release_group,
                language=None,
                confidence=0.9,
                reason="stub parser found a movie title and year in filename",
            )

        return AiParseResult(
            media_type=MediaType.UNKNOWN,
            title=None,
            original_title=filename,
            year=None,
            season=None,
            episode=None,
            resolution=resolution,
            release_group=release_group,
            language=None,
            confidence=0.2,
            reason="stub parser found not enough filename signals",
        )

    def generate_search_keyword(self, request: AiSearchKeywordRequest) -> AiSearchKeywordResult:
        candidate_name = request.selected_parent_name
        title, year = _extract_title_and_year(candidate_name)
        if title is None:
            title, year = _extract_title_and_year(request.selected_name)

        if title is None:
            keyword = request.rule_keyword
            confidence = max(request.rule_confidence, 0.35)
            reason = "stub keyword generator fell back to the rule keyword"
            explanation = "Stub generator could not infer a better title signal."
        else:
            keyword = title if year is None else f"{title} {year}"
            confidence = 0.86
            reason = "stub keyword generator recovered title signals from path context"
            explanation = "Stub generator used parent directory or filename context."

        return AiSearchKeywordResult(
            keyword=keyword,
            candidate_title=title,
            candidate_year=year,
            confidence=confidence,
            reason=reason,
            explanation=explanation,
            removed_tokens=request.removed_tokens,
        )


def _clean_title(value: str) -> str | None:
    title = re.sub(r"[._-]+", " ", value).strip()
    return title or None


def _find_resolution(value: str) -> str | None:
    match = RESOLUTION_PATTERN.search(value)
    return match.group(0).lower() if match is not None else None


def _find_release_group(value: str) -> str | None:
    if "-" not in value:
        return None
    release_group = value.rsplit("-", maxsplit=1)[-1].strip()
    return release_group or None


def _extract_title_and_year(value: str) -> tuple[str | None, int | None]:
    name = Path(value).name
    year_match = YEAR_PATTERN.search(name)
    if year_match is None:
        return _clean_title(name), None

    title = _clean_title(name[: year_match.start()])
    if title is None:
        return None, int(year_match.group(0))
    return title, int(year_match.group(0))


METADATA_YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")


class StubMetadataProvider:
    provider_name = "stub_metadata"

    def __init__(
        self,
        *,
        candidates: list | None = None,
        top1_confidence: float = 0.95,
        top2_confidence: float = 0.70,
        top1_year: int | None = 2019,
        **kwargs,
    ) -> None:
        if candidates is not None:
            self._candidates = list(candidates)
        else:
            self._candidates = None
        self._top1_confidence = top1_confidence
        self._top2_confidence = top2_confidence
        self._top1_year = top1_year

    def search_movie(
        self,
        keyword: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse:
        if self._candidates is not None:
            return MetadataProviderResponse(value=list(self._candidates))
        title, year = _parse_keyword(keyword)
        if title is None:
            return MetadataProviderResponse(
                error=MetadataProviderError(
                    provider=self.provider_name,
                    code="invalid_query",
                    message="search keyword did not contain enough title signals",
                    retryable=False,
                    payload={"keyword": keyword},
                )
            )

        slug = _md_slugify(title)
        provider_id = f"movie:{slug}:{year or 'unknown'}"
        candidate = MetadataCandidate(
            provider=self.provider_name,
            provider_id=provider_id,
            title=title,
            original_title=title,
            year=year,
            media_type="movie",
            overview=f"Stub overview for {title}.",
            poster_url=f"https://example.test/posters/{slug}.jpg",
            confidence=0.92 if year is not None else 0.78,
            match_reason="stub provider exact keyword match",
            payload={
                "language_priority": language_priority,
                "keyword": keyword,
            },
        )
        return MetadataProviderResponse(value=[candidate])

    def get_movie_details(
        self,
        provider_id: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse:
        movie = _movie_from_provider_id(provider_id)
        if movie is None:
            return _not_found(self.provider_name, provider_id)

        title, year = movie
        slug = _md_slugify(title)
        return MetadataProviderResponse(
            value=MetadataDetail(
                provider=self.provider_name,
                provider_id=provider_id,
                media_type="movie",
                title=title,
                original_title=title,
                year=year,
                plot=f"Stub plot for {title}.",
                runtime_minutes=126,
                premiered=None if year is None else f"{year}-02-10",
                rating=7.8,
                genres=["Drama", "Adventure"],
                countries=["CN"],
                studios=["Media Pilot Studio"],
                credits=self.get_movie_credits(provider_id).value or MetadataCredits(),
                external_ids=self.get_movie_external_ids(provider_id).value
                or MetadataExternalIds(None),
                images=self.get_movie_images(provider_id, language_priority=language_priority).value
                or MetadataImages(None, None, None),
                payload={"language_priority": language_priority, "slug": slug},
            )
        )

    def get_movie_credits(self, provider_id: str) -> MetadataProviderResponse:
        movie = _movie_from_provider_id(provider_id)
        if movie is None:
            return _not_found(self.provider_name, provider_id)

        title, _ = movie
        slug = _md_slugify(title)
        return MetadataProviderResponse(
            value=MetadataCredits(
                directors=[
                    MetadataPerson(
                        provider=self.provider_name,
                        provider_id=f"person:{slug}:director",
                        name="Stub Director",
                        role="Director",
                        profile_url=f"https://example.test/people/{slug}-director",
                        image_url=f"https://example.test/people/{slug}-director.jpg",
                    )
                ],
                actors=[
                    MetadataPerson(
                        provider=self.provider_name,
                        provider_id=f"person:{slug}:lead",
                        name="Stub Lead Actor",
                        role="Lead",
                        profile_url=f"https://example.test/people/{slug}-lead",
                        image_url=f"https://example.test/people/{slug}-lead.jpg",
                    )
                ],
                payload={"provider_id": provider_id},
            )
        )

    def get_movie_external_ids(
        self,
        provider_id: str,
    ) -> MetadataProviderResponse:
        movie = _movie_from_provider_id(provider_id)
        if movie is None:
            return _not_found(self.provider_name, provider_id)

        _, year = movie
        suffix = "0000000" if year is None else str(year)
        return MetadataProviderResponse(
            value=MetadataExternalIds(
                imdb_id=f"tt{suffix}",
                payload={"provider_id": provider_id},
            )
        )

    def get_movie_images(
        self,
        provider_id: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse:
        movie = _movie_from_provider_id(provider_id)
        if movie is None:
            return _not_found(self.provider_name, provider_id)

        title, _ = movie
        slug = _md_slugify(title)
        return MetadataProviderResponse(
            value=MetadataImages(
                poster_url=f"https://example.test/posters/{slug}.jpg",
                backdrop_url=f"https://example.test/backdrops/{slug}.jpg",
                logo_url=f"https://example.test/logos/{slug}.png",
                payload={"language_priority": language_priority},
            )
        )


def _parse_keyword(keyword: str) -> tuple[str | None, int | None]:
    cleaned = keyword.strip()
    if not cleaned:
        return None, None

    year_match = METADATA_YEAR_PATTERN.search(cleaned)
    if year_match is None:
        return cleaned, None

    title = cleaned[: year_match.start()].strip()
    if not title:
        return None, int(year_match.group(0))
    return title, int(year_match.group(0))


def _movie_from_provider_id(provider_id: str) -> tuple[str, int | None] | None:
    parts = provider_id.split(":")
    if len(parts) != 3 or parts[0] != "movie":
        return None

    slug = parts[1]
    title = slug.replace("-", " ").title()
    year = None if parts[2] == "unknown" else int(parts[2])
    return title, year


def _md_slugify(title: str) -> str:
    return "-".join(part for part in re.split(r"[^A-Za-z0-9]+", title.lower()) if part)


def _not_found(
    provider: str,
    provider_id: str,
) -> MetadataProviderResponse:
    return MetadataProviderResponse(
        error=MetadataProviderError(
            provider=provider,
            code="not_found",
            message="provider id was not found",
            retryable=False,
            payload={"provider_id": provider_id},
        )
    )
