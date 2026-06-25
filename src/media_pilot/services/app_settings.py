"""应用配置持久化服务 — ORM 单例记录 + 默认值合并"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.repository.models import AppSetting


_DEFAULT_METADATA_PROFILES = ("tmdb_movie", "tmdb_show", "tpdb_adult_movie")
_DEFAULT_LIBRARY_FORMATS = ("jellyfin",)
_DEFAULT_THRESHOLD_BYTES = 300 * 1024 * 1024  # 300MB
MAX_RATE_LIMIT_BYTES_PER_SECOND = 1024 * 1024 * 1024  # 1GiB/s

# Agent 源文件清理策略枚举 — 首版仅 keep / ask / trash, 不支持自动 delete
SOURCE_CLEANUP_POLICY_KEEP = "keep"
SOURCE_CLEANUP_POLICY_ASK = "ask"
SOURCE_CLEANUP_POLICY_TRASH = "trash"
SUPPORTED_SOURCE_CLEANUP_POLICIES: frozenset[str] = frozenset({
    SOURCE_CLEANUP_POLICY_KEEP,
    SOURCE_CLEANUP_POLICY_ASK,
    SOURCE_CLEANUP_POLICY_TRASH,
})


@dataclass
class AppSettings:
    """应用配置读/写 DTO"""

    enabled_metadata_profiles: list[str] = field(
        default_factory=lambda: list(_DEFAULT_METADATA_PROFILES)
    )
    enabled_library_formats: list[str] = field(
        default_factory=lambda: list(_DEFAULT_LIBRARY_FORMATS)
    )
    suspicious_file_threshold_bytes: int = _DEFAULT_THRESHOLD_BYTES
    metadata_auto_confirm_confidence: float = 0.9
    metadata_auto_confirm_margin: float = 0.08
    preferred_metadata_language: str = "zh"
    source_cleanup_policy: str = SOURCE_CLEANUP_POLICY_KEEP
    download_rate_limit_bytes_per_second: int = 0
    upload_rate_limit_bytes_per_second: int = 0
    synced_download_rate_limit_bytes_per_second: int | None = None
    synced_upload_rate_limit_bytes_per_second: int | None = None


_SUPPORTED_PROFILES = frozenset({"tmdb_movie", "tmdb_show", "tpdb_adult_movie"})
_SUPPORTED_LIBRARY_FORMATS = frozenset({"jellyfin"})


class SettingsValidationError(ValueError):
    """应用配置校验失败"""


class AppSettingsService:
    """应用配置读取/写入服务 — 单例 DB 记录 + 默认值回退"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def read(self) -> AppSettings:
        """从数据库读取配置，缺失字段回退默认值"""
        with self._session_factory() as session:
            return self._read_from_record(self._get_or_create_record(session))

    def read_using_session(self, session: Session) -> AppSettings:
        """使用现有 session 读取配置（不提交，由调用方控制事务）"""
        record = session.scalars(select(AppSetting)).first()
        if record is None:
            return AppSettings()
        return self._read_from_record(record)

    def _read_from_record(self, record: AppSetting) -> AppSettings:
        return AppSettings(
            enabled_metadata_profiles=(
                list(record.enabled_metadata_profiles)
                if record.enabled_metadata_profiles
                else list(_DEFAULT_METADATA_PROFILES)
            ),
            enabled_library_formats=(
                list(record.enabled_library_formats)
                if record.enabled_library_formats
                else list(_DEFAULT_LIBRARY_FORMATS)
            ),
            suspicious_file_threshold_bytes=(
                record.suspicious_file_threshold_bytes
                if record.suspicious_file_threshold_bytes > 0
                else _DEFAULT_THRESHOLD_BYTES
            ),
            metadata_auto_confirm_confidence=record.metadata_auto_confirm_confidence,
            metadata_auto_confirm_margin=record.metadata_auto_confirm_margin,
            preferred_metadata_language=(
                getattr(record, "preferred_metadata_language", "zh") or "zh"
            ),
            source_cleanup_policy=(
                getattr(record, "source_cleanup_policy", SOURCE_CLEANUP_POLICY_KEEP)
                or SOURCE_CLEANUP_POLICY_KEEP
            ),
            download_rate_limit_bytes_per_second=max(
                0, int(getattr(record, "download_rate_limit_bytes_per_second", 0) or 0)
            ),
            upload_rate_limit_bytes_per_second=max(
                0, int(getattr(record, "upload_rate_limit_bytes_per_second", 0) or 0)
            ),
            synced_download_rate_limit_bytes_per_second=getattr(
                record, "synced_download_rate_limit_bytes_per_second", None
            ),
            synced_upload_rate_limit_bytes_per_second=getattr(
                record, "synced_upload_rate_limit_bytes_per_second", None
            ),
        )

    def save(self, settings: AppSettings) -> None:
        """保存应用配置，写入前执行校验"""
        self._validate(settings)
        with self._session_factory() as session:
            record = self._get_or_create_record(session)
            record.enabled_metadata_profiles = settings.enabled_metadata_profiles
            record.enabled_library_formats = settings.enabled_library_formats
            record.suspicious_file_threshold_bytes = settings.suspicious_file_threshold_bytes
            record.metadata_auto_confirm_confidence = settings.metadata_auto_confirm_confidence
            record.metadata_auto_confirm_margin = settings.metadata_auto_confirm_margin
            record.preferred_metadata_language = settings.preferred_metadata_language
            record.source_cleanup_policy = settings.source_cleanup_policy
            record.download_rate_limit_bytes_per_second = (
                settings.download_rate_limit_bytes_per_second
            )
            record.upload_rate_limit_bytes_per_second = (
                settings.upload_rate_limit_bytes_per_second
            )
            session.commit()

    def mark_download_rate_limits_synced(
        self,
        *,
        download_rate_limit_bytes_per_second: int,
        upload_rate_limit_bytes_per_second: int,
    ) -> None:
        """记录下载器全局限速期望值已经成功同步到下载器。"""
        with self._session_factory() as session:
            record = self._get_or_create_record(session)
            record.synced_download_rate_limit_bytes_per_second = (
                download_rate_limit_bytes_per_second
            )
            record.synced_upload_rate_limit_bytes_per_second = (
                upload_rate_limit_bytes_per_second
            )
            session.commit()

    def _validate(self, settings: AppSettings) -> None:
        """校验应用配置：拒绝不支持的档案/格式、拒绝越界数值"""
        errors: list[str] = []

        unknown_profiles = set(settings.enabled_metadata_profiles) - _SUPPORTED_PROFILES
        if unknown_profiles:
            errors.append(f"不支持的元数据档案: {', '.join(sorted(unknown_profiles))}")

        unknown_formats = set(settings.enabled_library_formats) - _SUPPORTED_LIBRARY_FORMATS
        if unknown_formats:
            errors.append(f"不支持的媒体库格式: {', '.join(sorted(unknown_formats))}")

        if settings.suspicious_file_threshold_bytes < 0:
            errors.append("可疑文件阈值不能为负数")

        if not (0 <= settings.metadata_auto_confirm_confidence <= 1):
            errors.append("自动确认置信度必须在 [0, 1] 区间")

        if not (0 <= settings.metadata_auto_confirm_margin <= 1):
            errors.append("自动确认差距必须在 [0, 1] 区间")

        if settings.preferred_metadata_language not in ("zh", "en"):
            errors.append(
                f"不支持的语言偏好: {settings.preferred_metadata_language}，允许值: zh/en"
            )

        if settings.source_cleanup_policy not in SUPPORTED_SOURCE_CLEANUP_POLICIES:
            errors.append(
                f"不支持的源文件清理策略: {settings.source_cleanup_policy}，"
                f"允许值: {', '.join(sorted(SUPPORTED_SOURCE_CLEANUP_POLICIES))}"
            )

        if settings.download_rate_limit_bytes_per_second < 0:
            errors.append("全局下载限速不能为负数")
        if settings.upload_rate_limit_bytes_per_second < 0:
            errors.append("全局上传限速不能为负数")
        if settings.download_rate_limit_bytes_per_second > MAX_RATE_LIMIT_BYTES_PER_SECOND:
            errors.append("全局下载限速不能超过 1GiB/s")
        if settings.upload_rate_limit_bytes_per_second > MAX_RATE_LIMIT_BYTES_PER_SECOND:
            errors.append("全局上传限速不能超过 1GiB/s")

        if errors:
            raise SettingsValidationError("; ".join(errors))

    def _get_or_create_record(self, session: Session) -> AppSetting:
        record = session.scalars(select(AppSetting)).first()
        if record is None:
            record = AppSetting()
            session.add(record)
            session.commit()
        return record
