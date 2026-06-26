"""API v1 应用配置端点"""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.api.schemas import ApiEnvelope, ApiMessage
from media_pilot.api.settings_dtos import (
    AppSettingsDto,
    AppSettingsResponse,
    AppSettingsUpdateRequest,
    ConnectivityResponse,
    EnvConfigStatusDto,
    LibraryFormatOption,
    ProbeResultDto,
    ProfileOption,
)
from media_pilot.config import AppConfig
from media_pilot.services.app_settings import (
    AppSettings,
    AppSettingsService,
    SettingsValidationError,
)
from media_pilot.services.download_rate_limits import sync_download_rate_limits

router = APIRouter(prefix="/api/v1")

_PROFILE_LABELS: dict[str, str] = {
    "tmdb_movie": "TMDB 电影",
    "tmdb_show": "TMDB 剧集",
    "tpdb_adult_movie": "TPDB 成人影片",
}
_FORMAT_LABELS: dict[str, str] = {
    "jellyfin": "Jellyfin",
}


def _build_env_status(config: AppConfig) -> EnvConfigStatusDto:
    return EnvConfigStatusDto(
        tmdb_api_key="configured" if config.tmdb_api_key else "not_configured",
        llm_api_key="configured" if config.llm_api_key else "not_configured",
        llm_base_url="configured" if config.llm_base_url else "not_configured",
        llm_model="configured" if config.llm_model else "not_configured",
        tpdb_api_key="configured" if os.getenv("MEDIA_PILOT_TPDB_API_KEY") else "not_configured",
        trash_dir="configured" if config.trash_dir else "not_configured",
    )


def _adult_movies_dir_available(config: AppConfig) -> bool:
    """成人影片库根是否处于"可用"状态.

    与 ``validate_startup_config`` 对 ``config.adult_movies_dir`` 的检查规则
    保持一致 (route-adult-movie-library-root 收口):
    1. ``adult_movies_dir is not None`` (已显式配置)
    2. 路径在磁盘上存在
    3. 路径是一个目录 (而不是普通文件)

    三者同时满足才视为可用, 缺一即视为不可用. 设置页应在
    _build_profile_options 阶段把 supported 报为 False, 避免用户在
    UI 上启用后, 后端 validate_startup_config 才报错.
    """
    path = config.adult_movies_dir
    if path is None:
        return False
    if not path.exists():
        return False
    if not path.is_dir():
        return False
    return True


def _build_profile_options(settings: AppSettings, config: AppConfig) -> list[ProfileOption]:
    enabled = set(settings.enabled_metadata_profiles)
    # route-adult-movie-library-root 收口: TPDB 成人影片档案需要同时具备
    # TPDB API Key + 显式配置的成人影片库根 + 库根本身在磁盘上是一个
    # 可访问的目录. 缺一即视为不支持, 否则前端 enable=true 后, 后端
    # validate_startup_config 会因 adult_movies_dir does not exist /
    # is not a directory 失败, 出现"看似可启用但跑不起来"状态.
    tpdb_supported = (
        config.tpdb_api_key is not None
        and _adult_movies_dir_available(config)
    )
    return [
        ProfileOption(
            value="tmdb_movie",
            label=_PROFILE_LABELS.get("tmdb_movie", "tmdb_movie"),
            supported=True,
            enabled="tmdb_movie" in enabled,
        ),
        ProfileOption(
            value="tmdb_show",
            label=_PROFILE_LABELS.get("tmdb_show", "tmdb_show"),
            # TMDB 剧集档案与电影档案共用同一个 TMDB API Key 门禁:
            # 任意 TMDB 档案启用时必须配置 tmdb_api_key. 这里直接
            # 跟随 tpdb_supported 的"配置 + 库根存在"模式不必要 —
            # TMDB 剧集不依赖额外库根, 只复用 TMDB 凭据, 因此
            # supported 与 tmdb_movie 保持一致.
            supported=True,
            enabled="tmdb_show" in enabled,
        ),
        ProfileOption(
            value="tpdb_adult_movie",
            label=_PROFILE_LABELS.get("tpdb_adult_movie", "tpdb_adult_movie"),
            supported=tpdb_supported,
            enabled="tpdb_adult_movie" in enabled and tpdb_supported,
        ),
    ]


def _build_format_options(settings: AppSettings) -> list[LibraryFormatOption]:
    enabled = set(settings.enabled_library_formats)
    return [
        LibraryFormatOption(
            value="jellyfin",
            label=_FORMAT_LABELS.get("jellyfin", "jellyfin"),
            supported=True,
            enabled="jellyfin" in enabled,
        ),
    ]


def _db_locked_response() -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(
                level="error",
                code="db_locked",
                text="数据库暂时被占用，请稍后重试",
            )],
            meta={"retryable": True},
        ).model_dump(),
    )


@router.get("/settings")
def get_settings(request: Request) -> ApiEnvelope[AppSettingsResponse]:
    """返回应用配置、环境配置状态和可用选项"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )
    config: AppConfig | None = getattr(request.app.state, "config", None)

    if session_factory is None or config is None:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(
                level="error", code="not_configured", text="未配置数据库或服务"
            )],
            meta={},
        )

    service = AppSettingsService(session_factory)
    settings = service.read()

    return ApiEnvelope(
        status="success",
        data=AppSettingsResponse(
            app_settings=AppSettingsDto(
                enabled_metadata_profiles=settings.enabled_metadata_profiles,
                enabled_library_formats=settings.enabled_library_formats,
                suspicious_file_threshold_bytes=settings.suspicious_file_threshold_bytes,
                metadata_auto_confirm_confidence=settings.metadata_auto_confirm_confidence,
                metadata_auto_confirm_margin=settings.metadata_auto_confirm_margin,
                preferred_metadata_language=settings.preferred_metadata_language,
                source_cleanup_policy=settings.source_cleanup_policy,
                download_rate_limit_bytes_per_second=(
                    settings.download_rate_limit_bytes_per_second
                ),
                upload_rate_limit_bytes_per_second=(
                    settings.upload_rate_limit_bytes_per_second
                ),
            ),
            env_status=_build_env_status(config),
            available_profiles=_build_profile_options(settings, config),
            available_library_formats=_build_format_options(settings),
        ),
        messages=[],
        meta={},
    )


@router.put("/settings")
def update_settings(
    body: AppSettingsUpdateRequest,
    request: Request,
) -> ApiEnvelope[AppSettingsDto]:
    """更新应用配置，返回校验错误时 status=error"""
    session_factory: sessionmaker[Session] | None = getattr(
        request.app.state, "session_factory", None
    )

    if session_factory is None:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(
                level="error", code="not_configured", text="未配置数据库"
            )],
            meta={},
        )

    service = AppSettingsService(session_factory)
    current = service.read()

    # 合并请求体到当前配置
    updated = AppSettings(
        enabled_metadata_profiles=(
            body.enabled_metadata_profiles
            if body.enabled_metadata_profiles is not None
            else current.enabled_metadata_profiles
        ),
        enabled_library_formats=(
            body.enabled_library_formats
            if body.enabled_library_formats is not None
            else current.enabled_library_formats
        ),
        suspicious_file_threshold_bytes=(
            body.suspicious_file_threshold_bytes
            if body.suspicious_file_threshold_bytes is not None
            else current.suspicious_file_threshold_bytes
        ),
        metadata_auto_confirm_confidence=(
            body.metadata_auto_confirm_confidence
            if body.metadata_auto_confirm_confidence is not None
            else current.metadata_auto_confirm_confidence
        ),
        metadata_auto_confirm_margin=(
            body.metadata_auto_confirm_margin
            if body.metadata_auto_confirm_margin is not None
            else current.metadata_auto_confirm_margin
        ),
        preferred_metadata_language=(
            body.preferred_metadata_language
            if body.preferred_metadata_language is not None
            else current.preferred_metadata_language
        ),
        source_cleanup_policy=(
            body.source_cleanup_policy
            if body.source_cleanup_policy is not None
            else current.source_cleanup_policy
        ),
        download_rate_limit_bytes_per_second=(
            body.download_rate_limit_bytes_per_second
            if body.download_rate_limit_bytes_per_second is not None
            else current.download_rate_limit_bytes_per_second
        ),
        upload_rate_limit_bytes_per_second=(
            body.upload_rate_limit_bytes_per_second
            if body.upload_rate_limit_bytes_per_second is not None
            else current.upload_rate_limit_bytes_per_second
        ),
        synced_download_rate_limit_bytes_per_second=(
            current.synced_download_rate_limit_bytes_per_second
        ),
        synced_upload_rate_limit_bytes_per_second=(
            current.synced_upload_rate_limit_bytes_per_second
        ),
    )

    # 校验 TPDB：未配置 API Key 时不可启用
    config: AppConfig | None = getattr(request.app.state, "config", None)
    if (
        config is not None
        and "tpdb_adult_movie" in updated.enabled_metadata_profiles
        and not config.tpdb_api_key
    ):
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(
                level="error",
                code="validation_error",
                text="未配置 TPDB API Key，不能启用 TPDB 成人影片档案",
            )],
            meta={},
        )
    # route-adult-movie-library-root 收口: 即便 TPDB Key 已配, 成人影片
    # 库根缺失 / 不存在 / 不是目录也不能启用 tpdb_adult_movie 档案. 与
    # validate_startup_config 的成人影片库根依赖保持一致 — 避免前端
    # enable 成功、worker 启动失败的"看似可启用但实际跑不起来"状态.
    if (
        config is not None
        and "tpdb_adult_movie" in updated.enabled_metadata_profiles
        and config.tpdb_api_key
        and not _adult_movies_dir_available(config)
    ):
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(
                level="error",
                code="validation_error",
                text=(
                    "成人影片库根 (MEDIA_PILOT_ADULT_MOVIES_DIR) 不可用, "
                    "不能启用 TPDB 成人影片档案"
                ),
            )],
            meta={},
        )

    try:
        service.save(updated)
    except SettingsValidationError as exc:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(
                level="error", code="validation_error", text=str(exc)
            )],
            meta={},
        )
    except OperationalError:
        return _db_locked_response()

    saved = service.read()
    messages = [ApiMessage(level="info", code="settings_updated", text="配置已保存")]
    if config is not None:
        sync_result = sync_download_rate_limits(
            config=config,
            settings_service=service,
            settings=saved,
        )
        if sync_result.attempted and sync_result.succeeded:
            saved = service.read()
        elif sync_result.attempted and not sync_result.succeeded:
            messages.append(ApiMessage(
                level="warning",
                code="qbittorrent_rate_limit_sync_failed",
                text=sync_result.message,
            ))

    return ApiEnvelope(
        status="success",
        data=AppSettingsDto(
            enabled_metadata_profiles=saved.enabled_metadata_profiles,
            enabled_library_formats=saved.enabled_library_formats,
            suspicious_file_threshold_bytes=saved.suspicious_file_threshold_bytes,
            metadata_auto_confirm_confidence=saved.metadata_auto_confirm_confidence,
            metadata_auto_confirm_margin=saved.metadata_auto_confirm_margin,
            preferred_metadata_language=saved.preferred_metadata_language,
            source_cleanup_policy=saved.source_cleanup_policy,
            download_rate_limit_bytes_per_second=saved.download_rate_limit_bytes_per_second,
            upload_rate_limit_bytes_per_second=saved.upload_rate_limit_bytes_per_second,
        ),
        messages=messages,
        meta={},
    )


# 连通性探测缓存
_probe_cache: dict | None = None
_probe_cache_time: float = 0.0
_PROBE_CACHE_TTL = 15.0  # 15 秒缓存


@router.get("/settings/connectivity")
def get_connectivity(request: Request) -> ApiEnvelope[ConnectivityResponse]:
    """触发连通性探测并返回结果 — 15 秒内重复请求返回缓存"""
    global _probe_cache, _probe_cache_time

    config: AppConfig | None = getattr(request.app.state, "config", None)
    if config is None:
        return ApiEnvelope(
            status="error",
            data={},
            messages=[ApiMessage(
                level="error", code="not_configured", text="未配置服务"
            )],
            meta={},
        )

    now = time.monotonic()
    if _probe_cache is not None and (now - _probe_cache_time) < _PROBE_CACHE_TTL:
        return ApiEnvelope(
            status="success",
            data=ConnectivityResponse(probes=_probe_cache),
            messages=[ApiMessage(level="info", code="cache_hit", text="返回缓存结果")],
            meta={"cached": True},
        )

    from media_pilot.services.connectivity import run_all_probes

    probes = run_all_probes(config)
    _probe_cache = probes
    _probe_cache_time = now

    return ApiEnvelope(
        status="success",
        data=ConnectivityResponse(
            probes=[ProbeResultDto(**p) for p in probes]
        ),
        messages=[],
        meta={"cached": False},
    )
