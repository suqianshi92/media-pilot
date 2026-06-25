"""candidate_search 服务单元测试"""

from __future__ import annotations

from pathlib import Path

from media_pilot.adapters.metadata import (
    MetadataCandidate,
    MetadataProviderResponse,
)
from media_pilot.config import AppConfig


class StubSearchProvider:
    """返回预设候选的 stub provider"""

    def __init__(self, candidates: list[MetadataCandidate] | None = None,
                 error: str | None = None):
        self._candidates = candidates or []
        self._error = error
        self.provider_name = "stub"

    def search_movie(self, keyword: str, language_priority=None):
        if self._error:
            from media_pilot.adapters.metadata import MetadataProviderError
            return MetadataProviderResponse(
                error=MetadataProviderError(
                    provider="stub", code="TEST_ERROR",
                    message=self._error, retryable=False,
                )
            )
        return MetadataProviderResponse(value=list(self._candidates))

    def search_show(self, keyword: str, language_priority=None):
        return self.search_movie(keyword, language_priority=language_priority)

    def get_movie_details(self, provider_id: str, language_priority=None):
        raise NotImplementedError

    def get_show_details(self, provider_id: str, language_priority=None):
        raise NotImplementedError

    def get_movie_credits(self, provider_id: str):
        raise NotImplementedError

    def get_show_credits(self, provider_id: str):
        raise NotImplementedError

    def get_movie_external_ids(self, provider_id: str):
        raise NotImplementedError

    def get_show_external_ids(self, provider_id: str):
        raise NotImplementedError

    def get_movie_images(self, provider_id: str, language_priority=None):
        raise NotImplementedError

    def get_show_images(self, provider_id: str, language_priority=None):
        raise NotImplementedError


def _make_config() -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/tmp/dl"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp/ws"),
        movies_dir=Path("/tmp/movies"),
        shows_dir=Path("/tmp/shows"),
        database_dir=Path("/tmp/db"),
        tmdb_api_key="test-key",
    )


def _make_candidate(title: str, provider_id: str = "tt123", year: int = 2024) -> MetadataCandidate:
    return MetadataCandidate(
        provider="stub", provider_id=provider_id,
        title=title, original_title=None, year=year,
        media_type="movie", overview=None, poster_url=None,
        confidence=0.9, match_reason="title match",
    )


def test_search_returns_candidates(monkeypatch):
    """搜索返回匹配的候选列表"""
    from media_pilot.services.candidate_search import search_metadata_candidates

    expected = [_make_candidate("Test Movie")]
    monkeypatch.setattr(
        "media_pilot.services.candidate_search.create_metadata_provider_by_name",
        lambda config, name: StubSearchProvider(expected),
    )

    config = _make_config()
    result = search_metadata_candidates(
        config=config, provider_name="stub", keyword="Test Movie",
    )

    assert len(result) == 1
    assert result[0].title == "Test Movie"
    assert result[0].provider_id == "tt123"


def test_search_empty_result(monkeypatch):
    """搜索无结果返回空列表"""
    from media_pilot.services.candidate_search import search_metadata_candidates

    monkeypatch.setattr(
        "media_pilot.services.candidate_search.create_metadata_provider_by_name",
        lambda config, name: StubSearchProvider([]),
    )

    config = _make_config()
    result = search_metadata_candidates(
        config=config, provider_name="stub", keyword="NoMatch",
    )

    assert result == []


def test_search_provider_error(monkeypatch):
    """provider 失败返回空列表"""
    from media_pilot.services.candidate_search import search_metadata_candidates

    monkeypatch.setattr(
        "media_pilot.services.candidate_search.create_metadata_provider_by_name",
        lambda config, name: StubSearchProvider(error="API down"),
    )

    config = _make_config()
    result = search_metadata_candidates(
        config=config, provider_name="stub", keyword="Any",
    )

    assert result == []
