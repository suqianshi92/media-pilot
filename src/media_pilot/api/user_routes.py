from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from media_pilot.accounts.errors import InvalidUsernameError, ProtectedAdminError
from media_pilot.accounts.passwords import hash_password
from media_pilot.api.auth_dependencies import CurrentAdminDep
from media_pilot.api.auth_routes import CredentialsBody
from media_pilot.api.schemas import ApiEnvelope
from media_pilot.repository.account_repositories import UserRepository
from media_pilot.repository.models import User

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class CreateUserBody(CredentialsBody):
    can_access_adult: bool = False


class UpdateUserBody(BaseModel):
    is_enabled: bool | None = None
    can_access_adult: bool | None = None


class PasswordBody(BaseModel):
    password: str = Field(min_length=8, max_length=128)


def _user_data(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "can_access_adult": user.can_access_adult,
        "is_enabled": user.is_enabled,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


@router.get("")
def list_users(
    auth: CurrentAdminDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> ApiEnvelope[dict]:
    users, total = UserRepository(auth.session).list_page(
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return ApiEnvelope(
        status="success",
        data={"items": [_user_data(user) for user in users]},
        meta={"page": page, "page_size": page_size, "total": total},
    )


@router.post("")
def create_user(body: CreateUserBody, auth: CurrentAdminDep) -> ApiEnvelope[dict]:
    try:
        user = UserRepository(auth.session).create_user(
            username=body.username,
            password_hash=hash_password(body.password),
            can_access_adult=body.can_access_adult,
        )
        auth.session.commit()
    except (IntegrityError, InvalidUsernameError) as exc:
        auth.session.rollback()
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    return ApiEnvelope(status="success", data={"user": _user_data(user)})


@router.patch("/{user_id}")
def update_user(
    user_id: str,
    body: UpdateUserBody,
    auth: CurrentAdminDep,
) -> ApiEnvelope[dict]:
    repository = UserRepository(auth.session)
    user = repository.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        if body.is_enabled is not None:
            repository.set_enabled(user, body.is_enabled)
        if body.can_access_adult is not None:
            repository.set_adult_access(user, body.can_access_adult)
        auth.session.commit()
    except ProtectedAdminError as exc:
        auth.session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ApiEnvelope(status="success", data={"user": _user_data(user)})


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: str,
    body: PasswordBody,
    auth: CurrentAdminDep,
) -> ApiEnvelope[dict]:
    repository = UserRepository(auth.session)
    user = repository.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    repository.set_password(user, hash_password(body.password))
    auth.session.commit()
    return ApiEnvelope(status="success", data={})
