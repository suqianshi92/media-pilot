from pathlib import Path

import pytest

from media_pilot.adapters.factory import create_ai_adapter, create_metadata_provider
from media_pilot.adapters.openai_compatible_ai import OpenAICompatibleAiAdapter
from media_pilot.adapters.tmdb import TmdbMovieProvider
from media_pilot.config import AdapterMode, AppConfig, MetadataProviderMode


def test_create_ai_adapter_rejects_fake_mode() -> None:
    config = make_config(ai_adapter=AdapterMode.FAKE)

    with pytest.raises(ValueError, match="fake is no longer supported"):
        create_ai_adapter(config)


def test_create_ai_adapter_rejects_real_mode_without_llm_config() -> None:
    config = make_config(ai_adapter=AdapterMode.REAL)

    with pytest.raises(ValueError, match="llm_api_key"):
        create_ai_adapter(config)


def test_create_ai_adapter_returns_openai_adapter_when_configured() -> None:
    config = make_config(
        ai_adapter=AdapterMode.REAL,
        llm_api_key="test-key",
        llm_base_url="https://api.example.com/v1",
        llm_model="test-model",
    )

    adapter = create_ai_adapter(config)

    assert isinstance(adapter, OpenAICompatibleAiAdapter)


def test_create_metadata_provider_rejects_fake_mode() -> None:
    config = make_config(metadata_provider=MetadataProviderMode.FAKE)

    with pytest.raises(ValueError, match="fake is no longer supported"):
        create_metadata_provider(config)


def test_create_metadata_provider_requires_tmdb_api_key_for_tmdb_mode() -> None:
    config = make_config(metadata_provider=MetadataProviderMode.TMDB, tmdb_api_key=None)

    with pytest.raises(ValueError, match="tmdb_api_key is required"):
        create_metadata_provider(config)


def test_create_metadata_provider_builds_tmdb_provider_when_configured() -> None:
    config = make_config(
        metadata_provider=MetadataProviderMode.TMDB,
        tmdb_api_key="test-key",
    )

    provider = create_metadata_provider(config)

    assert isinstance(provider, TmdbMovieProvider)
    assert provider.api_key == "test-key"


def make_config(
    *,
    ai_adapter: AdapterMode = AdapterMode.NONE,
    ai_url: str | None = None,
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
    metadata_provider: MetadataProviderMode = MetadataProviderMode.TMDB,
    tmdb_api_key: str | None = "test-key",
) -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"),
        shows_dir=Path("/media/library/shows"),
        database_dir=Path("/media/db"),
        ai_adapter=ai_adapter,
        ai_url=ai_url,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        metadata_provider=metadata_provider,
        tmdb_api_key=tmdb_api_key,
    )
