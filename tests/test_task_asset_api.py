from pathlib import Path

from fastapi.testclient import TestClient

from media_pilot.app import create_app
from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import FileAsset
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository


def _make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path / "db",
    )


def _seed_task_with_asset(
    tmp_path: Path,
    *,
    role: str,
    asset_path: Path,
) -> tuple[TestClient, str]:
    config = _make_config(tmp_path)
    for directory in (
        config.downloads_dir,
            config.watch_dir,
        config.workspace_dir,
        config.movies_dir,
        config.shows_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)
    with session_factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path=str(config.downloads_dir / "Movie.2026.mkv"),
                status="completed",
                current_step="library_import_complete",
                media_type="movie",
            )
        )
        session.add(
            FileAsset(
                task_id=task.id,
                role=role,
                path=str(asset_path),
                size_bytes=asset_path.stat().st_size if asset_path.exists() else None,
            )
        )
        session.commit()
        task_id = task.id

    return TestClient(create_app(config=config, session_factory=session_factory)), task_id


def test_task_asset_api_returns_registered_poster_within_allowed_root(tmp_path: Path) -> None:
    poster = tmp_path / "movies" / "Movie (2026)" / "poster.jpg"
    poster.parent.mkdir(parents=True)
    poster.write_bytes(b"poster-bytes")
    client, task_id = _seed_task_with_asset(tmp_path, role="library_poster", asset_path=poster)

    response = client.get(f"/api/v1/tasks/{task_id}/assets/poster")

    assert response.status_code == 200
    assert response.content == b"poster-bytes"
    assert response.headers["content-type"].startswith("image/")


def test_task_asset_api_rejects_registered_asset_outside_allowed_roots(tmp_path: Path) -> None:
    unsafe_asset = tmp_path / "outside" / "poster.jpg"
    unsafe_asset.parent.mkdir(parents=True)
    unsafe_asset.write_bytes(b"unsafe")
    client, task_id = _seed_task_with_asset(
        tmp_path,
        role="library_poster",
        asset_path=unsafe_asset,
    )

    response = client.get(f"/api/v1/tasks/{task_id}/assets/poster")

    assert response.status_code == 403


def test_task_asset_api_returns_not_found_when_registered_asset_is_missing(tmp_path: Path) -> None:
    missing_asset = tmp_path / "movies" / "Movie (2026)" / "missing-poster.jpg"
    missing_asset.parent.mkdir(parents=True)
    client, task_id = _seed_task_with_asset(
        tmp_path,
        role="library_poster",
        asset_path=missing_asset,
    )

    response = client.get(f"/api/v1/tasks/{task_id}/assets/poster")

    assert response.status_code == 404
