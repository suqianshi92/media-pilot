"""Draft agent tools -- generate plans and previews without side effects."""

from __future__ import annotations

from media_pilot.agent.tools.base import (
    PermissionLevel,
    ToolContext,
    ToolDefinition,
    ToolResult,
)


# ── 6. draft_metadata_replacement ───────────────────────────────────

_DRAFT_METADATA_REPLACEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "provider_name": {"type": "string"},
        "provider_id": {"type": "string"},
        "media_type": {"type": "string", "enum": ["movie", "show"]},
    },
    "required": ["provider_name", "provider_id", "media_type"],
    "additionalProperties": False,
}


def _handle_draft_metadata_replacement(context: ToolContext, input_data: dict) -> ToolResult:
    from media_pilot.services.metadata_draft import ProviderError, fetch_metadata_draft

    provider_name = input_data["provider_name"]
    provider_id = input_data["provider_id"]
    media_type = input_data["media_type"]
    language = list(context.config.tmdb_language_priority)

    try:
        draft = fetch_metadata_draft(
            config=context.config,
            provider_name=provider_name,
            provider_id=provider_id,
            media_type=media_type,
            language_priority=language,
        )
    except ValueError as exc:
        return ToolResult(status="failure", summary=str(exc))
    except ProviderError as exc:
        return ToolResult(
            status="failure",
            summary=f"Provider error: {exc.provider_message}",
            data={"provider_error": {"code": exc.code, "message": exc.provider_message}},
        )
    except Exception as exc:
        return ToolResult(status="failure", summary=f"Provider call failed: {exc}")

    detail = draft.detail

    return ToolResult(
        status="success",
        summary=f"Draft fetched for {detail.title} ({detail.year}) from {provider_name}",
        data={
            "provider": detail.provider,
            "provider_id": detail.provider_id,
            "media_type": detail.media_type,
            "title": detail.title,
            "original_title": detail.original_title,
            "year": detail.year,
            "plot": detail.plot,
            "runtime_minutes": detail.runtime_minutes,
            "premiered": detail.premiered,
            "rating": detail.rating,
            "genres": list(detail.genres),
            "countries": list(detail.countries),
            "studios": list(detail.studios),
            "directors": draft.directors,
            "actors": draft.actors,
            "external_ids": {"imdb_id": draft.imdb_id},
            "images": {
                "poster_url": draft.poster_url,
                "backdrop_url": draft.backdrop_url,
                "logo_url": draft.logo_url,
            },
            "is_draft": True,
            "_note": "This is a DRAFT. No data was persisted to the database.",
        },
    )


def make_draft_metadata_replacement() -> ToolDefinition:
    return ToolDefinition(
        name="draft_metadata_replacement",
        description="Fetch metadata detail from a provider by ID and return it as a draft. Does NOT persist anything to the database.",
        parameters=_DRAFT_METADATA_REPLACEMENT_SCHEMA,
        permission_level=PermissionLevel.DRAFT,
        handler=_handle_draft_metadata_replacement,
    )


# ── 7. draft_publish_plan ───────────────────────────────────────────

_DRAFT_PUBLISH_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_draft_publish_plan(context: ToolContext, input_data: dict) -> ToolResult:
    from media_pilot.services.publish_plan_draft import build_publish_plan_draft

    task_id = input_data["task_id"]

    try:
        result = build_publish_plan_draft(
            session=context.session,
            config=context.config,
            task_id=task_id,
        )
    except ValueError as exc:
        return ToolResult(status="failure", summary=str(exc))

    if result.media_type == "show" and result.show:
        plan = result.show
        return ToolResult(
            status="success",
            summary=f"Draft show write plan ready",
            data={
                "media_type": "show",
                "show_title": plan.show_dir_name,
                "season": plan.season_dir_name,
                "episode_count": len(plan.episodes),
                "episodes": [
                    {
                        "episode": ep.episode,
                        "season": ep.season,
                        "source_file": str(ep.source_file),
                        "target_file": str(ep.target_file),
                    }
                    for ep in plan.episodes
                ],
                "target_dir": str(plan.target_dir),
                "final_target_dir": str(plan.final_target_dir),
                "tvshow_nfo_path": str(plan.tvshow_nfo_path),
                "season_nfo_path": str(plan.season_nfo_path),
                "is_draft": True,
                "_note": "This is a DRAFT. No data was persisted to the database.",
            },
        )
    elif result.movie:
        plan = result.movie
        data: dict = {
            "media_type": "movie",
            "target_dir": str(plan.target_dir),
            "final_target_dir": str(plan.final_target_dir),
            "target_file": str(plan.target_file),
            "final_target_file": str(plan.final_target_file),
            "nfo_path": str(plan.nfo_path),
            "poster_path": str(plan.poster_path),
            "fanart_path": str(plan.fanart_path),
            "clearlogo_path": str(plan.clearlogo_path),
            "subtitles": [
                {
                    "path": s.path,
                    "name": s.name,
                    "size_bytes": s.size_bytes,
                    "matched_by": s.matched_by,
                }
                for s in result.subtitles
            ],
            "is_draft": True,
            "_note": "This is a DRAFT. No data was persisted to the database.",
        }
        if result.warnings:
            data["warnings"] = result.warnings
        return ToolResult(status="success", summary="Draft movie write plan ready", data=data)
    else:
        return ToolResult(
            status="failure",
            summary=f"Unknown media_type: {result.media_type}",
        )


def make_draft_publish_plan() -> ToolDefinition:
    return ToolDefinition(
        name="draft_publish_plan",
        description="Build a movie or show write plan (target paths, NFO paths, etc.) as a draft. Does NOT execute or persist anything.",
        parameters=_DRAFT_PUBLISH_PLAN_SCHEMA,
        permission_level=PermissionLevel.DRAFT,
        handler=_handle_draft_publish_plan,
    )
