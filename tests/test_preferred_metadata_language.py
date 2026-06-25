"""应用配置 preferred_metadata_language 测试 — Phase 5"""

from unittest.mock import MagicMock

import pytest

from media_pilot.services.app_settings import (
    AppSettings,
    AppSettingsService,
    SettingsValidationError,
)


class TestPreferredMetadataLanguage:
    """测试 preferred_metadata_language 配置读写的正确性"""

    def test_default_is_zh(self):
        """新建设置默认 zh"""
        settings = AppSettings()
        assert settings.preferred_metadata_language == "zh"

    def test_accepts_en(self):
        """允许 en"""
        settings = AppSettings(preferred_metadata_language="en")
        svc = AppSettingsService(MagicMock())
        svc._validate(settings)  # Should not raise

    def test_rejects_invalid_language(self):
        """拒绝非法值"""
        settings = AppSettings(preferred_metadata_language="ja")
        svc = AppSettingsService(MagicMock())
        with pytest.raises(SettingsValidationError, match="zh/en"):
            svc._validate(settings)

    def test_read_returns_default_when_db_missing_field(self):
        """db 记录缺少 preferred_metadata_language 时回退默认 zh"""
        from media_pilot.repository.models import AppSetting
        record = AppSetting(
            suspicious_file_threshold_bytes=314572800,
            metadata_auto_confirm_confidence=0.9,
            metadata_auto_confirm_margin=0.08,
        )
        # Simulate old record without the field
        svc = AppSettingsService(MagicMock())
        result = svc._read_from_record(record)
        assert result.preferred_metadata_language == "zh"

    def test_read_returns_db_value_when_set(self):
        """db 记录有值时返回该值"""
        from media_pilot.repository.models import AppSetting
        record = AppSetting(
            suspicious_file_threshold_bytes=314572800,
            metadata_auto_confirm_confidence=0.9,
            metadata_auto_confirm_margin=0.08,
        )
        record.preferred_metadata_language = "en"
        svc = AppSettingsService(MagicMock())
        result = svc._read_from_record(record)
        assert result.preferred_metadata_language == "en"

    def test_settings_dto_has_field(self):
        """AppSettingsDto 包含 preferred_metadata_language"""
        from media_pilot.api.settings_dtos import AppSettingsDto
        dto = AppSettingsDto()
        assert dto.preferred_metadata_language == "zh"
        dto_en = AppSettingsDto(preferred_metadata_language="en")
        assert dto_en.preferred_metadata_language == "en"
    def test_dto_roundtrip(self):
        """AppSettingsDto 承载 preferred_metadata_language 往返"""
        from media_pilot.api.settings_dtos import AppSettingsDto, AppSettingsUpdateRequest
        dto = AppSettingsDto(preferred_metadata_language="en")
        assert dto.preferred_metadata_language == "en"
        # 验证 update request 能携带该字段
        req = AppSettingsUpdateRequest(preferred_metadata_language="zh")
        assert req.preferred_metadata_language == "zh"


    def test_update_request_has_field(self):
        """AppSettingsUpdateRequest 包含 preferred_metadata_language"""
        from media_pilot.api.settings_dtos import AppSettingsUpdateRequest
        req = AppSettingsUpdateRequest(preferred_metadata_language="en")
        assert req.preferred_metadata_language == "en"
        req_none = AppSettingsUpdateRequest()
        assert req_none.preferred_metadata_language is None
