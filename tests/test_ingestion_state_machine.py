from pathlib import Path

from media_pilot.config import AppConfig
from media_pilot.orchestration.state_machine import IngestTaskStatus, transition_task
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository


def make_config(database_dir: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"),
        shows_dir=Path("/media/library/shows"),
        database_dir=database_dir,
    )


def test_minimal_ingest_task_state_transitions(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        repository = IngestTaskRepository(session)
        task = repository.create(
            IngestTaskCreate(
                source_path="/media/downloads/Movie.2024.mkv",
                status=IngestTaskStatus.DISCOVERED,
                current_step="download_scan",
            )
        )

        transition_task(repository, task, IngestTaskStatus.WAITING_STABLE, "stability_check")
        transition_task(repository, task, IngestTaskStatus.CREATED, "task_created")
        session.commit()

        loaded = repository.get(task.id)

    assert loaded is not None
    assert loaded.status == "created"
    assert loaded.current_step == "task_created"
    assert loaded.failure_reason is None


def test_failed_transition_records_failure_reason(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        repository = IngestTaskRepository(session)
        task = repository.create(
            IngestTaskCreate(
                source_path="/media/downloads/Movie.2024.mkv",
                status=IngestTaskStatus.DISCOVERED,
                current_step="download_scan",
            )
        )

        transition_task(
            repository,
            task,
            IngestTaskStatus.FAILED,
            "download_scan",
            failure_reason="stat failed",
        )
        session.commit()

        loaded = repository.get(task.id)

    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.failure_reason == "stat failed"


def test_workflow_states_are_defined() -> None:
    assert IngestTaskStatus.WORKSPACE_IMPORTED == "workspace_imported"
    assert IngestTaskStatus.AI_PARSED == "ai_parsed"
    assert IngestTaskStatus.CANDIDATES_READY == "candidates_ready"
    assert IngestTaskStatus.PROCESSING == "processing"
    assert IngestTaskStatus.LIBRARY_IMPORT_COMPLETE == "library_import_complete"


def test_agent_states_are_defined() -> None:
    assert IngestTaskStatus.AGENT_RUNNING == "agent_running"
    assert IngestTaskStatus.WAITING_USER == "waiting_user"
    assert IngestTaskStatus.AGENT_FAILED == "agent_failed"
