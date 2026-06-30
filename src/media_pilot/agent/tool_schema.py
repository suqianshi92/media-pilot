"""Convert ToolDefinition parameters to OpenAI-compatible tool schemas.

Also provides permission-based filtering so the runner only exposes
the correct tools based on the selected runner mode.
"""

from __future__ import annotations

from media_pilot.agent.tools.base import PermissionLevel
from media_pilot.agent.tools.registry import ToolRegistry, get_tool_registry

_DEFAULT_PERMISSIONS = frozenset({PermissionLevel.READ_ONLY, PermissionLevel.DRAFT})
_AUTO_INGEST_PERMISSIONS = frozenset({
    PermissionLevel.READ_ONLY,
    PermissionLevel.DRAFT,
    PermissionLevel.WRITE,
})
# Freeform mode: same WRITE perms as auto_ingest, but different whitelist
_FREEFORM_PERMISSIONS = frozenset({
    PermissionLevel.READ_ONLY,
    PermissionLevel.DRAFT,
    PermissionLevel.WRITE,
})

# Tools that are explicitly whitelisted for auto_ingest mode.
AUTO_INGEST_WRITE_TOOL_WHITELIST: frozenset[str] = frozenset({
    "persist_metadata_selection",
    "fetch_and_save_metadata_detail",
    "publish_movie_to_library",
    "publish_show_to_library",
    "handle_source_cleanup",
})

# Freeform mode WRITE whitelist — adds revoke_publish and handle_source_cleanup,
# same as auto_ingest otherwise
FREEFORM_WRITE_TOOL_WHITELIST: frozenset[str] = frozenset({
    "persist_metadata_selection",
    "fetch_and_save_metadata_detail",
    "publish_movie_to_library",
    "publish_without_metadata",
    "revoke_publish",
    "handle_source_cleanup",
})


def _effective_allowed_permissions(mode: str) -> frozenset[PermissionLevel]:
    if mode in ("auto_ingest", "freeform"):
        return _FREEFORM_PERMISSIONS
    return _DEFAULT_PERMISSIONS


def tool_to_openai_schema(tool_def) -> dict:
    """Convert a single ToolDefinition to an OpenAI function-calling schema."""
    return {
        "type": "function",
        "function": {
            "name": tool_def.name,
            "description": tool_def.description,
            "parameters": tool_def.parameters,
        },
    }


def get_allowed_tool_schemas(
    registry: ToolRegistry | None = None,
    mode: str = "default",
) -> list[dict]:
    """Return OpenAI tool schemas for tools permitted by the given mode.

    ``mode="default"`` exposes only READ_ONLY and DRAFT tools.
    ``mode="auto_ingest"`` additionally exposes whitelisted WRITE tools.
    """
    if registry is None:
        registry = get_tool_registry()

    allowed_perms = _effective_allowed_permissions(mode)
    schemas: list[dict] = []
    for tool_def in registry.list_tools():
        if tool_def.permission_level not in allowed_perms:
            continue
        if tool_def.permission_level == PermissionLevel.WRITE:
            if mode == "auto_ingest" and tool_def.name not in AUTO_INGEST_WRITE_TOOL_WHITELIST:
                continue
            if mode == "freeform" and tool_def.name not in FREEFORM_WRITE_TOOL_WHITELIST:
                continue
        schemas.append(tool_to_openai_schema(tool_def))
    return schemas


def get_allowed_tool_names(
    registry: ToolRegistry | None = None,
    mode: str = "default",
) -> frozenset[str]:
    """Return the set of tool names permitted for the given runner mode."""
    if registry is None:
        registry = get_tool_registry()

    allowed_perms = _effective_allowed_permissions(mode)
    names: set[str] = set()
    for t in registry.list_tools():
        if t.permission_level not in allowed_perms:
            continue
        if t.permission_level == PermissionLevel.WRITE:
            if mode == "auto_ingest" and t.name not in AUTO_INGEST_WRITE_TOOL_WHITELIST:
                continue
            if mode == "freeform" and t.name not in FREEFORM_WRITE_TOOL_WHITELIST:
                continue
        names.add(t.name)
    return frozenset(names)
