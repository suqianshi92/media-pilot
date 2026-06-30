"""撤销发布后端测试 —— 预检三类场景 + 执行三类场景 + 边界"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from media_pilot.app import create_app
from media_pilot.config import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import (
    FileAsset,
    IngestTask,
    MediaCandidate,
    MediaSourceSelection,
    MetadataDetail,
    OperationRecord,
    WritePlan,
    WriteResult,
)
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

# ── 测试辅助 ──


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


def _create_published_task(
    tmp_path: Path,
    *,
    selected_path: str | None = "/media/downloads/test.mkv",
    input_path: str | None = None,
    bdmv_detected: bool = False,
    selection_payload: dict | None = None,
    publish_dir: str | None = None,
):
    """创建一个 library_import_complete 状态的任务，含必要的关联数据。"""
    if publish_dir is None:
        publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    task_source_path = input_path or selected_path
    session_factory = _make_session_factory(tmp_path)
    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=task_source_path,
            status="library_import_complete",
            current_step="library_import_complete",
        ))

        # 写入 MediaSourceSelection
        sel = MediaSourceSelection(
            task_id=task.id,
            input_path=input_path or selected_path,
            selected_path=selected_path,
            payload=selection_payload or {"bdmv_detected": bdmv_detected},
        )
        session.add(sel)

        # 写入 WriteResult（发布目录）
        wr = WriteResult(
            task_id=task.id,
            status="succeeded",
            payload={"target_dir": publish_dir},
        )
        session.add(wr)

        session.commit()
        return task.id, publish_dir, session_factory


# ── 预检测试 ──


def test_check_revoke_publish_source_available(tmp_path: Path):
    """主文件存在 + 非 BDMV → allowed=True，回到人工确认"""
    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    task_id, pd, session_factory = _create_published_task(
        tmp_path, selected_path=__file__, bdmv_detected=False, publish_dir=publish_dir
    )

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.get(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    assert data["data"]["allowed"] is True
    assert data["data"]["source_file_exists"] is True
    assert data["data"]["is_complex_structure"] is False
    assert "等待用户" in data["data"]["outcome_description"]


def test_check_revoke_publish_source_missing(tmp_path: Path):
    """主文件缺失 → allowed=True，删除任务"""
    task_id, pd, session_factory = _create_published_task(
        tmp_path, selected_path="/nonexistent/path.mkv", bdmv_detected=False
    )

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.get(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    assert data["data"]["allowed"] is True
    assert data["data"]["source_file_exists"] is False
    assert "已缺失" in data["data"]["outcome_description"]


def test_check_revoke_publish_bdmv(tmp_path: Path):
    """BDMV 复杂结构 → allowed=True，删除任务"""
    task_id, pd, session_factory = _create_published_task(
        tmp_path, selected_path=__file__, bdmv_detected=True
    )

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.get(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    assert data["data"]["allowed"] is True
    assert data["data"]["source_file_exists"] is True
    assert data["data"]["is_complex_structure"] is True
    assert "BDMV" in data["data"]["outcome_description"]


def test_check_revoke_publish_bdmv_source_kind_payload(tmp_path: Path):
    """新 BDMV selection 形状同样按复杂结构处理。"""
    from media_pilot.orchestration.revoke_publish import check_revoke_publish

    source_dir = tmp_path / "downloads" / "Disc Movie"
    (source_dir / "BDMV" / "STREAM").mkdir(parents=True)
    (source_dir / "BDMV" / "index.bdmv").write_text("bdmv")
    task_id, pd, session_factory = _create_published_task(
        tmp_path,
        selected_path=None,
        input_path=str(source_dir),
        selection_payload={
            "source_kind": "bdmv",
            "bdmv_dir": str(source_dir / "BDMV"),
        },
    )

    with session_factory() as session:
        result = check_revoke_publish(session, task_id=task_id)

    assert result.allowed is True
    assert result.source_file_exists is True
    assert result.is_complex_structure is True
    assert "BDMV" in result.outcome_description


def test_check_revoke_publish_not_published(tmp_path: Path):
    """非 library_import_complete 状态 → allowed=False"""
    session_factory = _make_session_factory(tmp_path)
    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path="/media/downloads/test.mkv",
            status="needs_confirmation",
            current_step="operator_confirmation",
        ))
        task_id = task.id
        session.commit()

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.get(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "error"
    assert data["data"]["allowed"] is False
    assert "仅已完成入库" in data["data"]["outcome_description"]


def test_check_revoke_publish_nonexistent_task(tmp_path: Path):
    """任务不存在 → allowed=False"""
    session_factory = _make_session_factory(tmp_path)
    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.get("/api/v1/tasks/nonexistent-id/revoke-publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "error"
    assert data["data"]["allowed"] is False


# ── 执行测试 ──


def test_execute_revoke_back_to_confirmation(tmp_path: Path):
    """主文件存在 + 非 BDMV → 删除发布目录，任务进入 waiting_user 并创建 AgentDecisionRequest"""
    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    task_id, pd, session_factory = _create_published_task(
        tmp_path, selected_path=__file__, bdmv_detected=False, publish_dir=publish_dir
    )

    # 创建假的发布目录
    pub_path = Path(publish_dir)
    pub_path.mkdir(parents=True, exist_ok=True)
    pub_path.joinpath("test.mkv").write_text("dummy")

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.post(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    assert data["data"]["status"] == "waiting_user"
    assert "等待用户" in data["data"]["outcome"]
    assert data["data"]["decision_id"] is not None

    # 验证发布目录已删除
    assert not pub_path.exists()

    # 验证任务状态
    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "waiting_user"

        # 验证 AgentDecisionRequest 已创建（post_revoke_action）
        from media_pilot.repository.models import AgentDecisionRequest
        decision = session.scalars(
            select(AgentDecisionRequest)
            .where(AgentDecisionRequest.task_id == task_id)
            .where(AgentDecisionRequest.decision_type == "post_revoke_action")
            .where(AgentDecisionRequest.status == "pending")
            .order_by(AgentDecisionRequest.created_at.desc())
        ).first()
        assert decision is not None
        assert decision.decision_type == "post_revoke_action"
        opts = decision.options if isinstance(decision.options, list) else []
        option_ids = [o["id"] for o in opts]
        assert "reingest_with_new_search" in option_ids
        assert "reingest_with_existing_metadata" in option_ids
        assert "delete_task_input" in option_ids

        # 注: ConfirmationRequest 旧通道已在 replace-legacy-confirmation-with-agent-decisions
        # 完全下线，类型已删除；旧"无 ConfirmationRequest"断言随之失效。
        # 当前 AgentDecisionRequest(decision_type=post_revoke_action) 已是唯一载体。


def test_execute_revoke_delete_task_source_missing(tmp_path: Path):
    """主文件缺失 → 删除发布目录 + 删除任务数据"""
    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    task_id, pd, session_factory = _create_published_task(
        tmp_path, selected_path="/nonexistent/path.mkv", bdmv_detected=False, publish_dir=publish_dir
    )

    pub_path = Path(publish_dir)
    pub_path.mkdir(parents=True, exist_ok=True)
    pub_path.joinpath("test.mkv").write_text("dummy")

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.post(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    assert data["data"]["status"] == "deleted"

    # 验证发布目录已删除
    assert not pub_path.exists()

    # 验证任务已删除
    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is None


def test_execute_revoke_delete_task_bdmv(tmp_path: Path):
    """BDMV → 删除发布目录 + 删除任务数据"""
    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    task_id, pd, session_factory = _create_published_task(
        tmp_path, selected_path=__file__, bdmv_detected=True, publish_dir=publish_dir
    )

    pub_path = Path(publish_dir)
    pub_path.mkdir(parents=True, exist_ok=True)
    pub_path.joinpath("BDMV").mkdir(parents=True, exist_ok=True)

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.post(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    assert data["data"]["status"] == "deleted"

    # 验证发布目录已删除
    assert not pub_path.exists()

    # 验证任务已删除
    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is None


def test_execute_revoke_not_allowed(tmp_path: Path):
    """非已发布任务 → 执行失败"""
    session_factory = _make_session_factory(tmp_path)
    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path="/media/downloads/test.mkv",
            status="needs_confirmation",
            current_step="operator_confirmation",
        ))
        task_id = task.id
        session.commit()

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.post(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "error"
    assert "revoke_not_allowed" == data["messages"][0]["code"]


# ── 取最新记录测试 ──


def test_check_revoke_publish_uses_latest_write_result(tmp_path: Path):
    """同任务多条 WriteResult 时应使用最新记录"""
    import time

    task_id, pd, session_factory = _create_published_task(
        tmp_path, selected_path=__file__, bdmv_detected=False
    )

    # 写入第二条 WriteResult（更新的记录，指向不同的 publish_dir）
    new_publish_dir = str(tmp_path / "library" / "movies" / "Updated Movie (2026)")
    with session_factory() as session:
        time.sleep(0.01)
        wr = WriteResult(
            task_id=task_id,
            status="succeeded",
            payload={"target_dir": new_publish_dir},
        )
        session.add(wr)
        session.commit()

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.get(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["data"]["publish_dir"] == new_publish_dir


def test_check_revoke_publish_uses_latest_source_selection(tmp_path: Path):
    """同任务多条 MediaSourceSelection 时应使用最新记录"""
    import time

    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")

    # 先用 _create_published_task 创建带第一条 selection 的基础数据
    task_id, pd, session_factory = _create_published_task(
        tmp_path, selected_path="/media/downloads/old_path.mkv", bdmv_detected=False,
        publish_dir=publish_dir,
    )

    # 写入第二条 selection（更新的记录，selected_path 指向存在的文件）
    with session_factory() as session:
        time.sleep(0.01)
        sel = MediaSourceSelection(
            task_id=task_id,
            input_path=str(__file__),
            selected_path=__file__,  # __file__ 存在
            payload={"bdmv_detected": False},
        )
        session.add(sel)
        session.commit()

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.get(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 最新 selection 指向 __file__（存在），应判定 source_file_exists=True
    assert data["data"]["source_file_exists"] is True
    assert data["data"]["is_complex_structure"] is False


def test_revoke_creates_agent_decision_not_confirmation(tmp_path: Path):
    """撤销后创建 AgentDecisionRequest (post_revoke_action)"""
    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    task_id, pd, session_factory = _create_published_task(
        tmp_path, selected_path=__file__, bdmv_detected=False, publish_dir=publish_dir
    )

    pub_path = Path(publish_dir)
    pub_path.mkdir(parents=True, exist_ok=True)
    pub_path.joinpath("test.mkv").write_text("dummy")

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.post(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

    # 验证 AgentDecisionRequest 创建
    with session_factory() as session:
        from media_pilot.repository.models import AgentDecisionRequest
        decision = session.scalars(
            select(AgentDecisionRequest)
            .where(AgentDecisionRequest.task_id == task_id)
            .where(AgentDecisionRequest.status == "pending")
            .order_by(AgentDecisionRequest.created_at.desc())
        ).first()
        assert decision is not None
        assert decision.decision_type == "post_revoke_action"

        # 注: ConfirmationRequest 旧通道已下线，类型已删除。


# ── 回归测试: 撤销后重新确认不应卡在旧 WriteResult ──


def test_revoke_cleans_publish_context_preserves_metadata(tmp_path: Path):
    """撤销发布清理 WriteResult/WritePlan/FileAsset，但保留 MetadataDetail"""
    import httpx
    from media_pilot.orchestration.revoke_publish import execute_revoke_publish
    from media_pilot.repository.models import WritePlan as WP, FileAsset as FA, MetadataDetail as MD

    # 1. 创建已发布任务，含完整发布上下文
    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    source_path = tmp_path / "downloads" / "test.mkv"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"movie")

    config = AppConfig(
        downloads_dir=source_path.parent,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)

    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(source_path),
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id, input_path=str(source_path),
            selected_path=str(source_path), payload={"bdmv_detected": False},
        ))
        session.add(WriteResult(
            task_id=task.id, status="succeeded",
            payload={"target_dir": publish_dir},
        ))
        session.add(WP(
            task_id=task.id, target_dir=publish_dir,
            target_file=str(Path(publish_dir) / "test.mkv"),
        ))
        session.add(FA(task_id=task.id, role="library_video", path=str(source_path)))
        session.add(MD(
            task_id=task.id, provider="tmdb", provider_id="movie:test-movie:2026",
            media_type="movie", title="Test Movie", year=2026,
        ))
        session.commit()
        task_id = task.id

    # 2. 执行撤销发布（主文件存在分支）
    with session_factory() as session:
        result = execute_revoke_publish(session, task_id=task_id)
        assert result.status == "waiting_user"
        assert result.decision_id is not None
        session.commit()

    # 3. 验证发布上下文已清理
    with session_factory() as session:
        assert session.scalars(
            select(WriteResult).where(WriteResult.task_id == task_id)
        ).first() is None
        assert session.scalars(
            select(WP).where(WP.task_id == task_id)
        ).first() is None
        assert session.scalars(
            select(FileAsset).where(FileAsset.task_id == task_id)
        ).first() is None
        # MetadataDetail 保留以供 reingest_with_existing_metadata 使用
        assert session.scalars(
            select(MetadataDetail).where(MetadataDetail.task_id == task_id)
        ).first() is not None


def test_revoke_delete_task_data_removes_write_plan_before_task(tmp_path: Path):
    """源文件缺失导致撤销删除任务数据时, WritePlan 必须先删避免 FK 残留。"""
    from media_pilot.orchestration.revoke_publish import execute_revoke_publish
    from media_pilot.repository.models import WritePlan as WP

    publish_dir = str(tmp_path / "library" / "movies" / "Published Movie (2026)")
    missing_source = tmp_path / "downloads" / "missing.mkv"
    Path(publish_dir).mkdir(parents=True, exist_ok=True)
    Path(publish_dir).joinpath("Published Movie (2026).mkv").write_bytes(b"movie")

    config = AppConfig(
        downloads_dir=missing_source.parent,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    for d in (config.downloads_dir, config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)

    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(missing_source),
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(missing_source),
            selected_path=str(missing_source),
            payload={},
        ))
        session.add(WriteResult(
            task_id=task.id,
            status="succeeded",
            payload={"target_dir": publish_dir},
        ))
        session.add(WP(
            task_id=task.id,
            target_dir=publish_dir,
            target_file=str(Path(publish_dir) / "Published Movie (2026).mkv"),
            nfo_path=str(Path(publish_dir) / "Published Movie (2026).nfo"),
            payload={},
        ))
        session.commit()
        task_id = task.id

    with session_factory() as session:
        result = execute_revoke_publish(session, task_id=task_id)
        assert result.status == "deleted"

    with session_factory() as session:
        assert session.get(IngestTask, task_id) is None
        assert session.scalars(
            select(WP).where(WP.task_id == task_id)
        ).first() is None


def test_execute_revoke_skip_sets_processing_status(tmp_path: Path):
    """撤销发布(skip_post_revoke_decision=True) 将任务置为 processing +
    post_revoke_reingest，而非停留在 agent_running 或 library_import_complete。"""
    from media_pilot.orchestration.revoke_publish import execute_revoke_publish
    from media_pilot.orchestration.state_machine import IngestTaskStatus

    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    source_path = tmp_path / "downloads" / "test.mkv"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"movie")

    config = AppConfig(
        downloads_dir=source_path.parent,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)

    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(source_path),
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id, input_path=str(source_path),
            selected_path=str(source_path), payload={"bdmv_detected": False},
        ))
        session.add(WriteResult(
            task_id=task.id, status="succeeded",
            payload={"target_dir": publish_dir},
        ))
        session.commit()
        task_id = task.id

    # 执行撤回(skip_post_revoke_decision=True)
    with session_factory() as session:
        result = execute_revoke_publish(
            session, task_id=task_id, skip_post_revoke_decision=True,
        )
        session.commit()

    assert result.status == "completed"
    assert result.decision_id is None

    # 验证：任务不再停在 library_import_complete 或 agent_running，
    # 而是合理的中间状态 processing + post_revoke_reingest
    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == IngestTaskStatus.PROCESSING
        assert task.current_step == "post_revoke_reingest"

        # 发布上下文应已清理
        assert session.scalars(
            select(WriteResult).where(WriteResult.task_id == task_id)
        ).first() is None


# ── 删除任务输入测试 ──


def test_delete_input_preview_resolves_path(tmp_path: Path):
    """预检正确解析任务输入路径"""
    from media_pilot.orchestration.delete_unpublished import preview_delete_input

    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        downloads_dir=downloads,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(downloads / "test.mkv"),
            status="waiting_user",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(downloads / "test.mkv"),
            selected_path=str(downloads / "test.mkv"),
        ))
        session.commit()
        task_id = task.id

    with session_factory() as session:
        result = preview_delete_input(session, task_id, config)

    assert result.allowed is True
    assert result.target_path is not None
    assert "test.mkv" in result.target_path


def test_delete_input_preview_refuses_outside_roots(tmp_path: Path):
    """越界路径拒绝删除"""
    from media_pilot.orchestration.delete_unpublished import preview_delete_input

    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        downloads_dir=downloads,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path="/etc/passwd",
            status="waiting_user",
        ))
        session.commit()
        task_id = task.id

    with session_factory() as session:
        result = preview_delete_input(session, task_id, config)

    assert result.allowed is False
    assert "不在受控根目录内" in result.outcome_description


def test_delete_input_preview_refuses_root_directory(tmp_path: Path):
    """受控根目录本身拒绝删除"""
    from media_pilot.orchestration.delete_unpublished import preview_delete_input

    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        downloads_dir=downloads,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(downloads),
            status="waiting_user",
        ))
        session.commit()
        task_id = task.id

    with session_factory() as session:
        result = preview_delete_input(session, task_id, config)

    assert result.allowed is False


def test_delete_input_execution_with_confirmation(tmp_path: Path):
    """二次确认后执行删除"""
    from media_pilot.orchestration.delete_unpublished import (
        execute_delete_input,
        preview_delete_input,
    )

    # 使用 tmp_path 下的子目录作为 downloads_dir
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        downloads_dir=downloads,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_file = downloads / "to_delete.mkv"
    source_file.write_text("test content")

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(source_file),
            status="waiting_user",
        ))
        session.commit()
        task_id = task.id

    # 预检应允许
    with session_factory() as session:
        preview = preview_delete_input(session, task_id, config)
        assert preview.allowed is True

    # 执行删除
    with session_factory() as session:
        result = execute_delete_input(session, task_id, config)
        assert result["status"] == "deleted"

    # 文件已删除
    assert not source_file.exists()

    # 任务标记为终态而非物理删除
    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "deleted"

        # OperationRecord 已保留
        from media_pilot.repository.models import OperationRecord
        op = session.scalars(
            select(OperationRecord).where(OperationRecord.task_id == task_id)
        ).first()
        assert op is not None
        assert op.operation_type == "delete_task_input"


# ── 人工辅助检索测试 ──


def test_manual_select_persists_candidate_without_confirmation(tmp_path: Path):
    """人工选择候选后直接落库 MediaCandidate"""
    from media_pilot.services.manual_selection import submit_manual_selection

    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        downloads_dir=downloads,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(downloads / "test.mkv"),
            status="agent_running",
        ))
        task_id = task.id

        # 创建 AgentRun 以便写入系统消息
        from media_pilot.repository.repositories import AgentRunCreate, AgentRunRepository
        run_repo = AgentRunRepository(session)
        run_repo.create(AgentRunCreate(task_id=task_id, current_step="agent_start"))
        session.commit()

    with session_factory() as session:
        result = submit_manual_selection(
            session=session,
            config=config,
            task_id=task_id,
            provider="tmdb",
            provider_id="movie:123",
            title="Test Movie",
            year=2026,
            media_type="movie",
        )
        session.commit()

    # 候选已保存（无 TMDB adapter 时 metadata detail 可能失败，但候选应保存）
    assert result.status in ("published", "waiting_user", "saved")

    with session_factory() as session:
        # 验证 MediaCandidate 已创建
        from media_pilot.repository.models import MediaCandidate
        candidate = session.scalars(
            select(MediaCandidate).where(MediaCandidate.task_id == task_id)
        ).first()
        assert candidate is not None
        assert candidate.title == "Test Movie"

        # 注: ConfirmationRequest 旧通道已下线，类型已删除。


def test_reingest_with_existing_metadata_fails_without_detail(tmp_path: Path):
    """无 MetadataDetail 时拒绝沿用现有元数据"""
    import pytest
    from media_pilot.services.post_revoke_handler import handle_reingest_with_existing_metadata

    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        downloads_dir=downloads,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(downloads / "test.mkv"),
            status="waiting_user",
        ))
        task_id = task.id
        session.commit()

    with session_factory() as session:
        with pytest.raises(ValueError) as exc_info:
            handle_reingest_with_existing_metadata(
                session=session, config=config, task_id=task_id,
            )
        err = exc_info.value.args[0]
        assert err["status_code"] == 400
        assert "没有可用的元数据详情" in err["detail"]


# ── post_revoke_action 决策列表与回复测试 ──


def test_list_agent_decisions_after_revoke_returns_array(tmp_path: Path):
    """撤回后 list agent-decisions 返回 options 为数组（非 {options:[...]}）。"""
    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    task_id, _pd, session_factory = _create_published_task(
        tmp_path, selected_path=__file__, bdmv_detected=False, publish_dir=publish_dir
    )

    pub_path = Path(publish_dir)
    pub_path.mkdir(parents=True, exist_ok=True)
    pub_path.joinpath("test.mkv").write_text("dummy")

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)

    client = TestClient(app)
    resp = client.post(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

    # 通过 API 列出 decisions
    resp2 = client.get(f"/api/v1/tasks/{task_id}/agent-decisions")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["status"] == "success"
    assert len(data["data"]) == 1

    decision = data["data"][0]
    assert decision["decision_type"] == "post_revoke_action"
    assert decision["status"] == "pending"

    # options 必须是数组
    opts = decision["options"]
    assert isinstance(opts, list), f"Expected list, got {type(opts)}"
    assert len(opts) == 3
    option_ids = [o["id"] for o in opts]
    assert "reingest_with_new_search" in option_ids
    assert "reingest_with_existing_metadata" in option_ids
    assert "delete_task_input" in option_ids


def test_post_revoke_reply_reingest_with_new_search(tmp_path: Path):
    """用户回复 reingest_with_new_search → Agent 从搜索重新开始，API 返回 success。"""
    from media_pilot.repository.models import AgentDecisionRequest
    from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
    from tests.agent_runner_helpers import MockLLMClient

    # 1. 创建已发布任务并执行撤回
    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    task_id, _pd, session_factory = _create_published_task(
        tmp_path, selected_path=__file__, bdmv_detected=False, publish_dir=publish_dir
    )
    pub_path = Path(publish_dir)
    pub_path.mkdir(parents=True, exist_ok=True)
    pub_path.joinpath("test.mkv").write_text("dummy")

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)
    client = TestClient(app)
    resp = client.post(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

    # 2. 获取 decision_id
    with session_factory() as session:
        decision = session.scalars(
            select(AgentDecisionRequest)
            .where(AgentDecisionRequest.task_id == task_id)
            .where(AgentDecisionRequest.status == "pending")
            .order_by(AgentDecisionRequest.created_at.desc())
        ).first()
        assert decision is not None
        decision_id = decision.id

    # 3. 用 MockLLM 回复 reingest_with_new_search
    mock = MockLLMClient()
    mock.add_text_response("已从搜索阶段重新开始处理任务。")

    with session_factory() as session:
        reply = ReplyInput(decision_id=decision_id, option_id="reingest_with_new_search")
        result = reply_to_decision(
            session=session, config=config, reply=reply,
            mock_llm_client=mock,
        )
        session.commit()

    assert result.status in ("completed", "waiting_user")
    assert result.run_id != ""

    # 4. 验证决策已标记为 decided
    with session_factory() as session:
        d = session.get(AgentDecisionRequest, decision_id)
        assert d is not None
        assert d.status == "decided"
        assert d.decision == {"option_id": "reingest_with_new_search", "type": "option"}


def test_post_revoke_reply_reingest_with_existing_metadata(tmp_path: Path):
    """用户回复 reingest_with_existing_metadata → 保留 MetadataDetail 重新入库。"""
    from media_pilot.repository.models import AgentDecisionRequest, MetadataDetail as MD
    from media_pilot.services.decision_reply import ReplyInput, reply_to_decision
    from tests.agent_runner_helpers import MockLLMClient

    # 1. 创建已发布任务（含 MetadataDetail）并执行撤回
    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    source_path = tmp_path / "downloads" / "test.mkv"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"movie")

    config = AppConfig(
        downloads_dir=source_path.parent,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)

    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(source_path),
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id, input_path=str(source_path),
            selected_path=str(source_path), payload={"bdmv_detected": False},
        ))
        session.add(WriteResult(
            task_id=task.id, status="succeeded",
            payload={"target_dir": publish_dir},
        ))
        session.add(MD(
            task_id=task.id, provider="tmdb", provider_id="movie:test:2026",
            media_type="movie", title="Test Movie", year=2026,
        ))
        session.commit()
        task_id = task.id

    # 创建发布目录
    pub_path = Path(publish_dir)
    pub_path.mkdir(parents=True, exist_ok=True)
    pub_path.joinpath("test.mkv").write_text("dummy")

    # 执行撤回
    with session_factory() as session:
        from media_pilot.orchestration.revoke_publish import execute_revoke_publish
        result = execute_revoke_publish(session, task_id=task_id)
        assert result.status == "waiting_user"
        decision_id = result.decision_id
        session.commit()

    # 回复
    mock = MockLLMClient()
    mock.add_text_response("已从发布阶段继续处理任务。")

    with session_factory() as session:
        reply = ReplyInput(decision_id=decision_id, option_id="reingest_with_existing_metadata")
        result = reply_to_decision(
            session=session, config=config, reply=reply,
            mock_llm_client=mock,
        )
        session.commit()

    assert result.status in ("completed", "waiting_user")
    assert result.run_id != ""


def test_post_revoke_reply_delete_input_preview(tmp_path: Path):
    """用户回复 delete_task_input → API 返回 delete_input_preview 状态。"""
    from media_pilot.repository.models import AgentDecisionRequest
    from media_pilot.services.decision_reply import ReplyInput, reply_to_decision

    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    task_id, _pd, session_factory = _create_published_task(
        tmp_path, selected_path=__file__, bdmv_detected=False, publish_dir=publish_dir
    )
    pub_path = Path(publish_dir)
    pub_path.mkdir(parents=True, exist_ok=True)
    pub_path.joinpath("test.mkv").write_text("dummy")

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)
    client = TestClient(app)
    resp = client.post(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200

    # 获取 decision_id
    with session_factory() as session:
        decision = session.scalars(
            select(AgentDecisionRequest)
            .where(AgentDecisionRequest.task_id == task_id)
            .where(AgentDecisionRequest.status == "pending")
            .order_by(AgentDecisionRequest.created_at.desc())
        ).first()
        decision_id = decision.id

    # 通过 API 回复 delete_task_input
    resp2 = client.post(f"/api/v1/agent-decisions/{decision_id}/reply", json={
        "option_id": "delete_task_input",
    })
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["status"] == "success"
    assert data["data"]["status"] == "delete_input_preview"
    assert data["data"]["task_id"] == task_id

    # 验证决策已保存
    with session_factory() as session:
        d = session.get(AgentDecisionRequest, decision_id)
        assert d.status == "decided"


def test_post_revoke_reply_invalid_option_400(tmp_path: Path):
    """回复非法的 option_id → 400"""
    from media_pilot.repository.models import AgentDecisionRequest

    publish_dir = str(tmp_path / "library" / "movies" / "Test Movie (2026)")
    task_id, _pd, session_factory = _create_published_task(
        tmp_path, selected_path=__file__, bdmv_detected=False, publish_dir=publish_dir
    )
    pub_path = Path(publish_dir)
    pub_path.mkdir(parents=True, exist_ok=True)
    pub_path.joinpath("test.mkv").write_text("dummy")

    config = _make_config(tmp_path)
    app = create_app(config=config, session_factory=session_factory)
    client = TestClient(app)
    resp = client.post(f"/api/v1/tasks/{task_id}/revoke-publish")
    assert resp.status_code == 200

    with session_factory() as session:
        decision = session.scalars(
            select(AgentDecisionRequest)
            .where(AgentDecisionRequest.task_id == task_id)
            .where(AgentDecisionRequest.status == "pending")
            .order_by(AgentDecisionRequest.created_at.desc())
        ).first()
        decision_id = decision.id

    # 回复非法 option
    resp2 = client.post(f"/api/v1/agent-decisions/{decision_id}/reply", json={
        "option_id": "nonexistent_option",
    })
    assert resp2.status_code == 400


def test_delete_input_preserves_audit_record(tmp_path: Path):
    """删除任务输入后保留 OperationRecord 和 IngestTask 主记录。"""
    from media_pilot.orchestration.delete_unpublished import execute_delete_input
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        downloads_dir=downloads,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_file = downloads / "to_delete.mkv"
    source_file.write_text("test content")

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(source_file),
            status="waiting_user",
            current_step="some_step",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(source_file),
            selected_path=str(source_file),
        ))
        asset = FileAsset(
            task_id=task.id,
            role="library_video",
            path=str(source_file),
            size_bytes=source_file.stat().st_size,
        )
        session.add(asset)
        session.flush()
        session.add(OperationRecord(
            task_id=task.id,
            file_asset_id=asset.id,
            operation_type="copy_to_staging",
            permission_level="write",
            target_path=str(source_file),
            status="succeeded",
            details={},
        ))
        session.commit()
        task_id = task.id

    # 执行删除
    with session_factory() as session:
        result = execute_delete_input(session, task_id, config)
        assert result["status"] == "deleted"

    assert not source_file.exists()

    # 验证审计保留
    with session_factory() as session:
        # IngestTask 标记为终态，未物理删除
        task = session.get(IngestTask, task_id)
        assert task is not None
        assert task.status == "deleted"
        assert task.current_step == "delete_task_input"

        # OperationRecord 已保留
        ops = session.scalars(
            select(OperationRecord).where(OperationRecord.task_id == task_id)
        ).all()
        assert len(ops) >= 1
        assert any(o.operation_type == "delete_task_input" for o in ops)
        copy_op = next(o for o in ops if o.operation_type == "copy_to_staging")
        assert copy_op.file_asset_id is None
        assert session.scalars(
            select(FileAsset).where(FileAsset.task_id == task_id)
        ).first() is None


# ── _quick_publish 状态检查测试 ──


def test_quick_publish_checks_execute_movie_write_failed_status(tmp_path: Path):
    """execute_movie_write 返回 failed → 不进入 library_import_complete。"""
    from unittest.mock import patch

    from media_pilot.orchestration.jellyfin_movie_writer import MovieWriteResult
    from media_pilot.services.manual_selection import _quick_publish

    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    source_file = downloads / "test.mkv"
    source_file.write_bytes(b"movie content")

    config = AppConfig(
        downloads_dir=downloads,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(source_file),
            status="agent_running",
            current_step="agent_running",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(source_file),
            selected_path=str(source_file),
        ))
        from media_pilot.repository.repositories import MetadataDetailRepository
        MetadataDetailRepository(session).save(
            task_id=task.id,
            provider="tmdb",
            provider_id="movie:123",
            media_type="movie",
            title="Test Movie",
            original_title="Test Movie",
            year=2026,
            payload={"overview": "Test overview", "images": {}},
        )
        session.commit()
        task_id = task.id

    # Mock execute_movie_write to return failed
    with patch(
        "media_pilot.orchestration.jellyfin_movie_writer.execute_movie_write",
        return_value=MovieWriteResult(status="failed", warnings=[]),
    ):
        with session_factory() as session:
            from media_pilot.repository.repositories import (
                MetadataDetailRepository,
                MediaSourceSelectionRepository,
            )

            task_repo = IngestTaskRepository(session)
            task = task_repo.get(task_id)

            from media_pilot.services.publish_plan_draft import _orm_detail_to_adapter
            detail_repo = MetadataDetailRepository(session)
            orm_detail = detail_repo.get_for_task(task_id)
            adapter_detail = _orm_detail_to_adapter(orm_detail)

            sel_repo = MediaSourceSelectionRepository(session)

            result = _quick_publish(session, config, task_id)
            session.commit()

    # 必须返回失败信息，任务不得进入 library_import_complete
    assert result.kind == "failed"
    assert "write_failed" in result.reason

    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task.status != "library_import_complete"


def test_quick_publish_happy_path_creates_assets_and_completes(tmp_path: Path):
    """execute_movie_write 返回 succeeded/warning → 任务完成，WriteResult/FileAsset 存在。"""
    from unittest.mock import patch

    from media_pilot.orchestration.jellyfin_movie_writer import MovieWriteResult
    from media_pilot.repository.models import FileAsset, WriteResult, WritePlan
    from media_pilot.services.manual_selection import _quick_publish

    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    source_file = downloads / "test.mkv"
    source_file.write_bytes(b"movie content")

    config = AppConfig(
        downloads_dir=downloads,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(source_file),
            status="agent_running",
            current_step="agent_running",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(source_file),
            selected_path=str(source_file),
        ))
        from media_pilot.repository.repositories import MetadataDetailRepository
        MetadataDetailRepository(session).save(
            task_id=task.id,
            provider="tmdb",
            provider_id="movie:123",
            media_type="movie",
            title="Test Movie",
            original_title="Test Movie",
            year=2026,
            payload={"overview": "Test overview", "images": {}},
        )
        session.commit()
        task_id = task.id

    # A side effect that also writes WritePlan + FileAsset + WriteResult to the session
    def fake_execute_success(
        session, *, task_id, source_path, detail, plan, client, **kwargs,
    ):
        wp = WritePlan(
            task_id=task_id,
            target_dir=str(plan.target_dir),
            target_file=str(plan.target_file),
            nfo_path=str(plan.nfo_path),
            payload={"overview": "Test overview", "images": {}},
        )
        session.add(wp)
        fa = FileAsset(
            task_id=task_id,
            role="library_video",
            path=str(plan.target_file),
            size_bytes=1024,
        )
        session.add(fa)
        from media_pilot.repository.repositories import WriteResultRepository
        WriteResultRepository(session).save(
            task_id,
            status="succeeded",
            payload={
                "target_dir": str(plan.final_target_dir),
                "target_file": str(plan.final_target_file),
            },
        )
        return MovieWriteResult(status="succeeded", warnings=[])

    with patch(
        "media_pilot.orchestration.jellyfin_movie_writer.execute_movie_write",
        side_effect=fake_execute_success,
    ):
        with session_factory() as session:
            from media_pilot.repository.repositories import (
                MetadataDetailRepository,
                MediaSourceSelectionRepository,
            )

            task_repo = IngestTaskRepository(session)
            task = task_repo.get(task_id)

            from media_pilot.services.publish_plan_draft import _orm_detail_to_adapter
            detail_repo = MetadataDetailRepository(session)
            orm_detail = detail_repo.get_for_task(task_id)
            adapter_detail = _orm_detail_to_adapter(orm_detail)

            sel_repo = MediaSourceSelectionRepository(session)

            result = _quick_publish(session, config, task_id)
            session.commit()

    assert result.kind == "published"

    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task.status == "library_import_complete"

        # Verify WritePlan, WriteResult and FileAsset exist
        plans = session.scalars(
            select(WritePlan).where(WritePlan.task_id == task_id)
        ).all()
        assert len(plans) >= 1

        results = session.scalars(
            select(WriteResult).where(WriteResult.task_id == task_id)
        ).all()
        assert len(results) >= 1
        assert any(r.status == "succeeded" for r in results)

        assets = session.scalars(
            select(FileAsset).where(FileAsset.task_id == task_id)
        ).all()
        assert len(assets) >= 1


# ── deleted 状态前端渲染测试 ──


def test_deleted_status_renders_in_task_list(tmp_path: Path):
    """delete_task_input 后任务列表 API 正常返回 deleted 状态。"""
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        downloads_dir=downloads,
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
    )
    for d in (config.workspace_dir, config.movies_dir, config.shows_dir, config.database_dir):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    session_factory = create_session_factory(config)

    source_file = downloads / "to_delete.mkv"
    source_file.write_text("test content")

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(source_file),
            status="waiting_user",
            current_step="some_step",
        ))
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(source_file),
            selected_path=str(source_file),
        ))
        session.commit()
        task_id = task.id

    # 执行删除
    from media_pilot.orchestration.delete_unpublished import execute_delete_input
    with session_factory() as session:
        result = execute_delete_input(session, task_id, config)
        assert result["status"] == "deleted"

    # 通过 API 获取任务详情
    from fastapi.testclient import TestClient
    from media_pilot.app import create_app

    app = create_app(config=config, session_factory=session_factory)
    client = TestClient(app)

    resp = client.get(f"/api/v1/tasks/{task_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["data"]["task"]["status_summary"]["status"] == "deleted"

    # 任务列表也能返回 deleted 任务
    resp_list = client.get("/api/v1/tasks")
    assert resp_list.status_code == 200
    list_data = resp_list.json()
    assert list_data["status"] == "success"
