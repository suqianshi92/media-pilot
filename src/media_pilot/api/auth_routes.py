from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from media_pilot.accounts.cookies import (
    clear_session_cookie,
    refresh_csrf_cookie,
    request_is_secure,
    set_session_cookie,
)
from media_pilot.accounts.errors import AlreadyInitializedError, InvalidUsernameError
from media_pilot.accounts.passwords import hash_password, verify_password
from media_pilot.accounts.session_service import SessionService
from media_pilot.api.auth_dependencies import CurrentAuthDep, SessionFactoryDep
from media_pilot.api.schemas import ApiEnvelope
from media_pilot.repository.account_repositories import UserRepository, normalize_username
from media_pilot.repository.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_DUMMY_PASSWORD_HASH = hash_password("media-pilot-dummy-password")


class CredentialsBody(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        try:
            normalize_username(value)
        except InvalidUsernameError as exc:
            raise ValueError(str(exc)) from exc
        return value


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


def _user_data(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "can_access_adult": user.can_access_adult,
        "is_enabled": user.is_enabled,
    }


@router.get("/status")
def get_auth_status(session_factory: SessionFactoryDep) -> ApiEnvelope[dict]:
    with session_factory() as session:
        initialized = UserRepository(session).has_users()
    return ApiEnvelope(status="success", data={"initialized": initialized})


@router.post("/initialize")
def initialize_account_system(
    body: CredentialsBody,
    request: Request,
    response: Response,
    session_factory: SessionFactoryDep,
) -> ApiEnvelope[dict]:
    with session_factory() as session:
        if UserRepository(session).has_users():
            raise HTTPException(status_code=409, detail="Already initialized")

    password_hash = hash_password(body.password)
    now = datetime.now(UTC)
    with session_factory() as session:
        try:
            admin = UserRepository(session).create_initial_admin(
                username=body.username,
                password_hash=password_hash,
            )
            token, _ = SessionService().create(session, user_id=admin.id, now=now)
            session.commit()
        except (AlreadyInitializedError, IntegrityError) as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Already initialized") from exc

    set_session_cookie(response, token, secure=request_is_secure(request))
    refresh_csrf_cookie(response, request)
    return ApiEnvelope(status="success", data={"user": _user_data(admin)})


@router.post("/login")
def login(
    body: CredentialsBody,
    request: Request,
    response: Response,
    session_factory: SessionFactoryDep,
) -> ApiEnvelope[dict]:
    now = datetime.now(UTC)
    with session_factory() as session:
        user = UserRepository(session).get_by_username(body.username)
        password_hash = (
            user.password_hash
            if user is not None and user.is_enabled
            else _DUMMY_PASSWORD_HASH
        )
        password_matches = verify_password(password_hash, body.password)
        if user is None or not user.is_enabled or not password_matches:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        token, _ = SessionService().create(session, user_id=user.id, now=now)
        session.commit()

    set_session_cookie(response, token, secure=request_is_secure(request))
    refresh_csrf_cookie(response, request)
    return ApiEnvelope(status="success", data={"user": _user_data(user)})


@router.get("/me")
def get_current_user(auth: CurrentAuthDep) -> ApiEnvelope[dict]:
    return ApiEnvelope(status="success", data={"user": _user_data(auth.user)})


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    auth: CurrentAuthDep,
) -> ApiEnvelope[dict]:
    SessionService().revoke_current(
        auth.session,
        token=auth.token,
        now=datetime.now(UTC),
    )
    auth.session.commit()
    clear_session_cookie(response, secure=request_is_secure(request))
    return ApiEnvelope(status="success", data={})


@router.post("/change-password")
def change_password(
    body: ChangePasswordBody,
    auth: CurrentAuthDep,
) -> ApiEnvelope[dict]:
    if not verify_password(auth.user.password_hash, body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    UserRepository(auth.session).set_password(
        auth.user,
        hash_password(body.new_password),
    )
    auth.session.commit()
    return ApiEnvelope(status="success", data={})
