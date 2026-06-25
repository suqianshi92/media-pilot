"""Section 5 integration tests: handle_source_cleanup in agent main + freeform flows.

Covers:
- 5.1 / 5.3: AUTO_INGEST_SYSTEM_PROMPT must reference handle_source_cleanup as wrap-up.
- 5.2 / 5.3: FREEFORM_SYSTEM_PROMPT must reference handle_source_cleanup for ingested tasks.
- Whitelist assertion: handle_source_cleanup must be in both whitelists.
- 5.4: auto_ingest wrap-up — after publish, agent calls handle_source_cleanup, success
       writes source_input_kept OperationRecord; failure (e.g. move error) does not
       change task.status away from library_import_complete.
- 5.5: freeform input — user can request keep/trash, tool refuses for non-ingested.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest


# ── 5.1 / 5.2 / 5.3 — Prompt + whitelist assertions ─────────────────────


class TestPromptsAndWhitelist:
    def test_auto_ingest_prompt_requires_handle_source_cleanup_as_wrapup(self) -> None:
        from media_pilot.agent.prompts import AUTO_INGEST_SYSTEM_PROMPT

        # 必须明确要求"发布成功后调用 handle_source_cleanup"作为收尾
        assert "handle_source_cleanup" in AUTO_INGEST_SYSTEM_PROMPT
        # wrap-up 暗示: prompt 应在 publish_movie_to_library 之后描述该工具
        # (Scope 段会先提到, 必须用 .find(pub_idx 之后) 锁 Workflow step 9 那次)
        pub_idx = AUTO_INGEST_SYSTEM_PROMPT.index("publish_movie_to_library")
        post_pub_idx = AUTO_INGEST_SYSTEM_PROMPT.find(
            "handle_source_cleanup", pub_idx,
        )
        assert post_pub_idx > pub_idx, (
            "Workflow step 9 必须在 publish_movie_to_library 之后提到 handle_source_cleanup"
        )
        # 明确"收尾"语义
        assert "wrap-up" in AUTO_INGEST_SYSTEM_PROMPT.lower() or "收尾" in AUTO_INGEST_SYSTEM_PROMPT

    def test_freeform_prompt_exposes_handle_source_cleanup_for_ingested(self) -> None:
        from media_pilot.agent.prompts import FREEFORM_SYSTEM_PROMPT

        # 通用 Agent 必须能调 handle_source_cleanup
        assert "handle_source_cleanup" in FREEFORM_SYSTEM_PROMPT
        # 必须限定为"已完成入库"任务
        assert "library_import_complete" in FREEFORM_SYSTEM_PROMPT
        # 必须强调工具内部校验是最终边界, 不能由 prompt 越权
        assert "enforce" in FREEFORM_SYSTEM_PROMPT.lower() or "internal" in FREEFORM_SYSTEM_PROMPT.lower()

    def test_handle_source_cleanup_in_auto_ingest_whitelist(self) -> None:
        from media_pilot.agent.tool_schema import (
            AUTO_INGEST_WRITE_TOOL_WHITELIST,
            FREEFORM_WRITE_TOOL_WHITELIST,
        )

        assert "handle_source_cleanup" in AUTO_INGEST_WRITE_TOOL_WHITELIST
        assert "handle_source_cleanup" in FREEFORM_WRITE_TOOL_WHITELIST


# ── 5.4 — Runner integration: auto_ingest calls handle_source_cleanup ───


class TestAutoIngestWrapup:
    @pytest.fixture
    def config_with_trash(self, tmp_path: Path):
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
            llm_api_key="test-key",
            llm_base_url="https://test.example.com/v1",
            llm_model="test-model",
        )

    def _make_ingested_task(self, session_factory, config) -> str:
        """Create a task in library_import_complete state with successful WriteResult."""
        from media_pilot.repository.models import WriteResult
        from media_pilot.repository.repositories import (
            IngestTaskCreate,
            IngestTaskRepository,
        )

        src = config.downloads_dir / "movie.mkv"
        src.write_bytes(b"content")

        with session_factory() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path=str(src),
                status="library_import_complete",
                current_step="library_import_complete",
                media_type="movie",
            ))
            session.add(WriteResult(
                task_id=task.id, status="succeeded",
                payload={"final_target_dir": "/tmp/library/Movie (2026)"},
            ))
            session.commit()
            return task.id

    def _set_policy(self, session_factory, policy: str) -> None:
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

    def test_auto_ingest_calls_handle_source_cleanup_as_wrapup(
        self, tmp_path, config_with_trash,
    ) -> None:
        """auto_ingest 收尾: agent 在 tool loop 里调 handle_source_cleanup, keep 策略下记录 source_input_kept."""
        from sqlalchemy import select
        from tests.test_api_v1 import _make_session_factory

        config = config_with_trash
        sf = _make_session_factory(tmp_path)
        # Reset config to use the trash fixture directories
        config = type(config)(
            downloads_dir=config.downloads_dir,
            watch_dir=config.watch_dir,
            workspace_dir=config.workspace_dir,
            movies_dir=config.movies_dir,
            shows_dir=config.shows_dir,
            database_dir=config.database_dir,
            trash_dir=config.trash_dir,
            llm_api_key="test-key",
            llm_base_url="https://test.example.com/v1",
            llm_model="test-model",
        )

        from media_pilot.agent.tools.registry import register_builtin_tools
        register_builtin_tools()
        task_id = self._make_ingested_task(sf, config)
        self._set_policy(sf, "keep")

        # Mock LLM: single wrap-up tool call to handle_source_cleanup
        class _Mock:
            def __init__(self):
                from media_pilot.agent.llm_client import LLMResponse
                self.responses = [LLMResponse(
                    content=None,
                    tool_calls=[{
                        "id": "call_cleanup",
                        "type": "function",
                        "function": {
                            "name": "handle_source_cleanup",
                            "arguments": json.dumps({"task_id": task_id}),
                        },
                    }],
                ), LLMResponse(content="Done.", tool_calls=[])]

            def chat(self, messages, tools=None):
                return self.responses.pop(0)

        mock = _Mock()
        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
            )
            session.commit()

        assert result.status == "completed"
        assert result.tool_call_count == 1

        with sf() as session:
            from media_pilot.repository.models import (
                IngestTask,
                OperationRecord,
            )
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                AgentToolCallRepository,
            )
            run = AgentRunRepository(session).list_by_task(task_id)[-1]
            tcs = AgentToolCallRepository(session).list_by_run(run.id)
            assert any(tc.tool_name == "handle_source_cleanup" for tc in tcs)
            cleanup_tc = [tc for tc in tcs if tc.tool_name == "handle_source_cleanup"][0]
            assert cleanup_tc.status == "completed", (
                f"keep 策略的 cleanup 调用应当成功, 实际 status={cleanup_tc.status} "
                f"err={cleanup_tc.error_message!r}"
            )

            op = session.scalars(
                select(OperationRecord).where(
                    OperationRecord.task_id == task_id,
                    OperationRecord.operation_type == "source_input_kept",
                )
            ).first()
            assert op is not None and op.status == "succeeded"

            # 任务状态不被工具回退到 ingest failure. auto_ingest runner
            # 把 task.status 切到 agent_running 但不主动恢复 — 这是 runner
            # 自己的语义, 不是工具的契约. 关键是不能变成 failed / agent_failed.
            task = session.get(IngestTask, task_id)
            assert task.status not in ("failed", "agent_failed"), (
                f"keep 收尾不应把任务标为失败, 实际={task.status}"
            )

    def test_auto_ingest_cleanup_failure_preserves_library_import_complete(
        self, tmp_path, config_with_trash, monkeypatch,
    ) -> None:
        """auto_ingest 收尾: 清理工具 trash move 失败 → 写 source_input_cleanup_failed, 任务保持 library_import_complete."""
        from sqlalchemy import select
        from tests.test_api_v1 import _make_session_factory

        config = config_with_trash
        sf = _make_session_factory(tmp_path)

        from media_pilot.agent.tools.registry import register_builtin_tools
        register_builtin_tools()
        task_id = self._make_ingested_task(sf, config)
        self._set_policy(sf, "trash")

        def _raise(*args, **kwargs):
            raise OSError("simulated move failure")
        monkeypatch.setattr(shutil, "move", _raise)

        class _Mock:
            def __init__(self):
                from media_pilot.agent.llm_client import LLMResponse
                self.responses = [LLMResponse(
                    content=None,
                    tool_calls=[{
                        "id": "call_cleanup",
                        "type": "function",
                        "function": {
                            "name": "handle_source_cleanup",
                            "arguments": json.dumps({"task_id": task_id}),
                        },
                    }],
                ), LLMResponse(content="Done.", tool_calls=[])]

            def chat(self, messages, tools=None):
                return self.responses.pop(0)

        mock = _Mock()
        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config, task_id=task_id,
                mode="auto_ingest", mock_llm_client=mock,
            )
            session.commit()

        # Tool failure surfaces as completed run with 1 failed tool call
        assert result.status == "completed"
        assert result.tool_call_count == 1

        with sf() as session:
            from media_pilot.repository.models import IngestTask, OperationRecord
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                AgentToolCallRepository,
            )
            run = AgentRunRepository(session).list_by_task(task_id)[-1]
            tcs = AgentToolCallRepository(session).list_by_run(run.id)
            cleanup_tc = [tc for tc in tcs if tc.tool_name == "handle_source_cleanup"]
            assert cleanup_tc and cleanup_tc[0].status == "failed"

            failed_op = session.scalars(
                select(OperationRecord).where(
                    OperationRecord.task_id == task_id,
                    OperationRecord.operation_type == "source_input_cleanup_failed",
                )
            ).first()
            assert failed_op is not None and failed_op.status == "failed"

            # 关键: 任务状态不能被回退到 ingest failure.
            # runner 会把 task.status 切到 agent_running, 但工具失败时
            # 必须不让它变成 failed / agent_failed. 这才是工具契约的范围.
            task = session.get(IngestTask, task_id)
            assert task.status not in ("failed", "agent_failed"), (
                f"清理失败不能把任务标为 ingest failure, 实际={task.status}"
            )


# ── 5.5 — Freeform input: user-driven cleanup request ──────────────────


class TestFreeformSourceCleanup:
    @pytest.fixture
    def config_with_trash(self, tmp_path: Path):
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
            llm_api_key="test-key",
            llm_base_url="https://test.example.com/v1",
            llm_model="test-model",
        )

    def _make_ingested_task(self, session_factory, config) -> str:
        from media_pilot.repository.models import WriteResult
        from media_pilot.repository.repositories import (
            IngestTaskCreate,
            IngestTaskRepository,
        )

        src = config.downloads_dir / "movie.mkv"
        src.write_bytes(b"content")

        with session_factory() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path=str(src),
                status="library_import_complete",
                current_step="library_import_complete",
                media_type="movie",
            ))
            session.add(WriteResult(
                task_id=task.id, status="succeeded", payload={},
            ))
            session.commit()
            return task.id

    def _set_policy(self, session_factory, policy: str) -> None:
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

    def _scripted_mock(self, tool_name: str, task_id: str):
        class _Mock:
            def __init__(self):
                from media_pilot.agent.llm_client import LLMResponse
                self.responses = [LLMResponse(
                    content=None,
                    tool_calls=[{
                        "id": "call_cleanup",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps({"task_id": task_id}),
                        },
                    }],
                ), LLMResponse(content="Done.", tool_calls=[])]

            def chat(self, messages, tools=None):
                return self.responses.pop(0)

        return _Mock()

    def test_freeform_user_ask_to_keep_triggers_handle_source_cleanup(
        self, tmp_path, config_with_trash,
    ) -> None:
        from sqlalchemy import select
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        from media_pilot.agent.tools.registry import register_builtin_tools
        register_builtin_tools()
        task_id = self._make_ingested_task(sf, config_with_trash)
        self._set_policy(sf, "keep")

        mock = self._scripted_mock("handle_source_cleanup", task_id)
        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config_with_trash, task_id=task_id,
                mode="freeform", mock_llm_client=mock,
                user_message_text="请帮我把源文件保留下来",
            )
            session.commit()

        assert result.status == "completed"
        assert result.tool_call_count == 1

        with sf() as session:
            from media_pilot.repository.models import OperationRecord
            op = session.scalars(
                select(OperationRecord).where(
                    OperationRecord.task_id == task_id,
                    OperationRecord.operation_type == "source_input_kept",
                )
            ).first()
            assert op is not None and op.status == "succeeded"

    def test_freeform_user_ask_to_trash_triggers_handle_source_cleanup(
        self, tmp_path, config_with_trash,
    ) -> None:
        from sqlalchemy import select
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        from media_pilot.agent.tools.registry import register_builtin_tools
        register_builtin_tools()
        task_id = self._make_ingested_task(sf, config_with_trash)
        self._set_policy(sf, "trash")

        src = config_with_trash.downloads_dir / "movie.mkv"
        assert src.exists()

        mock = self._scripted_mock("handle_source_cleanup", task_id)
        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config_with_trash, task_id=task_id,
                mode="freeform", mock_llm_client=mock,
                user_message_text="请把源文件移入回收区",
            )
            session.commit()

        assert result.status == "completed"

        # 源文件已被移动
        assert not src.exists()
        trash_files = list(config_with_trash.trash_dir.iterdir())
        assert any(p.name == "movie.mkv" for p in trash_files)

        with sf() as session:
            from media_pilot.repository.models import OperationRecord
            op = session.scalars(
                select(OperationRecord).where(
                    OperationRecord.task_id == task_id,
                    OperationRecord.operation_type == "source_input_trashed",
                )
            ).first()
            assert op is not None and op.status == "succeeded"

    def test_freeform_refuses_to_cleanup_non_ingested_task(
        self, tmp_path, config_with_trash,
    ) -> None:
        """未入库任务 (status=discovered) → 工具拒绝, 任务状态不变, 不写 source_input_kept."""
        from sqlalchemy import select
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        from media_pilot.agent.tools.registry import register_builtin_tools
        register_builtin_tools()

        # 创建 discovered 任务 (未入库)
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source/not-ingested.mkv",
                status="discovered",
                current_step="agent_start",
                media_type="movie",
            ))
            session.commit()
            task_id = task.id

        mock = self._scripted_mock("handle_source_cleanup", task_id)
        with sf() as session:
            from media_pilot.agent.runner import run_agent_turn
            result = run_agent_turn(
                session=session, config=config_with_trash, task_id=task_id,
                mode="freeform", mock_llm_client=mock,
                user_message_text="清理这个源文件",
            )
            session.commit()

        assert result.status == "completed"

        with sf() as session:
            from media_pilot.repository.models import (
                IngestTask,
                OperationRecord,
            )
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                AgentToolCallRepository,
            )
            run = AgentRunRepository(session).list_by_task(task_id)[-1]
            tcs = AgentToolCallRepository(session).list_by_run(run.id)
            cleanup_tc = [tc for tc in tcs if tc.tool_name == "handle_source_cleanup"]
            assert cleanup_tc
            assert cleanup_tc[0].status == "failed", (
                "未入库任务调用 handle_source_cleanup 应失败"
            )
            # 未入库任务有两个拒绝理由: pre-publish 状态 / 缺 WriteResult.
            # 两者都被接受 — 关键是工具拒绝了这次调用.
            err = cleanup_tc[0].error_message or ""
            assert (
                "post-publish" in err
                or "write result" in err
            ), f"未入库任务应被工具拒绝, err={err!r}"

            # 不应写入任何 cleanup 事件
            ops = session.scalars(
                select(OperationRecord).where(OperationRecord.task_id == task_id),
            ).all()
            cleanup_types = {
                "source_input_kept", "source_input_trashed", "source_input_cleanup_failed",
            }
            assert not any(op.operation_type in cleanup_types for op in ops)

            task = session.get(IngestTask, task_id)
            # 任务状态不能被工具改成 ingest failure. runner 自己把
            # status 切到 agent_running 那是它的契约, 不在本测试范围.
            assert task.status not in ("failed", "agent_failed"), (
                f"工具拒绝调用时不能让任务变成失败态, 实际={task.status}"
            )
