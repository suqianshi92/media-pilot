# ── Mock LLM Client ────────────────────────────────────────────────────


class MockLLMClient:
    """Programmable mock LLM client that returns pre-configured responses.

    Set ``responses`` to a list of ``LLMResponse``; each ``chat()`` call
    pops the next one.  Set ``raise_error`` to make the next call raise.
    """

    def __init__(self):
        from media_pilot.agent.llm_client import LLMResponse

        self.responses: list[LLMResponse] = []
        self.raise_error: Exception | None = None
        self.calls: list[dict] = []  # record all calls for assertions

    def add_text_response(self, content: str):
        from media_pilot.agent.llm_client import LLMResponse

        self.responses.append(LLMResponse(content=content, tool_calls=[]))

    def add_tool_calls(self, tool_calls: list[dict], content: str | None = None):
        from media_pilot.agent.llm_client import LLMResponse

        self.responses.append(LLMResponse(content=content, tool_calls=tool_calls))

    def chat(self, messages: list[dict], tools: list[dict] | None = None):
        self.calls.append({"messages": list(messages), "tools": list(tools) if tools else []})
        if self.raise_error is not None:
            err = self.raise_error
            self.raise_error = None
            raise err
        if not self.responses:
            from media_pilot.agent.llm_client import LLMResponse
            return LLMResponse(content="No more mock responses configured", tool_calls=[])
        return self.responses.pop(0)

    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None):
        """Streaming variant that yields deltas from the next pre-configured response."""
        self.calls.append({"messages": list(messages), "tools": list(tools) if tools else []})
        if self.raise_error is not None:
            err = self.raise_error
            self.raise_error = None
            raise err
        if not self.responses:
            from media_pilot.agent.llm_client import LLMResponse
            resp = LLMResponse(content="No more mock responses configured", tool_calls=[])
        else:
            resp = self.responses.pop(0)

        # Yield content deltas character by character
        content = resp.content or ""
        if resp.tool_calls:
            # Tool call response: first yield content (if any), then final tool_calls
            if content:
                yield {"content_delta": content, "tool_calls": None}
            yield {"content_delta": None, "tool_calls": resp.tool_calls}
        else:
            # Text-only response: yield content in chunks
            chunk_size = max(1, len(content) // 3) if content else 1
            for i in range(0, len(content), chunk_size):
                yield {"content_delta": content[i:i + chunk_size], "tool_calls": None}
            # Final empty yield
            yield {"content_delta": None, "tool_calls": None}


# ── Test Helpers ──────────────────────────────────────────────────────


def _make_config(database_dir):
    from pathlib import Path

    from media_pilot.config.settings import AppConfig

    return AppConfig(
        downloads_dir=Path("/tmp/dl"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp/ws"),
        movies_dir=Path("/tmp/movies"),
        shows_dir=Path("/tmp/shows"),
        database_dir=database_dir,
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
    )


def _make_task(session, source_path="/data/test.mkv", **kwargs):
    from media_pilot.repository.repositories import (
        IngestTaskCreate,
        IngestTaskRepository,
    )

    defaults = {
        "source_path": source_path,
        "status": "discovered",
        "current_step": "agent_start",
    }
    defaults.update(kwargs)
    task = IngestTaskRepository(session).create(IngestTaskCreate(**defaults))
    session.commit()
    return task
