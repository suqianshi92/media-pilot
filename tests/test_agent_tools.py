from pathlib import Path

import pytest


def _make_config(database_dir: Path):
    from media_pilot.config.settings import AppConfig

    return AppConfig(
        downloads_dir=Path("/tmp/dl"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp/ws"),
        movies_dir=Path("/tmp/movies"),
        shows_dir=Path("/tmp/shows"),
        database_dir=database_dir,
    )


def _make_tool_context(session, config, task_id: str):
    from media_pilot.agent.tools.base import ToolContext

    return ToolContext(session=session, config=config, task_id=task_id)


# ══════════════════════════════════════════════════════════════════════
# ToolRegistry
# ══════════════════════════════════════════════════════════════════════


class TestToolRegistry:
    def test_register_and_get(self):
        from media_pilot.agent.tools.base import (
            PermissionLevel,
            ToolDefinition,
            ToolResult,
        )
        from media_pilot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        tool = ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            permission_level=PermissionLevel.READ_ONLY,
            handler=lambda ctx, inp: ToolResult(status="success", summary="ok"),
        )
        registry.register(tool)
        assert registry.get("test_tool") is tool
        assert "test_tool" in [t.name for t in registry.list_tools()]

    def test_duplicate_register_raises(self):
        from media_pilot.agent.tools.base import (
            PermissionLevel,
            ToolDefinition,
            ToolResult,
        )
        from media_pilot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        tool = ToolDefinition(
            name="dup",
            description="dup",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            permission_level=PermissionLevel.READ_ONLY,
            handler=lambda ctx, inp: ToolResult(status="success", summary="ok"),
        )
        registry.register(tool)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(tool)

    def test_get_unknown_tool_raises(self):
        from media_pilot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        with pytest.raises(KeyError, match="Unknown tool"):
            registry.get("nonexistent")

    def test_validate_input_rejects_missing_required(self):
        from media_pilot.agent.tools.base import (
            PermissionLevel,
            ToolDefinition,
            ToolResult,
        )
        from media_pilot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        tool = ToolDefinition(
            name="requires_id",
            description="requires id",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
                "additionalProperties": False,
            },
            permission_level=PermissionLevel.READ_ONLY,
            handler=lambda ctx, inp: ToolResult(status="success", summary="ok"),
        )
        registry.register(tool)
        with pytest.raises(ValueError, match="Validation failed"):
            registry.validate_input("requires_id", {})

    def test_validate_input_rejects_extra_fields(self):
        from media_pilot.agent.tools.base import (
            PermissionLevel,
            ToolDefinition,
            ToolResult,
        )
        from media_pilot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        tool = ToolDefinition(
            name="no_extras",
            description="no extras",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
                "additionalProperties": False,
            },
            permission_level=PermissionLevel.READ_ONLY,
            handler=lambda ctx, inp: ToolResult(status="success", summary="ok"),
        )
        registry.register(tool)
        with pytest.raises(ValueError, match="unexpected fields"):
            registry.validate_input("no_extras", {"id": "abc", "extra": 1})

    def test_validate_input_accepts_valid(self):
        from media_pilot.agent.tools.base import (
            PermissionLevel,
            ToolDefinition,
            ToolResult,
        )
        from media_pilot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        tool = ToolDefinition(
            name="requires_id",
            description="requires id",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
                "additionalProperties": False,
            },
            permission_level=PermissionLevel.READ_ONLY,
            handler=lambda ctx, inp: ToolResult(status="success", summary="ok"),
        )
        registry.register(tool)
        registry.validate_input("requires_id", {"id": "abc"})

    def test_execute_returns_tool_result(self):
        from media_pilot.agent.tools.base import (
            PermissionLevel,
            ToolDefinition,
            ToolResult,
        )
        from media_pilot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="echo",
                description="echo",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                permission_level=PermissionLevel.READ_ONLY,
                handler=lambda ctx, inp: ToolResult(
                    status="success", summary="echo!", data={"echo": True}
                ),
            )
        )
        result = registry.execute("echo", _make_tool_context(None, None, "t1"), {})
        assert result.status == "success"
        assert result.data["echo"] is True

    def test_execute_catches_handler_exception(self):
        from media_pilot.agent.tools.base import (
            PermissionLevel,
            ToolDefinition,
        )
        from media_pilot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="crash",
                description="crashes",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                permission_level=PermissionLevel.READ_ONLY,
                handler=lambda ctx, inp: (_ for _ in ()).throw(RuntimeError("boom")),
            )
        )
        result = registry.execute("crash", _make_tool_context(None, None, "t1"), {})
        assert result.status == "failure"
        assert "internal error" in result.summary.lower()

    def test_register_builtin_tools_idempotent(self):
        from media_pilot.agent.tools.registry import (
            get_tool_registry,
            register_builtin_tools,
        )

        r = get_tool_registry()
        # Clear the real singleton for test isolation
        old = dict(r._tools)
        r._tools.clear()
        try:
            register_builtin_tools()
            names1 = {t.name for t in r.list_tools()}
            assert "get_task_context" in names1
            assert "draft_publish_plan" in names1
            assert len(names1) == 18  # 6 RO + 2 draft + 2 decision + 5 WRITE + 1 complex_input + 2 show (含 handle_source_cleanup)

            # Second call is idempotent
            register_builtin_tools()
            names2 = {t.name for t in r.list_tools()}
            assert names2 == names1
        finally:
            r._tools = old


# ══════════════════════════════════════════════════════════════════════
# get_task_context
# ══════════════════════════════════════════════════════════════════════


class TestGetTaskContextTool:
    def test_returns_task_context(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path="/data/test.mkv",
                    status="agent_running",
                    current_step="agent_start",
                    media_type="movie",
                    confidence=0.85,
                )
            )
            task.title = "Test Movie"
            task.year = 2024
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_get_task_context

        tool = make_get_task_context()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "success"
        assert result.data["title"] == "Test Movie"
        assert result.data["year"] == 2024
        assert result.data["status"] == "agent_running"
        assert result.data["source_path"] == "/data/test.mkv"

    def test_task_not_found(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        from media_pilot.agent.tools.read_only import make_get_task_context

        tool = make_get_task_context()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, "nonexistent"),
                {"task_id": "nonexistent"},
            )
        assert result.status == "failure"


# ══════════════════════════════════════════════════════════════════════
# scan_task_files
# ══════════════════════════════════════════════════════════════════════


class TestScanTaskFilesTool:
    def test_scan_directory_with_video_and_subtitle(self, tmp_path):
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        (src_dir / "movie.mkv").write_bytes(b"x" * 100)
        (src_dir / "movie.srt").write_bytes(b"y" * 50)
        (src_dir / "readme.txt").write_bytes(b"z" * 10)

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(src_dir),
                    status="agent_running",
                    current_step="agent_start",
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_scan_task_files

        tool = make_scan_task_files()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "success"
        assert result.data["video_count"] == 1
        assert result.data["subtitle_count"] == 1
        assert len(result.data["files"]) == 3

    def test_excludes_sample_videos(self, tmp_path):
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        (src_dir / "Sample.mkv").write_bytes(b"x" * 100)
        (src_dir / "movie.mkv").write_bytes(b"x" * 200)

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(src_dir),
                    status="agent_running",
                    current_step="agent_start",
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_scan_task_files

        tool = make_scan_task_files()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "success"
        assert result.data["video_count"] == 1
        assert len(result.data["excluded"]) == 1
        assert result.data["excluded"][0]["excluded_reason"] == "sample/trailer/auxiliary"

    def test_single_file_only_returns_that_file(self, tmp_path):
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        video = src_dir / "movie.mkv"
        video.write_bytes(b"x" * 100)
        (src_dir / "other.mkv").write_bytes(b"y" * 200)
        (src_dir / "movie.zh.srt").write_bytes(b"a" * 10)
        (src_dir / "movie.en.srt").write_bytes(b"b" * 10)
        (src_dir / "other.srt").write_bytes(b"c" * 10)

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(video),
                    status="agent_running",
                    current_step="agent_start",
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_scan_task_files

        tool = make_scan_task_files()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "success"
        assert result.data["video_count"] == 1
        # Only same-stem subtitles (movie.zh.srt, movie.en.srt) + the video itself
        assert result.data["subtitle_count"] == 2
        assert len(result.data["files"]) == 3
        # other.mkv and other.srt must NOT appear
        names = {f["name"] for f in result.data["files"]}
        assert names == {"movie.mkv", "movie.zh.srt", "movie.en.srt"}

    def test_source_path_not_found(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path="/nonexistent/path",
                    status="agent_running",
                    current_step="agent_start",
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_scan_task_files

        tool = make_scan_task_files()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "failure"


# ══════════════════════════════════════════════════════════════════════
# get_current_metadata
# ══════════════════════════════════════════════════════════════════════


class TestGetCurrentMetadataTool:
    def test_returns_metadata_when_exists(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path="/data/test.mkv",
                    status="agent_running",
                    current_step="agent_start",
                )
            )
            from media_pilot.repository.models import MetadataDetail

            detail = MetadataDetail(
                task_id=task.id,
                provider="tmdb",
                provider_id="550",
                media_type="movie",
                title="Fight Club",
                year=1999,
                payload={"plot": "First rule..."},
            )
            session.add(detail)
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_get_current_metadata

        tool = make_get_current_metadata()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "success"
        assert result.data["exists"] is True
        assert result.data["title"] == "Fight Club"
        assert result.data["year"] == 1999

    def test_returns_empty_when_no_metadata(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path="/data/test.mkv",
                    status="agent_running",
                    current_step="agent_start",
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_get_current_metadata

        tool = make_get_current_metadata()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "success"
        assert result.data["exists"] is False


# ══════════════════════════════════════════════════════════════════════
# get_metadata_candidates
# ══════════════════════════════════════════════════════════════════════


class TestGetMetadataCandidatesTool:
    def test_returns_candidates(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path="/data/test.mkv",
                    status="agent_running",
                    current_step="agent_start",
                )
            )
            from media_pilot.repository.models import MediaCandidate

            session.add(
                MediaCandidate(
                    task_id=task.id,
                    source="tmdb",
                    media_type="movie",
                    title="The Matrix",
                    year=1999,
                    external_id="603",
                    confidence=0.95,
                    reason="strong_match",
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_get_metadata_candidates

        tool = make_get_metadata_candidates()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "success"
        assert len(result.data["candidates"]) == 1
        assert result.data["candidates"][0]["title"] == "The Matrix"

    def test_empty_when_no_candidates(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path="/data/test.mkv",
                    status="agent_running",
                    current_step="agent_start",
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_get_metadata_candidates

        tool = make_get_metadata_candidates()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "success"
        assert result.data["candidates"] == []


# ══════════════════════════════════════════════════════════════════════
# draft_publish_plan
# ══════════════════════════════════════════════════════════════════════


class TestDraftPublishPlanTool:
    def test_movie_plan(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path="/data/downloads/The Matrix.mkv",
                    status="agent_running",
                    current_step="agent_start",
                    media_type="movie",
                )
            )
            from media_pilot.repository.models import MetadataDetail

            session.add(
                MetadataDetail(
                    task_id=task.id,
                    provider="tmdb",
                    provider_id="603",
                    media_type="movie",
                    title="The Matrix",
                    year=1999,
                    payload={"plot": "A computer hacker..."},
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.draft import make_draft_publish_plan

        tool = make_draft_publish_plan()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "success"
        assert result.data["media_type"] == "movie"
        assert result.data["is_draft"] is True
        assert "The Matrix" in result.data["final_target_file"]

    def test_missing_media_type(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path="/data/test.mkv",
                    status="agent_running",
                    current_step="agent_start",
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.draft import make_draft_publish_plan

        tool = make_draft_publish_plan()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "failure"
        assert "media_type" in result.summary.lower()

    def test_missing_metadata_detail(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path="/data/test.mkv",
                    status="agent_running",
                    current_step="agent_start",
                    media_type="movie",
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.draft import make_draft_publish_plan

        tool = make_draft_publish_plan()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "failure"
        assert "metadata" in result.summary.lower()

    def test_task_not_found(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        from media_pilot.agent.tools.draft import make_draft_publish_plan

        tool = make_draft_publish_plan()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, "nonexistent"),
                {"task_id": "nonexistent"},
            )
        assert result.status == "failure"

    def test_movie_plan_includes_same_stem_subtitles(self, tmp_path):
        src_dir = tmp_path / "downloads"
        src_dir.mkdir()
        video = src_dir / "The Matrix.mkv"
        video.write_bytes(b"x" * 100)
        (src_dir / "The Matrix.srt").write_bytes(b"y" * 50)
        (src_dir / "The Matrix.zh.ass").write_bytes(b"z" * 30)

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(video),
                    status="agent_running",
                    current_step="agent_start",
                    media_type="movie",
                )
            )
            from media_pilot.repository.models import MetadataDetail

            session.add(
                MetadataDetail(
                    task_id=task.id,
                    provider="tmdb",
                    provider_id="603",
                    media_type="movie",
                    title="The Matrix",
                    year=1999,
                    payload={"plot": "A computer hacker..."},
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.draft import make_draft_publish_plan

        tool = make_draft_publish_plan()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "success"
        assert result.data["media_type"] == "movie"
        assert len(result.data["subtitles"]) == 2
        sub_names = {s["name"] for s in result.data["subtitles"]}
        assert sub_names == {"The Matrix.srt", "The Matrix.zh.ass"}
        for s in result.data["subtitles"]:
            assert s["matched_by"] == "same_stem"

    def test_movie_plan_succeeds_without_subtitles(self, tmp_path):
        src_dir = tmp_path / "downloads"
        src_dir.mkdir()
        video = src_dir / "Solo.mkv"
        video.write_bytes(b"x" * 100)

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(video),
                    status="agent_running",
                    current_step="agent_start",
                    media_type="movie",
                )
            )
            from media_pilot.repository.models import MetadataDetail

            session.add(
                MetadataDetail(
                    task_id=task.id,
                    provider="tmdb",
                    provider_id="999",
                    media_type="movie",
                    title="Solo",
                    year=2023,
                    payload={},
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.draft import make_draft_publish_plan

        tool = make_draft_publish_plan()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        assert result.status == "success"
        assert result.data["subtitles"] == []
        assert "warnings" in result.data
        assert any("subtitle" in w.lower() for w in result.data["warnings"])


# ══════════════════════════════════════════════════════════════════════
# search_metadata handler behavioral tests
# (mock the service to exercise the handler logic without real API keys)
# ══════════════════════════════════════════════════════════════════════


class TestSearchMetadataBehavior:
    def test_no_candidates_no_errors_returns_failure_no_candidates(self, tmp_path):
        from unittest.mock import patch

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.services.metadata_search import MetadataSearchResult

        empty = MetadataSearchResult(candidates=[], errors=[])

        with patch(
            "media_pilot.services.metadata_search.search_metadata", return_value=empty
        ):
            from media_pilot.agent.tools.read_only import make_search_metadata

            tool = make_search_metadata()
            with sf() as session:
                result = tool.handler(
                    _make_tool_context(session, config, "t1"),
                    {"keyword": "NoSuchMovie"},
                )
        assert result.status == "failure"
        assert result.data["reason"] == "no_candidates"
        assert "keyword" in result.data

    def test_errors_and_no_candidates_returns_failure_provider_errors(self, tmp_path):
        from unittest.mock import patch

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.services.metadata_search import MetadataSearchResult

        err_result = MetadataSearchResult(
            candidates=[],
            errors=[{"query": "movie", "error": "timeout"}],
        )

        with patch(
            "media_pilot.services.metadata_search.search_metadata", return_value=err_result
        ):
            from media_pilot.agent.tools.read_only import make_search_metadata

            tool = make_search_metadata()
            with sf() as session:
                result = tool.handler(
                    _make_tool_context(session, config, "t1"),
                    {"keyword": "BrokenAPI"},
                )
        assert result.status == "failure"
        assert result.data["reason"] == "provider_errors"
        assert result.data["errors"] == [{"query": "movie", "error": "timeout"}]

    def test_partial_errors_with_candidates_returns_success_with_errors(self, tmp_path):
        from unittest.mock import patch

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.adapters.metadata import MetadataCandidate
        from media_pilot.services.metadata_search import MetadataSearchResult

        candidate = MetadataCandidate(
            provider="tmdb",
            provider_id="603",
            title="The Matrix",
            original_title=None,
            year=1999,
            media_type="movie",
            overview="A computer hacker...",
            poster_url=None,
            confidence=0.95,
            match_reason="keyword_match",
        )
        partial = MetadataSearchResult(
            candidates=[candidate],
            errors=[{"query": "show", "error": "timeout"}],
        )

        with patch(
            "media_pilot.services.metadata_search.search_metadata", return_value=partial
        ):
            from media_pilot.agent.tools.read_only import make_search_metadata

            tool = make_search_metadata()
            with sf() as session:
                result = tool.handler(
                    _make_tool_context(session, config, "t1"),
                    {"keyword": "Matrix"},
                )
        assert result.status == "success"
        assert len(result.data["candidates"]) == 1
        assert result.data["candidates"][0]["title"] == "The Matrix"
        assert "errors" in result.data
        assert result.data["errors"] == [{"query": "show", "error": "timeout"}]

    # ── threshold evaluation on search results ────────────────────────

    def test_single_high_confidence_candidate_has_clear_winner(self, tmp_path):
        """Single candidate with high confidence → has_clear_winner=true, best_candidate set."""
        from unittest.mock import patch

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.adapters.metadata import MetadataCandidate
        from media_pilot.services.metadata_search import MetadataSearchResult

        single = MetadataSearchResult(
            candidates=[
                MetadataCandidate(
                    provider="tmdb", provider_id="603", title="The Matrix",
                    original_title=None, year=1999, media_type="movie",
                    overview="...", poster_url=None, confidence=0.95,
                    match_reason="keyword_match",
                ),
            ],
            errors=[],
        )

        with patch(
            "media_pilot.services.metadata_search.search_metadata", return_value=single
        ):
            from media_pilot.agent.tools.read_only import make_search_metadata

            tool = make_search_metadata()
            with sf() as session:
                result = tool.handler(
                    _make_tool_context(session, config, "t1"),
                    {"keyword": "Matrix"},
                )

        assert result.status == "success"
        assert result.data["has_clear_winner"] is True
        assert result.data["best_candidate"] is not None
        assert result.data["best_candidate"]["title"] == "The Matrix"
        assert result.data["best_candidate"]["confidence"] == 0.95
        assert result.data["runner_up"] is None
        assert result.data["confidence_threshold"] == config.metadata_auto_confirm_confidence
        assert result.data["margin"] == config.metadata_auto_confirm_margin

    def test_low_confidence_candidate_no_clear_winner(self, tmp_path):
        """Candidate below confidence threshold → has_clear_winner=false."""
        from unittest.mock import patch

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.adapters.metadata import MetadataCandidate
        from media_pilot.services.metadata_search import MetadataSearchResult

        low = MetadataSearchResult(
            candidates=[
                MetadataCandidate(
                    provider="tmdb", provider_id="999", title="Obscure Movie",
                    original_title=None, year=2005, media_type="movie",
                    overview="...", poster_url=None, confidence=0.45,
                    match_reason="fuzzy_match",
                ),
            ],
            errors=[],
        )

        with patch(
            "media_pilot.services.metadata_search.search_metadata", return_value=low
        ):
            from media_pilot.agent.tools.read_only import make_search_metadata

            tool = make_search_metadata()
            with sf() as session:
                result = tool.handler(
                    _make_tool_context(session, config, "t1"),
                    {"keyword": "Obscure"},
                )

        assert result.status == "success"
        assert result.data["has_clear_winner"] is False
        assert result.data["best_candidate"] is not None
        assert result.data["best_candidate"]["confidence"] == 0.45

    def test_two_close_candidates_no_clear_winner(self, tmp_path):
        """Two candidates with close confidence (margin < threshold) → has_clear_winner=false."""
        from unittest.mock import patch

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        from media_pilot.adapters.metadata import MetadataCandidate
        from media_pilot.services.metadata_search import MetadataSearchResult

        close = MetadataSearchResult(
            candidates=[
                MetadataCandidate(
                    provider="tmdb", provider_id="603", title="The Matrix",
                    original_title=None, year=1999, media_type="movie",
                    overview="...", poster_url=None, confidence=0.88,
                    match_reason="keyword_match",
                ),
                MetadataCandidate(
                    provider="tmdb", provider_id="604", title="The Matrix Reloaded",
                    original_title=None, year=2003, media_type="movie",
                    overview="...", poster_url=None, confidence=0.82,
                    match_reason="keyword_match",
                ),
            ],
            errors=[],
        )

        with patch(
            "media_pilot.services.metadata_search.search_metadata", return_value=close
        ):
            from media_pilot.agent.tools.read_only import make_search_metadata

            tool = make_search_metadata()
            with sf() as session:
                result = tool.handler(
                    _make_tool_context(session, config, "t1"),
                    {"keyword": "Matrix"},
                )

        assert result.status == "success"
        assert result.data["has_clear_winner"] is False
        assert result.data["best_candidate"] is not None
        assert result.data["best_candidate"]["title"] == "The Matrix"
        assert result.data["runner_up"] is not None
        assert result.data["runner_up"]["title"] == "The Matrix Reloaded"
        # Verify margin computation reflects the gap
        assert result.data["best_candidate"]["confidence"] == 0.88
        assert result.data["runner_up"]["confidence"] == 0.82


# ══════════════════════════════════════════════════════════════════════
# Schema validation for search_metadata and draft_metadata_replacement
# (these tools need real API keys to execute, so we only test schema)
# ══════════════════════════════════════════════════════════════════════


class TestToolSchemas:
    def test_search_metadata_requires_keyword(self):
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

        register_builtin_tools()
        r = get_tool_registry()
        with pytest.raises(ValueError, match="missing required field"):
            r.validate_input("search_metadata", {})

    def test_search_metadata_accepts_keyword(self):
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

        register_builtin_tools()
        r = get_tool_registry()
        r.validate_input("search_metadata", {"keyword": "The Matrix"})

    def test_search_metadata_rejects_invalid_media_type(self):
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

        register_builtin_tools()
        r = get_tool_registry()
        with pytest.raises(ValueError, match="Validation failed"):
            r.validate_input("search_metadata", {"keyword": "test", "media_type": "invalid"})

    def test_search_metadata_accepts_valid_media_type(self):
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

        register_builtin_tools()
        r = get_tool_registry()
        r.validate_input("search_metadata", {"keyword": "test", "media_type": "movie"})
        r.validate_input("search_metadata", {"keyword": "test", "media_type": "show"})
        r.validate_input("search_metadata", {"keyword": "test", "media_type": "both"})

    def test_draft_metadata_replacement_requires_fields(self):
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

        register_builtin_tools()
        r = get_tool_registry()
        with pytest.raises(ValueError, match="missing required field"):
            r.validate_input("draft_metadata_replacement", {"provider_name": "tmdb"})

    def test_draft_metadata_replacement_rejects_invalid_media_type(self):
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

        register_builtin_tools()
        r = get_tool_registry()
        with pytest.raises(ValueError, match="Validation failed"):
            r.validate_input(
                "draft_metadata_replacement",
                {"provider_name": "tmdb", "provider_id": "550", "media_type": "invalid"},
            )

    def test_draft_metadata_replacement_accepts_valid(self):
        from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools

        register_builtin_tools()
        r = get_tool_registry()
        r.validate_input(
            "draft_metadata_replacement",
            {"provider_name": "tmdb", "provider_id": "550", "media_type": "movie"},
        )


# ══════════════════════════════════════════════════════════════════════
# get_auto_ingest_eligibility
# ══════════════════════════════════════════════════════════════════════


class TestGetAutoIngestEligibilityTool:
    def test_new_task_without_candidates_reports_no_metadata_candidates(self, tmp_path):
        """New task with no persisted candidates: eligibility correctly reports no_metadata_candidates."""
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        (src_dir / "movie.mkv").write_bytes(b"x" * 100)

        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(src_dir / "movie.mkv"),
                    status="discovered",
                    current_step="agent_start",
                    media_type="movie",
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_get_auto_ingest_eligibility

        tool = make_get_auto_ingest_eligibility()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )

        assert result.status == "success"
        # New task has no candidates → eligibility reports it as a blocking reason
        assert "no_metadata_candidates" in result.data["blocking_reasons"]
        assert result.data["eligible"] is False
        assert result.data["has_clear_winner"] is False
        assert result.data["candidate_count"] == 0
        # Task facts are present for Agent context
        assert result.data["task_facts"]["media_type"] == "movie"

    def test_eligible_single_movie_with_clear_winner(self, tmp_path):
        """Single movie with persisted high-confidence candidate → eligible."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        movies_dir = config.movies_dir
        movies_dir.mkdir(parents=True, exist_ok=True)
        (movies_dir / "movie.mkv").write_bytes(b"x" * 100)

        task_id = None
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )

            task = IngestTaskRepository(session).create(
                IngestTaskCreate(
                    source_path=str(movies_dir / "movie.mkv"),
                    status="agent_running",
                    current_step="agent_start",
                    media_type="movie",
                )
            )
            from media_pilot.repository.models import MediaCandidate

            session.add(
                MediaCandidate(
                    task_id=task.id,
                    source="tmdb",
                    media_type="movie",
                    title="The Matrix",
                    original_title=None,
                    year=1999,
                    external_id="603",
                    confidence=0.96,
                    reason="strong_match",
                )
            )
            session.commit()
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_get_auto_ingest_eligibility

        tool = make_get_auto_ingest_eligibility()
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )

        assert result.status == "success"
        assert result.data["eligible"] is True
        assert result.data["has_clear_winner"] is True
        assert result.data["best_candidate"] is not None
        assert result.data["best_candidate"]["title"] == "The Matrix"
        assert result.data["candidate_count"] == 1
        assert len(result.data["blocking_reasons"]) == 0

    def test_task_not_found(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        from media_pilot.agent.tools.read_only import make_get_auto_ingest_eligibility

        tool = make_get_auto_ingest_eligibility()
        config = _make_config(tmp_path)
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, "nonexistent"),
                {"task_id": "nonexistent"},
            )

        assert result.status == "success"  # eligibility check itself succeeds
        assert "task_not_found" in result.data["blocking_reasons"]
        assert result.data["eligible"] is False


# ══════════════════════════════════════════════════════════════════════
# Preselected Tool Exposure — read-only tools must surface preselected facts
# ══════════════════════════════════════════════════════════════════════


class TestPreselectedToolExposure:
    """regression: task 携带 preselected_metadata_* 三字段时, read-only
    工具必须主动暴露 ``preselected`` 块, 让 LLM 第一次调工具就能看到
    "已有强 winner" 事实, 不必再调 search_metadata 浪费 step."""

    def _make_preselected_task(self, session, source_path):
        from media_pilot.repository.repositories import (
            IngestTaskCreate,
            IngestTaskRepository,
        )

        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path=source_path,
                status="agent_running",
                current_step="agent_start",
                media_type="movie",
                preselected_metadata_provider="tmdb",
                preselected_metadata_external_id="movie:597",
                preselected_metadata_profile=None,
            )
        )
        task.title = "Titanic"
        task.year = 1997
        session.commit()
        return task

    def test_get_auto_ingest_eligibility_exposes_preselected(self, tmp_path):
        """get_auto_ingest_eligibility 命中 preselected 时, 顶层 data 必
        须带 ``preselected`` 块, 字段稳定 (provider / provider_id / media
        _type / confidence / source='task_preselected')."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        movies_dir = config.movies_dir
        movies_dir.mkdir(parents=True, exist_ok=True)
        video_path = movies_dir / "movie.mkv"
        video_path.write_bytes(b"x" * 100)

        task_id = None
        with sf() as session:
            task = self._make_preselected_task(session, str(video_path))
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_get_auto_ingest_eligibility

        tool = make_get_auto_ingest_eligibility()
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )

        assert result.status == "success"
        assert "preselected" in result.data
        preselected = result.data["preselected"]
        assert preselected["provider"] == "tmdb"
        assert preselected["provider_id"] == "movie:597"
        assert preselected["media_type"] == "movie"
        assert preselected["confidence"] == 1.0
        assert preselected["source"] == "task_preselected"
        # eligibility 必须走旁路, has_clear_winner=True
        assert result.data["has_clear_winner"] is True
        assert result.data["eligible"] is True
        assert "no_metadata_candidates" not in result.data["blocking_reasons"]

    def test_get_metadata_candidates_exposes_preselected(self, tmp_path):
        """get_metadata_candidates 命中 preselected 时, 顶层 data 必
        须带 ``preselected`` 块, 暴露真 provider (e.g. "tmdb") +
        candidate_source="preselected". 即便持久化候选列表为空,
        READ_ONLY 工具边界要求不得主动落库 — candidates 列表保持
        空, data.preselected 构造自 task 字段."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        task_id = None
        with sf() as session:
            task = self._make_preselected_task(session, "/data/test.mkv")
            task_id = task.id

            # 记录调用前的 MediaCandidate 数量, 验证 READ_ONLY 工具
            # 边界 — 不得隐式落库.
            from media_pilot.repository.repositories import (
                MediaCandidateRepository,
            )
            cand_count_before = len(
                MediaCandidateRepository(session).list_for_task(task_id)
            )

        from media_pilot.agent.tools.read_only import make_get_metadata_candidates

        tool = make_get_metadata_candidates()
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )

            # 关键回归: 不得新增 MediaCandidate (READ_ONLY 边界)
            from media_pilot.repository.repositories import (
                MediaCandidateRepository,
            )
            cand_count_after = len(
                MediaCandidateRepository(session).list_for_task(task_id)
            )
            assert cand_count_after == cand_count_before, (
                "get_metadata_candidates 不得新增 MediaCandidate; "
                f"before={cand_count_before}, after={cand_count_after}"
            )

        assert result.status == "success"
        # candidates 列表必须为空 (READ_ONLY 不得落库)
        assert result.data["candidates"] == [], (
            f"get_metadata_candidates 在无持久化 candidates 时不得"
            f"自动填充; actual={result.data['candidates']}"
        )
        # data.preselected 必须暴露, provider 字段是真 provider
        assert "preselected" in result.data
        preselected = result.data["preselected"]
        assert preselected["provider"] == "tmdb"
        assert preselected["provider_id"] == "movie:597"
        assert preselected["media_type"] == "movie"
        assert preselected["confidence"] == 1.0
        assert preselected["candidate_source"] == "preselected"
        assert preselected["source"] == "task_preselected"
        assert preselected["title"] == "Titanic"
        assert preselected["year"] == 1997

    def test_get_current_metadata_does_not_expose_preselected(self, tmp_path):
        """get_current_metadata 只看 MetadataDetail 持久化事实, 不暴露
        preselected 强事实. 这是有意的: 该工具的语义是"已有真实
        detail", preselected 是 preselection, 语义不同."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        task_id = None
        with sf() as session:
            task = self._make_preselected_task(session, "/data/test.mkv")
            task_id = task.id

        from media_pilot.agent.tools.read_only import make_get_current_metadata

        tool = make_get_current_metadata()
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )

        assert result.status == "success"
        # 没有真实 detail → exists=False, 也不暴露 preselected 块
        assert result.data["exists"] is False
        assert "preselected" not in result.data

    def test_get_metadata_candidates_with_persisted_candidate_uses_real_provider(
        self, tmp_path,
    ):
        """get_metadata_candidates + task 携带 preselected + 已持久化
        source='preselected' 候选 → data.preselected.provider 必须是
        真 metadata provider (从 payload.preselected_provider 读),
        不是 "preselected". 候选列表里这条 candidate 的 provider 字
        段同样必须是真 provider, candidate_source 单独标记 "preselected"."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        task_id = None
        with sf() as session:
            task = self._make_preselected_task(session, "/data/test.mkv")
            task_id = task.id
            # 落库一条 source="preselected" 候选, payload 写真 provider.
            # 模拟 DRAFT 路径已经走过的场景.
            from media_pilot.repository.models import MediaCandidate
            session.add(MediaCandidate(
                task_id=task_id, source="preselected",
                media_type="movie", title="Titanic", year=1997,
                external_id="movie:597", confidence=1.0,
                reason="preselected from DownloadTask",
                payload={"preselected_provider": "tmdb"},
            ))
            session.commit()

        from media_pilot.agent.tools.read_only import make_get_metadata_candidates

        tool = make_get_metadata_candidates()
        with sf() as session:
            result = tool.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )

        assert result.status == "success"
        # 候选列表里这条 preselected candidate 的 provider 字段必须是
        # "tmdb" (从 payload.preselected_provider 读), 不是 "preselected"
        cand = next(
            (c for c in result.data["candidates"]
             if c.get("external_id") == "movie:597"),
            None,
        )
        assert cand is not None
        assert cand["provider"] == "tmdb", (
            f"preselected candidate.provider 必须是真 provider; "
            f"actual={cand['provider']!r}"
        )
        assert cand["candidate_source"] == "preselected", (
            f"candidate_source 字段标记来源; "
            f"actual={cand.get('candidate_source')!r}"
        )
        # data.preselected 同样
        psf = result.data["preselected"]
        assert psf["provider"] == "tmdb"
        assert psf["candidate_source"] == "preselected"
        assert psf["title"] == "Titanic"
        assert psf["year"] == 1997

    def test_read_only_tools_do_not_create_metadata_detail(
        self, tmp_path,
    ):
        """regression: READ_ONLY 工具 (get_auto_ingest_eligibility /
        get_metadata_candidates) 不得隐式调 fetch_and_save_metadata_
        detail 拉网络. 落库 MetadataDetail 是 WRITE 工具的事."""
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        task_id = None
        with sf() as session:
            task = self._make_preselected_task(session, "/data/test.mkv")
            task_id = task.id
            from media_pilot.repository.repositories import (
                MetadataDetailRepository,
            )
            detail_before = MetadataDetailRepository(session).get_for_task(task_id)

        # 调 get_auto_ingest_eligibility
        from media_pilot.agent.tools.read_only import (
            make_get_auto_ingest_eligibility,
            make_get_metadata_candidates,
        )

        with sf() as session:
            get_auto_ingest_eligibility = make_get_auto_ingest_eligibility()
            get_auto_ingest_eligibility.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )
        with sf() as session:
            get_metadata_candidates = make_get_metadata_candidates()
            get_metadata_candidates.handler(
                _make_tool_context(session, config, task_id),
                {"task_id": task_id},
            )

        with sf() as session:
            from media_pilot.repository.repositories import (
                MetadataDetailRepository,
            )
            detail_after = MetadataDetailRepository(session).get_for_task(task_id)
            # 关键: 调 READ_ONLY 工具前后 MetadataDetail 状态一致 (都 None
            # 或都同一 record). 工具不得隐式拉网络 + 落库.
            assert (detail_before is None) == (detail_after is None), (
                f"READ_ONLY 工具不得新增/删除 MetadataDetail; "
                f"before={detail_before}, after={detail_after}"
            )
