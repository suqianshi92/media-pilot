"""source_cleanup_action 决策回复测试 — keep_input / trash_input / delete_input."""

from __future__ import annotations

from pathlib import Path

import pytest


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
    for d in (downloads, watch, workspace, movies, shows, db, trash):
        d.mkdir(parents=True, exist_ok=True)
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


def _make_published_task_with_source_selection(
    session_factory,
    config_with_trash,
) -> tuple[str, Path]:
    from media_pilot.repository.models import (
        IngestTask,
        MediaSourceSelection,
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
        session.add(WriteResult(
            task_id=task.id, status="succeeded", payload={},
        ))
        session.add(MediaSourceSelection(
            task_id=task.id,
            input_path=str(src),
            selected_path=str(src),
            confidence=1.0,
            reason="auto",
            payload={},
        ))
        session.commit()
        return task.id, src


def _create_pending_source_cleanup_decision(
    session_factory, task_id: str
) -> str:
    from media_pilot.repository.models import (
        AgentDecisionRequest,
        AgentRun,
    )
    from media_pilot.repository.repositories import (
        AgentRunCreate,
        AgentRunRepository,
    )

    with session_factory() as session:
        run_repo = AgentRunRepository(session)
        run = run_repo.create(AgentRunCreate(
            task_id=task_id,
            current_step="source_cleanup_decision",
        ))
        decision = AgentDecisionRequest(
            run_id=run.id,
            task_id=task_id,
            decision_type="source_cleanup_action",
            question="源文件清理方式？",
            free_text_allowed=False,
            options=[
                {"id": "keep_input", "label": "保留"},
                {"id": "trash_input", "label": "移入回收区"},
                {"id": "delete_input", "label": "进入删除预检"},
            ],
            payload={},
            status="pending",
        )
        session.add(decision)
        # run 切到 waiting_user
        run_repo.update_status(
            run, status="waiting_user",
            current_step="source_cleanup_decision",
        )
        session.commit()
        return decision.id


def _reply(session_factory, config_with_trash, decision_id: str, option_id: str):
    from media_pilot.services.decision_reply import (
        ReplyInput,
        reply_to_decision,
    )

    with session_factory() as session:
        result = reply_to_decision(
            session=session,
            config=config_with_trash,
            reply=ReplyInput(decision_id=decision_id, option_id=option_id),
        )
        session.commit()
        return result


# ── keep_input ────────────────────────────────────────────────────────


def test_keep_input_records_kept_and_does_not_move(
    session_factory, config_with_trash
) -> None:
    """keep_input: 写 source_input_kept, 不移动文件."""
    from sqlalchemy import select
    from media_pilot.repository.models import (
        IngestTask,
        OperationRecord,
    )

    task_id, src = _make_published_task_with_source_selection(
        session_factory, config_with_trash,
    )
    decision_id = _create_pending_source_cleanup_decision(
        session_factory, task_id,
    )

    result = _reply(session_factory, config_with_trash, decision_id, "keep_input")

    assert result.status == "source_cleanup_kept"
    assert src.exists(), "keep 不应移动源文件"

    with session_factory() as session:
        ops = session.scalars(
            select(OperationRecord).where(OperationRecord.task_id == task_id)
        ).all()
        types = [op.operation_type for op in ops]
        assert "source_input_kept" in types

        task = session.get(IngestTask, task_id)
        assert task.status == "library_import_complete"
        assert task.current_step == "source_cleanup_kept"


def test_keep_input_with_directory_input_records_input_path_not_selected_path(
    session_factory, config_with_trash
) -> None:
    """目录输入: keep_input handler 记录的 source_path 必须是 input_path (整块
    任务输入), 而不是 selected_path (主视频单文件). 与 trash 预检/执行使用的
    任务输入节点一致."""
    from sqlalchemy import select
    from media_pilot.repository.models import (
        IngestTask,
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

    decision_id = _create_pending_source_cleanup_decision(
        session_factory, task_id,
    )

    result = _reply(session_factory, config_with_trash, decision_id, "keep_input")

    assert result.status == "source_cleanup_kept"

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
            f"keep_input 应记录任务输入节点 (目录), 实际={op.source_path!r}"
        )
        assert op.source_path != str(main_mkv), (
            "不能误用 selected_path (主视频单文件) 代表任务输入节点"
        )

        task = session.get(IngestTask, task_id)
        assert task.status == "library_import_complete"
        assert task.current_step == "source_cleanup_kept"


# ── trash_input ───────────────────────────────────────────────────────


def test_trash_input_moves_to_trash(
    session_factory, config_with_trash
) -> None:
    """trash_input: 预检通过则移动整个任务输入节点到 trash_dir."""
    from sqlalchemy import select
    from media_pilot.repository.models import (
        IngestTask,
        OperationRecord,
    )

    task_id, src = _make_published_task_with_source_selection(
        session_factory, config_with_trash,
    )
    decision_id = _create_pending_source_cleanup_decision(
        session_factory, task_id,
    )

    result = _reply(session_factory, config_with_trash, decision_id, "trash_input")

    assert result.status == "source_cleanup_trashed"
    assert not src.exists()
    trash_files = list(config_with_trash.trash_dir.iterdir())
    assert any(p.name == "movie.mkv" for p in trash_files)

    with session_factory() as session:
        ops = session.scalars(
            select(OperationRecord).where(OperationRecord.task_id == task_id)
        ).all()
        types = [op.operation_type for op in ops]
        assert "source_input_trashed" in types

        task = session.get(IngestTask, task_id)
        assert task.status == "library_import_complete"
        assert task.current_step == "source_cleanup_trashed"


def test_trash_input_preflight_refused_records_failed(
    session_factory, config_with_trash
) -> None:
    """trash_input 触发预检失败时, 返回 source_cleanup_failed (不是 trashed),
    记录 source_input_cleanup_failed, 任务保持 library_import_complete."""
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

    # 任务源路径 = downloads 根目录本身 → 预检应拒绝
    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(config_with_trash.downloads_dir),
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(WriteResult(task_id=task.id, status="succeeded", payload={}))
        session.commit()
        task_id = task.id

    decision_id = _create_pending_source_cleanup_decision(
        session_factory, task_id,
    )

    result = _reply(session_factory, config_with_trash, decision_id, "trash_input")

    # 关键: 失败必须返回 source_cleanup_failed, 而不是 trashed.
    # 旧版本忽略 handler outcome 一律返回 trashed, 会让前端误判成功.
    assert result.status == "source_cleanup_failed", (
        f"trash_input 预检失败必须返回 source_cleanup_failed, 实际={result.status}"
    )
    # downloads 根目录还存在
    assert config_with_trash.downloads_dir.exists()

    with session_factory() as session:
        ops = session.scalars(
            select(OperationRecord).where(OperationRecord.task_id == task_id)
        ).all()
        types = [op.operation_type for op in ops]
        assert "source_input_cleanup_failed" in types
        # 失败事件不应误记为 source_input_trashed
        assert "source_input_trashed" not in types

        task = session.get(IngestTask, task_id)
        assert task.status == "library_import_complete"
        assert task.current_step == "source_cleanup_trash_refused"


def test_trash_input_move_failure_returns_source_cleanup_failed(
    session_factory, config_with_trash, monkeypatch
) -> None:
    """trash 移动本身失败 (shutil.move 抛错) → 返回 source_cleanup_failed, 不写 source_input_trashed.

    关键回归: 失败不能伪装成 trashed, 任务保持 library_import_complete.
    """
    import shutil

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

    # 预检能通过的目录输入
    src_dir = config_with_trash.downloads_dir / "movie"
    src_dir.mkdir()
    (src_dir / "main.mkv").write_bytes(b"x")

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path=str(src_dir),
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(WriteResult(task_id=task.id, status="succeeded", payload={}))
        session.commit()
        task_id = task.id

    decision_id = _create_pending_source_cleanup_decision(
        session_factory, task_id,
    )

    def _raise(*args, **kwargs):
        raise OSError("simulated move failure")
    monkeypatch.setattr(shutil, "move", _raise)

    result = _reply(session_factory, config_with_trash, decision_id, "trash_input")

    assert result.status == "source_cleanup_failed", (
        f"move 失败应返回 source_cleanup_failed, 实际={result.status}"
    )
    # 源目录未被移动
    assert src_dir.exists()
    assert (src_dir / "main.mkv").exists()

    with session_factory() as session:
        ops = session.scalars(
            select(OperationRecord).where(OperationRecord.task_id == task_id)
        ).all()
        types = [op.operation_type for op in ops]
        assert "source_input_cleanup_failed" in types
        assert "source_input_trashed" not in types

        task = session.get(IngestTask, task_id)
        assert task.status == "library_import_complete"
        assert task.current_step == "source_cleanup_trash_failed"


# ── delete_input ──────────────────────────────────────────────────────


def test_delete_input_returns_delete_input_preview(
    session_factory, config_with_trash
) -> None:
    """delete_input: 返回 delete_input_preview, 前端可继续走删除预检 API."""
    from media_pilot.repository.models import IngestTask, WriteResult
    from media_pilot.repository.repositories import (
        IngestTaskCreate,
        IngestTaskRepository,
    )

    with session_factory() as session:
        task = IngestTaskRepository(session).create(IngestTaskCreate(
            source_path="/tmp/source/movie.mkv",
            status="library_import_complete",
            current_step="library_import_complete",
        ))
        session.add(WriteResult(task_id=task.id, status="succeeded", payload={}))
        session.commit()
        task_id = task.id

    decision_id = _create_pending_source_cleanup_decision(
        session_factory, task_id,
    )

    result = _reply(session_factory, config_with_trash, decision_id, "delete_input")

    assert result.status == "delete_input_preview"

    with session_factory() as session:
        task = session.get(IngestTask, task_id)
        assert task.status == "library_import_complete"


# ── 非法 option / free_text 拒绝 ─────────────────────────────────────


def test_invalid_option_id_rejected(
    session_factory, config_with_trash
) -> None:
    """非法 option_id 直接 400."""
    task_id, _ = _make_published_task_with_source_selection(
        session_factory, config_with_trash,
    )
    decision_id = _create_pending_source_cleanup_decision(
        session_factory, task_id,
    )

    with session_factory() as session:
        from media_pilot.services.decision_reply import (
            ReplyInput,
            reply_to_decision,
        )

        with pytest.raises(ValueError) as exc_info:
            reply_to_decision(
                session=session,
                config=config_with_trash,
                reply=ReplyInput(decision_id=decision_id, option_id="not_a_real_option"),
            )

    assert exc_info.value.args[0]["status_code"] == 400


def test_free_text_reply_rejected_for_source_cleanup_action(
    session_factory, config_with_trash
) -> None:
    """source_cleanup_action 不允许 free_text, reply 应被拒绝."""
    task_id, _ = _make_published_task_with_source_selection(
        session_factory, config_with_trash,
    )
    decision_id = _create_pending_source_cleanup_decision(
        session_factory, task_id,
    )

    with session_factory() as session:
        from media_pilot.services.decision_reply import (
            ReplyInput,
            reply_to_decision,
        )

        with pytest.raises(ValueError) as exc_info:
            reply_to_decision(
                session=session,
                config=config_with_trash,
                reply=ReplyInput(
                    decision_id=decision_id, free_text="just chat with the agent",
                ),
            )

    assert exc_info.value.args[0]["status_code"] == 400


# ── 不消耗 LLM ────────────────────────────────────────────────────────


def test_source_cleanup_action_does_not_invoke_llm(
    session_factory, config_with_trash
) -> None:
    """所有 source_cleanup_action 选项都不调用 LLM, 路径里没有 run_agent_turn."""
    task_id, _ = _make_published_task_with_source_selection(
        session_factory, config_with_trash,
    )
    decision_id = _create_pending_source_cleanup_decision(
        session_factory, task_id,
    )

    mock_llm_called = {"count": 0}

    def _fake_llm(*args, **kwargs):
        mock_llm_called["count"] += 1
        return None

    with session_factory() as session:
        from media_pilot.services.decision_reply import (
            ReplyInput,
            reply_to_decision,
        )
        reply_to_decision(
            session=session,
            config=config_with_trash,
            reply=ReplyInput(decision_id=decision_id, option_id="keep_input"),
            mock_llm_client=_fake_llm,
        )
        session.commit()

    assert mock_llm_called["count"] == 0
