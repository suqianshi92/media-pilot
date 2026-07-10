"""API v1 合同测试 — 成功 / accepted / 错误 / 分页 / 字段命名"""

from pathlib import Path
from types import SimpleNamespace

from tests.auth_helpers import AuthenticatedTestClient as TestClient

from media_pilot.adapters.metadata import MetadataCandidate, MetadataProviderResponse
from media_pilot.app import create_app
from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import (
    MediaCandidate,
)
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository


def _make_stub_correct_movie_provider():
    """返回返回 'Correct Movie 2026' 候选的 stub provider"""
    return _StubCorrectMovieProvider()


class _StubCorrectMovieProvider:
    provider_name = "stub_metadata"

    def search_movie(self, keyword, *, language_priority):
        return MetadataProviderResponse(value=[
            MetadataCandidate(
                provider="stub_metadata",
                provider_id="movie:correct-movie:2026",
                title="Correct Movie",
                original_title="Correct Movie",
                year=2026,
                media_type="movie",
                overview="A corrected search result.",
                poster_url="https://example.test/posters/correct-movie.jpg",
                confidence=0.95,
                match_reason="strong_match",
            )
        ])


def _make_config(database_dir: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"),
        shows_dir=Path("/media/library/shows"),
        database_dir=database_dir,
    )


def _make_session_factory(tmp_path: Path):
    config = _make_config(tmp_path)
    initialize_database(config)
    return create_session_factory(config)


# ---- 健康检查 ----


def test_api_v1_health_returns_success() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["version"] == "v1"


# ---- 任务列表 ----


def test_api_v1_list_tasks_empty(tmp_path: Path) -> None:
    sf = _make_session_factory(tmp_path)
    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["data"]["items"] == []
    assert body["meta"]["total"] == 0
    assert body["meta"]["page"] == 1
    assert body["meta"]["page_size"] == 50


def test_api_v1_list_tasks_snake_case_fields(tmp_path: Path) -> None:
    sf = _make_session_factory(tmp_path)
    with sf() as session:
        IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/Test.Movie.2026.mkv",
                status="discovered", current_step="download_scan",
                media_type="movie", confidence=0.9,
            )
        )
        session.commit()
    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/tasks")
    body = resp.json()
    item = body["data"]["items"][0]
    for key in ("id", "source_path", "media_type", "can_confirm",
                "created_at", "updated_at", "status_summary"):
        assert key in item, f"missing snake_case field: {key}"
    ss = item["status_summary"]
    assert ss["status"] == "discovered"
    assert ss["current_step"] == "download_scan"
    assert "confidence_level" in ss
    assert "latest_message" in ss


def test_api_v1_list_tasks_filter_by_status(tmp_path: Path) -> None:
    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/Movie.mkv",
            status="completed", current_step="library_import_complete",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/Show.mkv",
            status="agent_failed", current_step="metadata_detail",
        ))
        session.commit()
    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/tasks?status=completed")
    body = resp.json()
    assert len(body["data"]["items"]) == 1
    assert body["data"]["items"][0]["source_path"] == "/media/downloads/Movie.mkv"
    assert body["meta"]["filters"] == {"status": "completed"}


def test_api_v1_list_tasks_unknown_filter(tmp_path: Path) -> None:
    sf = _make_session_factory(tmp_path)
    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/tasks?status=invalid")
    body = resp.json()
    assert body["status"] == "error"
    assert body["messages"][0]["code"] == "unknown_status_filter"


def test_api_v1_list_tasks_pagination(tmp_path: Path) -> None:
    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        for i in range(5):
            repo.create(IngestTaskCreate(
                source_path=f"/media/downloads/Movie.{i}.mkv",
                status="discovered",
            ))
        session.commit()
    client = TestClient(create_app(session_factory=sf))
    r1 = client.get("/api/v1/tasks?page=1&page_size=2")
    assert len(r1.json()["data"]["items"]) == 2
    assert r1.json()["meta"]["total"] == 5
    r3 = client.get("/api/v1/tasks?page=3&page_size=2")
    assert len(r3.json()["data"]["items"]) == 1


# ---- 任务列表 current_step 稳定性 ----


def test_api_v1_list_tasks_accepts_agent_failed_current_step(tmp_path: Path) -> None:
    """任务进入 status=agent_failed + current_step=agent_failed 时, 列表
    接口必须返回 200 JSON, 不得 500. 这是任务工作台面板触发的合法稳态,
    Pydantic 校验若只允许 TaskStep 枚举的子集会让整页 500.
    """

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/Movie.Agent.Failed.mkv",
                status="agent_failed", current_step="agent_failed",
                media_type="movie", failure_reason="LLM 返回空 response",
            )
        )
        session.commit()
    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/tasks?page_size=200")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["status"] == "success"
    item = body["data"]["items"][0]
    assert item["status_summary"]["status"] == "agent_failed"
    assert item["status_summary"]["current_step"] == "agent_failed"
    assert item["status_summary"]["failure_reason"] == "LLM 返回空 response"


def test_api_v1_list_tasks_accepts_dynamic_current_step_markers(tmp_path: Path) -> None:
    """动态 / 临时 marker (runner 的 `step_N` / 复杂输入决策的
    decision_type / show 阻塞的 block_reason) 在 DB 是 String(128),
    业务侧不收敛到枚举, DTO 必须按字符串接收, 不得 500.
    """

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        # 1) runner 写入的 step_N
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/runner.mkv",
            status="agent_running", current_step="step_3",
        ))
        # 2) 复杂输入决策的 decision_type
        repo.create(IngestTaskCreate(
            source_path="/media/watch/complex.mkv",
            status="waiting_user", current_step="select_primary_video",
        ))
        # 3) show 阻塞的 block_reason
        repo.create(IngestTaskCreate(
            source_path="/media/watch/show_blocked.mkv",
            status="waiting_user", current_step="no_supported_video",
        ))
        # 4) 完全未知的 marker, 也不应 500
        repo.create(IngestTaskCreate(
            source_path="/media/watch/unknown.mkv",
            status="agent_running", current_step="custom_future_step",
        ))
        session.commit()
    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/tasks?page_size=200")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 4
    seen = {item["status_summary"]["current_step"] for item in items}
    assert seen == {"step_3", "select_primary_video", "no_supported_video", "custom_future_step"}


def test_api_v1_list_tasks_accepts_all_stable_agent_steps(tmp_path: Path) -> None:
    """枚举里新加的稳定步骤 (target_conflict, post_revoke_reingest,
    manual_selection_blocked, source_cleanup_*, user_replied,
    agent_failed, agent_running 等) 都必须能被列表接口原样返回.
    """

    sf = _make_session_factory(tmp_path)
    stable_steps = [
        ("agent_running", "agent_running"),
        ("agent_failed", "agent_failed"),
        ("waiting_user", "user_replied"),
        ("waiting_user", "target_conflict"),
        ("waiting_user", "target_conflict_decided"),
        ("waiting_user", "manual_selection_blocked"),
        ("agent_running", "post_revoke_decision"),
        ("agent_running", "post_revoke_decided"),
        ("agent_running", "post_revoke_reingest"),
        ("agent_running", "source_cleanup_decision"),
        ("agent_running", "source_cleanup_decided"),
        ("library_import_complete", "source_cleanup_kept"),
        ("library_import_complete", "source_cleanup_trashed"),
        ("library_import_complete", "source_cleanup_trash_refused"),
        ("library_import_complete", "source_cleanup_trash_failed"),
        ("agent_failed", "max_tool_failures"),
        ("agent_failed", "max_steps_exceeded"),
        ("agent_failed", "llm_error"),
        ("agent_failed", "config_error"),
        ("agent_failed", "agent_interrupted"),
        ("waiting_user", "select_metadata_candidate"),
    ]
    with sf() as session:
        repo = IngestTaskRepository(session)
        for idx, (status, step) in enumerate(stable_steps):
            repo.create(IngestTaskCreate(
                source_path=f"/media/downloads/stable-{idx}.mkv",
                status=status, current_step=step,
            ))
        session.commit()
    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/tasks?page_size=200")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    seen = {item["status_summary"]["current_step"] for item in items}
    expected = {step for _, step in stable_steps}
    assert seen == expected




# ---- 任务详情 ----


def test_api_v1_task_detail_full_structure(tmp_path: Path) -> None:
    sf = _make_session_factory(tmp_path)
    with sf() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/Movie.2026.mkv",
                status="agent_failed", current_step="metadata_detail",
                media_type="movie", confidence=0.45,
            )
        )
        session.commit()
        tid = task.id
    client = TestClient(create_app(session_factory=sf))
    resp = client.get(f"/api/v1/tasks/{tid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    data = body["data"]
    for k in ("task", "source_selection", "search_keyword", "selected_candidate",
              "metadata_detail", "write_plan",
              "write_result", "file_assets", "provider_calls",
              "operation_records", "audit_logs", "timeline"):
        assert k in data, f"missing key: {k}"
    assert "confirmation_request" not in data, (
        "confirmation_request 字段已下线，API 不应再返回该字段"
    )
    assert data["task"]["source_path"] == "/media/downloads/Movie.2026.mkv"
    assert len(data["timeline"]) >= 1


def test_api_v1_task_detail_not_found(tmp_path: Path) -> None:
    sf = _make_session_factory(tmp_path)
    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/tasks/nonexistent")
    body = resp.json()
    assert body["status"] == "error"
    assert body["messages"][0]["code"] == "task_not_found"


# ---- 关键词重搜 ----


def test_api_v1_research_updates_candidates(tmp_path: Path, monkeypatch) -> None:
    """默认 scope=all，profile-aware 搜索返回候选和摘要（不再附带 confirmation_request）"""
    config = AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"), shows_dir=Path("/media/library/shows"),
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    initialize_database(config)
    sf = create_session_factory(config)
    with sf() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/Wrong.Movie.2026.mkv",
                status="agent_failed", current_step="metadata_detail",
                media_type="movie", confidence=0.9,
            )
        )
        session.commit()
        tid = task.id

    from media_pilot.services import manual_research as mr

    def _fake_manual_research(session, *, task_id, keyword, scope, config):
        return mr.ManualResearchResult(
            candidates=[
                MediaCandidate(
                    task_id=task_id,
                    source="stub_metadata",
                    external_id="movie:correct-movie:2026",
                    title="Correct Movie",
                    original_title="Correct Movie",
                    year=2026,
                    media_type="movie",
                    confidence=0.95,
                    reason="strong_match",
                    payload={
                        "overview": "A corrected search result.",
                        "poster_url": "https://example.test/posters/correct-movie.jpg",
                    },
                )
            ],
            summary=mr.SearchSummary(
                keyword=keyword,
                scope=scope,
                searched_profiles=[
                    mr.ProfileSearchStatus(
                        profile="tmdb_movie",
                        label="TMDB 电影",
                        provider="tmdb",
                        status="succeeded",
                        candidate_count=1,
                    )
                ],
                total_candidates=1,
                kept_existing_candidates=False,
            ),
        )

    monkeypatch.setattr(mr, "run_manual_research", _fake_manual_research)
    from media_pilot.api.task_dtos import ResearchKeywordRequest
    from media_pilot.api.v1 import research_candidates

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(session_factory=sf, config=config)
        )
    )
    envelope = research_candidates(
        tid,
        ResearchKeywordRequest(keyword="Correct Movie 2026"),
        request,  # type: ignore[arg-type]
    )
    body = envelope.model_dump(mode="json")
    assert body["status"] == "success"
    data = body["data"]
    assert "confirmation_request" not in data
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["title"] == "Correct Movie"
    assert data["candidates"][0]["year"] == 2026
    assert data["candidates"][0]["overview"] == "A corrected search result."
    assert data["candidates"][0]["poster_url"] == "https://example.test/posters/correct-movie.jpg"
    summary = data["search_summary"]
    assert summary["keyword"] == "Correct Movie 2026"
    assert summary["scope"] == "all"
    assert summary["total_candidates"] == 1
    assert summary["kept_existing_candidates"] is False
    assert len(summary["searched_profiles"]) == 1
    assert summary["searched_profiles"][0]["status"] == "succeeded"


def test_api_v1_research_empty_keyword(tmp_path: Path) -> None:
    config = _make_config(tmp_path / "db")
    initialize_database(config)
    sf = create_session_factory(config)
    client = TestClient(create_app(config=config, session_factory=sf))
    resp = client.post("/api/v1/tasks/some-id/research",
                       json={"keyword": "   "})
    body = resp.json()
    assert body["status"] == "error"
    assert body["messages"][0]["code"] == "empty_keyword"


def test_api_v1_research_scope_tmdb_show_accepted(tmp_path: Path) -> None:
    """scope 校验必须接受 tmdb_show, 不再被当成 unknown scope 拒绝.

    锁定 backend 收口 (simplify-docker-onboarding-and-diagnostics 第 12
    节): UI 把 TMDB 剧集档案作为可选项暴露给用户后, research 路由
    必须能接受 scope=tmdb_show 进入 profile-aware 搜索流程.
    """
    config = _make_config(tmp_path / "db")
    initialize_database(config)
    sf = create_session_factory(config)
    client = TestClient(create_app(config=config, session_factory=sf))
    resp = client.post(
        "/api/v1/tasks/some-id/research",
        json={"keyword": "Breaking Bad", "scope": "tmdb_show"},
    )
    body = resp.json()
    # 不应再以 invalid_scope 拒绝; 否则锁定被破坏.
    assert not any(
        m.get("code") == "invalid_scope" for m in body.get("messages", [])
    ), f"tmdb_show 仍被 scope 校验拒绝: {body}"


def test_api_v1_research_no_pending_discovered_task(tmp_path: Path, monkeypatch) -> None:
    """新流程下 research 不再校验 pending ConfirmationRequest；任何任务可重搜。
    返回 200 + candidates + search_summary。"""
    config = AppConfig(
        downloads_dir=Path("/media/downloads"),
        watch_dir=Path("/media/watch"),
        workspace_dir=Path("/media/workspace"),
        movies_dir=Path("/media/library/movies"), shows_dir=Path("/media/library/shows"),
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    initialize_database(config)
    from media_pilot.repository.models import AppSetting
    sf = create_session_factory(config)
    with sf() as session:
        if not session.get(AppSetting, "singleton"):
            session.add(AppSetting(id="singleton", enabled_metadata_profiles=["tmdb_movie"]))
            session.commit()
    with sf() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/Movie.mkv", status="discovered",
            )
        )
        session.commit()
        tid = task.id

    from media_pilot.orchestration import profile_search as ps
    monkeypatch.setattr(
        ps, "create_metadata_provider_by_name",
        lambda config_arg, provider_name: _make_stub_correct_movie_provider(),
    )
    client = TestClient(create_app(config=config, session_factory=sf))
    resp = client.post(f"/api/v1/tasks/{tid}/research",
                       json={"keyword": "Movie 2026"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert "candidates" in body["data"]
    assert "search_summary" in body["data"]
    assert "confirmation_request" not in body["data"]


# ---- 状态轮询 ----


def test_api_v1_task_status_returns_summary(tmp_path: Path) -> None:
    sf = _make_session_factory(tmp_path)
    with sf() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/Movie.mkv",
                status="queued", current_step="ai_parse",
                confidence=0.9,
            )
        )
        session.commit()
        tid = task.id
    client = TestClient(create_app(session_factory=sf))
    resp = client.get(f"/api/v1/tasks/{tid}/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    ss = body["data"]
    assert ss["status"] == "queued"
    assert ss["current_step"] == "ai_parse"
    assert "confidence_level" in ss
    assert "latest_message" in ss


def test_api_v1_task_status_not_found(tmp_path: Path) -> None:
    sf = _make_session_factory(tmp_path)
    client = TestClient(create_app(session_factory=sf))
    resp = client.get("/api/v1/tasks/nonexistent/status")
    body = resp.json()
    assert body["status"] == "error"
    assert body["messages"][0]["code"] == "task_not_found"


# 注: 旧 /confirmation 端点已在 replace-legacy-confirmation-with-agent-decisions
# 完全下线，相关 disabled-error 回归测试随之删除。
# 旧 `confirmed` / `needs_confirmation` 状态行为测试也已删除：
# 本分支允许 DB 清库, 不再为 legacy 状态保留业务行为契约；
# 旧状态写入约束由 test_normalize_agent_task_status_model 中 source-audit 覆盖。


def test_api_v1_no_database_returns_error() -> None:
    client = TestClient(create_app())
    resp = client.get("/api/v1/tasks")
    assert resp.status_code == 503


# ── Issue A: 删除任务 / 删除下载遇 DB locked 返回结构化 409 ────────────


def test_api_v1_delete_task_returns_409_envelope_on_db_locked(
    tmp_path: Path, monkeypatch,
) -> None:
    """delete_task 端点遇到 OperationalError("database is locked") 时,
    必须返回 409 + structured ApiEnvelope, 不是 500 / plaintext."""
    from media_pilot.repository.models import IngestTask
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
    from sqlalchemy.exc import OperationalError

    sf = _make_session_factory(tmp_path)
    config = _make_config(tmp_path)

    with sf() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path="/tmp/test-watch/Some.Movie.2026.mkv",
            status="agent_running",
            current_step="agent_running",
            media_type="movie",
        ))
        session.commit()
        task_id = task.id

    # monkeypatch delete_ingest_task 让它抛 OperationalError
    from media_pilot.api import v1 as v1_module

    def _raise_locked(session, task_id, config):
        raise OperationalError(
            "stmt", {}, Exception("database is locked"),
        )

    monkeypatch.setattr(
        v1_module, "_db_locked_response",  # 防止被覆盖
        v1_module._db_locked_response,
    )
    # 直接 monkeypatch delete_ingest_task 在模块内的引用
    from media_pilot.orchestration import delete_unpublished
    monkeypatch.setattr(
        delete_unpublished, "delete_ingest_task", _raise_locked,
    )

    app = create_app()
    app.state.session_factory = sf
    app.state.config = config
    client = TestClient(app)

    resp = client.post(f"/api/v1/tasks/{task_id}/delete")
    assert resp.status_code == 409
    body = resp.json()
    assert body["status"] == "error"
    assert body["messages"][0]["code"] == "db_locked"
    assert body["meta"]["retryable"] is True


def test_api_v1_delete_download_returns_409_envelope_on_db_locked(
    tmp_path: Path, monkeypatch,
) -> None:
    """delete_download 端点遇到 OperationalError 时, 同样 409 + structured envelope."""
    from media_pilot.repository.models import DownloadTask
    from media_pilot.repository.repositories import (
        DownloadTaskCreate, DownloadTaskRepository,
    )
    from sqlalchemy.exc import OperationalError

    sf = _make_session_factory(tmp_path)
    config = _make_config(tmp_path)

    with sf() as session:
        task = DownloadTaskRepository(session).create(DownloadTaskCreate(
            title="Test Movie", source="prowlarr",
            save_path="/tmp/test-downloads",
        ))
        session.commit()
        download_id = task.id

    from media_pilot.orchestration import delete_unpublished

    def _raise_locked(session, download_id, config):
        raise OperationalError(
            "stmt", {}, Exception("database is locked"),
        )

    monkeypatch.setattr(
        delete_unpublished, "delete_download_only", _raise_locked,
    )

    app = create_app()
    app.state.session_factory = sf
    app.state.config = config
    client = TestClient(app)

    resp = client.post(f"/api/v1/downloads/{download_id}/delete")
    assert resp.status_code == 409
    body = resp.json()
    assert body["status"] == "error"
    assert body["messages"][0]["code"] == "db_locked"


# ── Issue 2: decision reply envelope 状态正确 (success / 4xx) ──────────


def _setup_target_conflict_decision(tmp_path: Path):
    """构造一个 target_conflict 决策 + 必要 movie 上下文.

    复用 tests/test_target_conflict_handler.py 的 helper, 但放进
    test_api_v1 不依赖 cross-test 导入, 这里复刻简化版."""
    from media_pilot.repository.models import (
        MediaSourceSelection,
        MetadataDetail,
    )
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
        AgentRunCreate,
        AgentRunRepository,
        IngestTaskCreate,
        IngestTaskRepository,
    )

    sf = _make_session_factory(tmp_path)
    # 用 tmp_path 派生受控 config, 避免 _make_config 的硬编码 /media/* 路径.
    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "ws",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path / "db",
    )

    with sf() as session:
        video = tmp_path / "movie.mkv"
        video.write_bytes(b"video-bytes")

        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(video),
            status="agent_running",
            current_step="publish",
            media_type="movie",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(video),
            selected_path=str(video),
            confidence=1.0,
            reason="largest_video_file",
        ))
        session.add(MetadataDetail(
            task_id=task.id,
            provider="tmdb",
            provider_id="movie:568160",
            media_type="movie",
            title="天气之子",
            original_title="天気の子",
            year=2019,
            payload={
                "plot": "test",
                "images": {"poster": "https://example.test/poster.jpg"},
            },
        ))
        run = AgentRunRepository(session).create(AgentRunCreate(
            task_id=task.id, current_step="agent_start",
        ))
        decision = AgentDecisionRequestRepository(session).create(
            AgentDecisionRequestCreate(
                run_id=run.id,
                task_id=task.id,
                decision_type="target_conflict",
                question="发布目标已存在冲突，请选择处理方式。",
                free_text_allowed=False,
                options=[
                    {"id": "overwrite_target", "label": "覆盖发布目标"},
                    {"id": "cancel_publish", "label": "取消本次发布"},
                ],
                payload={
                    "final_target_dir": str(config.movies_dir / "Tenki"),
                    "final_target_file": str(
                        config.movies_dir / "Tenki" / "Tenki.mkv",
                    ),
                    "conflict": "target_file_already_exists",
                },
            ),
        )
        session.commit()
        return sf, config, task.id, run.id, decision.id


def test_api_v1_reply_overwrite_target_returns_success_envelope(
    tmp_path: Path, monkeypatch,
) -> None:
    """POST /api/v1/agent-decisions/{id}/reply overwrite_target 成功时
    必须返回 success envelope, data.status == "target_conflict_overwritten".
    旧实现只把 completed / waiting_user 视为 success, 此路径会被
    envelope.status = "error" 误标, 前端 apiPost 走 ApiError.onError."""
    sf, config, _task_id, _run_id, decision_id = _setup_target_conflict_decision(
        tmp_path,
    )

    # stub execute_movie_write 避免真正写盘
    class _StubWriteResult:
        status = "succeeded"
        warnings: list = []

    from media_pilot.orchestration import jellyfin_movie_writer
    monkeypatch.setattr(
        jellyfin_movie_writer, "execute_movie_write",
        lambda *a, **kw: _StubWriteResult(),
    )

    # 预置同名 .mkv 让 build_movie_write_plan 走到 conflict
    target_dir = config.movies_dir / "天气之子 (2019)"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "天气之子 (2019).mkv").write_bytes(b"old")

    app = create_app()
    app.state.session_factory = sf
    app.state.config = config
    client = TestClient(app)

    resp = client.post(
        f"/api/v1/agent-decisions/{decision_id}/reply",
        json={"option_id": "overwrite_target", "decided_by": "user"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success", (
        "overwrite_target 成功必须 envelope.status == success, "
        "旧实现因 _DECISION_REPLY_SUCCESS_STATUSES 缺漏误标 error"
    )
    assert body["data"]["status"] == "target_conflict_overwritten"
    # message level 不能是 error, 否则前端 toast 会误报
    assert body["messages"][0]["level"] == "info"
    # success path code 改为 result.status (前端按 status 弹对应 toast).
    # 旧实现是 f"agent_continue_{status}", 但 success 路径下不需要
    # "agent_continue" 前缀, status 本身就够稳定.
    assert body["messages"][0]["code"] == "target_conflict_overwritten"


def test_api_v1_reply_cancel_publish_returns_success_envelope(
    tmp_path: Path,
) -> None:
    """POST /api/v1/agent-decisions/{id}/reply cancel_publish 成功时
    必须返回 success envelope, data.status == "target_conflict_cancelled"."""
    sf, config, _task_id, _run_id, decision_id = _setup_target_conflict_decision(
        tmp_path,
    )

    app = create_app()
    app.state.session_factory = sf
    app.state.config = config
    client = TestClient(app)

    resp = client.post(
        f"/api/v1/agent-decisions/{decision_id}/reply",
        json={"option_id": "cancel_publish", "decided_by": "user"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["data"]["status"] == "target_conflict_cancelled"
    assert body["messages"][0]["level"] == "info"


def test_api_v1_reply_overwrite_target_structured_failure_returns_4xx(
    tmp_path: Path, monkeypatch,
) -> None:
    """overwrite handler 返回结构化 ValueError (resolver 失败 / 写入失败)
    时, API 必须返回 4xx + envelope.status == error + messages[0] 带
    code / retryable; 同时 pending decision 不能被消费 (decision.status
    保持 pending, run 不切到 completed)."""
    from media_pilot.repository.repositories import (
        AgentDecisionRequestRepository,
        AgentRunRepository,
        IngestTaskRepository,
    )

    sf, config, task_id, run_id, decision_id = _setup_target_conflict_decision(
        tmp_path,
    )

    # monkeypatch handle_overwrite_target 让它抛结构化 ValueError
    # (模拟 resolver 失败 / movie_write_failed)
    from media_pilot.services import target_conflict_handler

    def _raise_invalid_source(**kwargs):
        raise ValueError({
            "status_code": 422,
            "code": "no_main_video",
            "detail": "任务输入目录中没有可识别的主视频文件",
            "retryable": True,
        })

    monkeypatch.setattr(
        target_conflict_handler, "handle_overwrite_target", _raise_invalid_source,
    )

    app = create_app()
    app.state.session_factory = sf
    app.state.config = config
    client = TestClient(app)

    resp = client.post(
        f"/api/v1/agent-decisions/{decision_id}/reply",
        json={"option_id": "overwrite_target", "decided_by": "user"},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["messages"][0]["code"] == "no_main_video"
    assert body["messages"][0]["text"] == "任务输入目录中没有可识别的主视频文件"
    assert body["meta"]["retryable"] is True

    # 关键不变量: pending decision 没被消费. handler 抛错时, 决策回复
    # 路径必须 rollback, decision.status 保持 pending, run.status 不切
    # completed, task.status 不切 library_import_complete.
    with sf() as session:
        decision = AgentDecisionRequestRepository(session).get(decision_id)
        assert decision is not None
        assert decision.status == "pending", (
            "handler 抛错时决策必须保持 pending, 允许用户重试"
        )
        assert decision.decision is None

        run = AgentRunRepository(session).get(run_id)
        assert run is not None
        assert run.status != "completed", (
            "handler 抛错时 run.status 不能切到 completed, 避免 lock 任务"
        )

        task = IngestTaskRepository(session).get(task_id)
        assert task is not None
        assert task.status != "library_import_complete", (
            "handler 抛错时 task.status 不能切到 library_import_complete, "
            "避免用户重试时拿到 404"
        )


def test_api_v1_reply_overwrite_target_db_locked_returns_409(
    tmp_path: Path, monkeypatch,
) -> None:
    """reply overwrite_target 时, safe_commit 抛 OperationalError →
    决策保持 pending, 返 409 db_locked. 验证: 不在 5xx, envelope
    携带 retryable=True, 用户可重试."""
    sf, config, _task_id, _run_id, decision_id = _setup_target_conflict_decision(
        tmp_path,
    )

    # 强制 reply_to_decision 内部 safe_commit 抛 OperationalError.
    # v1.py 用的是函数内 import `from media_pilot.orchestration.db_retry
    # import safe_commit`, 直接 patch 源模块即可.
    from sqlalchemy.exc import OperationalError

    from media_pilot.orchestration import db_retry

    def _raise_locked(session):
        raise OperationalError("stmt", {}, Exception("database is locked"))

    monkeypatch.setattr(db_retry, "safe_commit", _raise_locked)

    app = create_app()
    app.state.session_factory = sf
    app.state.config = config
    client = TestClient(app)

    resp = client.post(
        f"/api/v1/agent-decisions/{decision_id}/reply",
        json={"option_id": "cancel_publish", "decided_by": "user"},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["messages"][0]["code"] == "db_locked"
    assert body["meta"]["retryable"] is True


# ---- 任务列表 SQL 分页契约 ----


def test_api_v1_list_tasks_sql_pagination_total_vs_page_size(tmp_path: Path) -> None:
    """跨页场景: meta.total 必须是全量总数, data.items 只含当前页.

    关键: 5 条任务, page_size=2 → page 1 返回 2 条 total=5, page 3 返回 1 条.
    这验证了 SQL `COUNT(*) + LIMIT/OFFSET` 已经替代 Python slice, 不再
    全量查后再切片.
    """

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        for i in range(5):
            repo.create(IngestTaskCreate(
                source_path=f"/media/downloads/Movie.{i}.mkv",
                status="discovered",
            ))
        session.commit()
    client = TestClient(create_app(session_factory=sf))

    r1 = client.get("/api/v1/tasks?page=1&page_size=2")
    body1 = r1.json()
    assert len(body1["data"]["items"]) == 2
    assert body1["meta"]["total"] == 5
    assert body1["meta"]["page"] == 1
    assert body1["meta"]["page_size"] == 2

    r2 = client.get("/api/v1/tasks?page=2&page_size=2")
    body2 = r2.json()
    assert len(body2["data"]["items"]) == 2
    assert body2["meta"]["total"] == 5
    assert body2["meta"]["page"] == 2

    r3 = client.get("/api/v1/tasks?page=3&page_size=2")
    body3 = r3.json()
    assert len(body3["data"]["items"]) == 1
    assert body3["meta"]["total"] == 5

    # 跨页 5 条 path 必须互不重复
    all_paths = (
        [it["source_path"] for it in body1["data"]["items"]]
        + [it["source_path"] for it in body2["data"]["items"]]
        + [it["source_path"] for it in body3["data"]["items"]]
    )
    assert len(set(all_paths)) == 5


def test_api_v1_list_tasks_sql_pagination_with_status_filter(tmp_path: Path) -> None:
    """status filter 分页: meta.total 只统计过滤后行, data.items 不含其它 status.

    4 条 agent_failed + 1 条 agent_running. status=agent_failed&page=2&page_size=2
    → total=4, page 2 含 2 条且全是 agent_failed.
    """

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        for i in range(4):
            repo.create(IngestTaskCreate(
                source_path=f"/media/downloads/agent_failed_{i}.mkv",
                status="agent_failed",
            ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/agent_running.mkv",
            status="agent_running",
        ))
        session.commit()
    client = TestClient(create_app(session_factory=sf))

    r1 = client.get("/api/v1/tasks?status=agent_failed&page=1&page_size=2")
    body1 = r1.json()
    assert body1["meta"]["total"] == 4
    assert len(body1["data"]["items"]) == 2
    for it in body1["data"]["items"]:
        assert it["status_summary"]["status"] == "agent_failed"

    r2 = client.get("/api/v1/tasks?status=agent_failed&page=2&page_size=2")
    body2 = r2.json()
    assert body2["meta"]["total"] == 4
    assert len(body2["data"]["items"]) == 2
    for it in body2["data"]["items"]:
        assert it["status_summary"]["status"] == "agent_failed"

    # 不得混入 agent_running
    running_resp = client.get("/api/v1/tasks?status=agent_running&page=1&page_size=10")
    assert running_resp.json()["meta"]["total"] == 1


def test_api_v1_list_tasks_unknown_status_filter_remains_rejected(tmp_path: Path) -> None:
    """未知 status filter 仍必须返回 unknown_status_filter, 不得退化为无 filter 全量查询."""

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/legit.mkv",
            status="waiting_user",
        ))
        session.commit()
    client = TestClient(create_app(session_factory=sf))

    resp = client.get("/api/v1/tasks?status=definitely_not_a_real_status")
    body = resp.json()
    assert body["status"] == "error"
    assert body["messages"][0]["code"] == "unknown_status_filter"
    # 关键: total 不得退化为 1 (legit 任务数)
    assert body["meta"] == {} or body["meta"].get("total") in (None, 0)


def test_api_v1_list_tasks_pagination_respects_attention_priority_order(tmp_path: Path) -> None:
    """跨页必须复用 attention priority 排序: page 1 第一条应是 waiting_user.

    关键: 4 条任务混合 priority, page_size=1, page 1 应该是 priority 1
    (waiting_user), 而不是创建时间最早或最晚的记录. 验证 SQL `LIMIT/OFFSET`
    与 `ORDER BY` 一起生效, 没有被 Python 后处理打乱.
    """

    sf = _make_session_factory(tmp_path)
    with sf() as session:
        repo = IngestTaskRepository(session)
        # 创建顺序故意与 priority 顺序不一致
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/library.mkv",
            status="library_import_complete",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/active.mkv",
            status="agent_running",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/failed.mkv",
            status="agent_failed",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/waiting.mkv",
            status="waiting_user",
        ))
        session.commit()
    client = TestClient(create_app(session_factory=sf))

    p1 = client.get("/api/v1/tasks?page=1&page_size=1").json()
    p2 = client.get("/api/v1/tasks?page=2&page_size=1").json()
    p3 = client.get("/api/v1/tasks?page=3&page_size=1").json()
    p4 = client.get("/api/v1/tasks?page=4&page_size=1").json()

    assert p1["data"]["items"][0]["source_path"] == "/media/downloads/waiting.mkv"
    assert p2["data"]["items"][0]["source_path"] == "/media/downloads/active.mkv"
    assert p3["data"]["items"][0]["source_path"] == "/media/downloads/failed.mkv"
    assert p4["data"]["items"][0]["source_path"] == "/media/downloads/library.mkv"
    for body in (p1, p2, p3, p4):
        assert body["meta"]["total"] == 4


# ── Issue 3: select_metadata_candidate reply envelope ──────────────


def _setup_select_metadata_candidate_decision(tmp_path: Path):
    """构造一个 select_metadata_candidate 决策 + movie 上下文.

    复用 _setup_target_conflict_decision 的方式 — 必要 task /
    run / MediaCandidate / MetadataDetail, 决策 question
    提示用户选候选. 决策 option_id 形如 "candidate_<cid>",
    对应 select_metadata_candidate 的 option 形状.
    """
    from media_pilot.repository.models import (
        MediaCandidate,
        MediaSourceSelection,
    )
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
        AgentRunCreate,
        AgentRunRepository,
        IngestTaskCreate,
        IngestTaskRepository,
    )

    sf = _make_session_factory(tmp_path)
    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "ws",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path / "db",
    )

    with sf() as session:
        video = tmp_path / "movie.mkv"
        video.write_bytes(b"video-bytes")

        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(video),
            status="agent_running",
            current_step="select_metadata_candidate",
            media_type="movie",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(video),
            selected_path=str(video),
            confidence=1.0,
            reason="largest_video_file",
        ))
        # 候选 — 后端 select_metadata_candidate 决策 option_id 形如
        # "candidate_<cid>", payload 携带稳定 candidate_id.
        cand = MediaCandidate(
            task_id=task.id,
            source="tmdb",
            media_type="movie",
            title="天气之子",
            original_title="天気の子",
            year=2019,
            external_id="tmdb:568160",
            confidence=0.85,
            reason="provider tmdb matched",
            payload={"overview": "test"},
        )
        session.add(cand)
        session.flush()
        candidate_id = cand.id

        run = AgentRunRepository(session).create(AgentRunCreate(
            task_id=task.id, current_step="select_metadata_candidate",
        ))
        AgentRunRepository(session).update_status(
            run, status="waiting_user",
            current_step="select_metadata_candidate",
        )
        decision = AgentDecisionRequestRepository(session).create(
            AgentDecisionRequestCreate(
                run_id=run.id,
                task_id=task.id,
                decision_type="select_metadata_candidate",
                question="请选择正确的元数据候选。",
                free_text_allowed=False,
                options=[{
                    "id": f"candidate_{candidate_id}",
                    "label": "天气之子 (2019)",
                    "description": "movie · confidence=0.85 · source=tmdb",
                    "payload": {
                        "candidate_id": candidate_id,
                        "provider": "tmdb",
                        "provider_id": "tmdb:568160",
                        "media_type": "movie",
                        "title": "天气之子",
                        "year": 2019,
                        "confidence": 0.85,
                    },
                }],
            ),
        )
        session.commit()
        return sf, config, task.id, run.id, decision.id, candidate_id


def test_api_v1_reply_select_metadata_published_returns_success_envelope(
    tmp_path: Path, monkeypatch,
) -> None:
    """POST /api/v1/agent-decisions/{id}/reply 在 select_metadata_candidate
    决策被用户选候选后, 走确定性 fetch + publish 路径成功时:
    - HTTP 200
    - envelope.status == "success"
    - data.status == "metadata_published"
    - messages[0].level == "info"
    - messages[0].code == "metadata_published"
    - messages[0].text 是业务文案, 不暴露 run_id
    - task.status == "library_import_complete"
    - run.status == "completed"

    旧实现 _DECISION_REPLY_SUCCESS_STATUSES 漏列 metadata_published,
    envelope.status="error", 前端 DecisionReplyCard 走 onError 弹红
    toast. 详见 fix-decision-reply-metadata-published-ui-sync.
    """
    from media_pilot.repository.repositories import (
        AgentRunRepository,
        IngestTaskRepository,
    )

    sf, config, _task_id, _run_id, decision_id, candidate_id = (
        _setup_select_metadata_candidate_decision(tmp_path)
    )

    # ── stub: fetch + publish + execute_movie_write 走通, 不真写盘 ──
    from media_pilot.repository.models import MetadataDetail
    from media_pilot.agent.tools import registry as registry_module
    from media_pilot.services import select_metadata_publish as smp_module
    from media_pilot.orchestration import jellyfin_movie_writer

    def _fake_fetch(*, session, config, task_id, provider_name, provider_id, media_type):
        session.add(MetadataDetail(
            task_id=task_id, provider=provider_name,
            provider_id=provider_id, media_type=media_type,
            title="天气之子", original_title="天気の子", year=2019,
            payload={"overview": "test"},
        ))
        session.flush()
        return smp_module.FetchAndSaveDetailResult(
            status="success", summary="ok", provider=provider_name,
            provider_id=provider_id, title="天气之子", year=2019,
        )

    class _StubWriteResult:
        status = "succeeded"
        warnings: list = []

    publish_called = {"count": 0}
    registry = type("_R", (), {})()  # namespace for execute
    def _fake_execute(tool_name, ctx, input_data):
        if tool_name == "publish_movie_to_library":
            publish_called["count"] += 1
            return type("_TR", (), {"status": "success", "summary": "published", "data": {}})()
        return type("_TR", (), {"status": "failure", "summary": "no stub", "data": {}})()

    registry.execute = _fake_execute
    monkeypatch.setattr(registry_module, "get_tool_registry", lambda: registry)
    monkeypatch.setattr(registry_module, "register_builtin_tools", lambda: None)

    monkeypatch.setattr(smp_module, "fetch_and_save_metadata_detail", _fake_fetch)
    monkeypatch.setattr(jellyfin_movie_writer, "execute_movie_write",
                        lambda *a, **kw: _StubWriteResult())

    app = create_app()
    app.state.session_factory = sf
    app.state.config = config
    client = TestClient(app)

    resp = client.post(
        f"/api/v1/agent-decisions/{decision_id}/reply",
        json={"option_id": f"candidate_{candidate_id}", "decided_by": "user"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # envelope 是 success, 不是 error. 旧实现 metadata_published 不在
    # _DECISION_REPLY_SUCCESS_STATUSES, 会被误标 error.
    assert body["status"] == "success", (
        "select_metadata_candidate 成功发布必须 envelope.status == success, "
        "旧实现因 _DECISION_REPLY_SUCCESS_STATUSES 缺漏误标 error"
    )
    assert body["data"]["status"] == "metadata_published"
    assert body["messages"][0]["level"] == "info"
    assert body["messages"][0]["code"] == "metadata_published"
    # message text 是 machine-readable status token (= result.status),
    # 不暴露 run_id (UUID) 或 "Agent run" 前缀, 也不应硬塞任何业务文案
    # (用户可见文案走前端 i18n agent.metadataPublished).
    text = body["messages"][0]["text"]
    assert text == "metadata_published", (
        f"success path message.text MUST == result.status 机器可读 token, "
        f"不得硬编码业务文案或 run_id, got: {text!r}"
    )
    assert "Agent run" not in text, (
        f"success path message 不得含 'Agent run' 内部前缀, got: {text!r}"
    )
    # 不得含 UUID 模式 (8-4-4-4-12)
    import re
    uuid_re = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        re.IGNORECASE,
    )
    assert not uuid_re.search(text), (
        f"success path message 不得暴露 run_id UUID, got: {text!r}"
    )
    # publish tool 应被调一次
    assert publish_called["count"] == 1

    # task / run 终态
    with sf() as session:
        task = IngestTaskRepository(session).get(_task_id)
        assert task is not None
        assert task.status == "library_import_complete"
        run = AgentRunRepository(session).get(_run_id)
        assert run is not None
        assert run.status == "completed"


def test_api_v1_reply_target_conflict_pending_is_success_envelope(
    tmp_path: Path, monkeypatch,
) -> None:
    """select_metadata_candidate 走 publish 时遇到 target_conflict, 后端
    已新建 target_conflict 决策让用户选 overwrite / cancel. 这条路径是
    成功进入下一步人工确认, 不是失败:

    - HTTP 200 + envelope.status="success" + level=info
    - data.status / message.code / message.text 都是 "target_conflict_pending"
      (machine-readable status hint)
    - task.status=waiting_user (新决策等用户处理)
    - message 不得暴露 run_id / 误用 metadata_published 业务文案

    旧实现因 target_conflict_pending 不在 _DECISION_REPLY_SUCCESS_STATUSES
    而误标 error envelope, 前端会弹红 toast + 不刷新缓存. 修复后必须
    走 success envelope, 同时前端 gate 在 data.status === "metadata_published"
    才弹"入库成功"toast, 这条路径不会触发入库 toast.
    """
    from media_pilot.repository.models import MetadataDetail
    from media_pilot.agent.tools import registry as registry_module
    from media_pilot.services import select_metadata_publish as smp_module
    from media_pilot.orchestration import jellyfin_movie_writer

    def _fake_fetch(*, session, config, task_id, provider_name, provider_id, media_type):
        session.add(MetadataDetail(
            task_id=task_id, provider=provider_name,
            provider_id=provider_id, media_type=media_type,
            title="X", original_title=None, year=2019, payload={},
        ))
        session.flush()
        return smp_module.FetchAndSaveDetailResult(
            status="success", summary="ok", provider=provider_name,
            provider_id=provider_id, title="X", year=2019,
        )

    def _fake_execute(tool_name, ctx, input_data):
        if tool_name == "publish_movie_to_library":
            return type("_TR", (), {
                "status": "failure",
                "summary": "target conflict",
                "data": {"requires_user": True, "reason": "target_conflict"},
            })()
        return type("_TR", (), {"status": "failure", "summary": "no stub", "data": {}})()

    registry = type("_R", (), {})()
    registry.execute = _fake_execute
    monkeypatch.setattr(registry_module, "get_tool_registry", lambda: registry)
    monkeypatch.setattr(registry_module, "register_builtin_tools", lambda: None)
    monkeypatch.setattr(smp_module, "fetch_and_save_metadata_detail", _fake_fetch)
    monkeypatch.setattr(jellyfin_movie_writer, "execute_movie_write",
                        lambda *a, **kw: type("_Stub", (), {"status": "succeeded", "warnings": []})())

    sf, config, _task_id, _run_id, decision_id, candidate_id = (
        _setup_select_metadata_candidate_decision(tmp_path)
    )

    app = create_app()
    app.state.session_factory = sf
    app.state.config = config
    client = TestClient(app)

    resp = client.post(
        f"/api/v1/agent-decisions/{decision_id}/reply",
        json={"option_id": f"candidate_{candidate_id}", "decided_by": "user"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 关键: target_conflict_pending MUST 视为 success envelope. 旧实现
    # 因 _DECISION_REPLY_SUCCESS_STATUSES 漏列而误标 error.
    assert body["status"] == "success", (
        f"target_conflict_pending 应当走 success envelope, "
        f"旧实现因 _DECISION_REPLY_SUCCESS_STATUSES 漏列而误标 error. "
        f"got status={body['status']!r}"
    )
    assert body["data"]["status"] == "target_conflict_pending"
    assert body["messages"][0]["code"] == "target_conflict_pending"
    assert body["messages"][0]["level"] == "info"
    # message.text 必须是 status token, 不得误用 metadata_published
    # 业务文案, 也不得含 run_id
    text = body["messages"][0]["text"]
    assert text == "target_conflict_pending", (
        f"success path message.text MUST == result.status 机器可读 token, "
        f"got: {text!r}"
    )
    assert "Agent run" not in text
    assert "metadata_published" not in text, (
        f"target_conflict_pending message 不得复用 metadata_published 业务文案, "
        f"got: {text!r}"
    )
    import re
    uuid_re = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        re.IGNORECASE,
    )
    assert not uuid_re.search(text)

    # task.status 必须是 waiting_user (新决策等用户处理), 不得标
    # library_import_complete (那是 metadata_published 成功路径的终态)
    with sf() as session:
        task = IngestTaskRepository(session).get(_task_id)
        assert task is not None
        assert task.status == "waiting_user", (
            f"target_conflict_pending 后 task.status 必须是 waiting_user "
            f"(等用户处理新决策), got: {task.status!r}"
        )
