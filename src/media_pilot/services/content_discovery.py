"""轻量内容发现服务。

第一版只复用现有 LLM 配置做流式文本推荐；不创建任务、不写数据库、
不复用入库任务 AgentRun / AgentToolCall 语义。
"""

from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import dataclass

from media_pilot.agent.llm_client import AgentLLMClient
from media_pilot.config import AppConfig

CONTENT_DISCOVERY_SYSTEM_PROMPT = (
    "你是 Media Pilot 的内容发现助手。只推荐可供用户自行选择的影视作品，"
    "并给出简短理由和资源查询关键词。不要提供下载站点、磁力链接、"
    "规避限制的方法或获取未授权内容的操作步骤。"
    "\n\n输出使用 Markdown 有序列表。固定字段，不固定条目数量；候选少时可以少给，"
    "不能为凑数编造。片名必须同时包含中文常用名和英文/原名；如果没有可靠中文名，"
    "只写英文/原名；不确定英文名或原名时不要编造。推荐搜索词必须是可直接粘贴到"
    "资源搜索框的标题类关键词，可理解为 resource_search_keywords：面向资源站搜索，"
    "应选择资源站更常见、更可能命中资源标题的关键词。普通电影优先英文原名或常见发行名；"
    "中文用户搜索英文电影时仍用英文原名，不要翻译成中文。推荐搜索词优先英文/原名 + 年份，"
    "其次中文名 + 年份；不要使用题材、剧情、人物、战役、国家、演员或风格词作为推荐搜索词。格式：\n"
    "1. **中文片名 / English or Original Title**（年份）\n"
    "   - 推荐理由：一句话说明为什么匹配用户偏好。\n"
    "   - 推荐搜索词：English or Original Title 年份、中文片名 年份"
)

ALLOWED_CONTENT_DISCOVERY_ROLES = {"user", "assistant"}


@dataclass(frozen=True, kw_only=True)
class ContentDiscoveryMessage:
    role: str
    content: str


class ContentDiscoveryInputError(ValueError):
    """Raised when discovery chat history is invalid."""


def build_content_discovery_messages(
    messages: list[ContentDiscoveryMessage],
) -> list[dict[str, str]]:
    if not messages:
        raise ContentDiscoveryInputError("messages must not be empty")

    out: list[dict[str, str]] = [
        {"role": "system", "content": CONTENT_DISCOVERY_SYSTEM_PROMPT}
    ]
    for message in messages:
        role = message.role.strip()
        content = message.content.strip()
        if role not in ALLOWED_CONTENT_DISCOVERY_ROLES:
            raise ContentDiscoveryInputError(f"unsupported message role: {role}")
        if not content:
            raise ContentDiscoveryInputError("message content must not be empty")
        out.append({"role": role, "content": content})
    return out


def stream_content_discovery(
    *,
    config: AppConfig,
    messages: list[ContentDiscoveryMessage] | None = None,
    chat_messages: list[dict[str, str]] | None = None,
    llm_client: AgentLLMClient | None = None,
) -> Generator[str, None, None]:
    """Yield SSE chunks for a content discovery turn."""

    if chat_messages is None:
        if messages is None:
            raise ContentDiscoveryInputError("messages must not be empty")
        chat_messages = build_content_discovery_messages(messages)
    client = llm_client or AgentLLMClient(config)

    try:
        for chunk in client.chat_stream(chat_messages):
            delta = chunk.get("content_delta")
            if delta:
                yield format_sse("delta", {"text": delta})
        yield format_sse("done", {})
    except Exception as exc:
        yield format_sse("error", {"message": str(exc)})


def format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
