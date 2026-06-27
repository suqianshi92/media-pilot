from datetime import UTC, datetime

from media_pilot.api.task_dtos import DownloadTaskSummary
from media_pilot.api.task_mapper import (
    _determine_file_format,
    _format_from_path,
    _map_metadata_detail,
)
from media_pilot.repository.models import (
    MediaSourceSelection,
    MetadataDetail,
    new_id,
)


def test_map_metadata_detail_reads_current_tmdb_payload_shape() -> None:
    detail = MetadataDetail(
        task_id="task-1",
        provider="tmdb",
        provider_id="movie:568160",
        media_type="movie",
        title="天气之子",
        original_title="天気の子",
        year=2019,
        payload={
            "plot": "离家少年与拥有晴天能力的少女相遇。",
            "premiered": "2019-07-19",
            "runtime_minutes": 112,
            "rating": 7.9,
            "genres": ["Animation", "Romance"],
            "countries": ["JP"],
            "studios": ["CoMix Wave Films"],
            "credits": {
                "directors": [
                    {
                        "provider": "tmdb",
                        "provider_id": "person:1",
                        "name": "新海诚",
                        "role": "Director",
                        "profile_url": None,
                        "image_url": "https://image.test/director.jpg",
                    }
                ],
                "actors": [
                    {
                        "provider": "tmdb",
                        "provider_id": "person:2",
                        "name": "醍醐虎汰朗",
                        "role": "森岛帆高",
                        "profile_url": None,
                        "image_url": "https://image.test/actor.jpg",
                    }
                ],
            },
            "external_ids": {
                "imdb_id": "tt9426210",
                "payload": {"tmdb_id": "568160"},
            },
            "images": {
                "poster_url": "https://image.test/poster.jpg",
                "backdrop_url": "https://image.test/fanart.jpg",
                "logo_url": "https://image.test/logo.png",
            },
        },
    )

    mapped = _map_metadata_detail("task-1", detail, [])

    assert mapped is not None
    assert mapped.provider == "tmdb"
    assert mapped.title == "天气之子"
    assert mapped.year == 2019
    assert len(mapped.genres) == 2
    assert len(mapped.directors) == 1
    assert len(mapped.actors) == 1


# 注: test_map_confirmation_request_handles_raw_keyword_source 已随
# _map_confirmation_request 一起删除。ConfirmationRequest 旧通道在
# replace-legacy-confirmation-with-agent-decisions 完全下线。
# raw → rule 关键词归一逻辑在 SearchKeyword DTO 序列化层保留，详见
# media_pilot.api.task_dtos.SearchKeywordDto。

# ── 文件格式判定测试 ──


def test_format_from_path_mkv() -> None:
    assert _format_from_path("/data/downloads/movie.mkv") == "MKV"


def test_format_from_path_mp4() -> None:
    assert _format_from_path("/data/movie.mp4") == "MP4"


def test_format_from_path_iso() -> None:
    assert _format_from_path("/data/disc.iso") == "ISO"


def test_format_from_path_dir_returns_dir() -> None:
    assert _format_from_path("/tmp") == "目录"


def test_format_from_path_unknown_extension() -> None:
    assert _format_from_path("/data/file.txt") == "未知"


def test_determine_file_format_from_selection_mkv() -> None:
    sel = MediaSourceSelection(
        id=new_id(),
        task_id="task-1",
        input_path="/data/downloads",
        selected_path="/data/downloads/movie.mkv",
        confidence=0.95,
        reason="test",
    )
    result = _determine_file_format(sel, None)
    assert result == "MKV"


def test_determine_file_format_from_selection_bdmv() -> None:
    sel = MediaSourceSelection(
        id=new_id(),
        task_id="task-1",
        input_path="/data/bdmv_dir",
        selected_path=None,
        confidence=0.0,
        reason="bdmv_detected",
        payload={"bdmv_detected": True, "stream_file_count": 5},
    )
    result = _determine_file_format(sel, None)
    assert result == "BDMV"


def test_determine_file_format_from_bdmv_source_kind() -> None:
    sel = MediaSourceSelection(
        id=new_id(),
        task_id="task-1",
        input_path="/data/movie",
        selected_path=None,
        confidence=1.0,
        reason="auto_bdmv_movie_dir",
        payload={"source_kind": "bdmv", "bdmv_dir": "/data/movie/BDMV"},
    )
    result = _determine_file_format(sel, None)
    assert result == "BDMV"


def test_determine_file_format_from_download_content_path() -> None:
    dl = DownloadTaskSummary(
        id="dl-1",
        title="test",
        source="prowlarr",
        save_path="/data/downloads",
        content_path="/data/downloads/test.iso",
        status="completed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    result = _determine_file_format(None, dl)
    assert result == "ISO"


def test_determine_file_format_fallback_none() -> None:
    result = _determine_file_format(None, None)
    assert result is None


def test_determine_file_format_dir_selected_path() -> None:
    sel = MediaSourceSelection(
        id=new_id(),
        task_id="task-1",
        input_path="/data/dir_input",
        selected_path="/data/dir_input",
        confidence=0.5,
        reason="no_supported_video",
    )
    result = _determine_file_format(sel, None)
    assert result == "目录"


# ── current_step 稳定性回归 ──


def test_map_to_task_summaries_with_agent_failed_step(tmp_path) -> None:
    """任务同时为 status=agent_failed + current_step=agent_failed 时,
    mapper 不应让 Pydantic 校验失败. 这一组合是任务工作台触发的合法
    稳态 (Agent runner 写入), 列表 / 详情 API 都依赖 mapper 不抛错.
    回归保护: 收口前 DTO 用 TaskStep 枚举校验, 写库时合法稳态会让
    /api/v1/tasks 整页 500, 前端按 JSON 解析纯文本错误体而崩.
    """

    from sqlalchemy import select

    from media_pilot.api.task_mapper import map_to_task_summaries
    from media_pilot.config import AppConfig
    from media_pilot.repository.database import create_session_factory, initialize_database
    from media_pilot.repository.models import IngestTask
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
    )
    for d in (
        config.downloads_dir, config.watch_dir, config.workspace_dir,
        config.movies_dir, config.shows_dir, config.database_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    sf = create_session_factory(config)
    with sf() as session:
        IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path="/media/downloads/agent-failed.mkv",
                status="agent_failed", current_step="agent_failed",
                media_type="movie", failure_reason="LLM 错误",
            )
        )
        session.commit()
        task = session.scalar(select(IngestTask))
        assert task is not None
        summaries = map_to_task_summaries(session, [task])

    assert len(summaries) == 1
    assert summaries[0].status_summary.status == "agent_failed"
    assert summaries[0].status_summary.current_step == "agent_failed"
    assert summaries[0].status_summary.failure_reason == "LLM 错误"


def test_map_to_task_summaries_with_dynamic_step_marker(tmp_path) -> None:
    """动态 / 临时 marker (如 runner 写的 `step_N` / 复杂输入决策的
    decision_type) 在 DB 是 String(128), DTO 必须按字符串接收, 不得 500.
    """

    from sqlalchemy import select

    from media_pilot.api.task_mapper import map_to_task_summaries
    from media_pilot.config import AppConfig
    from media_pilot.repository.database import create_session_factory, initialize_database
    from media_pilot.repository.models import IngestTask
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
    )
    for d in (
        config.downloads_dir, config.watch_dir, config.workspace_dir,
        config.movies_dir, config.shows_dir, config.database_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    sf = create_session_factory(config)
    with sf() as session:
        repo = IngestTaskRepository(session)
        repo.create(IngestTaskCreate(
            source_path="/media/downloads/dyn1.mkv",
            status="agent_running", current_step="step_3",
        ))
        repo.create(IngestTaskCreate(
            source_path="/media/watch/dyn2.mkv",
            status="waiting_user", current_step="select_primary_video",
        ))
        session.commit()
        tasks = session.scalars(select(IngestTask)).all()
        summaries = map_to_task_summaries(session, tasks)

    assert len(summaries) == 2
    seen = {s.status_summary.current_step for s in summaries}
    assert seen == {"step_3", "select_primary_video"}


# ── Show structure summary (fix-show-absolute-episode-ingest-and-agent-search-loop §6) ──


class TestShowStructureSummary:
    def test_show_structure_summary_for_absolute_episode_range(self, tmp_path):
        from media_pilot.api.task_mapper import _build_show_structure_summary
        from media_pilot.repository.models import IngestTask, EpisodeMapping

        from sqlalchemy.orm import Session
        from media_pilot.config import AppConfig
        from media_pilot.repository.database import (
            create_session_factory, initialize_database,
        )

        config = AppConfig(
            downloads_dir=tmp_path / "downloads",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "workspace",
            movies_dir=tmp_path / "library" / "movies",
            shows_dir=tmp_path / "library" / "shows",
            database_dir=tmp_path / "db",
        )
        for d in (config.downloads_dir, config.watch_dir,
                  config.workspace_dir, config.movies_dir,
                  config.shows_dir, config.database_dir):
            d.mkdir(parents=True, exist_ok=True)
        initialize_database(config)
        sf = create_session_factory(config)

        with sf() as session:
            from media_pilot.repository.repositories import (
                EpisodeMappingRepository, IngestTaskCreate, IngestTaskRepository,
            )
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/x/[51].mkv",
                status="agent_running", current_step="agent_running",
                media_type="show",
            ))
            session.commit()
            mappings = [
                EpisodeMapping(
                    task_id=task.id, file_path=f"/x/[{n}].mkv",
                    season=1, episode=n, source="absolute",
                )
                for n in range(51, 67)
            ]
            summary = _build_show_structure_summary(
                task=session.get(IngestTask, task.id),
                episode_mappings=mappings,
            )
            session.commit()

        assert summary is not None
        assert summary.status == "auto_publishable"
        assert summary.season == 1
        assert summary.episode_range == "S01E51-E66"
        assert summary.episode_count == 16
        assert summary.mapping_mode == "absolute"
        assert summary.mapping_mode_label == "absolute_episode_numbering"
        assert summary.block_reason is None

    def test_show_structure_summary_for_blocked_task(self, tmp_path):
        """任务失败时 summary 暴露 i18n key + 人话 message, 不暴露 raw JSON."""
        from media_pilot.api.task_mapper import _build_show_structure_summary
        from media_pilot.config import AppConfig
        from media_pilot.repository.database import (
            create_session_factory, initialize_database,
        )

        config = AppConfig(
            downloads_dir=tmp_path / "downloads",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "workspace",
            movies_dir=tmp_path / "library" / "movies",
            shows_dir=tmp_path / "library" / "shows",
            database_dir=tmp_path / "db",
        )
        for d in (config.downloads_dir, config.watch_dir,
                  config.workspace_dir, config.movies_dir,
                  config.shows_dir, config.database_dir):
            d.mkdir(parents=True, exist_ok=True)
        initialize_database(config)
        sf = create_session_factory(config)

        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate, IngestTaskRepository,
            )
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/x/cross.mkv",
                status="agent_failed",
                current_step="cross_season_not_supported",
                failure_reason="cross_season_not_supported",
                media_type="show",
            ))
            session.commit()
            summary = _build_show_structure_summary(
                task=session.get(
                    __import__(
                        "media_pilot.repository.models",
                        fromlist=["IngestTask"],
                    ).IngestTask, task.id,
                ),
                episode_mappings=[],
            )
            session.commit()

        assert summary is not None
        assert summary.status == "blocked"
        assert summary.block_reason == "cross_season_not_supported"
        assert summary.block_reason_label == "show_block_cross_season"
        assert "跨季" in (summary.block_reason_message or "")

    def test_show_structure_summary_none_for_movie_task(self, tmp_path):
        """media_type=movie → summary 是 None (电影没有剧集结构)."""
        from media_pilot.api.task_mapper import _build_show_structure_summary
        from media_pilot.config import AppConfig
        from media_pilot.repository.database import (
            create_session_factory, initialize_database,
        )

        config = AppConfig(
            downloads_dir=tmp_path / "downloads",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "workspace",
            movies_dir=tmp_path / "library" / "movies",
            shows_dir=tmp_path / "library" / "shows",
            database_dir=tmp_path / "db",
        )
        for d in (config.downloads_dir, config.watch_dir,
                  config.workspace_dir, config.movies_dir,
                  config.shows_dir, config.database_dir):
            d.mkdir(parents=True, exist_ok=True)
        initialize_database(config)
        sf = create_session_factory(config)
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate, IngestTaskRepository,
            )
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/x/movie.mkv",
                status="agent_running", current_step="agent_running",
                media_type="movie",
            ))
            session.commit()
            from media_pilot.repository.models import IngestTask as IT
            t = session.get(IT, task.id)
            summary = _build_show_structure_summary(task=t, episode_mappings=[])
            session.commit()

        assert summary is None
