from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from media_pilot.api.settings_dtos import AppSettingsUpdateRequest
from media_pilot.api.settings_routes import update_settings
from media_pilot.config.settings import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.resource_discovery.qbittorrent_adapter import QBittorrentAdapter
from media_pilot.services.app_settings import (
    AppSettings,
    AppSettingsService,
    SettingsValidationError,
)
from media_pilot.services.download_rate_limits import sync_download_rate_limits_on_startup


def _make_config(tmp_path: Path, *, qb_url: str = "http://qbittorrent:8080") -> AppConfig:
    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        qbittorrent_url=qb_url,
        qbittorrent_username="admin",
        qbittorrent_password="pass",
    )
    for directory in (
        config.downloads_dir,
        config.watch_dir,
        config.workspace_dir,
        config.movies_dir,
        config.shows_dir,
        config.database_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return config


def _make_request(tmp_path: Path, *, qb_url: str = "http://qbittorrent:8080"):
    config = _make_config(tmp_path, qb_url=qb_url)
    initialize_database(config)
    session_factory = create_session_factory(config)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(config=config, session_factory=session_factory)
        )
    )
    return request, session_factory


class TestQBittorrentGlobalRateLimits:
    def test_sets_global_download_and_upload_limits(self, tmp_path: Path, httpx_mock) -> None:
        httpx_mock.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=rate-limit"},
        )
        httpx_mock.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/transfer/setDownloadLimit",
            text="Ok.",
        )
        httpx_mock.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/transfer/setUploadLimit",
            text="Ok.",
        )

        adapter = QBittorrentAdapter(_make_config(tmp_path))

        assert adapter.set_global_rate_limits(
            download_rate_limit_bytes_per_second=1024 * 1024,
            upload_rate_limit_bytes_per_second=512 * 1024,
        )

        requests = httpx_mock.get_requests(method="POST")
        assert requests[1].url.path == "/api/v2/transfer/setDownloadLimit"
        assert requests[1].content == b"limit=1048576"
        assert requests[2].url.path == "/api/v2/transfer/setUploadLimit"
        assert requests[2].content == b"limit=524288"

    def test_zero_limits_are_sent_as_unlimited(self, tmp_path: Path, httpx_mock) -> None:
        httpx_mock.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/auth/login",
            headers={"Set-Cookie": "SID=rate-limit"},
        )
        httpx_mock.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/transfer/setDownloadLimit",
            text="Ok.",
        )
        httpx_mock.add_response(
            method="POST",
            url="http://qbittorrent:8080/api/v2/transfer/setUploadLimit",
            text="Ok.",
        )

        adapter = QBittorrentAdapter(_make_config(tmp_path))

        assert adapter.set_global_rate_limits(
            download_rate_limit_bytes_per_second=0,
            upload_rate_limit_bytes_per_second=0,
        )

        requests = httpx_mock.get_requests(method="POST")
        assert requests[1].content == b"limit=0"
        assert requests[2].content == b"limit=0"


class TestDownloadRateLimitSettings:
    def test_settings_reject_negative_rate_limits(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        initialize_database(config)
        service = AppSettingsService(create_session_factory(config))

        with pytest.raises(SettingsValidationError):
            service.save(AppSettings(upload_rate_limit_bytes_per_second=-1))

    def test_settings_reject_too_large_rate_limits(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        initialize_database(config)
        service = AppSettingsService(create_session_factory(config))

        with pytest.raises(SettingsValidationError):
            service.save(AppSettings(download_rate_limit_bytes_per_second=1024 * 1024 * 1024 + 1))

    def test_update_settings_syncs_qb_and_records_success(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        request, session_factory = _make_request(tmp_path)
        calls: list[dict] = []

        def fake_set_limits(self, **kwargs):  # noqa: ANN001, ARG001
            calls.append(kwargs)
            return True

        monkeypatch.setattr(QBittorrentAdapter, "set_global_rate_limits", fake_set_limits)

        envelope = update_settings(
            AppSettingsUpdateRequest(
                enabled_metadata_profiles=["tmdb_movie", "tmdb_show"],
                download_rate_limit_bytes_per_second=2 * 1024 * 1024,
                upload_rate_limit_bytes_per_second=512 * 1024,
            ),
            request,  # type: ignore[arg-type]
        )

        assert envelope.status == "success"
        assert [m.level for m in envelope.messages] == ["info"]

        settings = AppSettingsService(session_factory).read()
        assert settings.download_rate_limit_bytes_per_second == 2 * 1024 * 1024
        assert settings.upload_rate_limit_bytes_per_second == 512 * 1024
        assert settings.synced_download_rate_limit_bytes_per_second == 2 * 1024 * 1024
        assert settings.synced_upload_rate_limit_bytes_per_second == 512 * 1024
        assert calls == [{
            "download_rate_limit_bytes_per_second": 2 * 1024 * 1024,
            "upload_rate_limit_bytes_per_second": 512 * 1024,
        }]

    def test_update_settings_warning_when_qb_sync_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        request, session_factory = _make_request(tmp_path)

        def fake_set_limits(self, **kwargs):  # noqa: ANN001, ARG001
            return False

        monkeypatch.setattr(QBittorrentAdapter, "set_global_rate_limits", fake_set_limits)

        envelope = update_settings(
            AppSettingsUpdateRequest(
                enabled_metadata_profiles=["tmdb_movie", "tmdb_show"],
                upload_rate_limit_bytes_per_second=256 * 1024,
            ),
            request,  # type: ignore[arg-type]
        )

        assert envelope.status == "success"
        assert envelope.messages[0].level == "info"
        assert envelope.messages[1].level == "warning"
        assert envelope.messages[1].code == "qbittorrent_rate_limit_sync_failed"

        settings = AppSettingsService(session_factory).read()
        assert settings.upload_rate_limit_bytes_per_second == 256 * 1024
        assert settings.synced_upload_rate_limit_bytes_per_second is None


class TestDownloadRateLimitStartupSync:
    def test_startup_sync_skips_when_desired_values_already_synced(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        config = _make_config(tmp_path)
        initialize_database(config)
        session_factory = create_session_factory(config)
        service = AppSettingsService(session_factory)
        service.save(AppSettings(
            download_rate_limit_bytes_per_second=1024,
            upload_rate_limit_bytes_per_second=2048,
        ))
        service.mark_download_rate_limits_synced(
            download_rate_limit_bytes_per_second=1024,
            upload_rate_limit_bytes_per_second=2048,
        )

        calls = 0

        def fake_set_limits(self, **kwargs):  # noqa: ANN001, ARG001
            nonlocal calls
            calls += 1
            return True

        monkeypatch.setattr(QBittorrentAdapter, "set_global_rate_limits", fake_set_limits)

        result = sync_download_rate_limits_on_startup(
            config=config,
            session_factory=session_factory,
        )

        assert result.attempted is False
        assert calls == 0

    def test_startup_sync_attempts_when_previous_sync_missing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        config = _make_config(tmp_path)
        initialize_database(config)
        session_factory = create_session_factory(config)
        AppSettingsService(session_factory).save(AppSettings(
            download_rate_limit_bytes_per_second=1024,
            upload_rate_limit_bytes_per_second=2048,
        ))

        calls: list[dict] = []

        def fake_set_limits(self, **kwargs):  # noqa: ANN001, ARG001
            calls.append(kwargs)
            return True

        monkeypatch.setattr(QBittorrentAdapter, "set_global_rate_limits", fake_set_limits)

        result = sync_download_rate_limits_on_startup(
            config=config,
            session_factory=session_factory,
        )

        assert result.attempted is True
        assert calls == [{
            "download_rate_limit_bytes_per_second": 1024,
            "upload_rate_limit_bytes_per_second": 2048,
        }]
