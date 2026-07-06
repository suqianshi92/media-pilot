"""
资源发现 API 路由 — 自然语言搜索 + 下载提交

POST /api/v1/resource-discovery/search
POST /api/v1/resource-discovery/download
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.api.schemas import ApiEnvelope, ApiMessage
from media_pilot.config import AppConfig
from media_pilot.services.resource_discovery import search_resources, submit_download

router = APIRouter(prefix="/api/v1/resource-discovery")


# ── 请求模型 ──


class ResourceSearchBody(BaseModel):
    input_text: str = Field(..., min_length=1, description="资源查询关键词")
    search_type: str = Field("all", description="搜索类型覆盖: all / movie / adult")
    skip_intent: bool = Field(True, description="是否跳过 LLM 意图解析，直接用原始关键词搜索")


class ResourceDownloadBody(BaseModel):
    candidate_token: str = Field(..., description="候选句柄")
    # 以下字段仅前端登记用，后端从缓存取真实值
    title: str = Field("", description="资源标题（后端覆盖）")
    source: str = Field("prowlarr", description="来源（后端覆盖）")
    indexer: str = Field("", description="indexer 名称（后端覆盖）")
    # 客户端不可指定保存路径 — 保存路径来自后端配置
    # 4.3: 可选元数据预选
    preselected_profile: str | None = Field(None, description="预选 metadata profile")
    preselected_provider: str | None = Field(None, description="预选 provider")
    preselected_external_id: str | None = Field(None, description="预选 external_id")


# ── 路由 ──


@router.post("/search")
def search(body: ResourceSearchBody, request: Request) -> ApiEnvelope[dict]:
    """直接资源搜索 — 默认跳过 LLM 意图解析。"""
    config: AppConfig | None = getattr(request.app.state, "config", None)
    if config is None:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(level="error", code="not_configured", text="未配置服务")],
            meta={},
        )

    # 验证 search_type
    search_type = body.search_type
    if search_type not in ("all", "movie", "adult"):
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(
                level="error", code="invalid_search_type",
                text=f"无效搜索类型: {search_type}。允许值: all/movie/adult",
            )],
            meta={},
        )

    # 读取应用配置中的语言偏好和已启用 profile
    preferred_language = "zh"
    enabled_profiles: list[str] = ["tmdb_movie"]
    try:
        session_factory = getattr(request.app.state, "session_factory", None)
        if session_factory is not None:
            from media_pilot.services.app_settings import AppSettingsService
            svc = AppSettingsService(session_factory)
            settings = svc.read()
            preferred_language = settings.preferred_metadata_language
            enabled_profiles = list(settings.enabled_metadata_profiles)
    except Exception:
        pass

    result = search_resources(
        body.input_text.strip(), config,
        search_type_override=search_type,
        skip_intent=body.skip_intent,
        preferred_language=preferred_language,
        enabled_profiles=enabled_profiles,
    )

    if result["status"] == "error":
        return ApiEnvelope(
            status="error",
            data=result.get("data", {}),
            messages=[ApiMessage(level="error", code="search_failed", text=result["message"])],
            meta={},
        )

    return ApiEnvelope(
        status="success",
        data=result["data"],
        messages=[],
        meta={"query_used": result["data"].get("query_used", "")},
    )


@router.post("/download")
def download(body: ResourceDownloadBody, request: Request) -> ApiEnvelope[dict]:
    """提交下载到 qBittorrent — 保存路径来自后端配置"""
    config: AppConfig | None = getattr(request.app.state, "config", None)
    if config is None:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(level="error", code="not_configured", text="未配置服务")],
            meta={},
        )

    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )

    result = submit_download(
        config,
        candidate_token=body.candidate_token,
        title=body.title,
        source=body.source,
        indexer=body.indexer,
        session_factory=session_factory,
        preselected_profile=body.preselected_profile,
        preselected_provider=body.preselected_provider,
        preselected_external_id=body.preselected_external_id,
    )

    if result["status"] == "error":
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(level="error", code="download_failed", text=result["message"])],
            meta={},
        )

    return ApiEnvelope(
        status="success",
        data=result["data"],
        messages=[ApiMessage(level="info", code="submitted", text=result["message"])],
        meta={},
    )


# ── 轻量关键词清洗 ──

def _lightweight_clean_keyword(config, keyword: str, *, profile: str = "",
                              intent_context: dict | None = None,
                              preferred_language: str = "zh") -> str:
    """使用 LLM 清洗关键词：profile-aware，TMDB 与 TPDB 不同策略。

    LLM 不可用或调用失败时安全回退为原始关键词。
    """
    if not config.llm_api_key or not config.llm_base_url or not config.llm_model:
        return keyword

    is_tpdb = "adult" in profile.lower()

    # 构建上下文
    context_parts = []
    if intent_context:
        user_input = intent_context.get("user_input", "")
        intent = intent_context.get("intent", {})
        if user_input:
            context_parts.append(f"用户原始搜索: {user_input}")
        if intent.get("reason"):
            context_parts.append(f"意图分析: {intent['reason']}")
        if intent.get("preferred_title_candidates"):
            context_parts.append(
                f"候选片名: {', '.join(intent['preferred_title_candidates'])}"
            )
        if intent.get("adult_identifier_candidates"):
            context_parts.append(
                f"候选番号: {', '.join(intent['adult_identifier_candidates'])}"
            )
    context_str = "；".join(context_parts) if context_parts else ""

    if is_tpdb:
        system_content = (
            "你是番号清洗助手。从输入文本中只提取合法番号。"
            "\n\n要求：\n"
            "- 只输出完整番号（如 ABP-123）或合法番号前缀（如 ABP）\n"
            "- 不输出题材词、偏好词、剧情词、成人作品长标题\n"
            "- 如果没有可靠番号，返回空字符串（不猜测、不补全）\n"
            "- 只返回纯文本，不包含任何解释"
        )
    else:
        lang_hint = (
            "优先输出中文片名（若可用）"
            if preferred_language == "zh"
            else "优先输出英文原名"
        )
        system_content = (
            "你是电影搜索关键词清洗助手。从输入的原始文本中提取"
            "用于 TMDB 元数据搜索的最佳关键词。"
            "\n\n要求：\n"
            "- 保留影片标题和年份，去除分辨率、编码格式、压制组、音频格式等技术信息\n"
            f"- {lang_hint}，无把握时保留原名\n"
            "- 不要机翻或创造译名\n"
            "- 只返回纯文本关键词，不包含任何解释"
        )

    user_content = f"资源原标题: {keyword}"
    if context_str:
        user_content = f"{context_str}\n{user_content}"

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            timeout=15,
        )
        completion = client.chat.completions.create(
            model=config.llm_model,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
        )
        if completion.choices and completion.choices[0].message.content is not None:
            cleaned = completion.choices[0].message.content.strip()
            # TPDB: accept empty as valid (no guess)
            if is_tpdb:
                if cleaned or cleaned == "":
                    return cleaned
            else:
                if cleaned and cleaned != keyword:
                    return cleaned
    except Exception:
        pass
    return keyword


# ── 3.x: 候选识别 ──

class CandidateIdentifyBody(BaseModel):
    candidate_handle: str | None = Field(None, description="候选句柄（手动导入等场景可为空）")
    profile: str = Field(..., description="metadata profile 名称")
    keyword: str = Field(..., min_length=1, description="检索关键词")
    use_lightweight_cleanup: bool = Field(False, description="是否启用轻量 LLM 清洗")


@router.post("/identify")
def identify(body: CandidateIdentifyBody, request: Request) -> ApiEnvelope[dict]:
    """候选识别 — 对单个资源候选执行元数据检索"""
    config: AppConfig | None = getattr(request.app.state, "config", None)
    if config is None:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(level="error", code="not_configured", text="未配置服务")],
            meta={},
        )

    from media_pilot.services.candidate_search import search_metadata_candidates

    # 候选句柄消费：通过 handle 回查缓存中的资源候选标题，
    # 确保首轮识别以资源标题为主输入，不完全信任前端自由输入。
    # 当前只消费标题字段，不消费更多资源发现上下文（indexer/seeder 等）。
    candidate_context = ""
    intent_ctx = {}
    if body.candidate_handle:
        try:
            from media_pilot.services.candidate_cache import lookup_candidate
            cached, intent_ctx = lookup_candidate(body.candidate_handle)
            if cached is not None:
                candidate_context = (cached.get("title") or "").strip()
        except Exception:
            pass

    # 关键词：优先信任后端回查的候选标题；前端 keyword 作为二次输入
    keyword_used = body.keyword
    if not keyword_used.strip() and candidate_context:
        keyword_used = candidate_context
    if body.use_lightweight_cleanup:
        # 读取应用配置中的语言偏好
        preferred_language = "zh"
        try:
            session_factory = getattr(
                request.app.state, "session_factory", None
            )
            if session_factory is not None:
                from media_pilot.services.app_settings import AppSettingsService
                svc = AppSettingsService(session_factory)
                settings = svc.read()
                preferred_language = settings.preferred_metadata_language
        except Exception:
            pass
        keyword_used = _lightweight_clean_keyword(
            config, keyword_used,
            profile=body.profile,
            intent_context=intent_ctx,
            preferred_language=preferred_language,
        )

    # 解析 profile name → provider name（tmdb_movie → tmdb）
    from media_pilot.services.profile_registry import (
        get_profile_registry,
        register_builtin_profiles,
    )
    register_builtin_profiles()
    registry = get_profile_registry()
    try:
        profile = registry.get(body.profile)
    except KeyError:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(level="error", code="invalid_profile",
                                 text=f"不支持的 metadata profile: {body.profile}")],
            meta={},
        )
    provider_name = profile.provider_name

    try:
        candidates = search_metadata_candidates(
            config=config,
            provider_name=provider_name,
            keyword=keyword_used,
            profile=profile,
        )
    except ValueError as e:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(level="error", code="invalid_profile", text=str(e))],
            meta={},
        )
    except Exception as e:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(level="error", code="search_failed", text=str(e))],
            meta={},
        )

    # 返回前端友好的候选列表
    candidate_dicts = [
        {
            "provider": c.provider,
            "provider_id": c.provider_id,
            "title": c.title,
            "original_title": c.original_title,
            "year": c.year,
            "media_type": c.media_type,
            "overview": c.overview,
            "poster_url": c.poster_url,
            "confidence": c.confidence,
            "match_reason": c.match_reason,
        }
        for c in candidates
    ]

    # 候选上限 5（前端识别面板首屏约束）
    MAX_CANDIDATES = 5
    limited_candidates = candidate_dicts[:MAX_CANDIDATES]

    return ApiEnvelope(
        status="success",
        data={
            "keyword_used": keyword_used,
            "profile": body.profile,
            "candidates": limited_candidates,
        },
        messages=[],
        meta={"candidate_count": len(limited_candidates), "total_count": len(candidate_dicts)},
    )
