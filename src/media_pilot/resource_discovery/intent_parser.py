"""资源发现 LLM 意图解析器 — 复用现有 OpenAI-compatible 配置"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from media_pilot.resource_discovery.types import ResourceIntent

logger = logging.getLogger(__name__)

# 诊断日志中保留的原始 LLM 返回片段上限 (字节). 2KB 足以看到 JSON 截断
# 位置 / 备注说明, 但避免把搜索文本 (可能含用户输入) 全部暴露到日志.
DIAGNOSTIC_SNIPPET_MAX_BYTES = 2048

# 资源意图解析的 max_tokens 锁 — 不得回落到过小值, 否则复杂 prompt 下
# LLM 容易在 JSON 中段被截断, 触发 parse failure. 生产中 deepseek-v4-flash
# 会输出多行 JSON 并以 finish_reason=length 截断, 因此这里给足硬上限,
# 同时在 prompt 里约束短输出.
RESOURCE_INTENT_MAX_TOKENS = 2000

# 敏感字段脱敏正则: 覆盖 apikey / api_key / token / key / Authorization
# 风格的字段赋值或 HTTP header. 值部分替换为 [REDACTED], 字段名保留.
#
# 每个 pattern 都把"字段名 + 分隔符"作为唯一捕获组 (\1), 把要脱敏的值
# 放在 \1 之后, 由 re.sub(r"\1[REDACTED]") 替换. 这样既能保留字段名,
# 又能脱敏值.
_REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(apikey\s*[=:]\s*)[^\s,;\"'<>]+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;\"'<>]+"),
    re.compile(r"(?i)(\btoken\s*[=:]\s*)[^\s,;\"'<>]+"),
    re.compile(r"(?i)(\bkey\s*[=:]\s*)[^\s,;\"'<>]+"),
    # Authorization: 头的 scheme 部分 (Bearer / Basic) 也算"字段名"保留.
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic|token)\s+)[^\s,;\"'<>]+"),
    # 单独出现的 bearer xxx (不在 Authorization 头里)
    re.compile(r"(?i)(bearer\s+)[^\s,;\"'<>]+"),
)


def _redact_secrets(text: str) -> str:
    """脱敏文本中的 apikey / token / key / Authorization 风格字段值.

    字段名保留, 值部分替换为 [REDACTED]. 多次匹配全部替换.
    """
    out = text
    for pattern in _REDACTION_PATTERNS:
        out = pattern.sub(r"\1[REDACTED]", out)
    return out


def _truncate_snippet(text: str) -> str:
    """按字节上限截断文本, 截断时附加明确标记.

    截断以 UTF-8 字节为单位, 避免多字节字符被切半. 不在原字符中间切.
    """
    if not text:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= DIAGNOSTIC_SNIPPET_MAX_BYTES:
        return text
    # 截到上限, 然后 decode 一次. 末尾 � 是合理的, 因为我们记录
    # "已截断" 标记, 用户看到 � 就知道是字节边界, 不是内容.
    truncated = encoded[:DIAGNOSTIC_SNIPPET_MAX_BYTES].decode("utf-8", errors="replace")
    return f"{truncated}...[truncated at {DIAGNOSTIC_SNIPPET_MAX_BYTES}B]"


class ResourceIntentParser:
    """调用 LLM 解析用户自然语言输入为结构化搜索意图。

    复用现有 MEDIA_PILOT_LLM_* 环境变量配置的 OpenAI-compatible 客户端，
    不新增资源发现专用 LLM 配置项。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        from openai import OpenAI

        self._model = model
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout_seconds
        )

    def parse(
        self,
        user_input: str,
        *,
        preferred_language: str = "zh",
        enabled_profiles: list[str] | None = None,
    ) -> ResourceIntent:
        """解析用户自然语言输入，返回结构化搜索意图。

        简单片名也必须调用 LLM，不绕过。
        LLM 不可用时抛出异常，调用方负责处理。

        Args:
            user_input: 用户自然语言输入
            preferred_language: 用户偏好的元数据语言 (zh/en)
            enabled_profiles: 当前已启用的 metadata profile 名称列表
        """
        from media_pilot.adapters.llm_prompts import (
            RESOURCE_INTENT_SYSTEM_PROMPT,
            RESOURCE_INTENT_USER_TEMPLATE,
            parse_resource_intent_response,
        )

        user_message = RESOURCE_INTENT_USER_TEMPLATE.format(input_text=user_input)

        # 构建动态系统提示词：注入语言偏好和已启用 profile 约束
        system_content = self._build_system_prompt(
            RESOURCE_INTENT_SYSTEM_PROMPT,
            preferred_language=preferred_language,
            enabled_profiles=enabled_profiles,
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.1,
                max_tokens=RESOURCE_INTENT_MAX_TOKENS,
            )
            content = response.choices[0].message.content or ""
            try:
                intent = parse_resource_intent_response(content)
            except json.JSONDecodeError as exc:
                # 在 raise 之前写入诊断日志, 包含 model / finish_reason /
                # 原始长度 / JSON 错误 / 脱敏截断后的原始返回. API 响应
                # 仍然只用安全的用户文案, 不返回原始内容.
                # 优先使用 response.model (实际 LLM 返回的模型名), 缺失
                # 时回退到 self._model (请求时声明的模型).
                response_model = getattr(response, "model", None) or self._model
                self._log_parse_failure(
                    model=str(response_model),
                    response=response,
                    content=content,
                    error=exc,
                )
                raise IntentParseError(
                    "LLM 返回了无法解析的结果，请尝试用更明确的片名或关键词重新搜索"
                ) from exc
            # 注入原始查询文本到 intent
            return ResourceIntent(
                query_text=user_input,
                search_type=intent.search_type,
                title_candidates=intent.title_candidates,
                resource_keywords=intent.resource_keywords,
                quality_hint="",  # DEPRECATED
                profile_hint=intent.profile_hint,
                preferred_title_candidates=intent.preferred_title_candidates,
                adult_identifier_candidates=intent.adult_identifier_candidates,
                resource_search_keywords=intent.resource_search_keywords,
                reason=intent.reason,
                preferred_resolutions=intent.preferred_resolutions,
                preferred_sources=intent.preferred_sources,
                preferred_video_codecs=intent.preferred_video_codecs,
                preferred_hdr_tags=intent.preferred_hdr_tags,
                preferred_audio_tags=intent.preferred_audio_tags,
            )
        except IntentParseError:
            raise
        except Exception as exc:
            # 非 JSON 解析失败: 调用超时 / 网络错误 / SDK 异常, 仍然只
            # 返回安全的用户文案, 不暴露内部堆栈.
            logger.exception("LLM 资源意图解析失败 (非 JSON 解析异常)")
            raise IntentParseError(
                "调用 LLM 失败，请检查 LLM 配置或在设置中确认 API Key 和连接状态"
            ) from exc

    @staticmethod
    def _log_parse_failure(
        *,
        model: str,
        response: Any,
        content: str,
        error: Exception,
    ) -> None:
        """写入 JSON parse failure 诊断日志.

        必须包含 model, finish_reason, 原始内容长度, JSON 错误, 脱敏截断后
        的原始返回片段. 原始内容不得原样记录 (先脱敏再截断).
        """
        # 提取 finish_reason; response 可能是 mock, 用 getattr 容错.
        finish_reason = "<unknown>"
        try:
            choices = getattr(response, "choices", None)
            if choices:
                finish_reason = str(getattr(choices[0], "finish_reason", "<unknown>"))
        except Exception:
            pass
        redacted = _redact_secrets(content or "")
        snippet = _truncate_snippet(redacted)
        logger.warning(
            "LLM 资源意图 JSON 解析失败: model=%s finish_reason=%s "
            "content_length=%d error=%s snippet=%r",
            model,
            finish_reason,
            len(content or ""),
            error,
            snippet,
        )

    @staticmethod
    def _build_system_prompt(
        base_prompt: str,
        *,
        preferred_language: str,
        enabled_profiles: list[str] | None,
    ) -> str:
        """在基础系统提示词后追加运行时上下文约束。

        注入内容：
        - 用户偏好的元数据语言
        - 当前已启用的 metadata profile 列表
        - profile_hint 的合法值域约束
        """
        parts = [base_prompt]

        lang_name = "中文" if preferred_language == "zh" else "English"
        parts.append(
            f"\n\n【运行时上下文】\n"
            f"- 用户偏好元数据语言：{lang_name}\n"
        )

        if enabled_profiles:
            profile_list = ", ".join(enabled_profiles)
            parts.append(
                f"- 当前已启用的元数据档案（profile_hint 只能从以下值中选择）：{profile_list}\n"
            )
        else:
            parts.append(
                "- 当前已启用的元数据档案：tmdb_movie（默认）\n"
            )

        parts.append(
            "- preferred_title_candidates 列表：若用户偏好中文，中文片名排前；"
            "若偏好英文，英文/原名排前"
        )

        return "".join(parts)


class IntentParseError(Exception):
    """LLM 意图解析失败的结构化错误"""
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
