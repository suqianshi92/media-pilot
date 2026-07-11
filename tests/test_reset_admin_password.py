from datetime import UTC, datetime
from pathlib import Path

from media_pilot.accounts.passwords import hash_password, verify_password
from media_pilot.accounts.session_service import SessionService
from media_pilot.deployment.reset_admin_password import reset_initial_admin_password
from media_pilot.repository.account_repositories import UserRepository
from tests.test_api_v1 import _make_session_factory


def test_reset_initial_admin_password_revokes_sessions(tmp_path: Path) -> None:
    session_factory = _make_session_factory(tmp_path)
    with session_factory() as session:
        admin = UserRepository(session).create_initial_admin(
            username="Owner",
            password_hash=hash_password("old password"),
        )
        now = datetime.now(UTC)
        token, _ = SessionService().create(session, user_id=admin.id, now=now)
        session.commit()

    username = reset_initial_admin_password(session_factory, "new password")

    assert username == "Owner"
    with session_factory() as session:
        admin = UserRepository(session).get_by_username("owner")
        assert admin is not None
        assert verify_password(admin.password_hash, "new password")
        assert SessionService().authenticate(session, token=token, now=now) is None
