from media_pilot.adapters.ai import AiParseRequest, AiSearchKeywordRequest, MediaType
from tests.stubs import StubAiFilenameParser


def test_stub_ai_parser_returns_movie_candidate_from_year_filename() -> None:
    parser = StubAiFilenameParser()

    result = parser.parse_filename(
        AiParseRequest(filename="Example.Movie.2026.1080p.WEB-DL-GROUP.mkv")
    )

    assert result.media_type == MediaType.MOVIE
    assert result.title == "Example Movie"
    assert result.original_title == "Example.Movie.2026.1080p.WEB-DL-GROUP.mkv"
    assert result.year == 2026
    assert result.season is None
    assert result.episode is None
    assert result.resolution == "1080p"
    assert result.release_group == "GROUP"
    assert result.confidence == 0.9
    assert "year" in result.reason


def test_stub_ai_parser_returns_show_candidate_from_season_episode_filename() -> None:
    parser = StubAiFilenameParser()

    result = parser.parse_filename(AiParseRequest(filename="Example.Show.S02E03.720p-GROUP.mkv"))

    assert result.media_type == MediaType.SHOW
    assert result.title == "Example Show"
    assert result.year is None
    assert result.season == 2
    assert result.episode == 3
    assert result.resolution == "720p"
    assert result.release_group == "GROUP"
    assert result.confidence == 0.88
    assert "season and episode" in result.reason


def test_stub_ai_parser_returns_unknown_candidate_when_filename_has_no_signal() -> None:
    parser = StubAiFilenameParser()

    result = parser.parse_filename(AiParseRequest(filename="download.mkv"))

    assert result.media_type == MediaType.UNKNOWN
    assert result.title is None
    assert result.year is None
    assert result.season is None
    assert result.episode is None
    assert result.confidence == 0.2
    assert "not enough filename signals" in result.reason


def test_stub_ai_keyword_generator_uses_parent_directory_context() -> None:
    parser = StubAiFilenameParser()

    result = parser.generate_search_keyword(
        AiSearchKeywordRequest(
            input_path="/workspace/Example.Movie.2026",
            input_name="Example.Movie.2026",
            selected_path="/workspace/Example.Movie.2026/2160p-WEB-DL-GROUP.mkv",
            selected_name="2160p-WEB-DL-GROUP.mkv",
            selected_parent_name="Example.Movie.2026",
            rule_keyword="2160p WEB-DL",
            rule_confidence=0.3,
            quality_tokens=["2160p", "WEB-DL"],
            removed_tokens=["GROUP"],
        )
    )

    assert result.keyword == "Example Movie 2026"
    assert result.candidate_title == "Example Movie"
    assert result.candidate_year == 2026
    assert result.confidence == 0.86
