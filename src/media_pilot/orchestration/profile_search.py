"""多档案搜索 — 按启用档案顺序检索，LLM 路由回退"""

from __future__ import annotations

from dataclasses import dataclass, field

from media_pilot.adapters.factory import create_metadata_provider_by_name
from media_pilot.adapters.metadata import MetadataCandidate, MetadataProvider
from media_pilot.config import AppConfig
from media_pilot.services.profile_registry import MetadataProfile


@dataclass
class ProfileSearchResult:
    """多档案搜索结果"""
    profile: MetadataProfile | None = None
    provider: MetadataProvider | None = None
    provider_name: str = ""
    keyword: str = ""
    candidates: list[MetadataCandidate] = field(default_factory=list)
    has_clear_winner: bool = False
    searched_profiles: list[str] = field(default_factory=list)
    error_message: str | None = None


def quick_search(
    provider: MetadataProvider,
    keyword: str,
    *,
    profile: MetadataProfile | None = None,
) -> list[MetadataCandidate]:
    """轻量搜索 — 只查询 provider，不做持久化"""
    if profile is not None and profile.writer_profile == "show":
        response = provider.search_show(keyword, language_priority=["zh-CN", "en-US"])
    else:
        response = provider.search_movie(keyword, language_priority=["zh-CN", "en-US"])
    if response.error is not None:
        return []
    return response.value or []


def search_with_profiles(
    *,
    config: AppConfig,
    keyword: str,
    enabled_profiles: list[MetadataProfile],
    auto_confirm_confidence: float = 0.9,
    auto_confirm_margin: float = 0.08,
) -> ProfileSearchResult:
    """按档案顺序执行原始检索，出现明确高置信度候选后短路

    只做搜索，不持久化候选。由调用方决定使用哪个档案的结果。
    无 clear winner 时累积全部候选，供人工确认页展示。
    """
    from media_pilot.orchestration.auto_confirmation import has_clear_winner

    searched: list[str] = []
    all_candidates: list[MetadataCandidate] = []
    last_profile: MetadataProfile | None = None
    last_provider: MetadataProvider | None = None
    last_provider_name = ""

    for profile in enabled_profiles:
        try:
            provider = create_metadata_provider_by_name(config, profile.provider_name)
        except ValueError:
            continue  # provider 尚未实现，跳过

        searched.append(profile.name)
        candidates = quick_search(provider, keyword, profile=profile)
        all_candidates.extend(candidates)
        last_profile = profile
        last_provider = provider
        last_provider_name = getattr(provider, "provider_name", profile.provider_name)

        if has_clear_winner(
            candidates,
            confidence_threshold=auto_confirm_confidence,
            margin=auto_confirm_margin,
        ):
            return ProfileSearchResult(
                profile=profile,
                provider=provider,
                provider_name=getattr(provider, "provider_name", profile.provider_name),
                keyword=keyword,
                candidates=candidates,
                has_clear_winner=True,
                searched_profiles=searched,
            )

    return ProfileSearchResult(
        profile=last_profile,
        provider=last_provider,
        provider_name=last_provider_name,
        keyword=keyword,
        candidates=all_candidates,
        has_clear_winner=False,
        searched_profiles=searched,
    )
