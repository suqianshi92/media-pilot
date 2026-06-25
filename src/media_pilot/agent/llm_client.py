"""Thin OpenAI-compatible LLM client for Agent turn runner.

Uses the existing ``llm_api_key``, ``llm_base_url``, ``llm_model``, and
``llm_timeout_seconds`` configuration.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

from media_pilot.config import AppConfig


@dataclass(frozen=True, kw_only=True)
class LLMResponse:
    content: str | None = None
    tool_calls: list[dict] = field(default_factory=list)


class LLMConfigurationError(Exception):
    """Raised when required LLM configuration is missing."""


class AgentLLMClient:
    """Thin wrapper around the OpenAI SDK Chat Completions API."""

    def __init__(self, config: AppConfig) -> None:
        if not config.llm_api_key:
            raise LLMConfigurationError("llm_api_key is not configured")
        if not config.llm_base_url:
            raise LLMConfigurationError("llm_base_url is not configured")
        if not config.llm_model:
            raise LLMConfigurationError("llm_model is not configured")

        from openai import OpenAI

        self._client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            timeout=config.llm_timeout_seconds,
        )
        self._model = config.llm_model

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        """Send a Chat Completions request with optional tool definitions.

        Returns a ``LLMResponse`` with the assistant's text content and/or
        tool calls.  Raises the underlying OpenAI exception on API failure.
        """
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        completion = self._client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        msg = choice.message

        tool_calls: list[dict] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })

        return LLMResponse(content=msg.content, tool_calls=tool_calls)

    def chat_stream(
        self, messages: list[dict], tools: list[dict] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Stream a Chat Completions request, yielding delta dicts.

        Each yielded dict has:
          - ``content_delta``: str | None
          - ``tool_calls``: list[dict] | None (only on final chunk when present)

        Yields once per content delta, then a final dict with accumulated
        tool_calls if the model chose to call tools.
        """
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = self._client.chat.completions.create(**kwargs)

        accumulated_content = ""
        accumulated_tool_calls: dict[int, dict] = {}

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            # Handle content delta
            if delta.content:
                accumulated_content += delta.content
                yield {"content_delta": delta.content, "tool_calls": None}

            # Handle tool call delta (accumulate across chunks)
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in accumulated_tool_calls:
                        accumulated_tool_calls[idx] = {
                            "id": tc_delta.id or "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    acc = accumulated_tool_calls[idx]
                    if tc_delta.id:
                        acc["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            acc["function"]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            acc["function"]["arguments"] += tc_delta.function.arguments

        # Yield final accumulated state
        tool_calls_list = None
        if accumulated_tool_calls:
            tool_calls_list = list(accumulated_tool_calls.values())

        yield {
            "content_delta": None,
            "tool_calls": tool_calls_list,
            "content": accumulated_content or None,
        }
