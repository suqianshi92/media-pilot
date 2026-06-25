from pathlib import Path

from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.repositories import (
    IngestTaskCreate,
    IngestTaskRepository,
    MediaCandidateRepository,
    MediaSourceSelectionRepository,
    MetadataDetailRepository,
    SearchKeywordRepository,
    WritePlanRepository,
    WriteResultRepository,
)


def make_config(database_dir: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"),
        shows_dir=Path("/media/library/shows"),
        database_dir=database_dir,
    )


def test_ingest_task_repository_creates_and_reads_task(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        repository = IngestTaskRepository(session)
        task = repository.create(
            IngestTaskCreate(
                source_path="/media/downloads/Movie.2024.mkv",
                status="discovered",
                current_step="scan",
            )
        )
        session.commit()

        loaded = repository.get(task.id)

    assert loaded is not None
    assert loaded.id == task.id
    assert loaded.source_path == "/media/downloads/Movie.2024.mkv"
    assert loaded.status == "discovered"
    assert loaded.current_step == "scan"


def test_media_candidate_repository_persists_generic_candidates(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/Example.Movie.2026.mkv",
                status="needs_confirmation",
                current_step="operator_confirmation",
            )
        )
        repository = MediaCandidateRepository(session)

        saved = repository.add_candidate(
            task.id,
            source="tmdb",
            media_type="movie",
            title="Example Movie",
            original_title="Example Movie",
            year=2026,
            external_id="tmdb:example-movie-2026",
            confidence=0.93,
            reason="fake match from title and year",
            payload={
                "title": "Example Movie",
                "original_title": "Example Movie",
                "year": 2026,
                "media_type": "movie",
                "external_id": "tmdb:example-movie-2026",
                "summary": "Fake movie candidate.",
            },
        )
        session.commit()

        loaded = repository.list_for_task(task.id)

    assert saved.external_id == "tmdb:example-movie-2026"
    assert len(loaded) == 1
    assert loaded[0].source == "tmdb"
    assert loaded[0].title == "Example Movie"
    assert loaded[0].external_id == "tmdb:example-movie-2026"
    assert loaded[0].payload["summary"] == "Fake movie candidate."


# 注: ConfirmationRequest 旧通道已在 replace-legacy-confirmation-with-agent-decisions
# 完全下线。test_confirmation_repository_saves_candidate_decision 同步删除。
# 候选决策由 AgentDecisionRequest(decision_type=metadata_confirmation) 承载，
# 详见 tests/test_agent_decision_request_repository.py。


def test_phase_two_repositories_persist_reviewable_scrape_records(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/Example.Movie.2026.mkv",
                status="needs_confirmation",
                current_step="operator_confirmation",
            )
        )

        source_selection = MediaSourceSelectionRepository(session).save(
            task.id,
            input_path="/media/downloads/Example.Movie.2026",
            selected_path="/media/downloads/Example.Movie.2026/Example.Movie.2026.mkv",
            confidence=0.87,
            reason="largest_video_file",
            payload={
                "excluded_paths": [
                    "/media/downloads/Example.Movie.2026/sample.mkv",
                    "/media/downloads/Example.Movie.2026/poster.jpg",
                ]
            },
        )
        keyword_record = SearchKeywordRepository(session).save(
            task.id,
            keyword="Example Movie 2026",
            source="rule",
            confidence=0.91,
            reason="title_year_cleanup",
            payload={"tokens_removed": ["1080p", "GROUP"]},
        )
        metadata_detail = MetadataDetailRepository(session).save(
            task.id,
            provider="tmdb",
            provider_id="movie:1234",
            media_type="movie",
            title="Example Movie",
            original_title="Example Movie",
            year=2026,
            payload={
                "plot": "A fake movie for repository testing.",
                "external_ids": {"imdb": "tt1234567"},
                "images": {"poster": "/poster.jpg"},
            },
        )
        write_plan = WritePlanRepository(session).save(
            task.id,
            target_dir="/library/movies/Example Movie (2026)",
            target_file="/library/movies/Example Movie (2026)/Example Movie (2026).mkv",
            nfo_path="/library/movies/Example Movie (2026)/Example Movie (2026).nfo",
            payload={"conflict_check": "clear", "image_targets": ["poster", "fanart"]},
        )
        write_result = WriteResultRepository(session).save(
            task.id,
            status="warning",
            payload={
                "written_files": [
                    "/library/movies/Example Movie (2026)/Example Movie (2026).mkv",
                    "/library/movies/Example Movie (2026)/Example Movie (2026).nfo",
                ],
                "failed_images": ["clearlogo"],
                "warnings": ["clearlogo download failed"],
            },
        )
        session.commit()

        loaded_source_selection = MediaSourceSelectionRepository(session).get_for_task(task.id)
        loaded_keyword_records = SearchKeywordRepository(session).list_for_task(task.id)
        loaded_metadata_detail = MetadataDetailRepository(session).get_for_task(task.id)
        loaded_write_plan = WritePlanRepository(session).get_for_task(task.id)
        loaded_write_result = WriteResultRepository(session).get_for_task(task.id)

    assert source_selection.id == loaded_source_selection.id
    assert loaded_source_selection is not None
    assert loaded_source_selection.selected_path.endswith("Example.Movie.2026.mkv")
    assert loaded_source_selection.payload["excluded_paths"][0].endswith("sample.mkv")

    assert keyword_record.id == loaded_keyword_records[0].id
    assert loaded_keyword_records[0].keyword == "Example Movie 2026"
    assert loaded_keyword_records[0].payload["tokens_removed"] == ["1080p", "GROUP"]

    assert metadata_detail.id == loaded_metadata_detail.id
    assert loaded_metadata_detail is not None
    assert loaded_metadata_detail.provider == "tmdb"
    assert loaded_metadata_detail.payload["external_ids"]["imdb"] == "tt1234567"

    assert write_plan.id == loaded_write_plan.id
    assert loaded_write_plan is not None
    assert loaded_write_plan.target_dir.endswith("Example Movie (2026)")

    assert write_result.id == loaded_write_result.id
    assert loaded_write_result is not None
    assert loaded_write_result.status == "warning"
    assert loaded_write_result.payload["failed_images"] == ["clearlogo"]
