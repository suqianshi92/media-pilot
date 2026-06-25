from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from media_pilot.config import AppConfig
from media_pilot.orchestration.ingestion import create_ingest_task, scan_downloads
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import IngestTask
from media_pilot.repository.repositories import IngestTaskRepository


def make_config(database_dir: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"),
        shows_dir=Path("/media/library/shows"),
        database_dir=database_dir,
    )


def test_create_ingest_task_records_source_metadata(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    database_dir = tmp_path / "db"
    downloads_dir.mkdir()
    media_file = downloads_dir / "Movie.2024.mkv"
    media_file.write_bytes(b"movie")
    modified_at = media_file.stat().st_mtime
    discovered_at = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)

    initialize_database(make_config(database_dir))
    session_factory = create_session_factory(make_config(database_dir))
    candidate = scan_downloads(downloads_dir).candidates[0]

    with session_factory() as session:
        repository = IngestTaskRepository(session)
        task = create_ingest_task(repository, candidate, discovered_at=discovered_at)
        session.commit()

        loaded = repository.get(task.id)

    assert loaded is not None
    assert loaded.source_path == str(media_file)
    assert loaded.source_size_bytes == 5
    assert loaded.source_modified_at == datetime.fromtimestamp(modified_at, UTC)
    assert loaded.discovered_at == discovered_at
    assert loaded.status == "discovered"
    assert loaded.current_step == "download_scan"


def test_scan_and_create_task_keeps_original_download_file_unchanged(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    database_dir = tmp_path / "db"
    downloads_dir.mkdir()
    media_file = downloads_dir / "Sample.Movie.2026.mkv"
    original_content = b"sample movie"
    media_file.write_bytes(original_content)
    original_stat = media_file.stat()
    discovered_at = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)

    initialize_database(make_config(database_dir))
    session_factory = create_session_factory(make_config(database_dir))
    scan_result = scan_downloads(downloads_dir)

    with session_factory() as session:
        repository = IngestTaskRepository(session)
        create_ingest_task(repository, scan_result.candidates[0], discovered_at=discovered_at)
        session.commit()

        tasks = session.scalars(select(IngestTask)).all()

    assert len(scan_result.candidates) == 1
    assert len(tasks) == 1
    assert tasks[0].source_path == str(media_file)
    assert media_file.exists()
    assert media_file.read_bytes() == original_content
    assert media_file.stat().st_size == original_stat.st_size
    assert media_file.stat().st_mtime_ns == original_stat.st_mtime_ns
