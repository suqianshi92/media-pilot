"""元数据搜索流程模块。

负责从媒体源路径到 provider 候选的完整搜索过程。
workflow 主流程只消费 MetadataSearchOutcome。
"""

from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_pilot.adapters.ai import (
    AiProfileRouter,
    AiSearchKeywordGenerator,
    AiSearchKeywordRequest,
    AiSearchKeywordResult,
)
from media_pilot.adapters.metadata import MetadataCandidate as ProviderCandidate
from media_pilot.adapters.metadata import MetadataProvider
from media_pilot.config import AppConfig
from media_pilot.orchestration.auto_confirmation import has_clear_winner
from media_pilot.orchestration.search_keyword_generation import SearchKeywordResult
from media_pilot.orchestration.state_machine import IngestTaskStatus
from media_pilot.repository.models import AdapterCall
from media_pilot.repository.repositories import (
    IngestTaskRepository,
    MediaCandidateRepository,
    SearchKeywordRepository,
)


@dataclass(frozen=True)
class MetadataSearchOutcome:
    keyword_used: SearchKeywordResult
    provider_search_result: 'ProviderSearchResult'
    selected_metadata_provider: MetadataProvider | None = None
    selected_provider_name: str = ""
    profile_result: object | None = None


@dataclass
class ProviderSearchResult:
    candidates: list
    error_message: str | None = None


# ---- 内部 helper 函数 ----

def _ai_error_message(error: Exception) -> str:
    message = str(error)
    return message or error.__class__.__name__

def _mark_progress(session: Session, task_id: str, step: str) -> None:
    repository = IngestTaskRepository(session)
    task = repository.get(task_id)
    if task is not None:
        task.status = IngestTaskStatus.PROCESSING
        task.current_step = step
        session.commit()

def _record_failed_adapter_call(
    session: Session,
    task_id: str,
    *,
    adapter_name: str,
    action: str,
    request_summary: dict,
    error_message: str,
) -> None:
    session.add(
        AdapterCall(
            task_id=task_id,
            adapter_name=adapter_name,
            action=action,
            request_summary=request_summary,
            response_summary={},
            status="failed",
            error_message=error_message,
        )
    )

def provider_candidate_payload(candidate: ProviderCandidate) -> dict:
    return {
        "provider": candidate.provider,
        "provider_id": candidate.provider_id,
        "title": candidate.title,
        "original_title": candidate.original_title,
        "year": candidate.year,
        "media_type": candidate.media_type,
        "overview": candidate.overview,
        "poster_url": candidate.poster_url,
        "confidence": candidate.confidence,
        "match_reason": candidate.match_reason,
        "payload": candidate.payload,
    }

def _persist_provider_candidates(
    session: Session,
    task_id: str,
    profile_result,
    auto_confirm_confidence: float,
) -> None:
    """持久化 profile-aware 搜索的候选到 MediaCandidate 和 AdapterCall"""
    candidates = profile_result.candidates
    action = "search_show" if (
        profile_result.profile is not None and profile_result.profile.writer_profile == "show"
    ) else "search_movie"
    session.add(
        AdapterCall(
            task_id=task_id,
            adapter_name=profile_result.provider_name,
            action=action,
            request_summary={
                "keyword": profile_result.keyword,
                "profile": profile_result.profile.name if profile_result.profile else "",
            },
            response_summary={
                "candidate_count": len(candidates),
                "provider_ids": [c.provider_id for c in candidates],
            },
            status="succeeded",
        )
    )
    repository = MediaCandidateRepository(session)
    for candidate in candidates:
        repository.add_candidate(
            task_id,
            source=candidate.provider,
            media_type=candidate.media_type,
            title=candidate.title,
            original_title=candidate.original_title,
            year=candidate.year,
            external_id=candidate.provider_id,
            confidence=candidate.confidence,
            reason=candidate.match_reason,
            payload=provider_candidate_payload(candidate),
        )

def _search_provider_candidates(
    session: Session,
    *,
    task_id: str,
    keyword: str,
    metadata_provider: MetadataProvider | None,
    metadata_provider_name: str,
    writer_profile: str = "movie",
) -> ProviderSearchResult:
    if metadata_provider is None:
        return ProviderSearchResult(candidates=[])

    if writer_profile == "show":
        response = metadata_provider.search_show(keyword, language_priority=["zh-CN", "en-US"])
    else:
        response = metadata_provider.search_movie(keyword, language_priority=["zh-CN", "en-US"])
    action = "search_show" if writer_profile == "show" else "search_movie"
    if response.error is not None:
        _record_failed_adapter_call(
            session,
            task_id,
            adapter_name=metadata_provider_name,
            action=action,
            request_summary={"keyword": keyword, "language_priority": ["zh-CN", "en-US"]},
            error_message=response.error.message,
        )
        return ProviderSearchResult(candidates=[], error_message=response.error.message)

    candidates = response.value or []
    session.add(
        AdapterCall(
            task_id=task_id,
            adapter_name=metadata_provider_name,
            action=action,
            request_summary={"keyword": keyword, "language_priority": ["zh-CN", "en-US"]},
            response_summary={
                "candidate_count": len(candidates),
                "provider_ids": [candidate.provider_id for candidate in candidates],
            },
            status="succeeded",
        )
    )

    repository = MediaCandidateRepository(session)
    for candidate in candidates:
        repository.add_candidate(
            task_id,
            source=candidate.provider,
            media_type=candidate.media_type,
            title=candidate.title,
            original_title=candidate.original_title,
            year=candidate.year,
            external_id=candidate.provider_id,
            confidence=candidate.confidence,
            reason=candidate.match_reason,
            payload=provider_candidate_payload(candidate),
        )
    return ProviderSearchResult(candidates=candidates)

def _build_rule_search_keyword_result(selected_path: Path) -> SearchKeywordResult:
    from media_pilot.orchestration.raw_search_keyword import (
        STREAM_LIKE_EXTENSIONS,
        build_raw_search_keyword,
    )
    from media_pilot.orchestration.search_keyword_generation import generate_search_keyword

    if selected_path.suffix.lower() in STREAM_LIKE_EXTENSIONS:
        raw_keyword = build_raw_search_keyword(selected_path)
        return SearchKeywordResult(
            keyword=raw_keyword,
            source="raw",
            confidence=0.5,
            reason="raw_search_keyword",
            payload={"quality_tokens": [], "tokens_removed": [], "attempt": "raw"},
        )

    rule_result = generate_search_keyword(selected_path)
    return SearchKeywordResult(
        keyword=rule_result.keyword,
        source="raw",
        confidence=rule_result.confidence,
        reason=rule_result.reason,
        payload={**rule_result.payload, "attempt": "raw"},
    )

def _search_keyword_result_from_ai(rule_result, ai_result: AiSearchKeywordResult):
    return type(rule_result)(
        keyword=ai_result.keyword,
        source="llm",
        confidence=ai_result.confidence,
        reason=ai_result.reason,
        payload={
            "quality_tokens": list(rule_result.payload.get("quality_tokens", [])),
            "tokens_removed": ai_result.removed_tokens,
            "rule_keyword": rule_result.keyword,
            "rule_confidence": rule_result.confidence,
            "candidate_title": ai_result.candidate_title,
            "candidate_year": ai_result.candidate_year,
            "explanation": ai_result.explanation,
        },
    )

def _default_enabled_profiles(config: AppConfig) -> list[str]:
    """返回默认启用的档案列表，TPDB key 已配时自动包含 tpdb_adult_movie"""
    profiles = ["tmdb_movie"]
    if config.tpdb_api_key:
        profiles.append("tpdb_adult_movie")
    return profiles

def _validate_ai_search_keyword_result(result: object) -> None:
    if not isinstance(result, AiSearchKeywordResult):
        raise ValueError("invalid_ai_search_keyword_result")
    if not result.keyword.strip():
        raise ValueError("invalid_ai_search_keyword_result")
    if result.confidence < 0 or result.confidence > 1:
        raise ValueError("invalid_ai_search_keyword_result")


# ---- 主搜索流程 ----

def run_metadata_search(
    session: Session,
    *,
    task_id: str,
    config: AppConfig,
    selected_path: Path,
    source_path: Path,
    ai_keyword_generator: AiSearchKeywordGenerator | None,
    ai_keyword_adapter_name: str,
    profile_router: AiProfileRouter | None,
    metadata_provider: MetadataProvider | None,
    metadata_provider_name: str,
    show_structure_detected: bool = False,
) -> MetadataSearchOutcome:
    # ---- Provider-first 搜索 (profile-aware 或 legacy) ----
    raw_kw_result = _build_rule_search_keyword_result(selected_path)
    keyword_used = raw_kw_result

    from media_pilot.services.profile_registry import (
        get_profile_registry,
        register_builtin_profiles,
    )
    register_builtin_profiles()
    profile_registry = get_profile_registry()

    from media_pilot.repository.models import AppSetting
    db_settings = session.scalars(select(AppSetting)).first()
    enabled_names: list[str] = (
        list(db_settings.enabled_metadata_profiles)
        if db_settings and db_settings.enabled_metadata_profiles
        else _default_enabled_profiles(config)
    )

    # 剧集结构证据：将 tmdb_show 提升到搜索顺序首位
    if show_structure_detected and "tmdb_show" in enabled_names:
        enabled_names = ["tmdb_show"] + [n for n in enabled_names if n != "tmdb_show"]
    enabled_profiles = [
        profile_registry.get(name)
        for name in enabled_names
        if name in profile_registry.list_names()
    ]

    # 阶段 1：多档案检索或 legacy 单 provider 检索
    provider_search_result: ProviderSearchResult
    selected_metadata_provider = metadata_provider
    selected_provider_name = metadata_provider_name
    if enabled_profiles:
        from media_pilot.orchestration.profile_search import search_with_profiles
        _mark_progress(session, task_id, "raw_metadata_search")
        profile_result = search_with_profiles(
            config=config,
            keyword=raw_kw_result.keyword,
            enabled_profiles=enabled_profiles,
            auto_confirm_confidence=config.metadata_auto_confirm_confidence,
            auto_confirm_margin=config.metadata_auto_confirm_margin,
        )
        if profile_result.has_clear_winner:
            _persist_provider_candidates(
                session, task_id, profile_result,
                config.metadata_auto_confirm_confidence,
            )
            provider_search_result = ProviderSearchResult(
                candidates=profile_result.candidates
            )
            selected_provider_name = profile_result.provider_name
            selected_metadata_provider = profile_result.provider
        elif not profile_result.searched_profiles:
            # 所有档案的 provider 都不可用，回退到 legacy
            provider_search_result = _search_provider_candidates(
                session,
                task_id=task_id,
                keyword=raw_kw_result.keyword,
                metadata_provider=metadata_provider,
                metadata_provider_name=metadata_provider_name,
            )
        else:
            # 有档案搜索但无 clear winner — 持久化候选供人工确认
            if profile_result.candidates:
                _persist_provider_candidates(
                    session, task_id, profile_result,
                    config.metadata_auto_confirm_confidence,
                )
            provider_search_result = ProviderSearchResult(
                candidates=profile_result.candidates
            )
            selected_metadata_provider = profile_result.provider
            selected_provider_name = profile_result.provider_name
    else:
        # Legacy: 单 provider 搜索
        _mark_progress(session, task_id, "raw_metadata_search")
        provider_search_result = _search_provider_candidates(
            session,
            task_id=task_id,
            keyword=raw_kw_result.keyword,
            metadata_provider=metadata_provider,
            metadata_provider_name=metadata_provider_name,
        )
        profile_result = None

    # 阶段 2：全部档案不明确且 LLM 可用 → LLM 档案推荐与关键词清洗
    if ((profile_result is None or not profile_result.has_clear_winner)
            and (ai_keyword_generator is not None
                 or profile_router is not None)):
        _mark_progress(session, task_id, "llm_keyword_cleanup")
        router_used = False
        recommendation = None

        # ---- 档案路由器：LLM 推荐档案 + 单档案二次检索 ----
        # ---- 档案路由器：LLM 推荐 → 档案专属清洗 → 单档案二次检索 ----
        if profile_router is not None and enabled_profiles:
            from media_pilot.adapters.factory import (
                create_metadata_provider_by_name,
            )
            from media_pilot.orchestration.profile_search import (
                ProfileSearchResult,
            )
            search_keyword = raw_kw_result.keyword
            keyword_source_confidence = raw_kw_result.confidence
            try:
                recommendation = profile_router.recommend_profile(
                    input_text=selected_path.name,
                    enabled_profiles=enabled_names,
                )
                session.add(AdapterCall(
                    task_id=task_id,
                    adapter_name=ai_keyword_adapter_name,
                    action="recommend_profile",
                    request_summary={
                        "input_text": selected_path.name,
                        "enabled_profiles": enabled_names,
                    },
                    response_summary={
                        "recommended_profile": (
                            recommendation.recommended_profile
                        ),
                        "keyword": recommendation.keyword,
                        "confidence": recommendation.confidence,
                    },
                    status="succeeded",
                ))

                # 档案专属关键词清洗
                if ai_keyword_generator is not None:
                    try:
                        cleaned_kw = (
                            ai_keyword_generator
                            .generate_search_keyword(
                                AiSearchKeywordRequest(
                                    input_path=str(source_path),
                                    input_name=source_path.name,
                                    selected_path=str(
                                        selected_path
                                    ),
                                    selected_name=(
                                        selected_path.name
                                    ),
                                    selected_parent_name=(
                                        selected_path
                                        .parent.name
                                    ),
                                    rule_keyword=(
                                        raw_kw_result.keyword
                                    ),
                                    rule_confidence=(
                                        raw_kw_result.confidence
                                    ),
                                    quality_tokens=list(
                                        raw_kw_result.payload.get(
                                            "quality_tokens", []
                                        )
                                    ),
                                    removed_tokens=list(
                                        raw_kw_result.payload.get(
                                            "tokens_removed", []
                                        )
                                    ),
                                    profile=(
                                        recommendation
                                        .recommended_profile
                                    ),
                                )
                            )
                        )
                        session.add(AdapterCall(
                            task_id=task_id,
                            adapter_name=(
                                ai_keyword_adapter_name
                            ),
                            action="generate_search_keyword",
                            request_summary={
                                "profile": (
                                    recommendation
                                    .recommended_profile
                                ),
                                "router_keyword": (
                                    recommendation.keyword
                                ),
                            },
                            response_summary={
                                "keyword": cleaned_kw.keyword,
                                "confidence": (
                                    cleaned_kw.confidence
                                ),
                            },
                            status="succeeded",
                        ))
                        search_keyword = cleaned_kw.keyword
                        keyword_source_confidence = (
                            cleaned_kw.confidence
                        )
                    except Exception as kw_error:
                        _record_failed_adapter_call(
                            session, task_id,
                            adapter_name=(
                                ai_keyword_adapter_name
                            ),
                            action="generate_search_keyword",
                            request_summary={
                                "profile": (
                                    recommendation
                                    .recommended_profile
                                ),
                                "router_keyword": (
                                    recommendation.keyword
                                ),
                            },
                            error_message=(
                                _ai_error_message(kw_error)
                            ),
                        )
                        search_keyword = recommendation.keyword
                        keyword_source_confidence = (
                            recommendation.confidence
                        )
                else:
                    search_keyword = recommendation.keyword
                    keyword_source_confidence = (
                        recommendation.confidence
                    )

                # 用清洗后关键词执行推荐档案的单档案检索
                recommended_profile = profile_registry.get(
                    recommendation.recommended_profile
                )
                if recommended_profile is not None:
                    try:
                        single_provider = (
                            create_metadata_provider_by_name(
                                config,
                                recommended_profile.provider_name,
                            )
                        )
                    except ValueError:
                        single_provider = None
                    if single_provider is not None:
                        from media_pilot.orchestration.profile_search import (
                            quick_search,
                        )
                        router_candidates = quick_search(
                            single_provider, search_keyword,
                            profile=recommended_profile,
                        )
                        profile_result = ProfileSearchResult(
                            profile=recommended_profile,
                            provider=single_provider,
                            provider_name=(
                                recommended_profile.provider_name
                            ),
                            keyword=search_keyword,
                            candidates=router_candidates,
                            has_clear_winner=has_clear_winner(
                                router_candidates,
                                confidence_threshold=(
                                    config
                                    .metadata_auto_confirm_confidence
                                ),
                                margin=(
                                    config
                                    .metadata_auto_confirm_margin
                                ),
                            ),
                            searched_profiles=[
                                recommendation.recommended_profile
                            ],
                        )
                        if profile_result.has_clear_winner:
                            _persist_provider_candidates(
                                session, task_id,
                                profile_result,
                                config
                                .metadata_auto_confirm_confidence,
                            )
                            provider_search_result = (
                                ProviderSearchResult(
                                    candidates=(
                                        profile_result.candidates
                                    )
                                )
                            )
                            selected_provider_name = (
                                profile_result.provider_name
                            )
                            selected_metadata_provider = (
                                profile_result.provider
                            )
                        else:
                            if profile_result.candidates:
                                _persist_provider_candidates(
                                    session, task_id,
                                    profile_result,
                                    config
                                    .metadata_auto_confirm_confidence,
                                )
                            provider_search_result = (
                                ProviderSearchResult(
                                    candidates=(
                                        profile_result.candidates
                                    )
                                )
                            )
                            selected_metadata_provider = (
                                profile_result.provider
                            )
                            selected_provider_name = (
                                profile_result.provider_name
                            )
                    else:
                        provider_search_result = (
                            ProviderSearchResult(candidates=[])
                        )
                else:
                    provider_search_result = (
                        ProviderSearchResult(candidates=[])
                    )
                keyword_used = SearchKeywordResult(
                    keyword=search_keyword,
                    source="llm_router",
                    confidence=keyword_source_confidence,
                    reason=recommendation.reason,
                    payload={
                        "attempt": "llm_router",
                        "profile": (
                            recommendation.recommended_profile
                        ),
                    },
                )
                router_used = True
            except Exception as error:
                _record_failed_adapter_call(
                    session, task_id,
                    adapter_name=ai_keyword_adapter_name,
                    action="recommend_profile",
                    request_summary={
                        "input_text": (
                            selected_path.name
                        ),
                        "enabled_profiles": enabled_names,
                    },
                    error_message=_ai_error_message(error),
                )
                provider_search_result = (
                    ProviderSearchResult(candidates=[])
                )
                keyword_used = SearchKeywordResult(
                    keyword=raw_kw_result.keyword,
                    source="llm_router_failed",
                    confidence=raw_kw_result.confidence,
                    reason=(
                        "router failed, "
                        "fallback to rule keyword"
                    ),
                    payload={"attempt": "llm_router"},
                )
                router_used = True

        # ---- 回退：通用关键词生成器 ----
        if not router_used and ai_keyword_generator is not None:
            request = AiSearchKeywordRequest(
                input_path=str(source_path),
                input_name=source_path.name,
                selected_path=str(selected_path),
                selected_name=selected_path.name,
                selected_parent_name=selected_path.parent.name,
                rule_keyword=raw_kw_result.keyword,
                rule_confidence=raw_kw_result.confidence,
                quality_tokens=list(raw_kw_result.payload.get("quality_tokens", [])),
                removed_tokens=list(raw_kw_result.payload.get("tokens_removed", [])),
            )
            try:
                ai_kw = ai_keyword_generator.generate_search_keyword(request)
                _validate_ai_search_keyword_result(ai_kw)
                session.add(AdapterCall(
                    task_id=task_id,
                    adapter_name=ai_keyword_adapter_name,
                    action="generate_search_keyword",
                    request_summary=asdict(request),
                    response_summary=asdict(ai_kw),
                    status="succeeded",
                ))
                if enabled_profiles:
                    profile_result = search_with_profiles(
                        config=config,
                        keyword=ai_kw.keyword,
                        enabled_profiles=enabled_profiles,
                        auto_confirm_confidence=(
                            config.metadata_auto_confirm_confidence
                        ),
                        auto_confirm_margin=config.metadata_auto_confirm_margin,
                    )
                    if profile_result.has_clear_winner:
                        _persist_provider_candidates(
                            session, task_id, profile_result,
                            config.metadata_auto_confirm_confidence,
                        )
                        provider_search_result = ProviderSearchResult(
                            candidates=profile_result.candidates
                        )
                        selected_provider_name = profile_result.provider_name
                        selected_metadata_provider = profile_result.provider
                    elif not profile_result.searched_profiles:
                        provider_search_result = _search_provider_candidates(
                            session, task_id=task_id,
                            keyword=ai_kw.keyword,
                            metadata_provider=metadata_provider,
                            metadata_provider_name=metadata_provider_name,
                        )
                        selected_provider_name = metadata_provider_name
                        selected_metadata_provider = metadata_provider
                    else:
                        if profile_result.candidates:
                            _persist_provider_candidates(
                                session, task_id, profile_result,
                                config.metadata_auto_confirm_confidence,
                            )
                        provider_search_result = ProviderSearchResult(
                            candidates=profile_result.candidates
                        )
                        selected_metadata_provider = profile_result.provider
                        selected_provider_name = profile_result.provider_name
                else:
                    provider_search_result = _search_provider_candidates(
                        session, task_id=task_id,
                        keyword=ai_kw.keyword,
                        metadata_provider=metadata_provider,
                        metadata_provider_name=selected_provider_name,
                    )
                keyword_used = _search_keyword_result_from_ai(
                    raw_kw_result, ai_kw
                )
                keyword_used.payload["attempt"] = "llm"
            except Exception as error:
                _record_failed_adapter_call(
                    session, task_id,
                    adapter_name=ai_keyword_adapter_name,
                    action="generate_search_keyword",
                    request_summary=asdict(request),
                    error_message=_ai_error_message(error),
                )

    SearchKeywordRepository(session).save(
        task_id,
        keyword=keyword_used.keyword,
        source=keyword_used.source,
        confidence=keyword_used.confidence,
        reason=keyword_used.reason,
        payload=keyword_used.payload,
    )

    return MetadataSearchOutcome(
        keyword_used=keyword_used,
        provider_search_result=provider_search_result,
        selected_metadata_provider=selected_metadata_provider,
        selected_provider_name=selected_provider_name,
        profile_result=profile_result,
    )
