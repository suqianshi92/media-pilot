from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from media_pilot.adapters.llm_prompts import ProfileRecommendation


class MediaType(StrEnum):
    MOVIE = "movie"
    SHOW = "show"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True)
class AiParseRequest:
    filename: str


@dataclass(frozen=True, kw_only=True)
class AiParseResult:
    media_type: MediaType
    title: str | None
    original_title: str | None
    year: int | None
    season: int | None
    episode: int | None
    resolution: str | None
    release_group: str | None
    language: str | None
    confidence: float
    reason: str


@dataclass(frozen=True, kw_only=True)
class AiSearchKeywordRequest:
    input_path: str
    input_name: str
    selected_path: str
    selected_name: str
    selected_parent_name: str
    rule_keyword: str
    rule_confidence: float
    quality_tokens: list[str]
    removed_tokens: list[str]
    profile: str | None = None


@dataclass(frozen=True, kw_only=True)
class AiSearchKeywordResult:
    keyword: str
    candidate_title: str | None
    candidate_year: int | None
    confidence: float
    reason: str
    explanation: str
    removed_tokens: list[str]


class AiFilenameParser(Protocol):
    def parse_filename(self, request: AiParseRequest) -> AiParseResult:
        """Parse only the provided filename into a structured media candidate."""


class AiSearchKeywordGenerator(Protocol):
    def generate_search_keyword(self, request: AiSearchKeywordRequest) -> AiSearchKeywordResult:
        """Generate a search keyword when rule cleanup does not provide enough title signals."""


class AiProfileRouter(Protocol):
    def recommend_profile(
        self, *, input_text: str, enabled_profiles: list[str]
    ) -> ProfileRecommendation:
        """Recommend a metadata profile based on filename analysis."""
