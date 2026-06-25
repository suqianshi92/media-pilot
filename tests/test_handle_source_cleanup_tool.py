"""handle_source_cleanup Agent WRITE 工具测试 — 策略分支、状态门禁、预检失败、失败不覆盖任务状态."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


# ── fixtures / helpers ─────────────────────────────────────────────────


@pytest.fixture
def config_with_trash(tmp_path: Path):
    from media_pilot.config.settings import AppConfig

    downloads = tmp_path / "downloads"
    watch = tmp_path / "watch"
    workspace = tmp_path / "workspace"
    movies = tmp_path / "library" / "movies"
    shows = tmp_path / "library" / "shows"
    trash = tmp_path / "trash"
    db = tmp_path / "db"
    for d in (downloads, watch, workspace, movies, shows, db):
        d.mkdir(parents=True, exist_ok=True)
    trash.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        downloads_dir=downloads,
        watch_dir=watch,
        workspace_dir=workspace,
        movies_dir=movies,
        shows_dir=shows,
        database_dir=db,
        trash_dir=trash,
    )


@pytest.fixture
def session_factory(config_with_trash):
    from media_pilot.repository.database import (
        create_session_factory,
        initialize_database,
    )

    initialize_database(config_with_trash)
    return create_session_factory(config_with_trash)


def _make_tool_context(session, config, task_id: str, run_id: str | None = None):
    from media_pilot.agent.tools.base import ToolContext

    return ToolContext(
        session=session, config=config, task_id=task_id, run_id=run_id,
    )


def _create_published_task(
    session_factory,
    *,
    source_path: str | None = None,
    write_status: str = "succeeded",
) -> str:
    """创建一个已完成入库 (library_import_complete) 的任务 + 成功 WriteResult."""
    from media_pilot.repository.models import WriteResult
    from media_pilot.repository.repositories import (
        IngestTaskCreate,
        IngestTaskRepository,
    )

    with session_factory() as session:
        repo = IngestTaskRepository(session)
        task = repo.create(IngestTaskCreate(
            source_path=source_path or "/tmp/source/movie.mkv",
            status="library_import_complete",
            current_step="library_import_complete",
            media_type="movie",
        ))
        session.add(WriteResult(
            task_id=task.id,
            status=write_status,
            payload={"final_target_dir": "/tmp/library/Movie (2026)"},
        ))
        session.commit()
        return task.id


def _set_policy(session_factory, policy: str) -> None:
    from media_pilot.services.app_settings import (
        AppSettings,
        AppSettingsService,
    )

    with session_factory() as session:
        svc = AppSettingsService(session_factory)
        current = svc.read_using_session(session)
        svc.save(AppSettings(
            enabled_metadata_profiles=list(current.enabled_metadata_profiles),
            enabled_library_formats=list(current.enabled_library_formats),
            suspicious_file_threshold_bytes=current.suspicious_file_threshold_bytes,
            metadata_auto_confirm_confidence=current.metadata_auto_confirm_confidence,
            metadata_auto_confirm_margin=current.metadata_auto_confirm_margin,
            preferred_metadata_language=current.preferred_metadata_language,
            source_cleanup_policy=policy,
        ))


def _execute_tool(context, input_data: dict):
    from media_pilot.agent.tools.write import _handle_source_cleanup
    return _handle_source_cleanup(context, input_data)


def _create_task_with_status(
    session_factory,
    *,
    status: str,
    current_step: str,
    source_path: str,
) -> str:
    from media_pilot.repository.repositories import (
        IngestTaskCreate,
        IngestTaskRepository,
    )

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=source_path,
            status=status,
            current_step=current_step,
        ))
        session.commit()
        return task.id


def _create_published_task_with_write_result(
    session_factory,
    *,
    write_status: str,
    source_path: str = "/tmp/source/movie.mkv",
) -> str:
    from media_pilot.repository.models import WriteResult
    from media_pilot.repository.repositories import (
        IngestTaskCreate,
        IngestTaskRepository,
    )

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=source_path,
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(WriteResult(
            task_id=task.id,
            status=write_status,
            payload={},
        ))
        session.commit()
        return task.id


# ── 任务状态门禁 ───────────────────────────────────────────────────────


def test_handle_source_cleanup_refuses_non_published_task(
    session_factory, config_with_trash
) -> None:
    """任务处于 pre-publish 状态 (discovered / agent_start / agent_failed) → 拒绝清理."""
    task_id = _create_task_with_status(
        session_factory,
        status="discovered",
        current_step="agent_start",
        source_path="/tmp/source/movie.mkv",
    )

    with session_factory() as session:
        ctx = _make_tool_context(session, config_with_trash, task_id)
        result = _execute_tool(ctx, {"task_id": task_id})

    assert result.status == "failure"
    assert "post-publish" in result.summary
    assert result.data["task_status"] == "discovered"


def test_handle_source_cleanup_refuses_missing_write_result(
    session_factory, config_with_trash
) -> None:
    """任务 library_import_complete 但没有 WriteResult → 拒绝."""
    task_id = _create_task_with_status(
        session_factory,
        status="library_import_complete",
        current_step="library_import_complete",
        source_path="/tmp/source/movie.mkv",
    )

    with session_factory() as session:
        ctx = _make_tool_context(session, config_with_trash, task_id)
        result = _execute_tool(ctx, {"task_id": task_id})

    assert result.status == "failure"
    assert "write result" in result.summary


def test_handle_source_cleanup_refuses_failed_write_result(
    session_factory, config_with_trash
) -> None:
    """WriteResult.status == "failed" → 拒绝."""
    task_id = _create_published_task_with_write_result(
        session_factory, write_status="failed",
    )

    with session_factory() as session:
        ctx = _make_tool_context(session, config_with_trash, task_id)
        result = _execute_tool(ctx, {"task_id": task_id})

    assert result.status == "failure"
    assert result.data["write_result_status"] == "failed"


def test_handle_source_cleanup_refuses_unknown_task(
    session_factory, config_with_trash
) -> None:
    with session_factory() as session:
        ctx = _make_tool_context(session, config_with_trash, "nonexistent")
        result = _execute_tool(ctx, {"task_id": "nonexistent"})

    assert result.status == "failure"
    assert "not found" in result.summary


# ── keep 策略 ──────────────────────────────────────────────────────────


def test_keep_policy_records_kept_operation(
    session_factory, config_with_trash
) -> None:
    """keep 策略: 写入 source_input_kept OperationRecord, 不移动文件."""
    from sqlalchemy import select
    from media_pilot.repository.models import (
        OperationRecord,
        WriteResult,
    )
    from media_pilot.repository.repositories import (
        IngestTaskCreate,
        IngestTaskRepository,
    )

    src = config_with_trash.downloads_dir / "movie.mkv"
    src.write_bytes(b"x")

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(src),
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(WriteResult(task_id=task.id, status="succeeded", payload={}))
        session.commit()
        task_id = task.id

    _set_policy(session_factory, "keep")

    with session_factory() as session:
        ctx = _make_tool_context(session, config_with_trash, task_id)
        result = _execute_tool(ctx, {"task_id": task_id})
        session.commit()

    assert result.status == "success"
    assert result.data["action"] == "kept"
    assert src.exists(), "keep 策略不应移动源文件"

    with session_factory() as session:
        op = session.scalars(
            select(OperationRecord).where(OperationRecord.task_id == task_id)
        ).first()
        assert op is not None
        assert op.operation_type == "source_input_kept"
        assert op.status == "succeeded"


def test_keep_policy_with_directory_input_records_input_path_not_selected_path(
    session_factory, config_with_trash
) -> None:
    """目录输入: keep 策略记录的 source_path 必须是 input_path (整块任务输入),
    而不是 selected_path (主视频单文件). 与 trash 预检使用的任务输入节点一致."""
    from sqlalchemy import select
    from media_pilot.repository.models import (
        MediaSourceSelection,
        OperationRecord,
        WriteResult,
    )
    from media_pilot.repository.repositories import (
        IngestTaskCreate,
        IngestTaskRepository,
    )

    src_dir = config_with_trash.downloads_dir / "movie"
    src_dir.mkdir()
    main_mkv = src_dir / "movie.mkv"
    main_mkv.write_bytes(b"x")
    (src_dir / "extras.txt").write_bytes(b"y")

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(src_dir),
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(WriteResult(task_id=task.id, status="succeeded", payload={}))
        # input_path = 整块任务输入 (目录), selected_path = 主视频单文件.
        # 与"扫描/导入时记录的整块输入"语义保持一致.
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(src_dir),
            selected_path=str(main_mkv),
            confidence=1.0,
            reason="auto",
            payload={},
        ))
        session.commit()
        task_id = task.id

    _set_policy(session_factory, "keep")

    with session_factory() as session:
        ctx = _make_tool_context(session, config_with_trash, task_id)
        result = _execute_tool(ctx, {"task_id": task_id})
        session.commit()

    assert result.status == "success"

    with session_factory() as session:
        op = session.scalars(
            select(OperationRecord).where(
                OperationRecord.task_id == task_id,
                OperationRecord.operation_type == "source_input_kept",
            )
        ).first()
        assert op is not None
        # 关键断言: 记录的必须是整块任务输入节点 (目录), 而不是主视频单文件.
        assert op.source_path == str(src_dir), (
            f"keep 策略应记录任务输入节点 (目录), 实际={op.source_path!r}"
        )
        assert op.source_path != str(main_mkv), (
            "不能误用 selected_path (主视频单文件) 代表任务输入节点"
        )


# ── ask 策略 ───────────────────────────────────────────────────────────


def test_ask_policy_creates_source_cleanup_decision(
    session_factory, config_with_trash
) -> None:
    """ask 策略: 创建 source_cleanup_action decision, run 切到 waiting_user."""
    task_id = _create_published_task(session_factory)

    _set_policy(session_factory, "ask")

    with session_factory() as session:
        ctx = _make_tool_context(session, config_with_trash, task_id)
        result = _execute_tool(ctx, {"task_id": task_id})
        session.commit()

    assert result.status == "success"
    assert result.data["decision_type"] == "source_cleanup_action"
    decision_id = result.data["decision_id"]

    with session_factory() as session:
        from media_pilot.repository.models import (
            AgentDecisionRequest,
            AgentRun,
            IngestTask,
        )
        decision = session.get(AgentDecisionRequest, decision_id)
        assert decision is not None
        assert decision.decision_type == "source_cleanup_action"
        option_ids = {opt["id"] for opt in decision.options}
        assert option_ids == {"keep_input", "trash_input", "delete_input"}
        assert decision.free_text_allowed is False

        run = session.get(AgentRun, decision.run_id)
        assert run is not None
        assert run.status == "waiting_user"
        assert run.current_step == "source_cleanup_decision"

        task = session.get(IngestTask, task_id)
        assert task.status == "library_import_complete", (
            "ask 决策不影响 task.status"
        )


# ── trash 策略 ─────────────────────────────────────────────────────────


def test_trash_policy_moves_file_and_records(
    session_factory, config_with_trash
) -> None:
    """trash 策略 + 预检通过 → 移动文件 + 写 source_input_trashed OperationRecord."""
    from sqlalchemy import select
    from media_pilot.repository.models import (
        OperationRecord,
        WriteResult,
    )
    from media_pilot.repository.repositories import (
        IngestTaskCreate,
        IngestTaskRepository,
    )

    src = config_with_trash.downloads_dir / "movie.mkv"
    src.write_bytes(b"content")

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(src),
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(WriteResult(task_id=task.id, status="succeeded", payload={}))
        session.commit()
        task_id = task.id

    _set_policy(session_factory, "trash")

    with session_factory() as session:
        ctx = _make_tool_context(session, config_with_trash, task_id)
        result = _execute_tool(ctx, {"task_id": task_id})
        session.commit()

    assert result.status == "success"
    assert result.data["action"] == "trashed"
    moved = Path(result.data["trash_target"])
    assert moved.exists()
    assert not src.exists()
    assert moved.read_bytes() == b"content"

    with session_factory() as session:
        ops = session.scalars(
            select(OperationRecord).where(OperationRecord.task_id == task_id)
        ).all()
        types = [op.operation_type for op in ops]
        assert "source_input_trashed" in types


def test_trash_policy_with_preflight_failure_degrades_to_ask(
    session_factory, config_with_trash
) -> None:
    """trash 预检失败 → 降级为 ask, 写 source_cleanup_action decision."""
    # 任务指向 downloads 根目录本身 → 预检应拒绝
    task_id = _create_published_task(
        session_factory,
        source_path=str(config_with_trash.downloads_dir),
    )

    _set_policy(session_factory, "trash")

    with session_factory() as session:
        ctx = _make_tool_context(session, config_with_trash, task_id)
        result = _execute_tool(ctx, {"task_id": task_id})
        session.commit()

    assert result.status == "success"
    assert result.data["decision_requested"] is True
    assert result.data["preflight_reason"] == "refuse_protected_root"
    # 文件未移动
    assert config_with_trash.downloads_dir.exists()


def test_trash_policy_with_no_trash_dir_degrades_to_ask(
    tmp_path,
) -> None:
    """trash_dir 未配置时, trash 策略降级为 ask."""
    from media_pilot.config.settings import AppConfig
    from media_pilot.repository.database import (
        create_session_factory,
        initialize_database,
    )

    downloads = tmp_path / "downloads"
    watch = tmp_path / "watch"
    workspace = tmp_path / "workspace"
    movies = tmp_path / "library" / "movies"
    shows = tmp_path / "library" / "shows"
    db = tmp_path / "db"
    for d in (downloads, watch, workspace, movies, shows, db):
        d.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        downloads_dir=downloads,
        watch_dir=watch,
        workspace_dir=workspace,
        movies_dir=movies,
        shows_dir=shows,
        database_dir=db,
        trash_dir=None,
    )
    initialize_database(config)
    sf = create_session_factory(config)

    src = downloads / "movie.mkv"
    src.write_bytes(b"x")

    with sf() as session:
        from media_pilot.repository.models import WriteResult
        from media_pilot.repository.repositories import (
            IngestTaskCreate,
            IngestTaskRepository,
        )
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(src),
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(WriteResult(task_id=task.id, status="succeeded", payload={}))
        session.commit()
        task_id = task.id

    with sf() as session:
        from media_pilot.services.app_settings import (
            AppSettings,
            AppSettingsService,
        )
        svc = AppSettingsService(sf)
        current = svc.read_using_session(session)
        svc.save(AppSettings(
            enabled_metadata_profiles=list(current.enabled_metadata_profiles),
            enabled_library_formats=list(current.enabled_library_formats),
            suspicious_file_threshold_bytes=current.suspicious_file_threshold_bytes,
            metadata_auto_confirm_confidence=current.metadata_auto_confirm_confidence,
            metadata_auto_confirm_margin=current.metadata_auto_confirm_margin,
            preferred_metadata_language=current.preferred_metadata_language,
            source_cleanup_policy="trash",
        ))

    with sf() as session:
        ctx = _make_tool_context(session, config, task_id)
        result = _execute_tool(ctx, {"task_id": task_id})
        session.commit()

    assert result.status == "success"
    assert result.data["decision_type"] == "source_cleanup_action"
    assert result.data["preflight_reason"] == "trash_dir_not_configured"
    # 源文件未移动
    assert src.exists()


def test_trash_policy_move_failure_records_failed_without_changing_task_status(
    session_factory, config_with_trash, monkeypatch
) -> None:
    """trash 移动失败 → 写 source_input_cleanup_failed, 任务保持 library_import_complete."""
    from sqlalchemy import select
    from media_pilot.repository.models import (
        IngestTask,
        OperationRecord,
        WriteResult,
    )
    from media_pilot.repository.repositories import (
        IngestTaskCreate,
        IngestTaskRepository,
    )

    src = config_with_trash.downloads_dir / "movie.mkv"
    src.write_bytes(b"x")

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(src),
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(WriteResult(task_id=task.id, status="succeeded", payload={}))
        session.commit()
        task_id = task.id

    _set_policy(session_factory, "trash")

    def _raise(*args, **kwargs):
        raise OSError("simulated move failure")

    monkeypatch.setattr(shutil, "move", _raise)

    with session_factory() as session:
        ctx = _make_tool_context(session, config_with_trash, task_id)
        result = _execute_tool(ctx, {"task_id": task_id})
        session.commit()

    assert result.status == "failure"
    assert "simulated move failure" in result.summary
    assert result.data["action"] == "trash_failed"
    assert result.data["task_status_unchanged"] == "library_import_complete"

    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task.status == "library_import_complete", (
            "工具失败不应把任务回退到入库失败"
        )
        failed_op = session.scalars(
            select(OperationRecord).where(
                OperationRecord.task_id == task_id,
                OperationRecord.operation_type == "source_input_cleanup_failed",
            )
        ).first()
        assert failed_op is not None
        assert failed_op.status == "failed"


# ── schema validation ──────────────────────────────────────────────────


def test_handle_source_cleanup_schema_validates_task_id() -> None:
    from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

    register_builtin_tools()
    r = get_tool_registry()
    with pytest.raises(ValueError, match="missing required field"):
        r.validate_input("handle_source_cleanup", {})
    r.validate_input("handle_source_cleanup", {"task_id": "abc"})


def test_handle_source_cleanup_rejects_extra_fields() -> None:
    from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

    register_builtin_tools()
    r = get_tool_registry()
    with pytest.raises(ValueError, match="unexpected fields"):
        r.validate_input(
            "handle_source_cleanup",
            {"task_id": "abc", "extra": "field"},
        )
