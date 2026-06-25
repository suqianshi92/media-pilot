"""
资源发现领域类型与 Adapter 协议

首版不持久化搜索会话、资源候选或下载任务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# ── 搜索类型 ──

@dataclass(frozen=True, kw_only=True)
class ResourceSearchRequest:
    """Prowlarr 搜索请求"""
    query: str
    search_type: str  # "all" | "movie" | "adult"
    limit: int = 20


@dataclass(frozen=True, kw_only=True)
class ResourceCandidate:
    """规范化后的资源候选"""
    title: str
    indexer: str
    source: str
    download_url: str | None = None
    magnet_url: str | None = None
    size_bytes: int | None = None
    seeders: int = 0
    leechers: int = 0
    publish_date: str | None = None
    download_count: int = 0
    category: str = ""
    match_reason: str = ""
    relevance_score: float = 0.0
    relevance_level: str = "low"  # "high" | "medium" | "low"
    match_reasons: list[str] = field(default_factory=list)
    # 资源发布标签（从标题解析）
    release_tags: dict | None = None  # serialized ReleaseTags


@dataclass(frozen=True, kw_only=True)
class ResourceSearchResult:
    """资源搜索结果

    error_code 非空表示搜索本身失败（未配置/超时/HTTP 错误），
    此时 candidates 为空且 message 包含错误描述。
    error_code 为空表示搜索执行成功（结果可能为空）。
    """
    candidates: list[ResourceCandidate] = field(default_factory=list)
    query_used: str = ""
    search_type: str = "all"
    source: str = ""  # "prowlarr"
    message: str = ""
    error_code: str | None = None


# ── 下载提交 ──

@dataclass(frozen=True, kw_only=True)
class DownloadRequest:
    """下载提交请求 — 客户端不可指定保存路径"""
    download_url: str | None = None
    magnet_url: str | None = None
    title: str = ""
    source: str = ""
    indexer: str = ""


@dataclass(frozen=True, kw_only=True)
class DownloadSubmitResult:
    """下载提交结果 — 不持久化"""
    status: str  # "submitted" | "failed"
    title: str = ""
    source: str = ""
    message: str = ""
    info_hash: str | None = None


@dataclass(frozen=True, kw_only=True)
class ToolConnectionStatus:
    """外部工具连通性状态"""
    tool: str  # "prowlarr" | "qbittorrent"
    configured: bool
    reachable: bool
    authenticated: bool
    message: str = ""


# ── LLM 意图解析 ──

@dataclass(frozen=True, kw_only=True)
class ResourceIntent:
    """LLM 解析的结构化搜索意图"""
    query_text: str  # 原始用户输入
    search_type: str  # "movie" | "adult" | "all"
    title_candidates: list[str] = field(default_factory=list)
    resource_keywords: list[str] = field(default_factory=list)
    quality_hint: str = ""  # DEPRECATED: 保留以兼容旧数据，新代码使用 preferred_* 字段
    profile_hint: str = "unknown"  # "tmdb_movie" | "tmdb_show" | "tpdb_adult_movie" | "unknown"
    # 2.x: 语言感知的标题候选和成人标识
    preferred_title_candidates: list[str] = field(default_factory=list)
    adult_identifier_candidates: list[str] = field(default_factory=list)
    resource_search_keywords: list[str] = field(default_factory=list)
    reason: str = ""
    # 3.x: 结构化质量偏好（替代 quality_hint 字符串）
    preferred_resolutions: list[str] = field(default_factory=list)
    preferred_sources: list[str] = field(default_factory=list)
    preferred_video_codecs: list[str] = field(default_factory=list)
    preferred_hdr_tags: list[str] = field(default_factory=list)
    preferred_audio_tags: list[str] = field(default_factory=list)


# ── Adapter 协议 ──

class ResourceSearchAdapter(Protocol):
    """资源搜索 Adapter 协议 — 业务层仅依赖此协议"""

    def search(self, request: ResourceSearchRequest) -> ResourceSearchResult:
        ...

    def test_connection(self) -> ToolConnectionStatus:
        ...


class DownloadAdapter(Protocol):
    """下载 Adapter 协议 — 业务层仅依赖此协议"""

    def add_download(self, request: DownloadRequest) -> DownloadSubmitResult:
        ...

    def get_torrent_info(self, hashes: list[str]) -> list:  # noqa: F821 # Returns list[QBTorrentInfo]
        ...

    def test_connection(self) -> ToolConnectionStatus:
        ...


# ── qBittorrent 状态 DTO ──


@dataclass(frozen=True, kw_only=True)
class QBTorrentInfo:
    """qBittorrent torrent 状态（API v2 /torrents/info 字段子集）"""
    hash: str
    name: str
    save_path: str
    content_path: str | None = None
    progress: float = 0.0  # 0.0 - 1.0
    size_bytes: int = 0
    dlspeed: int = 0  # bytes/s
    upspeed: int = 0  # bytes/s
    num_seeds: int = 0
    num_leechs: int = 0
    num_complete: int = 0
    connections: int = 0
    state: str = ""  # e.g. "downloading", "uploading", "stalledUP", "checkingUP"


# ── 下载完成判定 ──

_QBT_COMPLETION_STATES: frozenset[str] = frozenset({
    "uploading",
    "stalledUP",
    "pausedUP",
    "queuedUP",
    "forcedUP",
})


def is_qb_torrent_completed(info: QBTorrentInfo) -> bool:
    """qBittorrent torrent 是否处于可入库的完成状态。

    要求 progress 100% 且状态属于完成类（uploading/stalledUP 等）。
    checkingUP 不算完成。
    """
    return info.progress >= 1.0 and info.state in _QBT_COMPLETION_STATES
