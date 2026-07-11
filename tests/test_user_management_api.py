from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_auth_api import csrf_headers, initialize, make_client


def _login(app, username: str, password: str) -> TestClient:
    client = TestClient(app)
    client.get("/api/v1/auth/status")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    return client


def test_admin_manages_users_with_server_side_pagination(tmp_path: Path) -> None:
    admin = make_client(tmp_path)
    initialize(admin)

    created = admin.post(
        "/api/v1/users",
        json={
            "username": "Alice",
            "password": "alice password",
            "can_access_adult": True,
        },
        headers=csrf_headers(admin),
    )
    assert created.status_code == 200
    alice = created.json()["data"]["user"]
    assert alice["role"] == "user"
    assert alice["is_enabled"] is True
    assert alice["can_access_adult"] is True

    listing = admin.get("/api/v1/users?page=1&page_size=1")
    assert listing.status_code == 200
    assert listing.json()["meta"] == {"page": 1, "page_size": 1, "total": 2}
    assert listing.json()["data"]["items"][0]["role"] == "admin"

    updated = admin.patch(
        f"/api/v1/users/{alice['id']}",
        json={"is_enabled": False, "can_access_adult": False},
        headers=csrf_headers(admin),
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["user"]["is_enabled"] is False

    protected = admin.patch(
        f"/api/v1/users/{listing.json()['data']['items'][0]['id']}",
        json={"is_enabled": False},
        headers=csrf_headers(admin),
    )
    assert protected.status_code == 409


def test_user_management_is_admin_only_and_revokes_sessions_on_reset(
    tmp_path: Path,
) -> None:
    admin = make_client(tmp_path)
    initialize(admin)
    alice = admin.post(
        "/api/v1/users",
        json={"username": "Alice", "password": "alice password"},
        headers=csrf_headers(admin),
    ).json()["data"]["user"]
    user = _login(admin.app, "alice", "alice password")
    assert user.get("/api/v1/users").status_code == 403

    invalid = admin.post(
        f"/api/v1/users/{alice['id']}/reset-password",
        json={"password": "short"},
        headers=csrf_headers(admin),
    )
    assert invalid.status_code == 422

    reset = admin.post(
        f"/api/v1/users/{alice['id']}/reset-password",
        json={"password": "replacement password"},
        headers=csrf_headers(admin),
    )
    assert reset.status_code == 200
    assert user.get("/api/v1/auth/me").status_code == 401
    assert _login(admin.app, "alice", "replacement password")


def test_disabling_user_immediately_revokes_existing_session(tmp_path: Path) -> None:
    admin = make_client(tmp_path)
    initialize(admin)
    alice = admin.post(
        "/api/v1/users",
        json={"username": "Alice", "password": "alice password"},
        headers=csrf_headers(admin),
    ).json()["data"]["user"]
    user = _login(admin.app, "alice", "alice password")

    response = admin.patch(
        f"/api/v1/users/{alice['id']}",
        json={"is_enabled": False},
        headers=csrf_headers(admin),
    )

    assert response.status_code == 200
    assert user.get("/api/v1/auth/me").status_code == 401


def test_user_changes_password_and_all_sessions_are_revoked(tmp_path: Path) -> None:
    admin = make_client(tmp_path)
    initialize(admin)
    admin.post(
        "/api/v1/users",
        json={"username": "Alice", "password": "alice password"},
        headers=csrf_headers(admin),
    )
    first = _login(admin.app, "alice", "alice password")
    second = _login(admin.app, "alice", "alice password")

    changed = first.post(
        "/api/v1/auth/change-password",
        json={"current_password": "alice password", "new_password": "new password"},
        headers=csrf_headers(first),
    )
    assert changed.status_code == 200
    assert first.get("/api/v1/auth/me").status_code == 401
    assert second.get("/api/v1/auth/me").status_code == 401
    assert _login(admin.app, "alice", "new password")
