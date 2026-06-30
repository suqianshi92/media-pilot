"""Source path safety rules shared by ingest gates.

Normal ingest sources must live under downloads/watch/workspace. Metadata
correction may additionally use a task-scoped republish source staged under a
library root; only ``.media-pilot-staging/<task_id>`` is allowed there.
"""

from __future__ import annotations

from pathlib import Path

from media_pilot.config import AppConfig


def is_safe_ingest_source_path(
    path: Path,
    config: AppConfig,
    *,
    task_id: str | None = None,
) -> bool:
    """Return True when ``path`` is an allowed ingest source.

    Library roots are intentionally not general source roots. The narrow
    exception is the current task's temporary republish staging directory.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return False

    for root in (config.downloads_dir, config.watch_dir, config.workspace_dir):
        if _is_under_existing_root(resolved, root):
            return True

    if task_id:
        for root in _library_roots(config):
            staging_root = root / ".media-pilot-staging" / task_id
            if _is_under_existing_root(resolved, staging_root):
                return True

    return False


def _library_roots(config: AppConfig) -> tuple[Path, ...]:
    roots = [config.movies_dir, config.shows_dir]
    if config.adult_movies_dir is not None:
        roots.append(config.adult_movies_dir)
    return tuple(roots)


def _is_under_existing_root(path: Path, root: Path) -> bool:
    try:
        return root.exists() and path.is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False
