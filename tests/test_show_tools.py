"""Task 3: 剧集 Agent 工具 (prepare_show_structure + publish_show_to_library) 测试.

覆盖场景:
- prepare_show_structure: 单集 S01E01 → auto_publishable, 落库 EpisodeMapping
- prepare_show_structure: 同季 S01E01..E03 → auto_publishable, 落库 3 条
- prepare_show_structure: 跨季 → 失败, 写入 agent_failed
- prepare_show_structure: 稀疏集 → 失败
- prepare_show_structure: 单文件多集 → 失败
- prepare_show_structure: Season 0 → 失败
- prepare_show_structure: 缺失 task / 缺失 source_path → 失败
- publish_show_to_library: 缺 EpisodeMapping → 失败
- publish_show_to_library: 缺 MetadataDetail → 失败
- publish_show_to_library: MetadataDetail.media_type != show → 失败
- publish_show_to_library: 目标冲突 → target_conflict 决策
- publish_show_to_library: 成功发布 → task 进 library_import_complete
- publish_show_to_library: 已 library_import_complete → 幂等
- publish_show_to_library: agent_failed 状态 → 拒绝
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────


def _make_config(tmp_path: Path):
    from media_pilot.config.settings import AppConfig

    return AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "ws",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path,
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        tmdb_api_key="test-tmdb-key",
    )


def _make_session_factory(tmp_path: Path):
    from media_pilot.repository.database import create_session_factory, initialize_database
    config = _make_config(tmp_path)
    initialize_database(config)
    return create_session_factory(config)


def _make_task(
    session, *, source_path: str, media_type: str | None = None,
    status: str = "discovered", current_step: str = "agent_start",
):
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

    task = IngestTaskRepository(session).create(IngestTaskCreate(
        source_path=source_path,
        status=status, current_step=current_step, media_type=media_type,
    ))
    session.commit()
    return task


def _make_run(session, task_id: str, *, status: str = "active",
              current_step: str | None = None):
    from media_pilot.repository.repositories import (
        AgentRunCreate,
        AgentRunRepository,
    )

    run = AgentRunRepository(session).create(
        AgentRunCreate(task_id=task_id, current_step=current_step),
    )
    if status != "active":
        AgentRunRepository(session).update_status(
            run, status=status, current_step=current_step,
        )
    session.commit()
    return run


def _tool_context(session, config, task_id: str, run_id: str | None = None):
    from media_pilot.agent.tools.base import ToolContext
    return ToolContext(
        session=session, config=config, task_id=task_id, run_id=run_id,
    )


def _make_metadata_detail(
    session,
    task_id: str,
    *,
    media_type: str = "show",
    provider: str = "tmdb",
    payload: dict | None = None,
):
    from media_pilot.repository.repositories import MetadataDetailRepository
    repo = MetadataDetailRepository(session)
    repo.save(
        task_id=task_id,
        provider=provider,
        provider_id="tmdb:show-1",
        media_type=media_type,
        title="Example Show",
        original_title="Example Show",
        year=2024,
        payload=payload or {
            "plot": "A show.",
            "genres": ["Drama"],
            "studios": ["Studio A"],
            "directors": [],
            "actors": [],
            "images": {
                "poster_url": None,
                "backdrop_url": None,
                "logo_url": None,
            },
            "external_ids": {"imdb_id": None},
        },
    )
    session.commit()


# ── prepare_show_structure ─────────────────────────────────────


class TestPrepareShowStructure:
    def test_single_episode_auto_publishable(self, tmp_path: Path):
        from media_pilot.agent.tools.show import (
            make_prepare_show_structure,
        )
        from media_pilot.repository.repositories import EpisodeMappingRepository

        source = tmp_path / "Example.Show.S01E01.mkv"
        source.write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_prepare_show_structure()

        with sf() as session:
            task = _make_task(session, source_path=str(source))
            ctx = _tool_context(session, _make_config(tmp_path), task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            session.commit()

            assert result.status == "success"
            assert result.data["auto_publishable"] is True
            assert result.data["season"] == 1
            assert result.data["episode_range"] == "S01E01"
            assert result.data["episode_count"] == 1

            mappings = EpisodeMappingRepository(session).get_by_task(task.id)
            assert len(mappings) == 1
            assert mappings[0].season == 1
            assert mappings[0].episode == 1

    def test_continuous_multi_episode_auto_publishable(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_prepare_show_structure
        from media_pilot.repository.repositories import EpisodeMappingRepository

        for i in range(1, 4):
            (tmp_path / f"Show.S01E{i:02d}.mkv").write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_prepare_show_structure()

        with sf() as session:
            task = _make_task(session, source_path=str(tmp_path))
            ctx = _tool_context(session, _make_config(tmp_path), task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            session.commit()

            assert result.status == "success"
            assert result.data["episode_range"] == "S01E01-E03"
            assert result.data["episode_count"] == 3
            mappings = EpisodeMappingRepository(session).get_by_task(task.id)
            assert len(mappings) == 3
            assert sorted(m.episode for m in mappings) == [1, 2, 3]

    def test_cross_season_blocked(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_prepare_show_structure

        (tmp_path / "Show.S01E05.mkv").write_bytes(b"v")
        (tmp_path / "Show.S02E01.mkv").write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_prepare_show_structure()

        with sf() as session:
            task = _make_task(session, source_path=str(tmp_path))
            ctx = _tool_context(session, _make_config(tmp_path), task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            session.commit()

            assert result.status == "failure"
            assert result.data["block_reason"] == "cross_season_not_supported"
            # 任务进入 agent_failed, failure_reason 写明
            assert task.status == "agent_failed"
            assert task.failure_reason == "cross_season_not_supported"

    def test_sparse_episodes_blocked(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_prepare_show_structure

        (tmp_path / "Show.S01E01.mkv").write_bytes(b"v")
        (tmp_path / "Show.S01E03.mkv").write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_prepare_show_structure()

        with sf() as session:
            task = _make_task(session, source_path=str(tmp_path))
            ctx = _tool_context(session, _make_config(tmp_path), task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            session.commit()

            assert result.status == "failure"
            assert result.data["block_reason"] == "sparse_episodes_not_supported"
            assert task.failure_reason == "sparse_episodes_not_supported"

    def test_season_0_blocked(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_prepare_show_structure

        (tmp_path / "Show.S00E01.mkv").write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_prepare_show_structure()

        with sf() as session:
            task = _make_task(session, source_path=str(tmp_path))
            ctx = _tool_context(session, _make_config(tmp_path), task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            session.commit()

            assert result.status == "failure"
            assert result.data["block_reason"] == "specials_season_0_not_supported"

    def test_single_file_multi_episode_blocked(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_prepare_show_structure

        source = tmp_path / "Show.S01E01E02.mkv"
        source.write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_prepare_show_structure()

        with sf() as session:
            task = _make_task(session, source_path=str(source))
            ctx = _tool_context(session, _make_config(tmp_path), task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            session.commit()

            assert result.status == "failure"
            assert (
                result.data["block_reason"]
                == "multi_episode_in_single_file_not_supported"
            )

    def test_missing_task_returns_failure(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_prepare_show_structure

        sf = _make_session_factory(tmp_path)
        tool = make_prepare_show_structure()

        with sf() as session:
            ctx = _tool_context(session, _make_config(tmp_path), "missing")
            result = tool.handler(ctx, {"task_id": "missing"})
            assert result.status == "failure"

    def test_missing_source_path_returns_failure(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_prepare_show_structure

        sf = _make_session_factory(tmp_path)
        tool = make_prepare_show_structure()

        with sf() as session:
            task = _make_task(session, source_path="")
            ctx = _tool_context(session, _make_config(tmp_path), task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            assert result.status == "failure"
            assert "source_path" in result.summary.lower()

    def test_sets_task_media_type_to_show(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_prepare_show_structure

        source = tmp_path / "Show.S01E01.mkv"
        source.write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_prepare_show_structure()

        with sf() as session:
            task = _make_task(session, source_path=str(source), media_type=None)
            ctx = _tool_context(session, _make_config(tmp_path), task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            session.commit()

            assert result.status == "success"
            assert task.media_type == "show"

    def test_absolute_numbering_without_detail_stays_continueable(
        self, tmp_path: Path,
    ):
        from media_pilot.agent.tools.show import make_prepare_show_structure
        from media_pilot.repository.repositories import IngestTaskRepository

        rezero_dir = tmp_path / "ReZero 3rd Season"
        rezero_dir.mkdir()
        for n in range(51, 67):
            (rezero_dir / f"[{n}].mkv").write_bytes(b"v")

        sf = _make_session_factory(tmp_path)
        tool = make_prepare_show_structure()
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_task(
                session,
                source_path=str(rezero_dir),
                media_type=None,
                status="agent_running",
                current_step="user_replied",
            )
            run = _make_run(session, task.id, status="active")
            ctx = _tool_context(session, config, task.id, run.id)

            result = tool.handler(ctx, {"task_id": task.id})
            session.commit()

            assert result.status == "success"
            assert result.data["auto_publishable"] is False
            assert result.data["requires_metadata_detail"] is True
            assert (
                result.data["block_reason"]
                == "absolute_episode_requires_metadata_detail"
            )

            task_after = IngestTaskRepository(session).get(task.id)
            assert task_after.media_type == "show"
            assert task_after.status == "agent_running"
            assert task_after.failure_reason is None

    def test_no_op_for_non_show_task_with_no_clear_structure(self, tmp_path: Path):
        """防御性旁路: LLM 误把 prepare_show_structure 用在非 show 任务
        (普通单文件电影) 上, 输入完全没有剧集结构
        (no_clear_show_structure). 工具必须返回 failure 但不得把
        task 切 agent_failed, 也不得把 run 切 failed. 让 LLM 看到失败
        摘要后可以继续走 movie 路径."""
        from media_pilot.agent.tools.show import make_prepare_show_structure

        source = tmp_path / "Example.Movie.2026.mkv"
        source.write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_prepare_show_structure()
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_task(
                session, source_path=str(source),
                media_type="movie", status="agent_running",
                current_step="user_replied",
            )
            run = _make_run(session, task.id, status="active")
            session.commit()

            ctx = _tool_context(session, config, task.id, run.id)
            result = tool.handler(ctx, {"task_id": task.id})
            session.commit()

            # 工具返回 failure, 但摘要里必须明确"not a show"
            assert result.status == "failure"
            assert result.data["not_a_show"] is True
            assert result.data["block_reason"] == "no_clear_show_structure"
            assert result.data["media_type"] == "movie"
            assert "not a show" in result.summary.lower()

            # 关键: task 不能被切到 agent_failed (这是普通电影, 不是 show)
            task_after = type(task)
            from media_pilot.repository.repositories import (
                AgentRunRepository,
                IngestTaskRepository,
            )
            task_after = IngestTaskRepository(session).get(task.id)
            assert task_after.status != "agent_failed", (
                f"非 show 任务被误判 agent_failed, 实际 status="
                f"{task_after.status}"
            )
            assert task_after.failure_reason != "no_clear_show_structure"
            # run 也必须保持 active (不能再写 failure_reason)
            run_after = AgentRunRepository(session).get(run.id)
            assert run_after.status != "failed", (
                f"非 show run 被误判 failed, 实际 status={run_after.status}"
            )
            assert run_after.error_message != "no_clear_show_structure"

    def test_real_show_task_with_no_clear_structure_still_fails(self, tmp_path: Path):
        """对照: 真正的 show 任务 (media_type='show') 碰到
        no_clear_show_structure, 仍走原有失败路径, 写 agent_failed /
        run failed. 防御性旁路不能影响真实失败语义."""
        from media_pilot.agent.tools.show import make_prepare_show_structure

        source = tmp_path / "Some.Misnamed.File.mkv"
        source.write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_prepare_show_structure()
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_task(
                session, source_path=str(source),
                media_type="show", status="agent_running",
                current_step="user_replied",
            )
            run = _make_run(session, task.id, status="active")
            session.commit()

            ctx = _tool_context(session, config, task.id, run.id)
            result = tool.handler(ctx, {"task_id": task.id})
            session.commit()

            assert result.status == "failure"
            assert result.data["block_reason"] == "no_clear_show_structure"
            # not_a_show 键不应存在 (真 show 任务不走防御旁路,
            # 失败路径里 data 不携带 not_a_show 标识)
            assert "not_a_show" not in result.data

            from media_pilot.repository.repositories import (
                AgentRunRepository,
                IngestTaskRepository,
            )
            task_after = IngestTaskRepository(session).get(task.id)
            run_after = AgentRunRepository(session).get(run.id)
            assert task_after.status == "agent_failed"
            assert task_after.failure_reason == "no_clear_show_structure"
            assert run_after.status == "failed"
            assert run_after.error_message == "no_clear_show_structure"


# ── publish_show_to_library ────────────────────────────────────


class TestPublishShowToLibrary:
    def test_missing_episode_mapping_returns_failure(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_publish_show_to_library

        sf = _make_session_factory(tmp_path)
        tool = make_publish_show_to_library()
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_task(
                session, source_path=str(tmp_path), media_type="show",
                status="agent_running", current_step="user_replied",
            )
            ctx = _tool_context(session, config, task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            assert result.status == "failure"
            assert result.data["reason"] == "no_episode_mapping"

    def test_recovers_absolute_episode_mapping_after_metadata_detail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        from contextlib import contextmanager

        import httpx

        from media_pilot.agent.tools.show import (
            make_prepare_show_structure,
            make_publish_show_to_library,
        )
        from media_pilot.repository.repositories import (
            EpisodeMappingRepository,
            IngestTaskRepository,
        )

        rezero_dir = tmp_path / "ReZero 3rd Season"
        rezero_dir.mkdir()
        for n in range(51, 67):
            (rezero_dir / f"[{n}].mkv").write_bytes(b"v")

        @contextmanager
        def _fake_client(*args, **kwargs):
            yield None

        monkeypatch.setattr(httpx, "Client", _fake_client)

        from media_pilot.orchestration import jellyfin_show_writer

        def _patched_download_image(client, url, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
            return b""

        monkeypatch.setattr(
            jellyfin_show_writer, "_download_image", _patched_download_image,
        )

        sf = _make_session_factory(tmp_path)
        prep_tool = make_prepare_show_structure()
        publish_tool = make_publish_show_to_library()
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_task(
                session,
                source_path=str(rezero_dir),
                media_type=None,
                status="agent_running",
                current_step="user_replied",
            )
            prep_ctx = _tool_context(session, config, task.id)
            prep_result = prep_tool.handler(prep_ctx, {"task_id": task.id})
            assert prep_result.status == "success"
            assert prep_result.data["requires_metadata_detail"] is True
            assert EpisodeMappingRepository(session).get_by_task(task.id) == []

            _make_metadata_detail(
                session,
                task.id,
                media_type="show",
                payload={
                    "plot": "A show.",
                    "genres": ["Drama"],
                    "studios": ["Studio A"],
                    "directors": [],
                    "actors": [],
                    "images": {
                        "poster_url": "https://img.test/poster.jpg",
                        "backdrop_url": None,
                        "logo_url": None,
                    },
                    "external_ids": {"imdb_id": None},
                    "raw": {
                        "seasons": [
                            {"season_number": 1, "episode_count": 66},
                        ]
                    },
                },
            )

            ctx = _tool_context(session, config, task.id)
            result = publish_tool.handler(ctx, {"task_id": task.id})
            session.commit()

            assert result.status == "success", result.summary
            assert result.data["episode_count"] == 16
            mappings = EpisodeMappingRepository(session).get_by_task(task.id)
            assert len(mappings) == 16
            assert mappings[0].season == 1
            assert mappings[0].episode == 51
            assert mappings[-1].episode == 66
            task_after = IngestTaskRepository(session).get(task.id)
            assert task_after.status == "library_import_complete"

    def test_missing_metadata_detail_returns_failure(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_publish_show_to_library
        from media_pilot.repository.repositories import EpisodeMappingRepository

        source = tmp_path / "Show.S01E01.mkv"
        source.write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_publish_show_to_library()
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_task(
                session, source_path=str(source), media_type="show",
                status="agent_running", current_step="user_replied",
            )
            EpisodeMappingRepository(session).save_mappings(
                task_id=task.id,
                entries=[{
                    "file_path": str(source), "season": 1, "episode": 1,
                    "source": "filename",
                }],
            )
            session.commit()
            ctx = _tool_context(session, config, task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            assert result.status == "failure"
            assert result.data["reason"] == "no_metadata_detail"

    def test_wrong_media_type_returns_failure(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_publish_show_to_library
        from media_pilot.repository.repositories import EpisodeMappingRepository

        source = tmp_path / "Show.S01E01.mkv"
        source.write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_publish_show_to_library()
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_task(
                session, source_path=str(source), media_type="show",
                status="agent_running", current_step="user_replied",
            )
            EpisodeMappingRepository(session).save_mappings(
                task_id=task.id,
                entries=[{
                    "file_path": str(source), "season": 1, "episode": 1,
                    "source": "filename",
                }],
            )
            # metadata type = movie (误存)
            _make_metadata_detail(session, task.id, media_type="movie")
            ctx = _tool_context(session, config, task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            assert result.status == "failure"
            assert result.data["reason"] == "wrong_media_type"

    def test_terminal_status_refuses_publish(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_publish_show_to_library

        sf = _make_session_factory(tmp_path)
        tool = make_publish_show_to_library()
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_task(
                session, source_path=str(tmp_path), media_type="show",
                status="agent_failed", current_step="agent_failed",
            )
            ctx = _tool_context(session, config, task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            assert result.status == "failure"
            assert "terminal" in result.summary.lower()

    def test_already_published_is_idempotent(self, tmp_path: Path):
        from media_pilot.agent.tools.show import make_publish_show_to_library

        sf = _make_session_factory(tmp_path)
        tool = make_publish_show_to_library()
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_task(
                session, source_path=str(tmp_path), media_type="show",
                status="library_import_complete",
                current_step="library_import_complete",
            )
            ctx = _tool_context(session, config, task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            assert result.status == "success"
            assert result.data["already_published"] is True

    def test_publish_succeeds_for_single_episode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """完整发布路径: 单集 → 写 staging → 移入 library → 任务 library_import_complete."""
        from media_pilot.agent.tools.show import make_publish_show_to_library
        from media_pilot.repository.repositories import (
            EpisodeMappingRepository,
            IngestTaskRepository,
        )

        source = tmp_path / "Show.S01E01.mkv"
        source.write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_publish_show_to_library()
        config = _make_config(tmp_path)

        # monkeypatch httpx.Client, 让 _download_image 跳过真实网络
        import httpx
        from contextlib import contextmanager

        @contextmanager
        def _fake_client(*args, **kwargs):
            yield None

        monkeypatch.setattr(httpx, "Client", _fake_client)

        # 显式准备一个写好的 poster 图, 让 _download_image 不再返回 None.
        # 改写 _download_image 让它在 client=None 时不报错且返回空 bytes.
        from media_pilot.orchestration import jellyfin_show_writer

        def _patched_download_image(client, url, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
            return b""

        monkeypatch.setattr(
            jellyfin_show_writer, "_download_image", _patched_download_image,
        )

        with sf() as session:
            task = _make_task(
                session, source_path=str(source), media_type="show",
                status="agent_running", current_step="user_replied",
            )
            EpisodeMappingRepository(session).save_mappings(
                task_id=task.id,
                entries=[{
                    "file_path": str(source), "season": 1, "episode": 1,
                    "source": "filename",
                }],
            )
            _make_metadata_detail(session, task.id, media_type="show")
            session.commit()
            ctx = _tool_context(session, config, task.id)
            result = tool.handler(ctx, {"task_id": task.id})
            session.commit()

            assert result.status == "success", result.summary
            assert result.data["status"] in ("succeeded", "warning")
            assert result.data["media_type"] == "show"
            assert result.data["episode_count"] == 1
            # 任务进入 library_import_complete
            task_after = IngestTaskRepository(session).get(task.id)
            assert task_after.status == "library_import_complete"
            assert task_after.current_step == "library_import_complete"

    def test_target_conflict_creates_decision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """目标冲突 → 创建 target_conflict 决策."""
        from media_pilot.agent.tools.show import make_publish_show_to_library
        from media_pilot.repository.repositories import (
            AgentDecisionRequestRepository,
            EpisodeMappingRepository,
        )

        source = tmp_path / "Show.S01E01.mkv"
        source.write_bytes(b"v")
        sf = _make_session_factory(tmp_path)
        tool = make_publish_show_to_library()
        config = _make_config(tmp_path)

        with sf() as session:
            task = _make_task(
                session, source_path=str(source), media_type="show",
                status="agent_running", current_step="user_replied",
            )
            EpisodeMappingRepository(session).save_mappings(
                task_id=task.id,
                entries=[{
                    "file_path": str(source), "season": 1, "episode": 1,
                    "source": "filename",
                }],
            )
            _make_metadata_detail(session, task.id, media_type="show")
            run = _make_run(session, task.id, status="active")
            session.commit()

            # 预创建最终目标, 触发 episode 级冲突 (新语义: show / season
            # 目录已存在不再算冲突, 但具体 episode 视频已存在仍算).
            target_show_dir = (
                config.shows_dir / "Example Show (2024)"
            )
            target_season_dir = target_show_dir / "Season 01"
            target_season_dir.mkdir(parents=True, exist_ok=True)
            # 在 season 目录里放一个同 episode 名的 final target 文件 —
            # 这是真正的 target_episode_file_exists 冲突. 文件名遵循
            # build_show_write_plan 的 file_stem 约定: "<title> (<year>) -
            # SxxExx<ext>".
            (target_season_dir / "Example Show (2024) - S01E01.mkv").write_bytes(b"existing")

            ctx = _tool_context(session, config, task.id, run.id)
            result = tool.handler(ctx, {"task_id": task.id})
            session.commit()

            assert result.status == "success"
            assert result.data["decision_requested"] is True
            assert result.data["decision_type"] == "target_conflict"
            decision_id = result.data["decision_id"]
            decision = AgentDecisionRequestRepository(session).get(decision_id)
            assert decision is not None
            assert decision.decision_type == "target_conflict"
            # payload 携带 show 标识
            assert decision.payload.get("media_type") == "show"
            # task 进入 waiting_user + current_step=target_conflict
            assert task.status == "waiting_user"
            assert task.current_step == "target_conflict"
