"""LLM prompt profile 模块 — 定义各 profile 的 prompt 和结构化输出校验"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMKeywordResult:
    keyword: str
    candidate_title: str | None
    candidate_year: int | None
    confidence: float
    reason: str
    explanation: str
    removed_tokens: list[str]


@dataclass(frozen=True)
class ProfileRecommendation:
    """LLM 档案推荐结果"""
    recommended_profile: str
    keyword: str
    confidence: float
    reason: str
    removed_tokens: list[str]
    profile_payload: dict


@dataclass
class LLMKeywordParseError:
    message: str


TMDB_MOVIE_SYSTEM_PROMPT = """\
你是一个电影文件名清洗助手，输入是从下载站或 P2P 网络获取的电影文件名，\
可能包含网站宣传、发布组、编码、清晰度等信息。你需要从中提取可用于 TMDB 搜索的关键词。

要求：
- 优先保留中文片名；没有中文则保留英文/原始片名。
- 去除网站名称、网址、发布组名称、编码、清晰度标记、扩展名等噪声。
- 如可能，识别电影发行年份。
- 如只能确定一个候选标题，一并提供；否则为 null。

你必须只返回一个 JSON 对象：
{
  "keyword": "搜索关键词（非空字符串）",
  "candidate_title": "候选电影标题或 null",
  "candidate_year": 候选年份或 null,
  "confidence": 0.0-1.0 的置信度,
  "reason": "提取策略简要说明",
  "explanation": "详细清洗步骤说明",
  "removed_tokens": ["移除的噪声 token 列表"]
}"""

TMDB_MOVIE_USER_TEMPLATE = '请清洗以下电影文件名："{input_text}"'

# ---- Profile Router Prompt (任务 2.5) ----

PROFILE_ROUTER_SYSTEM_PROMPT = """\
你是一个媒体文件档案推荐助手。根据文件名、父目录名和已启用的元数据档案列表，\
推荐最合适的单一档案并输出对应的搜索关键词。

已启用的档案列表由用户提供，你只能从中选择一个。

档案说明：
- tmdb_movie: TMDB 普通电影，使用标题+年份搜索
- tmdb_show: TMDB 剧集（电视剧/动画/纪录片等分季分集内容），使用标题+年份搜索
- tpdb_adult_movie: TPDB 成人影片，使用番号搜索（如 ABCD-123）

要求：
- 只推荐已启用档案之一，不得推荐未启用的档案。
- 如果文件名/路径中有明显季集结构（如 S01E01、Season 1），应优先推荐 tmdb_show。
- 如果文件名/路径中没有成人影片番号特征（如 ABCD-123 或 ABCD123），不得推荐 tpdb_adult_movie。
- 如果无法明确判断电影还是剧集，优先推荐 tmdb_movie。

你必须只返回一个 JSON 对象：
{
  "recommended_profile": "档案名称（来自已启用列表）",
  "keyword": "清洗后的搜索关键词",
  "confidence": 0.0-1.0,
  "reason": "推荐理由",
  "removed_tokens": ["移除的噪声 token 列表"],
  "profile_payload": {
    "type": "与 recommended_profile 相同",
    ...档案专属字段
  }
}

若推荐 tmdb_movie 或 tmdb_show，profile_payload 为：
{
  "type": "tmdb_movie 或 tmdb_show",
  "candidate_title": "候选标题",
  "candidate_year": 年份或 null,
  "original_title": "原始标题或 null"
}

若推荐 tpdb_adult_movie，profile_payload 为：
{
  "type": "tpdb_adult_movie",
  "identifier": "用于搜索的番号",
  "identifier_type": "normalized_code 或 raw_code 或 title",
  "normalized_code": "标准番号（如 ABCD-123）或 null",
  "raw_code": "原始提取的番号"
}"""

PROFILE_ROUTER_USER_TEMPLATE = (
    '已启用档案：{enabled_profiles}\n'
    '请分析以下文件并推荐档案："{input_text}"'
)

# ---- TPDB 番号规范化 Prompt (任务 2.6) ----

TPDB_NORMALIZE_SYSTEM_PROMPT = """\
你是一个成人影片番号清洗助手。你只从文件名、父目录名或选中媒体路径中已有的证据恢复番号。

核心规则：
- 只从已有证据中恢复番号，不允许凭空猜测或编造番号。
- 番号格式通常为：字母部分-数字部分（如 ABCD-123, XYZ-001）。
- 常见需要纠正的情况：
  - 缺少横杠：ABCD123 → ABCD-123
  - 大小写混乱：abcd-123 → ABCD-123（番号通常大写）
  - 网站前缀：www.example.com@ABCD-123 → ABCD-123
  - 广告词：高清 ABCD-123 无码 → ABCD-123
- 如果文件名中没有任何番号特征（字母+数字组合），必须返回 confidence 为 0 并说明无法提取。

你必须只返回一个 JSON 对象：
{
  "keyword": "规范后的搜索关键词",
  "identifier": "规范后的番号或空字符串",
  "identifier_type": "normalized_code 或 raw_code 或 title",
  "normalized_code": "标准格式番号或空字符串",
  "raw_code": "原始提取的番号或空字符串",
  "confidence": 0.0-1.0,
  "reason": "提取/规范化理由或无法提取原因",
  "explanation": "详细清洗步骤说明",
  "removed_tokens": ["移除的噪声 token 列表"]
}

注意：
- 如果没有足够证据提取番号，keyword 可为空字符串，confidence 为 0。
- identifier_type 为 "title" 仅当确实只能通过标题搜索时使用（极少见）。
- normalized_code 应该是直接可用于 TPDB 搜索的标准番号格式。"""

TPDB_NORMALIZE_USER_TEMPLATE = '请清洗以下文件名中的番号："{input_text}"'


def parse_llm_keyword_response(
    raw_text: str,
    *,
    profile: str = "tmdb_movie",
) -> LLMKeywordResult | LLMKeywordParseError:
    """解析 LLM 返回的 JSON 响应，对格式、类型、范围做严格校验"""
    _ = profile  # 预留扩展

    text = raw_text.strip()

    # 去除 Markdown 代码块包裹
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return LLMKeywordParseError(message=f"LLM 响应不是有效 JSON: {exc}")

    if not isinstance(data, dict):
        return LLMKeywordParseError(message="LLM 响应必须是 JSON 对象")

    # keyword 非空字符串
    keyword = data.get("keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        return LLMKeywordParseError(message="keyword 必须是非空字符串")

    # candidate_title 字符串或 null
    candidate_title = data.get("candidate_title")
    if candidate_title is not None and not isinstance(candidate_title, str):
        return LLMKeywordParseError(message="candidate_title 必须是字符串或 null")

    # candidate_year 整数或 null
    candidate_year = data.get("candidate_year")
    if candidate_year is not None and not isinstance(candidate_year, int):
        return LLMKeywordParseError(message="candidate_year 必须是整数或 null")

    # confidence 0-1
    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)):
        return LLMKeywordParseError(message="confidence 必须是数字")
    if confidence < 0 or confidence > 1:
        return LLMKeywordParseError(
            message=f"confidence 必须在 0-1 之间，收到 {confidence}"
        )

    # reason 非空字符串
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return LLMKeywordParseError(message="reason 必须是非空字符串")

    # explanation 非空字符串
    explanation = data.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        return LLMKeywordParseError(message="explanation 必须是非空字符串")

    # removed_tokens 字符串数组
    removed_tokens = data.get("removed_tokens")
    if not isinstance(removed_tokens, list) or not all(
        isinstance(t, str) for t in removed_tokens
    ):
        return LLMKeywordParseError(message="removed_tokens 必须是字符串数组")

    return LLMKeywordResult(
        keyword=keyword.strip(),
        candidate_title=candidate_title.strip() if candidate_title else None,
        candidate_year=candidate_year,
        confidence=float(confidence),
        reason=reason.strip(),
        explanation=explanation.strip(),
        removed_tokens=removed_tokens,
    )


def parse_profile_router_response(
    raw_text: str,
    *,
    enabled_profiles: list[str],
) -> dict | LLMKeywordParseError:
    """解析 LLM 档案推荐响应，校验 recommended_profile 在已启用列表中"""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return LLMKeywordParseError(message=f"LLM 响应不是有效 JSON: {exc}")

    if not isinstance(data, dict):
        return LLMKeywordParseError(message="LLM 响应必须是 JSON 对象")

    # recommended_profile 必须在启用列表中
    profile = data.get("recommended_profile")
    if not isinstance(profile, str) or not profile.strip():
        return LLMKeywordParseError(message="recommended_profile 必须是非空字符串")
    if profile not in enabled_profiles:
        return LLMKeywordParseError(
            message=f"recommended_profile '{profile}' 不在已启用档案列表中"
        )

    # keyword 非空字符串
    keyword = data.get("keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        return LLMKeywordParseError(message="keyword 必须是非空字符串")

    # confidence 0-1
    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)):
        return LLMKeywordParseError(message="confidence 必须是数字")
    if confidence < 0 or confidence > 1:
        return LLMKeywordParseError(message="confidence 必须在 0-1 之间")

    return data


def parse_tpdb_keyword_response(
    raw_text: str,
) -> LLMKeywordResult | LLMKeywordParseError:
    """解析 TPDB 番号规范化响应，keyword 为主搜索词"""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return LLMKeywordParseError(message=f"TPDB LLM 响应不是有效 JSON: {exc}")

    if not isinstance(data, dict):
        return LLMKeywordParseError(message="TPDB LLM 响应必须是 JSON 对象")

    # keyword 优先使用 normalized_code，回退到 keyword 字段
    keyword = data.get("normalized_code") or data.get("keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        return LLMKeywordParseError(message="TPDB keyword/normalized_code 必须是非空字符串")

    # confidence 0-1
    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)):
        return LLMKeywordParseError(message="TPDB confidence 必须是数字")
    if confidence < 0 or confidence > 1:
        return LLMKeywordParseError(message=f"TPDB confidence 必须在 0-1 之间，收到 {confidence}")

    reason = data.get("reason", "")
    explanation = data.get("explanation", "")
    removed_tokens = data.get("removed_tokens", [])

    return LLMKeywordResult(
        keyword=keyword.strip(),
        candidate_title=None,
        candidate_year=None,
        confidence=float(confidence),
        reason=reason.strip() if reason else "tpdb_normalize",
        explanation=explanation.strip() if explanation else "",
        removed_tokens=removed_tokens if isinstance(removed_tokens, list) else [],
    )


# ── Resource Discovery Intent Prompt (Phase 3) ──

RESOURCE_INTENT_SYSTEM_PROMPT = """\
你是一个媒体资源搜索助手。用户会用自然语言描述想下载的电影或影片。\
你需要解析用户意图，输出结构化搜索参数。

要求：
- 判断搜索类型：movie（普通电影）、adult（成人影片）、all（不确定时）
- 提取候选片名（中文名和英文/原名）
- preferred_title_candidates：面向元数据识别（TMDB/TPDB），可按用户语言偏好排序；\
  中文用户搜索英文电影时中文片名排前，英文/原名排后
- resource_search_keywords：面向资源站搜索（Prowlarr），应选择资源站更常见的、\
  更可能命中资源标题的关键词；普通电影优先英文原名或常见发行名；\
  中文用户搜索英文电影时仍用英文原名，不要翻译成中文
- 若为成人影片，提取完整番号（如 ABP-123）或合法番号前缀（如 ABP）；\
  不输出题材/剧情/性感词；resource_search_keywords 也使用番号
- resource_keywords：向后兼容的通用关键词组合
- 识别用户的质量偏好，按结构化字段输出：
  preferred_resolutions: ["2160p", "1080p"] 等
  preferred_sources: ["REMUX", "BluRay", "WEB-DL"] 等
  preferred_video_codecs: ["HEVC", "AVC", "AV1"] 等
  preferred_hdr_tags: ["HDR10", "Dolby Vision"] 等
  preferred_audio_tags: ["Atmos", "TrueHD", "DTS-HD"] 等
  无明确偏好时返回空数组
- 推断对应的元数据 profile 偏好：普通电影tmdb_movie，剧集（分季分集）tmdb_show，\
  成人影片tpdb_adult_movie
- 情色题材的普通电影（如 Fifty Shades of Grey）仍属于 movie，不得标记为 adult
- 输出必须短且紧凑：只返回紧凑 JSON，不要 Markdown，不要解释，不要在 JSON 外输出任何文字
- 数组长度限制：title_candidates / preferred_title_candidates / adult_identifier_candidates \
  最多 2 项；\
  resource_keywords / resource_search_keywords 最多 3 项；质量偏好数组每类最多 3 项；无偏好返回 []
- reason 只写一句短理由（中文不超过 40 字，英文不超过 80 字符）
- 示例：用户输入"速度与激情8"
  preferred_title_candidates:["速度与激情8","The Fate of the Furious"]
  resource_search_keywords:["The Fate of the Furious 8",
    "The Fate of the Furious 2017","Fast and Furious 8"]

你必须只返回一个 JSON 对象：
{
  "search_type": "movie|adult|all",
  "title_candidates": ["最多2个候选片名"],
  "preferred_title_candidates": ["最多2个按语言偏好排序的片名"],
  "resource_keywords": ["最多3个通用关键词"],
  "resource_search_keywords": ["最多3个资源站关键词"],
  "preferred_resolutions": ["最多3个清晰度标签"],
  "preferred_sources": ["最多3个来源标签"],
  "preferred_video_codecs": ["最多3个编码标签"],
  "preferred_hdr_tags": ["最多3个HDR标签"],
  "preferred_audio_tags": ["最多3个音频标签"],
  "profile_hint": "tmdb_movie|tmdb_show|tpdb_adult_movie|unknown",
  "adult_identifier_candidates": ["最多2个完整番号或合法前缀，普通电影为空数组"],
  "reason": "一句短理由"
}"""

RESOURCE_INTENT_USER_TEMPLATE = '用户输入："{input_text}"'


def parse_resource_intent_response(response_text: str):  # noqa: F821 (ResourceIntent deferred by __future__ annotations)
    """解析 LLM 返回的资源搜索意图 JSON。

    处理 Markdown code fence（```json ... ```），校验字段类型和值域。
    """
    import re

    from media_pilot.resource_discovery.types import ResourceIntent

    # 去掉 Markdown code fence
    cleaned = response_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 如果去掉 fence 后仍然失败，尝试原始文本
        data = json.loads(response_text)

    # 字段值域校验
    search_type = data.get("search_type", "all")
    if search_type not in ("movie", "adult", "all"):
        search_type = "all"

    profile_hint = data.get("profile_hint", "unknown")
    if profile_hint not in ("tmdb_movie", "tmdb_show", "tpdb_adult_movie", "unknown"):
        profile_hint = "unknown"

    title_candidates = data.get("title_candidates", [])
    if not isinstance(title_candidates, list):
        title_candidates = [str(title_candidates)] if title_candidates else []

    resource_keywords = data.get("resource_keywords", [])
    if not isinstance(resource_keywords, list):
        resource_keywords = [str(resource_keywords)] if resource_keywords else []

    def _str_list(raw, key="<unknown>"):  # type: ignore[no-untyped-def]
        if isinstance(raw, list):
            return [str(x) for x in raw if isinstance(x, str) and x.strip()]
        return []

    return ResourceIntent(
        query_text="",
        search_type=search_type,
        title_candidates=[str(t) for t in title_candidates],
        resource_keywords=[str(k) for k in resource_keywords],
        quality_hint="",  # DEPRECATED
        profile_hint=profile_hint,
        preferred_title_candidates=[
            str(t) for t in (data.get("preferred_title_candidates") or [])
        ],
        adult_identifier_candidates=[
            str(a) for a in (data.get("adult_identifier_candidates") or [])
        ],
        resource_search_keywords=[
            str(k) for k in (data.get("resource_search_keywords") or [])
        ],
        reason=str(data.get("reason", "")),
        preferred_resolutions=_str_list(data.get("preferred_resolutions"), "resolutions"),
        preferred_sources=_str_list(data.get("preferred_sources"), "sources"),
        preferred_video_codecs=_str_list(data.get("preferred_video_codecs"), "codecs"),
        preferred_hdr_tags=_str_list(data.get("preferred_hdr_tags"), "hdr"),
        preferred_audio_tags=_str_list(data.get("preferred_audio_tags"), "audio"),
    )
