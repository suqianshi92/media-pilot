from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm.attributes import set_committed_value

from media_pilot.accounts.session_service import (
    SESSION_LIFETIME,
    SESSION_RENEWAL_INTERVAL,
    SessionService,
)
from media_pilot.config import AppConfig
from media_pilot.repository.account_repositories import UserRepository
from media_pilot.repository.database import create_session_factory, initialize_database


def make_config(database_dir: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"),
        shows_dir=Path("/media/library/shows"),
        database_dir=database_dir,
    )


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def test_session_service_stores_only_token_hash_and_authenticates(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)
    now = datetime(2026, 7, 10, tzinfo=UTC)

    with session_factory() as session:
        user = UserRepository(session).create_user(
            username="Alice",
            password_hash="argon-user",
        )
        token, account_session = SessionService().create(
            session,
            user_id=user.id,
            now=now,
        )
        session.commit()

        assert len(token) >= 32
        assert account_session.token_hash != token
        assert account_session.expires_at == now + SESSION_LIFETIME

        authenticated = SessionService().authenticate(session, token=token, now=now)
        assert authenticated is not None
        assert authenticated.user.id == user.id
        assert authenticated.renewed is False


def test_session_service_renews_at_most_once_per_24_hours(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)
    created_at = datetime(2026, 7, 10, tzinfo=UTC)

    with session_factory() as session:
        user = UserRepository(session).create_user(
            username="Alice",
            password_hash="argon-user",
        )
        token, account_session = SessionService().create(
            session,
            user_id=user.id,
            now=created_at,
        )
        session.commit()

        before_boundary = SessionService().authenticate(
            session,
            token=token,
            now=created_at + SESSION_RENEWAL_INTERVAL - timedelta(seconds=1),
        )
        assert before_boundary is not None
        assert before_boundary.renewed is False
        assert account_session.expires_at == created_at + SESSION_LIFETIME

        renewal_time = created_at + SESSION_RENEWAL_INTERVAL
        at_boundary = SessionService().authenticate(
            session,
            token=token,
            now=renewal_time,
        )
        assert at_boundary is not None
        assert at_boundary.renewed is True
        assert as_utc(account_session.last_active_at) == renewal_time
        assert as_utc(account_session.expires_at) == renewal_time + SESSION_LIFETIME

        session.commit()
        set_committed_value(account_session, "last_active_at", created_at)
        set_committed_value(
            account_session,
            "expires_at",
            created_at + SESSION_LIFETIME,
        )

        stale_retry = SessionService().authenticate(
            session,
            token=token,
            now=renewal_time,
        )
        assert stale_retry is not None
        assert stale_retry.renewed is False


def test_session_service_rejects_expired_disabled_and_revoked_sessions(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)
    now = datetime(2026, 7, 10, tzinfo=UTC)

    with session_factory() as session:
        users = UserRepository(session)
        user = users.create_user(username="Alice", password_hash="argon-user")
        token, _ = SessionService().create(session, user_id=user.id, now=now)
        session.commit()

        assert SessionService().authenticate(
            session,
            token=token,
            now=now + SESSION_LIFETIME,
        ) is None

        disabled_token, _ = SessionService().create(session, user_id=user.id, now=now)
        users.set_enabled(user, False)
        session.commit()
        assert SessionService().authenticate(
            session,
            token=disabled_token,
            now=now,
        ) is None

        users.set_enabled(user, True)
        session.commit()
        assert SessionService().authenticate(
            session,
            token=disabled_token,
            now=now,
        ) is None

        token, _ = SessionService().create(session, user_id=user.id, now=now)
        SessionService().revoke_current(session, token=token, now=now)
        session.commit()
        assert SessionService().authenticate(session, token=token, now=now) is None
