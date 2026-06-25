"""External AI adapter boundaries."""

from media_pilot.adapters.ai import AiFilenameParser, AiParseRequest, AiParseResult, MediaType
from media_pilot.adapters.factory import create_ai_adapter

MODULE_BOUNDARY = "external service adapters"

__all__ = [
    "AiFilenameParser",
    "AiParseRequest",
    "AiParseResult",
    "MediaType",
    "MODULE_BOUNDARY",
    "create_ai_adapter",
]
