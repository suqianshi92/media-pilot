"""
Prowlarr ResourceSearchAdapter — HTTP 客户端封装

使用 httpx 调用 Prowlarr API，映射搜索类型为 categories，
规范化返回结果为 ResourceCandidate 并排序。
"""

from __future__ import annotations

import logging

import httpx

from media_pilot.config.settings import AppConfig
from media_pilot.resource_discovery.types import (
    ResourceCandidate,
    ResourceSearchRequest,
    ResourceSearchResult,
    ToolConnectionStatus,
)

logger = logging.getLogger(__name__)

# 搜索类型 → Prowlarr category IDs
# https://prowlarr.com/docs/api/
_CATEGORY_MAP: dict[str, list[int]] = {
    "movie": [2000],
    "show": [5000],
    "adult": [6000],
    "all": [2000, 5000, 6000],
    "standard": [2000, 5000],
}


def _sanitize_log_url(url: str) -> str:
    """移除 URL 中的敏感 token，仅保留 scheme+host 用于日志"""
    from urllib.parse import urlparse, urlunparse

    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, "/***", "", "", ""))


class ProwlarrAdapter:
    """Prowlarr 资源搜索 Adapter — 实现 ResourceSearchAdapter 协议"""

    def __init__(self, config: AppConfig) -> None:
        self._url = config.prowlarr_url.rstrip("/") if config.prowlarr_url else ""
        self._api_key = config.prowlarr_api_key
        self._timeout = httpx.Timeout(config.prowlarr_timeout_seconds)

    # ── ResourceSearchAdapter 协议方法 ──

    def search(self, request: ResourceSearchRequest) -> ResourceSearchResult:
        """执行 Prowlarr 搜索并返回规范化结果"""
        if not self._url or not self._api_key:
            return ResourceSearchResult(
            source="prowlarr",
            query_used=request.query,
            search_type=request.search_type,
            message="Prowlarr 未配置：缺少 URL 或 API Key",
            error_code="not_configured",
        )

        categories = _CATEGORY_MAP.get(request.search_type, [2000])

        params: list[tuple[str, str]] = [
            ("query", request.query),
            ("type", "search"),
        ]
        for cat in categories:
            params.append(("categories", str(cat)))
        params.append(("limit", str(request.limit)))

        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(
                    f"{self._url}/api/v1/search",
                    params=params,
                    headers={"X-Api-Key": self._api_key},
                )
                resp.raise_for_status()
                raw = resp.json()
        except httpx.TimeoutException:
            logger.warning("Prowlarr 搜索超时: query=%s", request.query)
            return ResourceSearchResult(
                source="prowlarr",
                query_used=request.query,
                search_type=request.search_type,
                message=f"Prowlarr 搜索超时（{self._timeout.read}s）",
                error_code="timeout",
            )
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Prowlarr 搜索 HTTP %d: %s",
                exc.response.status_code,
                _sanitize_log_url(str(exc.request.url)),
            )
            return ResourceSearchResult(
                source="prowlarr",
                query_used=request.query,
                search_type=request.search_type,
                message=f"Prowlarr 搜索失败（HTTP {exc.response.status_code}）",
                error_code="http_error",
            )
        except Exception as exc:
            logger.warning(
                "Prowlarr 搜索异常: %s — %s",
                _sanitize_log_url(self._url),
                exc,
            )
            return ResourceSearchResult(
                source="prowlarr",
                query_used=request.query,
                search_type=request.search_type,
                message=f"Prowlarr 搜索失败：{exc}",
                error_code="unknown_error",
            )

        candidates = self._normalize(raw)

        if not candidates:
            return ResourceSearchResult(
                source="prowlarr",
                query_used=request.query,
                search_type=request.search_type,
                message=f'未找到与 "{request.query}" 相关的资源',
            )

        return ResourceSearchResult(
            candidates=candidates,
            source="prowlarr",
            query_used=request.query,
            search_type=request.search_type,
            message=f"找到 {len(candidates)} 个候选",
        )

    def test_connection(self) -> ToolConnectionStatus:
        """探测 Prowlarr 连通性"""
        if not self._url:
            return ToolConnectionStatus(
                tool="prowlarr",
                configured=False,
                reachable=False,
                authenticated=False,
                message="Prowlarr 未配置 URL",
            )
        if not self._api_key:
            return ToolConnectionStatus(
                tool="prowlarr",
                configured=False,
                reachable=False,
                authenticated=False,
                message="Prowlarr 未配置 API Key",
            )

        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(
                    f"{self._url}/api/v1/system/status",
                    headers={"X-Api-Key": self._api_key},
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                return ToolConnectionStatus(
                    tool="prowlarr",
                    configured=True,
                    reachable=True,
                    authenticated=False,
                    message="Prowlarr 认证失败（HTTP 401）— 请检查 API Key",
                )
            return ToolConnectionStatus(
                tool="prowlarr",
                configured=True,
                reachable=False,
                authenticated=False,
                message=f"Prowlarr 连接失败（HTTP {exc.response.status_code}）",
            )
        except httpx.TimeoutException:
            return ToolConnectionStatus(
                tool="prowlarr",
                configured=True,
                reachable=False,
                authenticated=False,
                message="Prowlarr 连接超时",
            )
        except Exception as exc:
            return ToolConnectionStatus(
                tool="prowlarr",
                configured=True,
                reachable=False,
                authenticated=False,
                message=f"Prowlarr 连接失败：{exc}",
            )

        return ToolConnectionStatus(
            tool="prowlarr",
            configured=True,
            reachable=True,
            authenticated=True,
            message="连接正常",
        )

    # ── 内部方法 ──

    @staticmethod
    def _normalize(items: list[dict]) -> list[ResourceCandidate]:
        """将 Prowlarr 返回项规范化为 ResourceCandidate"""
        from dataclasses import asdict

        from media_pilot.resource_discovery.release_tags import parse_release_tags

        result: list[ResourceCandidate] = []
        for item in items:
            title = item.get("title", "")
            tags = parse_release_tags(title)
            raw_categories = item.get("categories") or []
            category_ids = [
                str(category.get("id", ""))
                if isinstance(category, dict)
                else str(category)
                for category in raw_categories
            ]
            result.append(
                ResourceCandidate(
                    title=title,
                    indexer=item.get("indexer", "unknown"),
                    source="prowlarr",
                    download_url=item.get("downloadUrl"),
                    magnet_url=item.get("magnetUrl"),
                    size_bytes=item.get("size"),
                    seeders=item.get("seeders", 0),
                    leechers=item.get("leechers", 0),
                    publish_date=item.get("publishDate"),
                    download_count=item.get("grabs", 0) or item.get("downloadCount", 0),
                    category=",".join(category_ids),
                    release_tags=asdict(tags),
                )
            )
        return result
