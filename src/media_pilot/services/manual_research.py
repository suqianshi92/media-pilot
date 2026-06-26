"""手动重搜服务 — profile-aware 人工关键词重搜，不依赖 ConfirmationRequest。

新行为：
1. 保存 SearchKeywordRecord (source=manual)
2. 解析 scope → 目标 profiles
3. 对每个 profile 调用 quick_search
4. 去重、排序、写入 MediaCandidate / AdapterCall
5. 返回候选给用户选择；不自动发布、不创建决策

旧 ConfirmationRequest 通道已下线；该服务不再创建或修改 ConfirmationRequest。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_pilot.adapters.metadata import MetadataCandidate, MetadataProvider
from media_pilot.config import AppConfig
from media_pilot.orchestration.metadata_search_flow import provider_candidate_payload
from media_pilot.orchestration.profile_search import quick_search
from media_pilot.repository.models import (
    AdapterCall,
    MediaCandidate,
)
from media_pilot.repository.repositories import SearchKeywordRepository

ResearchScope = Literal["all", "tmdb_movie", "tmdb_show", "tpdb_adult_movie"]

VALID_SCOPES: set[ResearchScope] = {"all", "tmdb_movie", "tmdb_show", "tpdb_adult_movie"}


@dataclass
class ProfileSearchStatus:
    """单个 profile 的搜索状态"""

    profile: str
    label: str
    provider: str
    status: Literal["succeeded", "failed", "skipped"]
    candidate_count: int = 0
    error_message: str | None = None


@dataclass
class SearchSummary:
    """手动重搜摘要"""

    keyword: str
    scope: ResearchScope
    searched_profiles: list[ProfileSearchStatus] = field(default_factory=list)
    total_candidates: int = 0
    kept_existing_candidates: bool = False


@dataclass
class ManualResearchResult:
    """手动重搜结果"""

    candidates: list[MediaCandidate]
    summary: SearchSummary


def run_manual_research(
    session: Session,
    *,
    task_id: str,
    keyword: str,
    scope: ResearchScope,
    config: AppConfig,
) -> ManualResearchResult:
    """执行 profile-aware 手动重搜。

    本接口只负责“找候选并落库”，不负责发布。用户确认候选后再通过
    ``submit_manual_selection`` 进入确定性写入路径，避免搜索动作本身产生
    隐式副作用。
    """
    # 1. 记录人工关键词
    SearchKeywordRepository(session).save(
        task_id,
        keyword=keyword,
        source="manual",
        confidence=1.0,
        reason="operator_search_override",
        payload={},
    )

    # 2. 解析 scope → 目标 profiles
    from media_pilot.services.profile_registry import (
        get_profile_registry,
        register_builtin_profiles,
    )
    register_builtin_profiles()
    registry = get_profile_registry()

    # 读取持久化应用设置，复用 AppSettingsService 保持默认值与顺序一致
    from media_pilot.repository.database import create_session_factory
    from media_pilot.services.app_settings import AppSettingsService
    svc = AppSettingsService(create_session_factory(config))
    app_settings = svc.read_using_session(session)
    enabled_names = list(app_settings.enabled_metadata_profiles)
    if not enabled_names:
        enabled_names = ["tmdb_movie"]

    if scope == "all":
        target_profiles = [
            registry.get(name)
            for name in enabled_names
            if name in registry.list_names()
        ]
    else:
        if scope not in registry.list_names():
            return _error_result(task_id, keyword, scope, session)
        target_profiles = [registry.get(scope)]

    # 构建 provider_name → profile 顺序映射（用于排序）
    provider_order: dict[str, int] = {}
    for idx, prof in enumerate(target_profiles):
        provider_order[prof.provider_name] = idx

    # 3. 搜索每个 profile
    profile_statuses: list[ProfileSearchStatus] = []
    all_candidates: list[MetadataCandidate] = []
    any_success = False

    for profile in target_profiles:
        provider: MetadataProvider | None = None
        label = profile.label

        # 检查是否启用
        if profile.name not in set(enabled_names):
            profile_statuses.append(ProfileSearchStatus(
                profile=profile.name,
                label=label,
                provider=profile.provider_name,
                status="skipped",
                error_message="profile 未在应用设置中启用",
            ))
            continue

        # 检查 provider 是否可创建
        try:
            from media_pilot.orchestration.profile_search import (
                create_metadata_provider_by_name as _create_by_name,
            )
            provider = _create_by_name(
                config, profile.provider_name
            )
        except ValueError as exc:
            profile_statuses.append(ProfileSearchStatus(
                profile=profile.name,
                label=label,
                provider=profile.provider_name,
                status="skipped",
                error_message=str(exc),
            ))
            continue

        # 执行搜索
        try:
            candidates = quick_search(provider, keyword)
            profile_statuses.append(ProfileSearchStatus(
                profile=profile.name,
                label=label,
                provider=profile.provider_name,
                status="succeeded",
                candidate_count=len(candidates),
            ))
            all_candidates.extend(candidates)
            any_success = True

            # 记录 AdapterCall
            session.add(AdapterCall(
                task_id=task_id,
                adapter_name=getattr(provider, "provider_name", profile.provider_name),
                action="search_movie",
                request_summary={"keyword": keyword, "profile": profile.name},
                response_summary={"candidate_count": len(candidates)},
                status="succeeded",
            ))
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            profile_statuses.append(ProfileSearchStatus(
                profile=profile.name,
                label=label,
                provider=profile.provider_name,
                status="failed",
                error_message=error_message,
            ))
            # 记录失败 AdapterCall
            session.add(AdapterCall(
                task_id=task_id,
                adapter_name=profile.provider_name,
                action="search_movie",
                request_summary={"keyword": keyword, "profile": profile.name},
                response_summary={},
                status="failed",
                error_message=error_message,
            ))

    # 4. 处理结果
    summary = SearchSummary(
        keyword=keyword,
        scope=scope,
        searched_profiles=profile_statuses,
        total_candidates=0,
        kept_existing_candidates=False,
    )

    if not any_success:
        # 全部失败 → 保留旧候选；不创建决策，交由 UI 展示各 profile 状态。
        summary.kept_existing_candidates = True
        session.commit()
        return ManualResearchResult(
            candidates=list(_existing_candidates(session, task_id)),
            summary=summary,
        )

    # 5. 去重（同 provider 内）
    deduped = _deduplicate_candidates(all_candidates)

    # 6. 排序
    sorted_candidates = _sort_candidates(deduped, provider_order)

    # 7. 先删除旧候选，再持久化新候选
    for old in _existing_candidates(session, task_id):
        session.delete(old)
    session.flush()  # 确保删除生效后再 add

    persisted: list[MediaCandidate] = []
    for c in sorted_candidates:
        payload = provider_candidate_payload(c)
        mc = MediaCandidate(
            task_id=task_id,
            source=c.provider,
            media_type=c.media_type,
            title=c.title,
            original_title=c.original_title,
            year=c.year,
            external_id=c.provider_id,
            confidence=c.confidence,
            reason=c.match_reason,
            payload=payload,
        )
        session.add(mc)
        persisted.append(mc)

    summary.total_candidates = len(persisted)

    session.commit()

    return ManualResearchResult(
        candidates=persisted,
        summary=summary,
    )


def _error_result(
    task_id: str, keyword: str, scope: ResearchScope, session: Session,
) -> ManualResearchResult:
    """构造错误场景的结果。"""
    return ManualResearchResult(
        candidates=list(_existing_candidates(session, task_id)),
        summary=SearchSummary(
            keyword=keyword,
            scope=scope,
            searched_profiles=[],
            total_candidates=0,
            kept_existing_candidates=True,
        ),
    )


def _existing_candidates(
    session: Session, task_id: str
) -> list[MediaCandidate]:
    """获取任务当前的候选列表"""
    return list(session.scalars(
        select(MediaCandidate)
        .where(MediaCandidate.task_id == task_id)
        .order_by(MediaCandidate.created_at.asc())
    ))


def _deduplicate_candidates(
    candidates: list[MetadataCandidate],
) -> list[MetadataCandidate]:
    """同 provider 内按 provider_id 去重，保留置信度最高候选"""
    seen: dict[tuple[str, str], MetadataCandidate] = {}
    for c in candidates:
        key = (c.provider, c.provider_id)
        if key not in seen or (
            c.confidence is not None
            and (seen[key].confidence is None or c.confidence > seen[key].confidence)
        ):
            seen[key] = c
    return list(seen.values())


def _sort_candidates(
    candidates: list[MetadataCandidate],
    provider_order: dict[str, int] | None = None,
) -> list[MetadataCandidate]:
    """候选排序：置信度高优先，然后按配置档案顺序，再按主封面可用性

    provider_order 映射 provider_name → 配置档案中的位置序号。
    """
    order = provider_order or {}

    def _sort_key(c: MetadataCandidate) -> tuple:
        confidence = -(c.confidence or 0)
        profile_order = order.get(c.provider, 99)
        has_poster = 0 if c.poster_url else 1
        return (confidence, profile_order, has_poster)
    return sorted(candidates, key=_sort_key)
