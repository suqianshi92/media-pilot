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
from media_pilot.accounts.task_access import TaskAccessScope
from media_pilot.repository.account_repositories import UserRepository
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
    session = session_factory()
    try:
        if not UserRepository(session).has_users():
            raise HTTPException(status_code=503, detail="Initialization required")
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            raise HTTPException(status_code=401, detail="Authentication required")
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


CurrentAuthDep = Annotated[
    AuthContext,
    Depends(get_current_auth, scope="function"),
]


def get_current_admin(auth: CurrentAuthDep) -> AuthContext:
    if auth.user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return auth


CurrentAdminDep = Annotated[AuthContext, Depends(get_current_admin)]


def get_task_access_scope(auth: CurrentAuthDep) -> TaskAccessScope:
    return TaskAccessScope(
        user_id=auth.user.id,
        can_view_all_tasks=auth.user.role == "admin",
        can_access_adult=auth.user.role == "admin" or auth.user.can_access_adult,
    )


TaskAccessDep = Annotated[TaskAccessScope, Depends(get_task_access_scope)]
