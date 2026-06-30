from pathlib import Path

from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import FileAsset, WriteResult
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
from media_pilot.services.no_metadata_publish import publish_without_metadata


def _make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        llm_api_key="test-key",
        llm_base_url="https://example.test/v1",
        llm_model="test-model",
        tmdb_api_key="tmdb-key",
    )


def _session_factory(config: AppConfig):
    initialize_database(config)
    return create_session_factory(config)


def test_publish_file_without_metadata_copies_video_and_same_stem_subtitle(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    config.watch_dir.mkdir(parents=True)
    source = config.watch_dir / "Rare.Movie.1080p.mkv"
    source.write_bytes(b"video")
    subtitle = config.watch_dir / "Rare.Movie.1080p.zh.srt"
    subtitle.write_text("subtitle", encoding="utf-8")

    sf = _session_factory(config)
    with sf() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(source),
            status="waiting_user",
            current_step="metadata_unavailable_action",
            media_type="movie",
        ))
        result = publish_without_metadata(
            session=session, config=config, task_id=task.id,
        )
        session.commit()

        assert result.status == "published"
        task_after = IngestTaskRepository(session).get(task.id)
        assert task_after is not None
        assert task_after.status == "library_import_complete"
        assert task_after.metadata_status == "none"

        final_dir = config.movies_dir / "Rare.Movie.1080p"
        assert (final_dir / "Rare.Movie.1080p.mkv").read_bytes() == b"video"
        assert (final_dir / "Rare.Movie.1080p.zh.srt").read_text(encoding="utf-8") == "subtitle"
        assert not (config.movies_dir / ".media-pilot-staging" / task.id).exists()
        assert not any(final_dir.glob("*.nfo"))
        assert not any(final_dir.glob("*.jpg"))

        roles = {asset.role for asset in session.query(FileAsset).filter_by(task_id=task.id)}
        assert roles == {"library_video", "library_subtitle"}
        write_result = session.query(WriteResult).filter_by(task_id=task.id).one()
        assert write_result.payload["publish_mode"] == "no_metadata"


def test_publish_bdmv_without_metadata_copies_disc_directory_only(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    source = config.watch_dir / "Unknown Disc"
    stream_dir = source / "BDMV" / "STREAM"
    stream_dir.mkdir(parents=True)
    (source / "BDMV" / "index.bdmv").write_text("index", encoding="utf-8")
    (stream_dir / "00001.m2ts").write_bytes(b"m2ts")
    (source / "CERTIFICATE").mkdir()

    sf = _session_factory(config)
    with sf() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(source),
            status="waiting_user",
            current_step="metadata_unavailable_action",
            media_type="movie",
        ))
        result = publish_without_metadata(
            session=session, config=config, task_id=task.id,
        )
        session.commit()

        assert result.status == "published"
        final_dir = config.movies_dir / "Unknown Disc"
        assert (final_dir / "BDMV" / "index.bdmv").read_text(encoding="utf-8") == "index"
        assert (final_dir / "BDMV" / "STREAM" / "00001.m2ts").read_bytes() == b"m2ts"
        assert not (final_dir / "movie.nfo").exists()
        assert not any(final_dir.glob("*.jpg"))

        task_after = IngestTaskRepository(session).get(task.id)
        assert task_after is not None
        assert task_after.metadata_status == "none"


def test_agent_running_rejected_by_default_but_allowed_for_agent_internal_path(
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path)
    config.watch_dir.mkdir(parents=True)
    source = config.watch_dir / "Unknown.Agent.Path.mkv"
    source.write_bytes(b"video")

    sf = _session_factory(config)
    with sf() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(source),
            status="agent_running",
            current_step="no_metadata_publish",
            media_type="movie",
        ))

        rejected = publish_without_metadata(
            session=session, config=config, task_id=task.id,
        )
        assert rejected.status == "rejected"
        assert rejected.blocking_reasons == ["agent_running"]

        allowed = publish_without_metadata(
            session=session,
            config=config,
            task_id=task.id,
            allow_agent_running=True,
        )
        session.commit()

        assert allowed.status == "published"
        task_after = IngestTaskRepository(session).get(task.id)
        assert task_after is not None
        assert task_after.status == "library_import_complete"
        assert task_after.metadata_status == "none"
