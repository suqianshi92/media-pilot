from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.accounts.cookies import (
    SESSION_COOKIE_NAME,
    refresh_csrf_cookie,
    request_is_secure,
    set_session_cookie,
)
from media_pilot.accounts.session_service import SessionService
from media_pilot.repository.models import AccountSession, User


def get_session_factory(request: Request) -> sessionmaker[Session]:
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        raise HTTPException(status_code=503, detail="Database is not configured")
    return session_factory


SessionFactoryDep = Annotated[sessionmaker[Session], Depends(get_session_factory)]


@dataclass(frozen=True)
class AuthContext:
    session: Session
    user: User
    account_session: AccountSession
    token: str


def get_current_auth(
    request: Request,
    response: Response,
    session_factory: SessionFactoryDep,
) -> Generator[AuthContext, None, None]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")

    session = session_factory()
    try:
        authenticated = SessionService().authenticate(
            session,
            token=token,
            now=datetime.now(UTC),
        )
        if authenticated is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if authenticated.renewed:
            session.commit()
            set_session_cookie(
                response,
                token,
                secure=request_is_secure(request),
            )
            refresh_csrf_cookie(response, request)
        yield AuthContext(
            session=session,
            user=authenticated.user,
            account_session=authenticated.account_session,
            token=token,
        )
    finally:
        session.close()


CurrentAuthDep = Annotated[AuthContext, Depends(get_current_auth)]
