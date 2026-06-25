"""Shared metadata search boundary -- query providers, normalise results.

Returns structured candidates and errors. Does NOT persist anything;
callers that need persistence must use a separate, explicit path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from media_pilot.adapters.factory import create_metadata_provider_by_name
from media_pilot.adapters.metadata import MetadataCandidate
from media_pilot.config import AppConfig


@dataclass(frozen=True, kw_only=True)
class MetadataSearchResult:
    candidates: list[MetadataCandidate] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


def search_metadata(
    *,
    config: AppConfig,
    provider_name: str,
    keyword: str,
    language_priority: list[str],
    media_type: Literal["movie", "show", "both"] = "both",
) -> MetadataSearchResult:
    """Search movie and/or show endpoints for the given keyword.

    Returns a ``MetadataSearchResult`` with normalised candidates and
    any provider errors encountered.  An error from one endpoint does
    not prevent the other from contributing candidates.

    ``media_type`` controls which endpoints are queried (default: both).
    """
    try:
        provider = create_metadata_provider_by_name(config, provider_name)
    except ValueError as exc:
        return MetadataSearchResult(errors=[{"query": "provider", "error": str(exc)}])

    candidates: list[MetadataCandidate] = []
    errors: list[dict] = []

    # TPDB provider 没有 ``search_show`` (成人影片库), 之前调用会抛
    # AttributeError 冒到 agent runner 计作 hard tool failure. 这里预先
    # 用结构化 error 表达 "TPDB 不支持剧集", 让 Agent 切到 movie 路径或
    # 切到其它 provider, 不污染 hard-failure 计数器. spec:
    # ``agent-metadata-search-loop-guard`` / Requirement: TPDB+show 结构化拒绝.
    if (
        provider_name == "tpdb"
        and media_type in ("show", "both")
    ):
        errors.append({
            "query": "show",
            "error": (
                "TPDB provider does not support show search. "
                "Switch provider=tmdb, or restrict media_type to movie."
            ),
            "code": "provider_show_not_supported",
        })

    if media_type in ("movie", "both"):
        try:
            movie_resp = provider.search_movie(keyword, language_priority=language_priority)
        except Exception as exc:
            errors.append({"query": "movie", "error": str(exc)})
        else:
            if movie_resp.error is not None:
                errors.append({
                    "query": "movie",
                    "error": f"{movie_resp.error.code}: {movie_resp.error.message}",
                })
            elif movie_resp.value:
                candidates.extend(movie_resp.value)

    if media_type in ("show", "both") and provider_name != "tpdb":
        try:
            show_resp = provider.search_show(keyword, language_priority=language_priority)
        except Exception as exc:
            errors.append({"query": "show", "error": str(exc)})
        else:
            if show_resp.error is not None:
                errors.append({
                    "query": "show",
                    "error": f"{show_resp.error.code}: {show_resp.error.message}",
                })
            elif show_resp.value:
                candidates.extend(show_resp.value)

    return MetadataSearchResult(candidates=candidates, errors=errors)
