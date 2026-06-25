"""Shared publish plan draft service -- build movie/show write plans without side effects.

Returns typed ``PublishPlanDraft``. Does NOT persist, move files, or execute writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from media_pilot.adapters.metadata import (
    MetadataCredits,
    MetadataDetail,
    MetadataExternalIds,
    MetadataImages,
    MetadataPerson,
)
from media_pilot.config import AppConfig
from media_pilot.orchestration.jellyfin_movie_writer import MovieWritePlanDraft
from media_pilot.orchestration.jellyfin_show_writer import ShowWritePlanDraft
from media_pilot.services.library_root_resolver import resolve_library_root
from media_pilot.services.task_input_analysis import FileInfo


@dataclass(frozen=True, kw_only=True)
class PublishPlanDraft:
    media_type: str
    movie: MovieWritePlanDraft | None = None
    show: ShowWritePlanDraft | None = None
    subtitles: list[FileInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _orm_detail_to_adapter(orm_detail) -> MetadataDetail:
    """Convert ORM MetadataDetail to the adapter-level MetadataDetail."""
    payload = orm_detail.payload or {}

    def _dict_to_person(d: dict) -> MetadataPerson:
        return MetadataPerson(
            provider=orm_detail.provider,
            provider_id=d.get("provider_id"),
            name=d.get("name", ""),
            role=d.get("role"),
            profile_url=d.get("profile_url"),
            image_url=d.get("image_url"),
        )

    credits = MetadataCredits(
        directors=[_dict_to_person(d) for d in (payload.get("directors") or [])],
        actors=[_dict_to_person(d) for d in (payload.get("actors") or [])],
    )
    external_ids = MetadataExternalIds(imdb_id=payload.get("imdb_id"))
    images = MetadataImages(
        poster_url=payload.get("poster_url"),
        backdrop_url=payload.get("backdrop_url"),
        logo_url=payload.get("logo_url"),
    )

    return MetadataDetail(
        provider=orm_detail.provider,
        provider_id=orm_detail.provider_id,
        media_type=orm_detail.media_type,
        title=orm_detail.title or "",
        original_title=orm_detail.original_title,
        year=orm_detail.year,
        plot=payload.get("plot") or payload.get("overview"),
        runtime_minutes=payload.get("runtime_minutes"),
        premiered=payload.get("premiered") or payload.get("release_date"),
        rating=payload.get("rating"),
        genres=list(payload.get("genres") or []),
        countries=list(payload.get("countries") or payload.get("production_countries") or []),
        studios=list(payload.get("studios") or payload.get("production_companies") or []),
        credits=credits,
        external_ids=external_ids,
        images=images,
        payload=payload,
    )


def build_publish_plan_draft(
    *,
    session,
    config: AppConfig,
    task_id: str,
) -> PublishPlanDraft:
    """Build a movie or show write plan draft for the given task.

    For movie tasks, same-stem subtitles are included in the plan as a preview;
    missing or ambiguous subtitles produce warnings but never block the plan.

    Raises ``ValueError`` if the task is missing media_type or metadata detail,
    or if show structure detection fails.
    """
    from media_pilot.orchestration.jellyfin_movie_writer import build_movie_write_plan
    from media_pilot.orchestration.jellyfin_show_writer import (
        EpisodeTarget,
        build_show_write_plan,
    )
    from media_pilot.orchestration.search_keyword_generation import detect_show_structure
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MediaSourceSelectionRepository,
        MetadataDetailRepository,
    )
    from media_pilot.services.task_input_analysis import analyze_task_input

    task_repo = IngestTaskRepository(session)
    task = task_repo.get(task_id)
    if task is None:
        raise ValueError(f"Task {task_id} not found")

    if not task.media_type:
        raise ValueError("Task has no media_type; cannot determine write plan type")

    detail_repo = MetadataDetailRepository(session)
    orm_detail = detail_repo.get_for_task(task_id)
    if orm_detail is None:
        raise ValueError("No metadata detail exists for this task; cannot build write plan")

    adapter_detail = _orm_detail_to_adapter(orm_detail)

    if task.media_type == "show":
        source_path = Path(task.source_path)
        ep_result = detect_show_structure(source_path)
        if ep_result is None or not ep_result.entries:
            raise ValueError("Could not detect show structure (no SxxExx pattern found)")

        episodes = [
            EpisodeTarget(
                episode=e.episode,
                season=e.season,
                source_file=Path(e.file_path),
                target_file=Path(),
            )
            for e in ep_result.entries
        ]

        plan = build_show_write_plan(
            shows_dir=config.shows_dir,
            episodes=episodes,
            detail=adapter_detail,
            task_id=task.id,
            provider=orm_detail.provider,
        )

        return PublishPlanDraft(media_type="show", show=plan)

    # ── movie: scan for same-stem subtitles ─────────────────────────
    source_path = Path(task.source_path)
    source_sel_repo = MediaSourceSelectionRepository(session)
    selection = source_sel_repo.get_for_task(task.id)
    video_source = Path(selection.selected_path) if selection and selection.selected_path else source_path

    plan = build_movie_write_plan(
        movies_dir=resolve_library_root(
            config, media_type="movie", provider=orm_detail.provider,
        ),
        source_path=video_source,
        detail=adapter_detail,
        task_id=task.id,
        provider=orm_detail.provider,
    )

    # 字幕规划: 优先消费 MediaSourceSelection.payload.selected_subtitles
    # (用户已通过 select_subtitles 决策明确选择), 否则回退到 same-stem
    # 自动扫描. 用户未选择 → payload 没有该 key → 同源字幕自动带入,
    # 非同源字幕不进入发布.
    subtitles: list[FileInfo] = []
    warnings: list[str] = []
    user_selected_subs: list[str] = []
    if selection is not None and isinstance(selection.payload, dict):
        raw = selection.payload.get("selected_subtitles")
        if isinstance(raw, list):
            user_selected_subs = [p for p in raw if isinstance(p, str)]

    if user_selected_subs:
        for sub_path in user_selected_subs:
            sub = Path(sub_path)
            if sub.exists() and sub.is_file():
                subtitles.append(FileInfo(
                    path=str(sub),
                    name=sub.name,
                    size_bytes=sub.stat().st_size,
                    type="subtitle",
                    matched_by="user_selected",
                ))
            else:
                warnings.append(f"user_selected_subtitle_missing:{sub}")
    else:
        try:
            analysis = analyze_task_input(video_source)
            subs = [f for f in analysis.files if f.type == "subtitle"]
            subtitles = subs
            if not subs:
                warnings.append("No same-stem subtitles found alongside video source")
        except Exception:
            warnings.append("Could not scan for subtitles alongside video source")

    return PublishPlanDraft(
        media_type="movie",
        movie=plan,
        subtitles=subtitles,
        warnings=warnings,
    )
