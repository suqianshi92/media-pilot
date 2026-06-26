"""Filesystem-safe naming helpers for library publish paths."""

from __future__ import annotations

import re

MAX_PATH_COMPONENT_BYTES = 180

_ILLEGAL_CHARS_RE = re.compile(r'[\u0000-\u001F\u007F/\\:*?"<>|]+')
_WHITESPACE_RE = re.compile(r"\s+")
_ADULT_CODE_RE = re.compile(r"\b([A-Z]{2,12})[-_\s]?(\d{2,6})\b", re.IGNORECASE)


def safe_path_component(value: str, *, max_bytes: int = MAX_PATH_COMPONENT_BYTES) -> str:
    """Return a single filesystem-safe path component.

    The limit is byte-based, not character-based, because common filesystems
    cap each path component at 255 bytes. We use a conservative 180-byte budget
    so suffixes like ``-poster.jpg`` or `` - S01E01.mkv`` still fit.
    """
    cleaned = _ILLEGAL_CHARS_RE.sub(" ", value)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip(" .-_")
    if not cleaned:
        cleaned = "untitled"
    return _truncate_utf8_component(cleaned, max_bytes=max_bytes)


def movie_directory_name(
    title: str,
    year: int | None,
    *,
    identifier: str | None = None,
) -> str:
    """Build a Jellyfin movie directory name with a safe byte budget.

    Adult providers commonly return extremely long descriptive titles. When a
    stable identifier is available, prefer ``IDENTIFIER (Year)`` for the path
    and keep the full title in NFO metadata.
    """
    if identifier:
        base = identifier.strip()
    else:
        base = title
    return title_year_component(base, year)


def movie_path_identifier(
    *,
    provider: str,
    title: str | None,
    original_title: str | None,
    provider_id: str | None,
    payload: dict | None,
) -> str | None:
    """Return a compact stable identifier for providers that have one.

    For TPDB/JAV metadata, the filesystem path should use the catalog number
    when available. The full descriptive title remains in the NFO metadata.
    """
    if provider != "tpdb":
        return None

    payload = payload or {}
    external_ids = payload.get("external_ids")
    external_payload = (
        external_ids.get("payload")
        if isinstance(external_ids, dict) and isinstance(external_ids.get("payload"), dict)
        else {}
    )
    candidates = [
        original_title,
        payload.get("external_id"),
        payload.get("sku"),
        external_payload.get("external_id"),
        title,
        provider_id,
    ]
    for value in candidates:
        if not isinstance(value, str):
            continue
        normalized = _normalize_adult_code(value)
        if normalized:
            return normalized
    return None


def safe_file_stem(
    value: str,
    *,
    extension: str,
    max_bytes: int = MAX_PATH_COMPONENT_BYTES,
) -> str:
    """Return a safe file stem while reserving byte budget for extension."""
    extension_bytes = len(extension.encode("utf-8"))
    stem_budget = max(1, max_bytes - extension_bytes)
    return safe_path_component(value, max_bytes=stem_budget)


def title_year_component(
    title: str,
    year: int | None,
    *,
    max_bytes: int = MAX_PATH_COMPONENT_BYTES,
) -> str:
    suffix = f" ({year})" if year is not None else ""
    suffix_bytes = len(suffix.encode("utf-8"))
    title_budget = max(1, max_bytes - suffix_bytes)
    safe_title = safe_path_component(title, max_bytes=title_budget).strip(" -_")
    if not safe_title:
        safe_title = "untitled"
    return f"{safe_title}{suffix}"


def _truncate_utf8_component(value: str, *, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value

    suffix = "..."
    suffix_bytes = len(suffix.encode("utf-8"))
    budget = max(1, max_bytes - suffix_bytes)
    out = bytearray()
    for char in value:
        chunk = char.encode("utf-8")
        if len(out) + len(chunk) > budget:
            break
        out.extend(chunk)
    truncated = out.decode("utf-8", errors="ignore").strip(" .-_")
    if not truncated:
        truncated = "untitled"
    return f"{truncated}{suffix}"


def _normalize_adult_code(value: str) -> str | None:
    match = _ADULT_CODE_RE.search(value.upper())
    if not match:
        return None
    prefix, number = match.groups()
    return f"{prefix}-{number}"
