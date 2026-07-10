from hmac import compare_digest
from secrets import token_urlsafe

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from media_pilot.accounts.cookies import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    request_is_secure,
    set_csrf_cookie,
)

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ANONYMOUS_CSRF_PATHS = frozenset({
    "/api/v1/auth/initialize",
    "/api/v1/auth/login",
})


async def csrf_middleware(request: Request, call_next) -> Response:
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME)
    requires_csrf = request.method in _UNSAFE_METHODS and (
        request.url.path in _ANONYMOUS_CSRF_PATHS
        or SESSION_COOKIE_NAME in request.cookies
    )
    if requires_csrf:
        header_token = request.headers.get("x-csrf-token")
        if not csrf_token or not header_token or not compare_digest(
            csrf_token, header_token
        ):
            response: Response = JSONResponse(
                status_code=403,
                content={"detail": "CSRF token validation failed"},
            )
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)

    if not csrf_token and request.url.path.startswith("/api/"):
        set_csrf_cookie(
            response,
            token_urlsafe(32),
            secure=request_is_secure(request),
        )
    return response
