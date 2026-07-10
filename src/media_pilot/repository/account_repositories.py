from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from media_pilot.accounts.errors import (
    AlreadyInitializedError,
    InvalidUsernameError,
    ProtectedAdminError,
)
from media_pilot.repository.models import AccountSession, User


def normalize_username(username: str) -> str:
    if not 1 <= len(username) <= 128 or "\x00" in username:
        raise InvalidUsernameError("username must contain 1 to 128 valid characters")
    normalized = username.casefold()
    if len(normalized) > 128:
        raise InvalidUsernameError("normalized username exceeds 128 characters")
    return normalized


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def has_users(self) -> bool:
        statement = select(func.count()).select_from(User)
        return bool(self._session.execute(statement).scalar_one())

    def get(self, user_id: str) -> User | None:
        return self._session.get(User, user_id)

    def get_by_username(self, username: str) -> User | None:
        statement = select(User).where(
            User.normalized_username == normalize_username(username)
        )
        return self._session.scalars(statement).first()

    def create_initial_admin(self, *, username: str, password_hash: str) -> User:
        if self.has_users():
            raise AlreadyInitializedError("account system is already initialized")
        user = User(
            username=username,
            normalized_username=normalize_username(username),
            password_hash=password_hash,
            role="admin",
            can_access_adult=True,
            is_enabled=True,
        )
        self._session.add(user)
        self._session.flush()
        return user

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        can_access_adult: bool = False,
    ) -> User:
        user = User(
            username=username,
            normalized_username=normalize_username(username),
            password_hash=password_hash,
            role="user",
            can_access_adult=can_access_adult,
            is_enabled=True,
        )
        self._session.add(user)
        self._session.flush()
        return user

    def set_enabled(self, user: User, is_enabled: bool) -> User:
        if user.role == "admin" and not is_enabled:
            raise ProtectedAdminError("initial admin cannot be disabled")
        if not is_enabled:
            AccountSessionRepository(self._session).revoke_all_for_user(
                user.id,
                now=datetime.now(UTC),
            )
        user.is_enabled = is_enabled
        return user

    def set_adult_access(self, user: User, can_access_adult: bool) -> User:
        if user.role == "admin" and not can_access_adult:
            raise ProtectedAdminError("initial admin must retain adult access")
        user.can_access_adult = can_access_adult
        return user


class AccountSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        user_id: str,
        token_hash: str,
        now: datetime,
        expires_at: datetime,
    ) -> AccountSession:
        account_session = AccountSession(
            user_id=user_id,
            token_hash=token_hash,
            last_active_at=now,
            expires_at=expires_at,
        )
        self._session.add(account_session)
        self._session.flush()
        return account_session

    def get_active_by_token_hash(
        self,
        token_hash: str,
        *,
        now: datetime,
    ) -> AccountSession | None:
        statement = select(AccountSession).where(
            AccountSession.token_hash == token_hash,
            AccountSession.revoked_at.is_(None),
            AccountSession.expires_at > now,
        )
        return self._session.scalars(statement).first()

    def get_by_token_hash(self, token_hash: str) -> AccountSession | None:
        statement = select(AccountSession).where(AccountSession.token_hash == token_hash)
        return self._session.scalars(statement).first()

    def renew_if_due(
        self,
        account_session: AccountSession,
        *,
        now: datetime,
        renewal_interval: timedelta,
        expires_at: datetime,
    ) -> bool:
        statement = (
            update(AccountSession)
            .where(
                AccountSession.id == account_session.id,
                AccountSession.revoked_at.is_(None),
                AccountSession.expires_at > now,
                AccountSession.last_active_at <= now - renewal_interval,
            )
            .values(last_active_at=now, expires_at=expires_at)
            .execution_options(synchronize_session=False)
        )
        result = self._session.execute(statement)
        renewed = bool(result.rowcount)
        self._session.refresh(account_session)
        return renewed

    def revoke_all_for_user(self, user_id: str, *, now: datetime) -> int:
        statement = select(AccountSession).where(
            AccountSession.user_id == user_id,
            AccountSession.revoked_at.is_(None),
        )
        active_sessions = list(self._session.scalars(statement))
        for account_session in active_sessions:
            account_session.revoked_at = now
        return len(active_sessions)
