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
from media_pilot.repository.models import AccountSession, AgentDecisionRequest, User
from media_pilot.repository.repositories import (
    DownloadTaskRepository,
    IngestTaskRepository,
)


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


def require_adult_access(auth: AuthContext) -> None:
    if auth.user.role != "admin" and not auth.user.can_access_adult:
        raise HTTPException(status_code=403, detail="Adult content access required")


def get_task_access_scope(auth: CurrentAuthDep) -> TaskAccessScope:
    return TaskAccessScope(
        user_id=auth.user.id,
        can_view_all_tasks=auth.user.role == "admin",
        can_access_adult=auth.user.role == "admin" or auth.user.can_access_adult,
    )


TaskAccessDep = Annotated[TaskAccessScope, Depends(get_task_access_scope)]


def build_stream_authorizer(
    session_factory: sessionmaker[Session],
    *,
    token: str,
    task_id: str | None = None,
    require_adult_access: bool = False,
):
    """构造流式连接使用的短会话复核函数；每次调用独立获取连接。"""

    def authorize() -> bool:
        with session_factory() as session:
            authenticated = SessionService().authenticate(
                session,
                token=token,
                now=datetime.now(UTC),
            )
            if authenticated is None:
                return False
            if authenticated.renewed:
                session.commit()
            user = authenticated.user
            can_access_adult = user.role == "admin" or user.can_access_adult
            if require_adult_access and not can_access_adult:
                return False
            if task_id is None:
                return True
            access_scope = TaskAccessScope(
                user_id=user.id,
                can_view_all_tasks=user.role == "admin",
                can_access_adult=can_access_adult,
            )
            return (
                IngestTaskRepository(session).get_authorized(task_id, access_scope)
                is not None
            )

    return authorize


def require_authorized_ingest_task(
    task_id: str,
    session_factory: SessionFactoryDep,
    access_scope: TaskAccessDep,
) -> None:
    with session_factory() as session:
        if IngestTaskRepository(session).get_authorized(task_id, access_scope) is None:
            raise HTTPException(status_code=404, detail="Task not found")


def require_authorized_download_task(
    download_id: str,
    session_factory: SessionFactoryDep,
    access_scope: TaskAccessDep,
) -> None:
    with session_factory() as session:
        if (
            DownloadTaskRepository(session).get_authorized(
                download_id,
                access_scope,
            )
            is None
        ):
            raise HTTPException(status_code=404, detail="Download task not found")


def require_authorized_agent_decision(
    decision_id: str,
    session_factory: SessionFactoryDep,
    access_scope: TaskAccessDep,
) -> None:
    with session_factory() as session:
        decision = session.get(AgentDecisionRequest, decision_id)
        if decision is None or IngestTaskRepository(session).get_authorized(
            decision.task_id,
            access_scope,
        ) is None:
            raise HTTPException(status_code=404, detail="Decision not found")
