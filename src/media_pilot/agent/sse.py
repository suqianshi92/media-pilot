"""SSE streaming support for AgentRuns.

Provides an event emitter that hooks into the Agent loop and emits
coarse-grained SSE events for the frontend to consume.
"""

from __future__ import annotations

import json
import queue
from dataclasses import dataclass, field
from enum import StrEnum


class AgentStreamEventType(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_DELTA = "assistant_delta"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_FINISHED = "tool_call_finished"
    DECISION_CREATED = "decision_created"
    RUN_FINISHED = "run_finished"
    ERROR = "error"


@dataclass(frozen=True, kw_only=True)
class AgentStreamEvent:
    event: AgentStreamEventType
    data: dict = field(default_factory=dict)

    def to_sse(self) -> str:
        return f"event: {self.event.value}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


class AgentStreamEmitter:
    """Thread-safe emitter for AgentRun SSE events.

    Wraps a queue.Queue; the SSE endpoint reads from it while the Agent
    loop writes to it from a background thread.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[AgentStreamEvent | None] = queue.Queue()

    def emit(self, event: AgentStreamEvent) -> None:
        self._queue.put(event)

    def close(self) -> None:
        self._queue.put(None)

    def __iter__(self):
        while True:
            event = self._queue.get()
            if event is None:
                break
            yield event
