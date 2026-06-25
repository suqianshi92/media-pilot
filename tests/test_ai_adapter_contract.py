from dataclasses import fields
from typing import Protocol

from media_pilot.adapters.ai import AiFilenameParser, AiParseRequest, AiParseResult, MediaType


def test_ai_filename_parser_contract_is_provider_agnostic() -> None:
    assert issubclass(AiFilenameParser, Protocol)

    request_fields = {field.name for field in fields(AiParseRequest)}
    result_fields = {field.name for field in fields(AiParseResult)}

    assert request_fields == {"filename"}
    assert result_fields == {
        "media_type",
        "title",
        "original_title",
        "year",
        "season",
        "episode",
        "resolution",
        "release_group",
        "language",
        "confidence",
        "reason",
    }


def test_ai_parse_result_represents_structured_candidate() -> None:
    result = AiParseResult(
        media_type=MediaType.MOVIE,
        title="Example Movie",
        original_title="Example.Movie.2026.1080p-GROUP.mkv",
        year=2026,
        season=None,
        episode=None,
        resolution="1080p",
        release_group="GROUP",
        language="zh-CN",
        confidence=0.92,
        reason="filename contains a title, year, resolution, and release group",
    )

    assert result.media_type == MediaType.MOVIE
    assert result.title == "Example Movie"
    assert result.confidence == 0.92
