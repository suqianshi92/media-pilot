"""元数据候选检索服务 — 从入库 workflow 抽离的可复用单 profile 搜索"""

from __future__ import annotations

from media_pilot.adapters.factory import create_metadata_provider_by_name
from media_pilot.adapters.metadata import MetadataCandidate
from media_pilot.config import AppConfig
from media_pilot.services.profile_registry import MetadataProfile


def search_metadata_candidates(
    *,
    config: AppConfig,
    provider_name: str,
    keyword: str,
    language_priority: tuple[str, ...] = ("zh-CN", "en-US"),
    profile: MetadataProfile | None = None,
) -> list[MetadataCandidate]:
    """搜索单个 metadata profile，返回规范化候选列表。

    不持久化、不创建 AdapterCall、不入库 workflow。
    供资源发现页候选识别和入库流程复用。
    """
    provider = create_metadata_provider_by_name(config, provider_name)
    if profile is not None and profile.writer_profile == "show":
        response = provider.search_show(keyword, language_priority=list(language_priority))
    else:
        response = provider.search_movie(keyword, language_priority=list(language_priority))
    if response.error is not None:
        return []
    return response.value or []
