"""
资源发现服务 — 编排 LLM 意图解析 + Prowlarr 搜索 + qBittorrent 下载

首版不持久化搜索会话、资源候选或下载任务。
"""

from __future__ import annotations

import logging

from media_pilot.accounts.task_classification import is_adult_metadata_selection
from media_pilot.config.settings import AppConfig
from media_pilot.resource_discovery.intent_parser import IntentParseError, ResourceIntentParser
from media_pilot.resource_discovery.prowlarr_adapter import ProwlarrAdapter
from media_pilot.resource_discovery.qbittorrent_adapter import QBittorrentAdapter
from media_pilot.resource_discovery.types import (
    DownloadRequest,
    ResourceSearchRequest,
    ToolConnectionStatus,
)
from media_pilot.services.candidate_cache import (
    _CANDIDATE_CACHE,  # noqa: F401 — 向后兼容旧导入
    _CANDIDATE_TTL_SECONDS,  # noqa: F401 — 向后兼容旧导入
    lookup_candidate,
    store_candidate,
)

logger = logging.getLogger(__name__)

# 向后兼容别名
_store_candidate = store_candidate  # noqa: F811
_lookup_candidate = lookup_candidate  # noqa: F811

def search_resources(
    user_input: str, config: AppConfig, *,
    search_type_override: str | None = None,
    skip_intent: bool = False,
    preferred_language: str = "zh",
    enabled_profiles: list[str] | None = None,
) -> dict:
    """自然语言搜索资源：LLM 意图解析 → Prowlarr 搜索 → 规范化结果。

    Args:
        search_type_override: 如果传入 "movie"/"adult"/"all"，则覆盖 LLM 解析出的
            search_type，直接以指定类型搜索 Prowlarr。
        skip_intent: 如果为 True，完全绕过 LLM 意图解析，直接用原始输入作为关键词搜索。

    Returns:
        dict: {"status": "success"|"error", "data": {...}, "message": str}
    """

    # 1. LLM 意图解析（手动模式时跳过）
    if skip_intent:
        intent = _make_raw_intent(user_input)
    else:
        if not config.llm_api_key or not config.llm_base_url or not config.llm_model:
            return {
                "status": "error",
                "data": {},
                "message": (
                    "LLM 未配置，无法进行资源搜索。"
                    "请在设置中配置 LLM API Key、Base URL 和 Model。"
                ),
            }

        try:
            parser = ResourceIntentParser(
                api_key=config.llm_api_key,
                base_url=config.llm_base_url,
                model=config.llm_model,
                timeout_seconds=config.llm_timeout_seconds,
            )
            intent = parser.parse(
                user_input,
                preferred_language=preferred_language,
                enabled_profiles=enabled_profiles,
            )
        except IntentParseError as exc:
            return {
                "status": "error",
                "data": {},
                "message": exc.message,
            }

    # 2. 关键词回退搜索 — 优先 resource_search_keywords，回退 resource_keywords，再回退用户输入
    keywords = (
        intent.resource_search_keywords
        or intent.resource_keywords
        or [user_input]
    )
    adapter = ProwlarrAdapter(config)
    search_type = search_type_override or intent.search_type

    search_result = None
    last_result = None
    for kw in keywords:
        result = adapter.search(
            ResourceSearchRequest(query=kw, search_type=search_type, limit=50)
        )
        last_result = result
        if result.error_code:
            # 硬错误（未配置等）直接返回
            if result.error_code in ("not_configured",):
                search_result = result
                break
            continue  # timeout/http_error 尝试下一个关键词
        if result.candidates:
            search_result = result
            break
    else:
        # 所有关键词都失败或无结果
        search_result = last_result

    if search_result is None:
        return {
            "status": "error",
            "data": {},
            "message": "搜索失败",
        }

    # 4. Prowlarr 错误检查
    if search_result.error_code:
        return {
            "status": "error",
            "data": {},
            "message": search_result.message,
        }

    # 5. 相关性评分与排序
    from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

    ranker = ResourceCandidateRanker(intent)
    ranked = ranker.rank(search_result.candidates)

    # 6. 组装响应 — 移除下载凭证，不返前端
    intent_dict_for_cache = {
        "query_text": intent.query_text,
        "search_type": intent.search_type,
        "title_candidates": intent.title_candidates,
        "resource_keywords": intent.resource_keywords,
        "profile_hint": intent.profile_hint,
        "preferred_title_candidates": intent.preferred_title_candidates,
        "adult_identifier_candidates": intent.adult_identifier_candidates,
        "resource_search_keywords": intent.resource_search_keywords,
        "reason": intent.reason,
        "preferred_resolutions": intent.preferred_resolutions,
        "preferred_sources": intent.preferred_sources,
        "preferred_video_codecs": intent.preferred_video_codecs,
        "preferred_hdr_tags": intent.preferred_hdr_tags,
        "preferred_audio_tags": intent.preferred_audio_tags,
    }
    intent_context = {
        "user_input": user_input,
        "search_type": search_type,
        "intent": intent_dict_for_cache,
    }
    candidates = [
        _candidate_for_response(c, intent_context=intent_context) for c in ranked
    ]

    return {
        "status": "success",
        "data": {
            "candidates": candidates,
            "query_used": search_result.query_used,
            "search_type": search_result.search_type,
            "source": search_result.source,
            "message": search_result.message,
            "intent": {
                "query_text": intent.query_text,
                "search_type": intent.search_type,
                "title_candidates": intent.title_candidates,
                "resource_keywords": intent.resource_keywords,
                "profile_hint": intent.profile_hint,
                "preferred_title_candidates": intent.preferred_title_candidates,
                "adult_identifier_candidates": intent.adult_identifier_candidates,
                "resource_search_keywords": intent.resource_search_keywords,
                "reason": intent.reason,
                "preferred_resolutions": intent.preferred_resolutions,
                "preferred_sources": intent.preferred_sources,
                "preferred_video_codecs": intent.preferred_video_codecs,
                "preferred_hdr_tags": intent.preferred_hdr_tags,
                "preferred_audio_tags": intent.preferred_audio_tags,
            },
        },
        "message": search_result.message,
    }


def submit_download(
    config: AppConfig,
    *,
    candidate_token: str,
    title: str = "",
    source: str = "",
    indexer: str = "",
    session_factory=None,
    preselected_profile: str | None = None,
    preselected_provider: str | None = None,
    preselected_external_id: str | None = None,
    owner_user_id: str | None = None,
) -> dict:
    """提交下载到 qBittorrent，成功后创建持久化下载任务。

    session_factory 可选：传入 sqlalchemy sessionmaker 时，下载成功后会
    创建 DownloadTask 并返回 download_task_id。不传入时仅提交下载。
    """
    from media_pilot.repository.repositories import DownloadTaskCreate, DownloadTaskRepository

    cached, _ctx = lookup_candidate(candidate_token)
    if cached is None:
        return {
            "status": "error",
            "data": {},
            "message": "候选已过期，请重新搜索",
        }

    download_url = cached.download_url
    magnet_url = cached.magnet_url
    candidate_title = cached.title
    candidate_source = cached.source
    candidate_indexer = cached.indexer
    cached_search_type = _ctx.get("search_type") or _ctx.get("intent", {}).get(
        "search_type"
    )
    is_adult = cached_search_type == "adult" or is_adult_metadata_selection(
        profile=preselected_profile,
        provider=preselected_provider,
    )

    url_to_add = download_url or magnet_url
    if not url_to_add:
        return {
            "status": "error",
            "data": {},
            "message": "无可下载链接（magnet 或 URL）",
        }

    if not config.qbittorrent_url:
        return {
            "status": "error",
            "data": {},
            "message": "qBittorrent 未配置：缺少 URL",
        }

    adapter = QBittorrentAdapter(config)

    # 4.2: 先创建 DownloadTask 获取稳定 ID，再提交 qB 并携带下载关联标签
    download_task_id = None
    tag = None
    if session_factory is not None:
        with session_factory() as session:
            repo = DownloadTaskRepository(session)
            task = repo.create(
                DownloadTaskCreate(
                    owner_user_id=owner_user_id,
                    is_adult=is_adult,
                    title=candidate_title,
                    source=candidate_source,
                    save_path=config.qbittorrent_save_path,
                    indexer=candidate_indexer,
                    status="submitting",
                )
            )
            session.commit()
            download_task_id = task.id
            tag = f"media-pilot:{download_task_id}"

    result = adapter.add_download(
        DownloadRequest(
            download_url=download_url,
            magnet_url=magnet_url,
            title=candidate_title,
            source=candidate_source,
            indexer=candidate_indexer,
        ),
        tag=tag,
    )

    if result.status == "submitted":
        # 更新已创建的下载任务：记录 hash + 预选 + 标记为 submitted
        if download_task_id and session_factory is not None:
            with session_factory() as session:
                repo = DownloadTaskRepository(session)
                task = repo.get(download_task_id)
                if task is not None:
                    # 4.3: 下载成功后写入元数据预选
                    if preselected_profile:
                        task.preselected_metadata_profile = preselected_profile
                        task.preselected_metadata_provider = preselected_provider
                        task.preselected_metadata_external_id = preselected_external_id
                    repo.update_sync_status(
                        task,
                        qb_hash=result.info_hash,
                        status="submitted",
                    )
                    session.commit()

        data: dict = {"info_hash": result.info_hash}
        if download_task_id:
            data["download_task_id"] = download_task_id
        return {
            "status": "success",
            "data": data,
            "message": result.message,
        }
    else:
        # 提交失败 → 标记预创建的下载任务为 failed
        if download_task_id and session_factory is not None:
            with session_factory() as session:
                repo = DownloadTaskRepository(session)
                task = repo.get(download_task_id)
                if task is not None:
                    repo.update_sync_status(
                        task,
                        status="failed",
                        error_message=result.message,
                    )
                    session.commit()
        return {
            "status": "error",
            "data": {},
            "message": result.message,
        }


# ── 连通性探测 ──


def probe_prowlarr(config: AppConfig) -> dict:
    """探测 Prowlarr 连通性"""
    from datetime import UTC, datetime

    adapter = ProwlarrAdapter(config)
    status = adapter.test_connection()

    return {
        "provider": "prowlarr",
        "status": _tool_status_to_probe(status),
        "message": status.message,
        "checked_at": datetime.now(UTC).isoformat(),
        "latency_ms": None,
    }


def probe_qbittorrent(config: AppConfig) -> dict:
    """探测 qBittorrent 连通性"""
    from datetime import UTC, datetime

    adapter = QBittorrentAdapter(config)
    status = adapter.test_connection()

    return {
        "provider": "qbittorrent",
        "status": _tool_status_to_probe(status),
        "message": status.message,
        "checked_at": datetime.now(UTC).isoformat(),
        "latency_ms": None,
    }


def _make_raw_intent(user_input: str):
    """构造一个跳过 LLM 的原始意图，直接使用用户输入作为搜索关键词。"""
    from media_pilot.resource_discovery.types import ResourceIntent

    return ResourceIntent(
        query_text=user_input,
        search_type="all",
        title_candidates=[user_input],
        resource_keywords=[user_input],
        resource_search_keywords=[user_input],
        profile_hint="unknown",
        preferred_title_candidates=[],
        adult_identifier_candidates=[],
        reason="手动模式：绕过 LLM 意图解析，直接使用原始关键词搜索",
        preferred_resolutions=[],
        preferred_sources=[],
        preferred_video_codecs=[],
        preferred_hdr_tags=[],
        preferred_audio_tags=[],
    )


def _candidate_for_response(c, intent_context: dict | None = None) -> dict:
    """构建面向前端的候选字典，排除下载凭证字段"""

    from media_pilot.resource_discovery.release_tags import (
        ReleaseTags,
        parse_release_tags,
    )

    token = store_candidate(c, intent_context=intent_context)

    # 计算 display_tags：从 release_tags 或实时解析标题
    display_tags: list[str] = []
    if c.release_tags and isinstance(c.release_tags, dict):
        rt = ReleaseTags(
            resolutions=c.release_tags.get("resolutions", []),
            sources=c.release_tags.get("sources", []),
            codecs=c.release_tags.get("codecs", []),
            hdr_tags=c.release_tags.get("hdr_tags", []),
            audio_tags=c.release_tags.get("audio_tags", []),
        )
        display_tags = rt.display_tags(max_tags=5)
    else:
        parsed = parse_release_tags(c.title)
        display_tags = parsed.display_tags(max_tags=5)

    return {
        "candidate_token": token,
        "title": c.title,
        "indexer": c.indexer,
        "source": c.source,
        "size_bytes": c.size_bytes,
        "seeders": c.seeders,
        "leechers": c.leechers,
        "publish_date": c.publish_date,
        "download_count": c.download_count,
        "category": c.category,
        "match_reason": c.match_reason,
        "downloadable": bool(c.download_url or c.magnet_url),
        "relevance_score": c.relevance_score,
        "relevance_level": c.relevance_level,
        "match_reasons": c.match_reasons,
        "release_tags": c.release_tags,
        "display_tags": display_tags,
    }


def _tool_status_to_probe(status: ToolConnectionStatus) -> str:
    if not status.configured:
        return "not_configured"
    if not status.reachable:
        return "failed"
    if not status.authenticated:
        return "failed"
    return "ok"
