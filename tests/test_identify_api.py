"""候选识别 API 端点测试"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from media_pilot.app import create_app
from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database


def _make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )


def test_identify_returns_candidates(tmp_path: Path, monkeypatch) -> None:
    """POST /identify 返回候选列表"""
    from media_pilot.adapters.metadata import MetadataCandidate

    config = _make_config(tmp_path)
    for d in (config.downloads_dir, config.watch_dir, config.workspace_dir,
              config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    app = create_app(config=config, session_factory=session_factory)

    # Stub provider
    class StubProvider:
        provider_name = "stub"
        def search_movie(self, keyword, language_priority=None):
            from media_pilot.adapters.metadata import MetadataProviderResponse
            return MetadataProviderResponse(value=[
                MetadataCandidate(
                    provider="stub", provider_id="tt123", title="Test",
                    original_title=None, year=2024, media_type="movie",
                    overview=None, poster_url=None, confidence=0.9,
                    match_reason="test",
                )
            ])

    monkeypatch.setattr(
        "media_pilot.services.candidate_search.create_metadata_provider_by_name",
        lambda c, n: StubProvider(),
    )

    client = TestClient(app)
    resp = client.post("/api/v1/resource-discovery/identify", json={
        "candidate_handle": "tok-abc",
        "profile": "tmdb_movie",
        "keyword": "Test Movie",
        "use_lightweight_cleanup": False,
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["data"]["keyword_used"] == "Test Movie"
    assert data["data"]["profile"] == "tmdb_movie"
    assert len(data["data"]["candidates"]) == 1
    assert data["data"]["candidates"][0]["title"] == "Test"


def test_identify_empty_result(tmp_path: Path, monkeypatch) -> None:
    """候选识别无结果返回空列表"""
    config = _make_config(tmp_path)
    for d in (config.downloads_dir, config.watch_dir, config.workspace_dir,
              config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    app = create_app(config=config, session_factory=session_factory)

    class StubProvider:
        provider_name = "stub"
        def search_movie(self, keyword, language_priority=None):
            from media_pilot.adapters.metadata import MetadataProviderResponse
            return MetadataProviderResponse(value=[])

    monkeypatch.setattr(
        "media_pilot.services.candidate_search.create_metadata_provider_by_name",
        lambda c, n: StubProvider(),
    )

    client = TestClient(app)
    resp = client.post("/api/v1/resource-discovery/identify", json={
        "candidate_handle": "tok-xyz",
        "profile": "tmdb_movie",
        "keyword": "NoMatch",
        "use_lightweight_cleanup": False,
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["data"]["candidates"] == []


def test_identify_invalid_profile(tmp_path: Path, monkeypatch) -> None:
    """无效 profile 返回错误"""
    config = _make_config(tmp_path)
    for d in (config.downloads_dir, config.watch_dir, config.workspace_dir,
              config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    app = create_app(config=config, session_factory=session_factory)

    # raise ValueError when called with invalid profile
    monkeypatch.setattr(
        "media_pilot.services.candidate_search.create_metadata_provider_by_name",
        lambda c, n: (_ for _ in ()).throw(ValueError("unsupported profile: bad")),
    )

    client = TestClient(app)
    resp = client.post("/api/v1/resource-discovery/identify", json={
        "candidate_handle": "tok-bad",
        "profile": "bad_profile",
        "keyword": "test",
        "use_lightweight_cleanup": False,
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"
    assert data["messages"][0]["code"] == "invalid_profile"


def test_identify_without_candidate_handle(tmp_path: Path, monkeypatch) -> None:
    """手动上传等场景不传 candidate_handle 时识别仍正常返回"""
    from media_pilot.adapters.metadata import MetadataCandidate

    config = _make_config(tmp_path)
    for d in (config.downloads_dir, config.watch_dir, config.workspace_dir,
              config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    app = create_app(config=config, session_factory=session_factory)

    class StubProvider:
        provider_name = "stub"
        def search_movie(self, keyword, language_priority=None):
            from media_pilot.adapters.metadata import MetadataProviderResponse
            return MetadataProviderResponse(value=[
                MetadataCandidate(
                    provider="stub", provider_id="tt456", title="ManualTest",
                    original_title=None, year=2025, media_type="movie",
                    overview=None, poster_url=None, confidence=0.8,
                    match_reason="manual",
                )
            ])

    monkeypatch.setattr(
        "media_pilot.services.candidate_search.create_metadata_provider_by_name",
        lambda c, n: StubProvider(),
    )

    client = TestClient(app)
    resp = client.post("/api/v1/resource-discovery/identify", json={
        "profile": "tmdb_movie",
        "keyword": "ManualTest",
        "use_lightweight_cleanup": False,
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["data"]["keyword_used"] == "ManualTest"
    assert data["data"]["profile"] == "tmdb_movie"
    assert len(data["data"]["candidates"]) == 1
    assert data["data"]["candidates"][0]["title"] == "ManualTest"
