"""ToolRegistry -- singleton registry for agent tool definitions."""

from __future__ import annotations

import logging
import time
from typing import Any

from media_pilot.agent.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolResult,
)

logger = logging.getLogger(__name__)


def _validate_against_schema(
    schema: dict[str, Any],
    input_data: dict[str, Any],
    tool_name: str,
) -> None:
    """Lightweight JSON Schema validation: required fields, types, no extras."""
    if not isinstance(input_data, dict):
        raise ValueError(
            f"Validation failed for tool '{tool_name}': input must be an object"
        )

    required: list[str] = schema.get("required", [])
    for key in required:
        if key not in input_data:
            raise ValueError(
                f"Validation failed for tool '{tool_name}': missing required field '{key}'"
            )

    if not schema.get("additionalProperties", True):
        allowed = set(schema.get("properties", {}).keys())
        extra = set(input_data.keys()) - allowed
        if extra:
            raise ValueError(
                f"Validation failed for tool '{tool_name}': unexpected fields: {', '.join(sorted(extra))}"
            )

    props = schema.get("properties", {})
    for key, value in input_data.items():
        if key not in props:
            continue
        prop = props[key]
        expected_type = prop.get("type")
        if expected_type == "string" and not isinstance(value, str):
            raise ValueError(
                f"Validation failed for tool '{tool_name}': field '{key}' must be a string"
            )
        if expected_type == "integer" and not isinstance(value, int):
            raise ValueError(
                f"Validation failed for tool '{tool_name}': field '{key}' must be an integer"
            )

        enum_values = prop.get("enum")
        if enum_values is not None and value not in enum_values:
            raise ValueError(
                f"Validation failed for tool '{tool_name}': field '{key}' must be one of {enum_values}"
            )


class ToolRegistry:
    """Registry of agent tool definitions."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def validate_input(self, tool_name: str, input_data: dict[str, Any]) -> None:
        tool = self.get(tool_name)
        _validate_against_schema(tool.parameters, input_data, tool_name)

    def execute(
        self,
        tool_name: str,
        context: ToolContext,
        input_data: dict[str, Any],
    ) -> ToolResult:
        tool = self.get(tool_name)
        self.validate_input(tool_name, input_data)
        started_at = time.perf_counter()
        try:
            result = tool.handler(context, input_data)
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info("Tool %s succeeded in %d ms", tool_name, duration_ms)
            return result
        except Exception:
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            logger.exception("Tool %s failed after %d ms", tool_name, duration_ms)
            return ToolResult(
                status="failure",
                summary=f"Tool '{tool_name}' failed: internal error",
                data={"error_message": "Internal tool error", "duration_ms": duration_ms},
            )


_registry = ToolRegistry()


def get_tool_registry() -> ToolRegistry:
    return _registry


def register_builtin_tools() -> None:
    """Register all built-in tools. Idempotent."""
    if get_tool_registry().list_tools():
        return

    from media_pilot.agent.tools.complex_input import (
        make_prepare_complex_input_decision,
    )
    from media_pilot.agent.tools.decision import (
        make_prepare_select_metadata_candidate_decision,
        make_request_user_decision,
    )
    from media_pilot.agent.tools.draft import (
        make_draft_metadata_replacement,
        make_draft_publish_plan,
    )
    from media_pilot.agent.tools.read_only import (
        make_get_auto_ingest_eligibility,
        make_get_current_metadata,
        make_get_metadata_candidates,
        make_get_task_context,
        make_scan_task_files,
        make_search_metadata,
    )
    from media_pilot.agent.tools.show import (
        make_prepare_show_structure,
        make_publish_show_to_library,
    )
    from media_pilot.agent.tools.write import (
        make_fetch_and_save_metadata_detail,
        make_handle_source_cleanup,
        make_persist_metadata_selection,
        make_publish_movie_to_library,
        make_revoke_publish,
    )

    tools = [
        make_get_task_context(),
        make_scan_task_files(),
        make_get_current_metadata(),
        make_search_metadata(),
        make_get_metadata_candidates(),
        make_draft_metadata_replacement(),
        make_draft_publish_plan(),
        make_request_user_decision(),
        make_prepare_select_metadata_candidate_decision(),
        make_persist_metadata_selection(),
        make_fetch_and_save_metadata_detail(),
        make_publish_movie_to_library(),
        make_get_auto_ingest_eligibility(),
        make_revoke_publish(),
        make_handle_source_cleanup(),
        make_prepare_complex_input_decision(),
        make_prepare_show_structure(),
        make_publish_show_to_library(),
    ]
    for tool in tools:
        _registry.register(tool)
