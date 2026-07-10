from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from media_pilot.accounts.errors import (
    AlreadyInitializedError,
    ProtectedAdminError,
    UserDeletionForbiddenError,
)
from media_pilot.accounts.passwords import (
    InvalidPasswordError,
    hash_password,
    validate_password,
    verify_password,
)
from media_pilot.config import AppConfig
from media_pilot.repository.account_repositories import (
    AccountSessionRepository,
    UserRepository,
    normalize_username,
)
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


def test_password_policy_preserves_input_and_uses_argon2id() -> None:
    password = "  密码 123  "

    assert validate_password(password) == password
    password_hash = hash_password(password)

    assert password_hash.startswith("$argon2id$")
    assert password not in password_hash
    assert verify_password(password_hash, password) is True
    assert verify_password(password_hash, password.strip()) is False


def test_password_verification_rejects_malformed_hashes() -> None:
    assert verify_password("not-an-argon2-hash", "password") is False


def test_password_verification_rejects_out_of_policy_input_before_argon2() -> None:
    oversized_password = "x" * 129
    oversized_hash = hash_password("valid-password")

    assert verify_password(oversized_hash, oversized_password) is False


@pytest.mark.parametrize("password", ["1234567", "x" * 129])
def test_password_policy_rejects_values_outside_8_to_128_characters(
    password: str,
) -> None:
    with pytest.raises(InvalidPasswordError):
        validate_password(password)


def test_user_repository_enforces_case_insensitive_identity_and_single_admin(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        users = UserRepository(session)
        admin = users.create_initial_admin(
            username="Owner",
            password_hash="argon-admin",
        )
        session.commit()

        assert admin.role == "admin"
        assert admin.can_access_adult is True
        assert admin.is_enabled is True
        assert users.get_by_username("OWNER") == admin
        assert normalize_username("Owner") == "owner"

        with pytest.raises(AlreadyInitializedError):
            users.create_initial_admin(
                username="AnotherOwner",
                password_hash="argon-other",
            )

        users.create_user(username="Alice", password_hash="argon-user")
        session.commit()
        with pytest.raises(IntegrityError):
            users.create_user(username="alice", password_hash="argon-duplicate")
            session.flush()


def test_initial_admin_cannot_be_disabled_or_lose_adult_access(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        users = UserRepository(session)
        admin = users.create_initial_admin(
            username="Owner",
            password_hash="argon-admin",
        )

        with pytest.raises(ProtectedAdminError):
            users.set_enabled(admin, False)
        with pytest.raises(ProtectedAdminError):
            users.set_adult_access(admin, False)

        admin.role = "user"
        with pytest.raises(ProtectedAdminError):
            session.flush()
        session.rollback()


def test_users_cannot_be_physically_deleted(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        user = UserRepository(session).create_user(
            username="Alice",
            password_hash="argon-user",
        )
        session.delete(user)

        with pytest.raises(UserDeletionForbiddenError):
            session.flush()


def test_server_sessions_can_be_looked_up_and_revoked_per_user(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    initialize_database(config)
    session_factory = create_session_factory(config)
    now = datetime(2026, 7, 10, tzinfo=UTC)

    with session_factory() as session:
        user = UserRepository(session).create_user(
            username="Alice",
            password_hash="argon-user",
        )
        sessions = AccountSessionRepository(session)
        first = sessions.create(
            user_id=user.id,
            token_hash="a" * 64,
            now=now,
            expires_at=now + timedelta(days=30),
        )
        second = sessions.create(
            user_id=user.id,
            token_hash="b" * 64,
            now=now,
            expires_at=now + timedelta(days=30),
        )
        session.commit()

        assert sessions.get_active_by_token_hash("a" * 64, now=now) == first
        assert sessions.revoke_all_for_user(user.id, now=now) == 2
        session.commit()

        assert sessions.get_active_by_token_hash("a" * 64, now=now) is None
        assert first.revoked_at == now
        assert second.revoked_at == now
