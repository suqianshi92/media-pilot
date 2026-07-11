from pathlib import Path

from fastapi.testclient import TestClient

from media_pilot.accounts.passwords import hash_password
from media_pilot.app import create_app
from media_pilot.config import AppConfig
from media_pilot.repository.account_repositories import UserRepository
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.repositories import (
    DownloadTaskCreate,
    DownloadTaskRepository,
    IngestTaskCreate,
    IngestTaskRepository,
)
from tests.auth_helpers import AuthenticatedTestClient


def _make_app(tmp_path: Path):
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
    return create_app(config=config, session_factory=session_factory), session_factory


def _login(app, *, username: str, password: str) -> TestClient:
    client = TestClient(app)
    client.get("/api/v1/auth/status")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-CSRF-Token": client.cookies["media_pilot_csrf"]},
    )
    assert response.status_code == 200
    return client


def test_task_lists_filter_owner_and_adult_before_total_and_pagination(
    tmp_path: Path,
) -> None:
    app, session_factory = _make_app(tmp_path)
    admin_client = AuthenticatedTestClient(app)

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
        downloads = DownloadTaskRepository(session)
        for owner_user_id, is_adult, suffix in (
            (alice.id, False, "alice-normal"),
            (alice.id, True, "alice-adult"),
            (bob.id, False, "bob-normal"),
            (None, False, "system"),
        ):
            ingest.create(IngestTaskCreate(
                source_path=f"/data/{suffix}.mkv",
                status="discovered",
                owner_user_id=owner_user_id,
                is_adult=is_adult,
            ))
            downloads.create(DownloadTaskCreate(
                title=f"{suffix}.mkv",
                source="prowlarr",
                save_path="/data/downloads",
                owner_user_id=owner_user_id,
                is_adult=is_adult,
            ))
        session.commit()

    alice_client = _login(
        app,
        username="alice",
        password="alice password",
    )

    tasks = alice_client.get("/api/v1/tasks?page=1&page_size=1").json()
    downloads = alice_client.get("/api/v1/downloads?page=1&page_size=1").json()
    flows = alice_client.get("/api/v1/flows?page=1&page_size=1").json()
    assert tasks["meta"]["total"] == 1
    assert tasks["data"]["items"][0]["source_path"].endswith("alice-normal.mkv")
    assert tasks["data"]["items"][0]["owner_username"] is None
    assert downloads["meta"]["total"] == 1
    assert downloads["data"]["items"][0]["title"] == "alice-normal.mkv"
    assert flows["meta"]["total"] == 2

    with session_factory() as session:
        alice = UserRepository(session).get_by_username("alice")
        assert alice is not None
        UserRepository(session).set_adult_access(alice, True)
        session.commit()

    assert alice_client.get("/api/v1/tasks").json()["meta"]["total"] == 2
    assert alice_client.get("/api/v1/downloads").json()["meta"]["total"] == 2
    assert alice_client.get("/api/v1/flows").json()["meta"]["total"] == 4

    admin_tasks = admin_client.get("/api/v1/tasks").json()
    assert admin_tasks["meta"]["total"] == 4
    task_owners = {
        item["source_path"]: item["owner_username"]
        for item in admin_tasks["data"]["items"]
    }
    assert task_owners["/data/alice-normal.mkv"] == "Alice"
    assert task_owners["/data/system.mkv"] == "系统"

    admin_downloads = admin_client.get("/api/v1/downloads").json()
    assert admin_downloads["meta"]["total"] == 4
    download_owners = {
        item["title"]: item["owner_username"]
        for item in admin_downloads["data"]["items"]
    }
    assert download_owners["alice-normal.mkv"] == "Alice"
    assert download_owners["system.mkv"] == "系统"

    admin_flows = admin_client.get("/api/v1/flows").json()
    assert admin_flows["meta"]["total"] == 8
    assert {item["owner_username"] for item in admin_flows["data"]["items"]} >= {
        "Alice",
        "系统",
    }
