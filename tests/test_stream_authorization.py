import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from media_pilot.accounts.passwords import hash_password
from media_pilot.accounts.session_service import SessionService
from media_pilot.config import AppConfig
from media_pilot.repository.account_repositories import UserRepository
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
from media_pilot.services.content_discovery import (
    ContentDiscoveryMessage,
    build_content_discovery_messages,
)


def test_stream_wrapper_rechecks_authorization_while_source_is_idle() -> None:
    from media_pilot.accounts.stream_authorization import (
        stream_with_periodic_authorization,
    )

    release_source = threading.Event()
    checks = 0

    def source():
        yield "first"
        release_source.wait(timeout=1)
        yield "late"

    def authorize() -> bool:
        nonlocal checks
        checks += 1
        return False

    started = time.monotonic()
    items = list(stream_with_periodic_authorization(
        source(),
        authorize=authorize,
        authorization_error="denied",
        interval_seconds=0.02,
    ))

    assert items == ["first", "denied"]
    assert checks == 1
    assert time.monotonic() - started < 0.5


def test_content_discovery_prompt_forbids_adult_recommendations_without_permission() -> None:
    messages = build_content_discovery_messages(
        [ContentDiscoveryMessage(role="user", content="recommend something")],
        can_access_adult=False,
    )

    assert "不得推荐成人内容" in messages[0]["content"]


def test_stream_authorizer_rechecks_session_and_task_access(tmp_path: Path) -> None:
    from media_pilot.api.auth_dependencies import build_stream_authorizer

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
    with session_factory() as session:
        alice = UserRepository(session).create_user(
            username="Alice",
            password_hash=hash_password("alice password"),
        )
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path="/data/alice.mkv",
            status="agent_running",
            owner_user_id=alice.id,
        ))
        token, _ = SessionService().create(
            session,
            user_id=alice.id,
            now=datetime.now(UTC),
        )
        session.commit()

    authorize = build_stream_authorizer(
        session_factory,
        token=token,
        task_id=task.id,
    )
    assert authorize() is True

    with session_factory() as session:
        loaded = IngestTaskRepository(session).get(task.id)
        assert loaded is not None
        loaded.owner_user_id = "another-user"
        session.commit()

    assert authorize() is False
