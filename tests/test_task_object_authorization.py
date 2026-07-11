from pathlib import Path

from fastapi.testclient import TestClient

from media_pilot.accounts.passwords import hash_password
from media_pilot.api.auth_dependencies import (
    require_authorized_download_task,
    require_authorized_ingest_task,
)
from media_pilot.app import create_app
from media_pilot.config import AppConfig
from media_pilot.repository.account_repositories import UserRepository
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import AgentDecisionRequest, AgentRun
from media_pilot.repository.repositories import (
    DownloadTaskCreate,
    DownloadTaskRepository,
    IngestTaskCreate,
    IngestTaskRepository,
)
from tests.auth_helpers import AuthenticatedTestClient


def _login(app, username: str, password: str) -> TestClient:
    client = TestClient(app)
    client.get("/api/v1/auth/status")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-CSRF-Token": client.cookies["media_pilot_csrf"]},
    )
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = client.cookies["media_pilot_csrf"]
    return client


def test_other_users_and_inaccessible_adult_tasks_are_not_found(
    tmp_path: Path,
) -> None:
    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path / "db",
    )
    initialize_database(config)
    session_factory = create_session_factory(config)
    app = create_app(config=config, session_factory=session_factory)
    AuthenticatedTestClient(app)

    with session_factory() as session:
        users = UserRepository(session)
        alice = users.create_user(
            username="Alice",
            password_hash=hash_password("alice password"),
        )
        bob = users.create_user(
            username="Bob",
            password_hash=hash_password("bob password"),
        )
        ingest = IngestTaskRepository(session)
        own = ingest.create(IngestTaskCreate(
            source_path="/data/alice.mkv",
            status="discovered",
            owner_user_id=alice.id,
        ))
        other = ingest.create(IngestTaskCreate(
            source_path="/data/bob.mkv",
            status="discovered",
            owner_user_id=bob.id,
        ))
        adult = ingest.create(IngestTaskCreate(
            source_path="/data/alice-adult.mkv",
            status="discovered",
            owner_user_id=alice.id,
            is_adult=True,
        ))
        downloads = DownloadTaskRepository(session)
        other_download = downloads.create(DownloadTaskCreate(
            title="bob.mkv",
            source="prowlarr",
            save_path="/data/downloads",
            owner_user_id=bob.id,
        ))
        run = AgentRun(task_id=other.id, status="waiting_user")
        session.add(run)
        session.flush()
        decision = AgentDecisionRequest(
            run_id=run.id,
            task_id=other.id,
            decision_type="test",
            status="pending",
        )
        session.add(decision)
        own_run = AgentRun(task_id=own.id, status="waiting_user")
        session.add(own_run)
        session.flush()
        own_conflict = AgentDecisionRequest(
            run_id=own_run.id,
            task_id=own.id,
            decision_type="target_conflict",
            status="pending",
        )
        session.add(own_conflict)
        session.commit()

    client = _login(app, "alice", "alice password")

    assert client.get(f"/api/v1/tasks/{own.id}").status_code == 200
    assert client.get(f"/api/v1/tasks/{other.id}").status_code == 404
    assert client.get(f"/api/v1/tasks/{adult.id}").status_code == 404
    assert client.post(f"/api/v1/tasks/{other.id}/process").status_code == 404
    assert client.get(f"/api/v1/downloads/{other_download.id}").status_code == 404
    assert client.post(
        f"/api/v1/downloads/{other_download.id}/pause"
    ).status_code == 404
    assert client.post(
        f"/api/v1/agent-decisions/{decision.id}/reply",
        json={"option_id": "continue"},
    ).status_code == 404
    assert client.post(
        f"/api/v1/agent-decisions/{own_conflict.id}/reply",
        json={"option_id": "overwrite_target"},
    ).status_code == 403


def test_every_task_object_route_declares_the_matching_authorization_dependency() -> None:
    app = create_app()

    for route in app.routes:
        path = getattr(route, "path", "")
        dependant = getattr(route, "dependant", None)
        dependency_calls = (
            {dependency.call for dependency in dependant.dependencies}
            if dependant is not None
            else set()
        )
        if "/tasks/{task_id}" in path:
            assert require_authorized_ingest_task in dependency_calls, path
        if "/downloads/{download_id}" in path:
            assert require_authorized_download_task in dependency_calls, path
