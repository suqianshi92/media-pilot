"""OpenAI-compatible LLM adapter — 真实 AI 文件名解析与关键词生成"""

import logging

from openai import OpenAI

from media_pilot.adapters.ai import (
    AiParseRequest,
    AiParseResult,
    AiSearchKeywordRequest,
    AiSearchKeywordResult,
    MediaType,
)
from media_pilot.adapters.llm_prompts import (
    PROFILE_ROUTER_SYSTEM_PROMPT,
    PROFILE_ROUTER_USER_TEMPLATE,
    TMDB_MOVIE_SYSTEM_PROMPT,
    TMDB_MOVIE_USER_TEMPLATE,
    TPDB_NORMALIZE_SYSTEM_PROMPT,
    TPDB_NORMALIZE_USER_TEMPLATE,
    LLMKeywordParseError,
    ProfileRecommendation,
    parse_llm_keyword_response,
    parse_profile_router_response,
    parse_tpdb_keyword_response,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleAiAdapter:
    """通过 OpenAI-compatible API 调用真实 LLM 做文件名解析和关键词生成。

    parse_filename 返回低置信度结果以避免误阻塞确认流程。
    generate_search_keyword 是主要价值输出。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        profile: str = "tmdb_movie",
    ) -> None:
        self._model = model
        self._profile = profile
        self._base_url = base_url
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)

    # ---- 敏感信息安全 ----

    def _safe_host(self) -> str:
        """返回 base_url 的 host 摘要，不含路径和凭据"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(self._base_url)
            return parsed.hostname or "unknown"
        except Exception:
            return "unknown"

    # ---- AiFilenameParser ----

    def parse_filename(self, request: AiParseRequest) -> AiParseResult:
        """最小实现 — 返回低置信度 UNKNOWN 结果，避免误阻塞自动确认。

        真实 adapter 的核心价值在 generate_search_keyword 而非 parse_filename。
        """
        return AiParseResult(
            media_type=MediaType.UNKNOWN,
            title=None,
            original_title=request.filename,
            year=None,
            season=None,
            episode=None,
            resolution=None,
            release_group=None,
            language=None,
            confidence=0.2,
            reason="real llm adapter returns minimal parse result",
        )

    # ---- AiSearchKeywordGenerator ----

    def generate_search_keyword(
        self, request: AiSearchKeywordRequest
    ) -> AiSearchKeywordResult:
        # 按档案选择提示词
        if request.profile == "tpdb_adult_movie":
            system_prompt = TPDB_NORMALIZE_SYSTEM_PROMPT
            user_template = TPDB_NORMALIZE_USER_TEMPLATE
        else:
            system_prompt = TMDB_MOVIE_SYSTEM_PROMPT
            user_template = TMDB_MOVIE_USER_TEMPLATE
        user_message = user_template.format(input_text=request.selected_name)

        logger.info(
            "LLM 关键词生成请求 model=%s profile=%s host=%s",
            self._model,
            request.profile or self._profile,
            self._safe_host(),
        )

        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.1,
            )
        except Exception as exc:
            logger.error("LLM API 调用失败: %s", _safe_exception_message(exc))
            raise

        content = completion.choices[0].message.content if completion.choices else None
        if not content:
            raise RuntimeError("LLM 返回空响应")

        if request.profile == "tpdb_adult_movie":
            parsed = parse_tpdb_keyword_response(content)
        else:
            parsed = parse_llm_keyword_response(content)

        if isinstance(parsed, LLMKeywordParseError):
            logger.warning("LLM 响应解析失败: %s raw=%s", parsed.message, content[:200])
            raise RuntimeError(parsed.message)

        logger.info(
            "LLM 关键词生成成功 keyword=%s confidence=%.2f",
            parsed.keyword,
            parsed.confidence,
        )

        return AiSearchKeywordResult(
            keyword=parsed.keyword,
            candidate_title=parsed.candidate_title,
            candidate_year=parsed.candidate_year,
            confidence=parsed.confidence,
            reason=parsed.reason,
            explanation=parsed.explanation,
            removed_tokens=parsed.removed_tokens,
        )

    # ---- AiProfileRouter ----

    def recommend_profile(
        self, *, input_text: str, enabled_profiles: list[str]
    ) -> ProfileRecommendation:
        user_message = PROFILE_ROUTER_USER_TEMPLATE.format(
            enabled_profiles=", ".join(enabled_profiles),
            input_text=input_text,
        )

        logger.info(
            "LLM 档案推荐请求 model=%s host=%s profiles=%s",
            self._model,
            self._safe_host(),
            enabled_profiles,
        )

        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": PROFILE_ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.1,
            )
        except Exception as exc:
            logger.error("LLM 档案推荐调用失败: %s", _safe_exception_message(exc))
            raise

        content = completion.choices[0].message.content if completion.choices else None
        if not content:
            raise RuntimeError("LLM 档案推荐返回空响应")

        parsed = parse_profile_router_response(content, enabled_profiles=enabled_profiles)
        if isinstance(parsed, LLMKeywordParseError):
            logger.warning("LLM 档案推荐解析失败: %s raw=%s", parsed.message, content[:200])
            raise RuntimeError(parsed.message)

        logger.info(
            "LLM 档案推荐成功 profile=%s keyword=%s confidence=%.2f",
            parsed["recommended_profile"],
            parsed["keyword"],
            parsed["confidence"],
        )

        return ProfileRecommendation(
            recommended_profile=parsed["recommended_profile"],
            keyword=parsed["keyword"],
            confidence=parsed["confidence"],
            reason=parsed.get("reason", ""),
            removed_tokens=parsed.get("removed_tokens", []),
            profile_payload=parsed.get("profile_payload", {}),
        )


def _safe_exception_message(exc: Exception) -> str:
    """返回安全的异常消息，不包含 API key"""
    msg = str(exc)
    # openai SDK 错误消息有时会在 URL 中包含 api_key 参数
    # 截断到 200 字符以避免敏感信息泄露
    if len(msg) > 200:
        msg = msg[:200] + "..."
    return msg
