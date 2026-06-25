from pathlib import Path

from media_pilot.adapters.metadata import MetadataCandidate
from media_pilot.config import AppConfig
from media_pilot.orchestration import profile_search
from media_pilot.services.profile_registry import MetadataProfile
from tests.stubs import StubMetadataProvider


def test_search_with_profiles_returns_clear_winner(monkeypatch, tmp_path: Path) -> None:
    provider = StubMetadataProvider(
        candidates=[
            MetadataCandidate(
                provider="stub",
                provider_id="movie:stub:1",
                title="测试影片",
                original_title="Test Movie",
                year=2026,
                media_type="movie",
                overview="测试",
                poster_url="https://example.test/poster.jpg",
                confidence=0.95,
                match_reason="stub_match",
            )
        ]
    )
    monkeypatch.setattr(
        profile_search,
        "create_metadata_provider_by_name",
        lambda config, provider_name: provider,
    )

    result = profile_search.search_with_profiles(
        config=AppConfig(
downloads_dir=tmp_path / "downloads",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "workspace",
            movies_dir=tmp_path / "movies",
            shows_dir=tmp_path / "shows",
            database_dir=tmp_path / "db",
        ),
        keyword="测试影片",
        enabled_profiles=[
            MetadataProfile(
                name="tmdb_movie",
                label="TMDB 电影",
                provider_name="tmdb",
                prompt_profile="tmdb_movie",
            )
        ],
        auto_confirm_confidence=0.9,
        auto_confirm_margin=0.08,
    )

    assert result.has_clear_winner is True
    assert result.provider is provider
    assert result.provider_name == "stub_metadata"
    assert result.searched_profiles == ["tmdb_movie"]
