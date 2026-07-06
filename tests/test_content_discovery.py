"""内容发现第一版测试。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from media_pilot.app import create_app
from media_pilot.config import AppConfig
from media_pilot.services.content_discovery import (
    CONTENT_DISCOVERY_SYSTEM_PROMPT,
    ContentDiscoveryMessage,
    build_content_discovery_messages,
    stream_content_discovery,
)


def _make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path / "db",
        llm_api_key="sk-test",
        llm_base_url="https://llm.example.test/v1",
        llm_model="test-model",
    )


class _StreamingLLM:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] | None = None

    def chat_stream(self, messages):
        self.messages = messages
        yield {"content_delta": "1. **荒野猎人**（2015）", "tool_calls": None}
        yield {"content_delta": "\n   - 推荐搜索词：荒野猎人 2015", "tool_calls": None}
        yield {"content_delta": None, "content": "ignored", "tool_calls": None}


def test_build_content_discovery_messages_injects_fixed_system_prompt() -> None:
    messages = build_content_discovery_messages([
        ContentDiscoveryMessage(role="user", content="推荐现代西部片"),
        ContentDiscoveryMessage(role="assistant", content="可以看《赴汤蹈火》。"),
        ContentDiscoveryMessage(role="user", content="更冷峻一点"),
    ])

    assert messages[0] == {"role": "system", "content": CONTENT_DISCOVERY_SYSTEM_PROMPT}
    system_prompt = messages[0]["content"]
    assert "中文常用名和英文/原名" in system_prompt
    assert "优先英文/原名 + 年份" in system_prompt
    assert "不要使用题材、剧情、人物、战役" in system_prompt
    assert messages[1:] == [
        {"role": "user", "content": "推荐现代西部片"},
        {"role": "assistant", "content": "可以看《赴汤蹈火》。"},
        {"role": "user", "content": "更冷峻一点"},
    ]


def test_stream_content_discovery_yields_text_deltas_and_done(tmp_path: Path) -> None:
    llm = _StreamingLLM()

    chunks = list(stream_content_discovery(
        config=_make_config(tmp_path),
        messages=[ContentDiscoveryMessage(role="user", content="推荐现代西部片")],
        llm_client=llm,  # type: ignore[arg-type]
    ))

    assert llm.messages is not None
    assert llm.messages[0]["role"] == "system"
    assert "不要提供下载站点" in llm.messages[0]["content"]
    assert "推荐搜索词必须是可直接粘贴到资源搜索框的标题类关键词" in llm.messages[0]["content"]
    assert chunks == [
        'event: delta\ndata: {"text": "1. **荒野猎人**（2015）"}\n\n',
        'event: delta\ndata: {"text": "\\n   - 推荐搜索词：荒野猎人 2015"}\n\n',
        "event: done\ndata: {}\n\n",
    ]


def test_content_discovery_stream_route_is_registered(tmp_path: Path, monkeypatch) -> None:
    class RouteLLM(_StreamingLLM):
        pass

    monkeypatch.setattr(
        "media_pilot.services.content_discovery.AgentLLMClient",
        lambda _config: RouteLLM(),
    )

    client = TestClient(create_app(config=_make_config(tmp_path)))
    with client.stream(
        "POST",
        "/api/v1/content-discovery/stream",
        json={"messages": [{"role": "user", "content": "推荐现代西部片"}]},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'event: delta\ndata: {"text": "1. **荒野猎人**（2015）"}' in body
    assert "event: done" in body
