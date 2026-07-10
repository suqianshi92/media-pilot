from fastapi import Request, Response

from media_pilot.accounts.session_service import SESSION_LIFETIME

SESSION_COOKIE_NAME = "media_pilot_session"
CSRF_COOKIE_NAME = "media_pilot_csrf"
COOKIE_MAX_AGE = int(SESSION_LIFETIME.total_seconds())


def request_is_secure(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    original_scheme = forwarded_proto.split(",", 1)[0].strip().lower()
    return original_scheme == "https" or request.url.scheme == "https"


def set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, *, secure: bool) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def set_csrf_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def refresh_csrf_cookie(response: Response, request: Request) -> None:
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if token:
        set_csrf_cookie(response, token, secure=request_is_secure(request))
