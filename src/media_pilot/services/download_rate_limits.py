"""下载器全局限速同步服务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from media_pilot.config.settings import AppConfig
from media_pilot.resource_discovery.qbittorrent_adapter import QBittorrentAdapter
from media_pilot.services.app_settings import AppSettings, AppSettingsService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadRateLimitSyncResult:
    attempted: bool
    succeeded: bool
    message: str = ""


def sync_download_rate_limits(
    *,
    config: AppConfig,
    settings_service: AppSettingsService,
    settings: AppSettings,
    force: bool = False,
) -> DownloadRateLimitSyncResult:
    """把应用配置中的全局限速同步到 qBittorrent。

    `force=False` 时，如果期望值已经成功同步过，则跳过，避免每次启动
    都重复打 qB API。不读取 qB 当前值，Media Pilot 保存的期望值是准。
    """
    desired_download = settings.download_rate_limit_bytes_per_second
    desired_upload = settings.upload_rate_limit_bytes_per_second

    if (
        not force
        and settings.synced_download_rate_limit_bytes_per_second == desired_download
        and settings.synced_upload_rate_limit_bytes_per_second == desired_upload
    ):
        return DownloadRateLimitSyncResult(attempted=False, succeeded=True)

    adapter = QBittorrentAdapter(config)
    if not adapter.set_global_rate_limits(
        download_rate_limit_bytes_per_second=desired_download,
        upload_rate_limit_bytes_per_second=desired_upload,
    ):
        return DownloadRateLimitSyncResult(
            attempted=True,
            succeeded=False,
            message="qBittorrent 全局限速同步失败，请检查下载器连接状态",
        )

    settings_service.mark_download_rate_limits_synced(
        download_rate_limit_bytes_per_second=desired_download,
        upload_rate_limit_bytes_per_second=desired_upload,
    )
    return DownloadRateLimitSyncResult(attempted=True, succeeded=True)


def sync_download_rate_limits_on_startup(
    *,
    config: AppConfig,
    session_factory: sessionmaker[Session],
) -> DownloadRateLimitSyncResult:
    """应用启动时补同步下载器全局限速；失败只记录 warning。"""
    if not config.qbittorrent_url or not config.qbittorrent_password:
        return DownloadRateLimitSyncResult(attempted=False, succeeded=True)

    settings_service = AppSettingsService(session_factory)
    settings = settings_service.read()
    if (
        settings.download_rate_limit_bytes_per_second == 0
        and settings.upload_rate_limit_bytes_per_second == 0
        and settings.synced_download_rate_limit_bytes_per_second is None
        and settings.synced_upload_rate_limit_bytes_per_second is None
    ):
        return DownloadRateLimitSyncResult(attempted=False, succeeded=True)

    result = sync_download_rate_limits(
        config=config,
        settings_service=settings_service,
        settings=settings,
    )
    if result.attempted and not result.succeeded:
        logger.warning(result.message)
    return result
