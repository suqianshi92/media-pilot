"""内容发现 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from media_pilot.accounts.stream_authorization import (
    stream_with_periodic_authorization,
)
from media_pilot.api.auth_dependencies import CurrentAuthDep, build_stream_authorizer
from media_pilot.config import AppConfig
from media_pilot.services.content_discovery import (
    ContentDiscoveryInputError,
    ContentDiscoveryMessage,
    build_content_discovery_messages,
    format_sse,
    stream_content_discovery,
)

router = APIRouter(prefix="/api/v1/content-discovery")


class ContentDiscoveryMessageBody(BaseModel):
    role: str = Field(..., description="user / assistant")
    content: str = Field(..., min_length=1, description="消息内容")


class ContentDiscoveryStreamBody(BaseModel):
    messages: list[ContentDiscoveryMessageBody] = Field(..., min_length=1)


@router.post("/stream")
def stream(
    body: ContentDiscoveryStreamBody,
    request: Request,
    auth: CurrentAuthDep,
) -> StreamingResponse:
    config: AppConfig | None = getattr(request.app.state, "config", None)
    session_factory = getattr(request.app.state, "session_factory", None)
    if config is None or session_factory is None:
        return _single_error_stream("未配置服务")

    messages = [
        ContentDiscoveryMessage(role=message.role, content=message.content)
        for message in body.messages
    ]
    try:
        chat_messages = build_content_discovery_messages(
            messages,
            can_access_adult=auth.user.can_access_adult,
        )
    except ContentDiscoveryInputError as exc:
        return _single_error_stream(str(exc))

    source = stream_content_discovery(config=config, chat_messages=chat_messages)
    authorized_source = stream_with_periodic_authorization(
        source,
        authorize=build_stream_authorizer(
            session_factory,
            token=auth.token,
            require_adult_access=auth.user.can_access_adult,
        ),
        authorization_error=format_sse(
            "error",
            {"message": "authorization_revoked"},
        ),
    )
    return StreamingResponse(
        authorized_source,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _single_error_stream(message: str) -> StreamingResponse:
    return StreamingResponse(
        iter([format_sse("error", {"message": message})]),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
