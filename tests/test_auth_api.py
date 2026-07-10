from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

import media_pilot.api.auth_routes as auth_routes
from media_pilot.app import create_app
from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import AccountSession

SESSION_COOKIE = "media_pilot_session"
CSRF_COOKIE = "media_pilot_csrf"


def make_client(tmp_path: Path, *, base_url: str = "http://testserver") -> TestClient:
    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path / "db",
    )
    initialize_database(config)
    return TestClient(
        create_app(config=config, session_factory=create_session_factory(config)),
        base_url=base_url,
    )


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[CSRF_COOKIE]}


def initialize(client: TestClient):
    client.get("/api/v1/auth/status")
    response = client.post(
        "/api/v1/auth/initialize",
        json={"username": "Owner", "password": "owner password"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    return response


def test_auth_status_issues_csrf_cookie_and_reports_initialization(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    before = client.get("/api/v1/auth/status")
    assert before.status_code == 200
    assert before.json()["data"] == {"initialized": False}
    assert client.cookies[CSRF_COOKIE]
    csrf_set_cookie = next(
        value
        for value in before.headers.get_list("set-cookie")
        if value.startswith(f"{CSRF_COOKIE}=")
    )
    assert "HttpOnly" not in csrf_set_cookie
    assert "SameSite=lax" in csrf_set_cookie
    assert "Path=/" in csrf_set_cookie

    initialize(client)
    after = client.get("/api/v1/auth/status")
    assert after.json()["data"] == {"initialized": True}


def test_initialize_creates_admin_and_authenticated_session(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    initialize_response = initialize(client)
    user = initialize_response.json()["data"]["user"]

    assert user["username"] == "Owner"
    assert user["role"] == "admin"
    assert user["can_access_adult"] is True
    assert user["is_enabled"] is True
    assert client.cookies[SESSION_COOKIE]
    session_set_cookie = next(
        value
        for value in initialize_response.headers.get_list("set-cookie")
        if value.startswith(f"{SESSION_COOKIE}=")
    )
    assert "HttpOnly" in session_set_cookie
    assert "SameSite=lax" in session_set_cookie
    assert "Path=/" in session_set_cookie
    assert "Max-Age=2592000" in session_set_cookie
    assert "Secure" not in session_set_cookie
    assert "Domain=" not in session_set_cookie
    assert any(
        value.startswith(f"{CSRF_COOKIE}=")
        for value in initialize_response.headers.get_list("set-cookie")
    )

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["user"]["username"] == "Owner"

    client.get("/api/v1/auth/status")
    duplicate = client.post(
        "/api/v1/auth/initialize",
        json={"username": "Other", "password": "other password"},
        headers=csrf_headers(client),
    )
    assert duplicate.status_code == 409


def test_login_and_logout_use_current_database_session(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    initialize(client)
    client.post("/api/v1/auth/logout", headers=csrf_headers(client))

    assert client.get("/api/v1/auth/me").status_code == 401

    wrong = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "wrong password"},
        headers=csrf_headers(client),
    )
    assert wrong.status_code == 401

    logged_in = client.post(
        "/api/v1/auth/login",
        json={"username": "OWNER", "password": "owner password"},
        headers=csrf_headers(client),
    )
    assert logged_in.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200

    logged_out = client.post("/api/v1/auth/logout", headers=csrf_headers(client))
    assert logged_out.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_verifies_dummy_hash_when_username_does_not_exist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = make_client(tmp_path)
    client.get("/api/v1/auth/status")
    verified_hashes: list[str] = []

    def fake_verify(password_hash: str, _password: str) -> bool:
        verified_hashes.append(password_hash)
        return False

    monkeypatch.setattr(auth_routes, "verify_password", fake_verify)

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "missing", "password": "wrong password"},
        headers=csrf_headers(client),
    )

    assert response.status_code == 401
    assert len(verified_hashes) == 1
    assert verified_hashes[0].startswith("$argon2id$")

def test_logout_revokes_only_current_device_and_requires_csrf(tmp_path: Path) -> None:
    first = make_client(tmp_path)
    initialize(first)

    second = make_client(tmp_path)
    second.get("/api/v1/auth/status")
    login_response = second.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "owner password"},
        headers=csrf_headers(second),
    )
    assert login_response.status_code == 200

    missing_csrf = first.post("/api/v1/auth/logout")
    assert missing_csrf.status_code == 403
    assert first.get("/api/v1/auth/me").status_code == 200

    assert first.post(
        "/api/v1/auth/logout",
        headers=csrf_headers(first),
    ).status_code == 200
    assert first.get("/api/v1/auth/me").status_code == 401
    assert second.get("/api/v1/auth/me").status_code == 200

def test_auth_mutations_require_matching_csrf_cookie_and_header(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.get("/api/v1/auth/status")

    missing = client.post(
        "/api/v1/auth/initialize",
        json={"username": "Owner", "password": "owner password"},
    )
    assert missing.status_code == 403

    mismatched = client.post(
        "/api/v1/auth/initialize",
        json={"username": "Owner", "password": "owner password"},
        headers={"X-CSRF-Token": "wrong-token"},
    )
    assert mismatched.status_code == 403


def test_initialize_rejects_username_that_expands_past_database_limit(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    client.get("/api/v1/auth/status")

    response = client.post(
        "/api/v1/auth/initialize",
        json={"username": "ß" * 128, "password": "owner password"},
        headers=csrf_headers(client),
    )

    assert response.status_code == 422


def test_initialize_rejects_username_with_nul_character(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.get("/api/v1/auth/status")

    response = client.post(
        "/api/v1/auth/initialize",
        json={"username": "owner\u0000name", "password": "owner password"},
        headers=csrf_headers(client),
    )

    assert response.status_code == 422


def test_session_cookie_attributes_follow_original_https_scheme(tmp_path: Path) -> None:
    client = make_client(tmp_path, base_url="https://testserver")
    client.get("/api/v1/auth/status", headers={"X-Forwarded-Proto": "https"})

    response = client.post(
        "/api/v1/auth/initialize",
        json={"username": "Owner", "password": "owner password"},
        headers={**csrf_headers(client), "X-Forwarded-Proto": "https"},
    )

    session_set_cookie = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(f"{SESSION_COOKIE}=")
    )
    assert "HttpOnly" in session_set_cookie
    assert "SameSite=lax" in session_set_cookie
    assert "Path=/" in session_set_cookie
    assert "Secure" in session_set_cookie
    assert "Domain=" not in session_set_cookie


def test_sliding_session_refreshes_session_and_csrf_cookies(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    initialize(client)
    now = datetime.now(UTC)
    with client.app.state.session_factory() as session:
        account_session = session.scalars(select(AccountSession)).one()
        account_session.last_active_at = now - timedelta(hours=25)
        account_session.expires_at = now + timedelta(days=29)
        session.commit()

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    set_cookies = response.headers.get_list("set-cookie")
    assert any(value.startswith(f"{SESSION_COOKIE}=") for value in set_cookies)
    assert any(value.startswith(f"{CSRF_COOKIE}=") for value in set_cookies)
