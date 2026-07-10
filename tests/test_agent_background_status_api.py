"""GET /api/v1/agent-background/status 路由测试.

覆盖 tasks 3.1 ~ 3.4:
- 3.1 endpoint 存在, 返回统一 envelope
- 3.2 enabled / disabled / needs_attention / recently_failed / history 分桶
- 3.3 不提供手动运行接口
- 3.4 history 截断到 10 条 + 敏感字段被脱敏
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.auth_helpers import AuthenticatedTestClient as TestClient

from media_pilot.app import create_app
from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
from media_pilot.services.agent_background_status import (
    BackgroundState,
    BackgroundStatusService,
    HistoryLevel,
    reset_default_background_status_service,
    set_default_background_status_service,
)


def _config(tmp_path: Path, *, with_llm: bool = True) -> AppConfig:
    cfg = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    for d in (
        cfg.downloads_dir, cfg.watch_dir, cfg.workspace_dir,
        cfg.movies_dir, cfg.shows_dir, cfg.database_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    if with_llm:
        cfg = AppConfig(
            **{
                **cfg.__dict__,
                "llm_api_key": "test-key",
                "llm_base_url": "https://test.example.com/v1",
                "llm_model": "test-model",
            },
        )
    return cfg


def _build_app(tmp_path: Path, *, with_llm: bool = True):
    cfg = _config(tmp_path, with_llm=with_llm)
    initialize_database(cfg)
    sf = create_session_factory(cfg)
    return create_app(config=cfg, session_factory=sf), sf


@pytest.fixture(autouse=True)
def _reset_status():
    reset_default_background_status_service()
    yield
    reset_default_background_status_service()


# ---- 3.1 endpoint 存在 + envelope 形状 ----


def test_endpoint_returns_unified_envelope(tmp_path) -> None:
    app, _ = _build_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/v1/agent-background/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    data = body["data"]
    for key in (
        "enabled", "state", "summary", "disabled_reasons",
        "waiting_user_count", "agent_failed_count",
        "last_run", "history", "current_task_id", "current_download_id",
    ):
        assert key in data, f"missing field: {key}"


# ---- 3.2 / 3.4 状态分桶 ----


def test_disabled_state_includes_reasons(tmp_path) -> None:
    """worker 禁用时, state=disabled 且 disabled_reasons 非空."""

    app, _ = _build_app(tmp_path, with_llm=False)
    client = TestClient(app)
    resp = client.get("/api/v1/agent-background/status")
    data = resp.json()["data"]
    assert data["state"] == BackgroundState.DISABLED.value
    assert data["enabled"] is False
    # 至少含 1 条 validate_startup_config 给出的原因
    assert len(data["disabled_reasons"]) >= 1
    assert all(isinstance(r, str) for r in data["disabled_reasons"])


def test_needs_attention_state_reported(tmp_path) -> None:
    app, sf = _build_app(tmp_path)
    with sf() as session:
        IngestTaskRepository(session).create(IngestTaskCreate(
            source_path="/media/watch/a.mkv",
            status="waiting_user", current_step="manual_selection_blocked",
        ))
        session.commit()
    client = TestClient(app)
    resp = client.get("/api/v1/agent-background/status")
    data = resp.json()["data"]
    assert data["state"] == BackgroundState.NEEDS_ATTENTION.value
    assert data["waiting_user_count"] == 1


def test_recently_failed_state_reported(tmp_path) -> None:
    app, sf = _build_app(tmp_path)
    with sf() as session:
        IngestTaskRepository(session).create(IngestTaskCreate(
            source_path="/media/watch/b.mkv",
            status="agent_failed", current_step="agent_failure",
        ))
        session.commit()
    client = TestClient(app)
    resp = client.get("/api/v1/agent-background/status")
    data = resp.json()["data"]
    assert data["state"] == BackgroundState.RECENTLY_FAILED.value
    assert data["agent_failed_count"] == 1


def test_history_truncated_to_ten_entries(tmp_path) -> None:
    app, _ = _build_app(tmp_path)
    status = get_status_singleton()
    for i in range(15):
        status.record_event(
            phase="processing_task",
            level=HistoryLevel.INFO,
            summary=f"event-{i}",
        )
    client = TestClient(app)
    resp = client.get("/api/v1/agent-background/status")
    history = resp.json()["data"]["history"]
    assert len(history) == 10
    assert history[-1]["summary"] == "event-14"


def test_history_redacts_sensitive_tokens(tmp_path) -> None:
    app, _ = _build_app(tmp_path)
    status = get_status_singleton()
    secret = "sk-abcdefghijklmnopqrstuvwx"  # 26 chars
    status.record_event(
        phase="processing_task",
        level=HistoryLevel.ERROR,
        summary=f"调用外部 API 失败: {secret}",
    )
    client = TestClient(app)
    resp = client.get("/api/v1/agent-background/status")
    history = resp.json()["data"]["history"]
    assert "[redacted]" in history[0]["summary"]
    assert secret not in history[0]["summary"]


def test_history_entry_does_not_carry_id_longer_than_8(tmp_path) -> None:
    app, _ = _build_app(tmp_path)
    status = get_status_singleton()
    full_id = "abcdef00-1111-2222-3333-444455556666"
    status.record_event(
        phase="processing_task",
        level=HistoryLevel.INFO,
        summary="ok",
        task_id=full_id,
    )
    client = TestClient(app)
    resp = client.get("/api/v1/agent-background/status")
    entry = resp.json()["data"]["history"][0]
    assert entry["task_id"] == "abcdef00"
    assert full_id not in entry["task_id"]


# ---- 3.3 不暴露写接口 ----


def test_no_write_endpoint_exposed(tmp_path) -> None:
    """不应提供"立即跑一轮"或重试 / 暂停后台线程的写接口."""

    app, _ = _build_app(tmp_path)
    client = TestClient(app)
    # GET 状态可用
    assert client.get("/api/v1/agent-background/status").status_code == 200
    # 任何 POST/PUT/PATCH 都不应被接受
    for method in ("post", "put", "patch"):
        response = getattr(client, method)(
            "/api/v1/agent-background/status", json={},
        )
        assert response.status_code in (404, 405), (
            f"agent-background/status 暴露了 {method.upper()} 写接口"
        )
    # DELETE 用专用方法
    response = client.delete("/api/v1/agent-background/status")
    assert response.status_code in (404, 405)
    # 其它写路径也不应存在
    for path in (
        "/api/v1/agent-background/run",
        "/api/v1/agent-background/retry",
        "/api/v1/agent-background/start",
        "/api/v1/agent-background/stop",
    ):
        response = client.post(path, json={})
        assert response.status_code == 404, (
            f"意外暴露控制端点: {path}"
        )


# ---- 错误边界 ----


def test_endpoint_requires_database_backed_admin_session(tmp_path) -> None:
    """后台诊断不在无数据库时降级为匿名公开接口。"""

    app = create_app(config=None, session_factory=None, enable_background_processor=False)
    client = TestClient(app)
    resp = client.get("/api/v1/agent-background/status")
    assert resp.status_code == 503


# ---- helpers ----


def get_status_singleton() -> BackgroundStatusService:
    from media_pilot.services.agent_background_status import (
        get_default_background_status_service,
    )
    return get_default_background_status_service()
