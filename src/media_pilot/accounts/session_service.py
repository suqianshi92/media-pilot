from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy.orm import Session

from media_pilot.repository.account_repositories import AccountSessionRepository
from media_pilot.repository.models import AccountSession, User

SESSION_LIFETIME = timedelta(days=30)
SESSION_RENEWAL_INTERVAL = timedelta(hours=24)


@dataclass(frozen=True)
class AuthenticatedSession:
    user: User
    account_session: AccountSession
    renewed: bool


def hash_session_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SessionService:
    def create(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime,
    ) -> tuple[str, AccountSession]:
        token = token_urlsafe(32)
        account_session = AccountSessionRepository(session).create(
            user_id=user_id,
            token_hash=hash_session_token(token),
            now=now,
            expires_at=now + SESSION_LIFETIME,
        )
        return token, account_session

    def authenticate(
        self,
        session: Session,
        *,
        token: str,
        now: datetime,
    ) -> AuthenticatedSession | None:
        repository = AccountSessionRepository(session)
        account_session = repository.get_active_by_token_hash(
            hash_session_token(token),
            now=now,
        )
        if account_session is None:
            return None

        user = session.get(User, account_session.user_id)
        if user is None or not user.is_enabled:
            return None

        renewal_due = (
            now - _as_utc(account_session.last_active_at)
            >= SESSION_RENEWAL_INTERVAL
        )
        renewed = renewal_due and repository.renew_if_due(
            account_session,
            now=now,
            renewal_interval=SESSION_RENEWAL_INTERVAL,
            expires_at=now + SESSION_LIFETIME,
        )

        return AuthenticatedSession(
            user=user,
            account_session=account_session,
            renewed=renewed,
        )

    def revoke_current(
        self,
        session: Session,
        *,
        token: str,
        now: datetime,
    ) -> bool:
        repository = AccountSessionRepository(session)
        account_session = repository.get_by_token_hash(hash_session_token(token))
        if account_session is None or account_session.revoked_at is not None:
            return False
        account_session.revoked_at = now
        return True
