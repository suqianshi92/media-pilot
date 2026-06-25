"""API v1 应用配置 DTO — 请求/响应体"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---- 应用配置（可编辑） ----

class AppSettingsDto(BaseModel):
    """应用配置 — 前端可编辑的非敏感字段"""
    enabled_metadata_profiles: list[str] = Field(
        default_factory=lambda: ["tmdb_movie", "tmdb_show"]
    )
    enabled_library_formats: list[str] = Field(default_factory=lambda: ["jellyfin"])
    suspicious_file_threshold_bytes: int = 314572800
    metadata_auto_confirm_confidence: float = 0.9
    metadata_auto_confirm_margin: float = 0.08
    preferred_metadata_language: str = "zh"
    source_cleanup_policy: str = "keep"
    download_rate_limit_bytes_per_second: int = 0
    upload_rate_limit_bytes_per_second: int = 0


# ---- 环境配置状态（只读） ----

class EnvConfigStatusDto(BaseModel):
    """环境配置状态 — 只展示已配置/未配置，不返回密钥内容"""
    tmdb_api_key: Literal["configured", "not_configured"] = "not_configured"
    llm_api_key: Literal["configured", "not_configured"] = "not_configured"
    llm_base_url: Literal["configured", "not_configured"] = "not_configured"
    llm_model: Literal["configured", "not_configured"] = "not_configured"
    tpdb_api_key: Literal["configured", "not_configured", "unsupported"] = "unsupported"
    trash_dir: Literal["configured", "not_configured"] = "not_configured"


# ---- 可用选项（前端渲染用） ----

class ProfileOption(BaseModel):
    """单个元数据档案的可用性"""
    value: str
    label: str
    supported: bool
    enabled: bool


class LibraryFormatOption(BaseModel):
    """单个媒体库格式的可用性"""
    value: str
    label: str
    supported: bool
    enabled: bool


class AppSettingsResponse(BaseModel):
    """GET /api/v1/settings 响应体"""
    app_settings: AppSettingsDto
    env_status: EnvConfigStatusDto
    available_profiles: list[ProfileOption]
    available_library_formats: list[LibraryFormatOption]


class AppSettingsUpdateRequest(BaseModel):
    """PUT /api/v1/settings 请求体 — 只允许更新非敏感字段"""
    enabled_metadata_profiles: list[str] | None = None
    enabled_library_formats: list[str] | None = None
    suspicious_file_threshold_bytes: int | None = None
    metadata_auto_confirm_confidence: float | None = None
    metadata_auto_confirm_margin: float | None = None
    preferred_metadata_language: str | None = None
    source_cleanup_policy: str | None = None
    download_rate_limit_bytes_per_second: int | None = None
    upload_rate_limit_bytes_per_second: int | None = None


# ---- 连通性探测 ----

ProbeStatus = Literal["ok", "not_configured", "failed", "unsupported", "probing"]


class ProbeResultDto(BaseModel):
    """单个 provider 的连通性探测结果"""
    provider: str
    status: ProbeStatus
    message: str = ""
    checked_at: str | None = None
    latency_ms: int | None = None


class ConnectivityResponse(BaseModel):
    """GET /api/v1/settings/connectivity 响应体"""
    probes: list[ProbeResultDto]
