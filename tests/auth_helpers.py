from fastapi import FastAPI
from fastapi.testclient import TestClient

from media_pilot.repository.database import create_session_factory, initialize_database

_USERNAME = "test-admin"
_PASSWORD = "test-admin-password"
_CSRF_COOKIE = "media_pilot_csrf"


class AuthenticatedTestClient(TestClient):
    """通过公开认证 API 建立管理员会话的业务接口测试客户端。"""

    def __init__(self, app: FastAPI, **kwargs) -> None:
        session_factory = getattr(app.state, "session_factory", None)
        config = getattr(app.state, "config", None)
        if session_factory is None and config is not None:
            initialize_database(config)
            session_factory = create_session_factory(config)
            app.state.session_factory = session_factory

        super().__init__(app, **kwargs)
        if session_factory is None:
            return

        status = self.get("/api/v1/auth/status")
        assert status.status_code == 200
        headers = {"X-CSRF-Token": self.cookies[_CSRF_COOKIE]}
        if status.json()["data"]["initialized"]:
            auth_response = self.post(
                "/api/v1/auth/login",
                json={"username": _USERNAME, "password": _PASSWORD},
                headers=headers,
            )
        else:
            auth_response = self.post(
                "/api/v1/auth/initialize",
                json={"username": _USERNAME, "password": _PASSWORD},
                headers=headers,
            )
        assert auth_response.status_code == 200
        self.headers["X-CSRF-Token"] = self.cookies[_CSRF_COOKIE]
