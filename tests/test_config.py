from pathlib import Path

from media_pilot.config import AdapterMode, AppConfig, LibraryFormat, MetadataProviderMode


def test_app_config_captures_required_directories_and_modes() -> None:
    config = AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"),
        shows_dir=Path("/media/library/shows"),
        database_dir=Path("/media/db"),
    )

    assert config.downloads_dir == Path("/media/downloads")
    assert config.workspace_dir == Path("/media/workspace")
    assert config.movies_dir == Path("/media/library/movies")
    assert config.shows_dir == Path("/media/library/shows")
    assert config.database_dir == Path("/media/db")
    assert config.ai_adapter == AdapterMode.NONE
    assert config.metadata_provider == MetadataProviderMode.TMDB
    assert config.library_format == LibraryFormat.JELLYFIN
    assert config.qbittorrent_url == ""
    assert config.ai_url is None
    assert config.tmdb_api_key is None
    assert config.tmdb_base_url == "https://api.themoviedb.org/3"
    assert config.tmdb_language_priority == ("zh-CN", "en-US")
    assert config.tmdb_timeout_seconds == 10.0
    assert config.tmdb_image_base_url == "https://image.tmdb.org/t/p"
    assert config.tmdb_poster_size == "w780"
    assert config.tmdb_backdrop_size == "w1280"
    assert config.tmdb_logo_size == "w500"
    assert config.tmdb_profile_size == "w185"
    assert config.metadata_auto_confirm_confidence == 0.9
