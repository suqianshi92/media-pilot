"""Shared preselected metadata resolution -- resolve DownloadTask preselection fields.

Returns a ``MetadataDetail`` without persisting anything or mutating task state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from media_pilot.adapters.metadata import MetadataDetail
from media_pilot.config import AppConfig


@dataclass(frozen=True, kw_only=True)
class PreselectedMetadataResult:
    detail: MetadataDetail
    is_show: bool


def resolve_preselected_metadata(
    *,
    config: AppConfig,
    provider_name: str,
    external_id: str,
    profile: str | None = None,
    language_priority: list[str] | None = None,
) -> PreselectedMetadataResult:
    """Resolve a preselected metadata reference from a DownloadTask into a detail.

    Returns ``PreselectedMetadataResult`` on success. Raises ``ValueError`` for
    unknown providers, and the underlying provider exception on fetch failure.
    """
    from media_pilot.adapters.factory import create_metadata_provider_by_name

    if language_priority is None:
        language_priority = ["zh-CN", "en-US"]

    provider = create_metadata_provider_by_name(config, provider_name)

    is_show = profile is not None and profile in ("tmdb_show",)

    if is_show:
        provider_id = f"show:{external_id}"
        response = provider.get_show_details(provider_id, language_priority=language_priority)
    else:
        response = provider.get_movie_details(external_id, language_priority=language_priority)

    if response.error is not None or response.value is None:
        msg = "preselection detail fetch failed" if response.error is None else response.error.message
        raise ValueError(msg)

    return PreselectedMetadataResult(detail=response.value, is_show=is_show)
