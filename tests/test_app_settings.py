"""应用配置默认值测试 — 验证首次启动返回预期默认值"""

from pathlib import Path

import pytest

from media_pilot.repository.database import create_session_factory, initialize_database


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    return tmp_path / "config_test"


def _build_app_config(config_dir: Path):
    from media_pilot.config.settings import AppConfig

    app_config = AppConfig(
downloads_dir=config_dir / "downloads",
        watch_dir=config_dir / "watch",
        workspace_dir=config_dir / "workspace",
        movies_dir=config_dir / "library" / "movies",
        shows_dir=config_dir / "library" / "shows",
        database_dir=config_dir / "db",
    )
    for d in (
        app_config.downloads_dir,
        app_config.workspace_dir,
        app_config.movies_dir,
        app_config.shows_dir,
        app_config.database_dir,
    ):
        d.mkdir(parents=True)
    return app_config


def test_default_settings_on_first_startup(config_dir: Path) -> None:
    """首次启动 — 数据库无配置记录时,合并默认值返回 TMDB 优先、Jellyfin、300MB"""
    from media_pilot.services.app_settings import AppSettingsService

    app_config = _build_app_config(config_dir)

    initialize_database(app_config)
    session_factory = create_session_factory(app_config)

    service = AppSettingsService(session_factory)
    settings = service.read()

    # 元数据配置档案：默认 TMDB + TPDB（TPDB key 未配时前端会提示暂不支持）
    assert settings.enabled_metadata_profiles == ["tmdb_movie", "tmdb_show", "tpdb_adult_movie"]
    # 媒体库格式：默认 Jellyfin
    assert settings.enabled_library_formats == ["jellyfin"]
    # 可疑文件阈值：默认 300MB
    assert settings.suspicious_file_threshold_bytes == 300 * 1024 * 1024
    # 自动确认置信度沿用 AppConfig 默认值
    assert isinstance(settings.metadata_auto_confirm_confidence, float)
    assert 0 <= settings.metadata_auto_confirm_confidence <= 1
    # 源文件清理策略默认 keep
    assert settings.source_cleanup_policy == "keep"


def test_save_and_read_settings(config_dir: Path) -> None:
    """保存后读取返回持久化值，不返回默认值"""
    from media_pilot.services.app_settings import AppSettings, AppSettingsService

    app_config = _build_app_config(config_dir)

    initialize_database(app_config)
    session_factory = create_session_factory(app_config)

    service = AppSettingsService(session_factory)

    update = AppSettings(
        enabled_metadata_profiles=["tmdb_movie"],
        enabled_library_formats=["jellyfin"],
        suspicious_file_threshold_bytes=500 * 1024 * 1024,
        metadata_auto_confirm_confidence=0.85,
        metadata_auto_confirm_margin=0.10,
    )
    service.save(update)

    settings = service.read()
    assert settings.suspicious_file_threshold_bytes == 500 * 1024 * 1024
    assert settings.metadata_auto_confirm_confidence == 0.85


def test_read_merges_missing_fields_with_defaults(config_dir: Path) -> None:
    """数据库只存了部分字段时，其他字段回退默认值"""
    from media_pilot.services.app_settings import AppSettings, AppSettingsService

    app_config = _build_app_config(config_dir)

    initialize_database(app_config)
    session_factory = create_session_factory(app_config)

    service = AppSettingsService(session_factory)

    # 只存可疑文件阈值一个字段
    update = AppSettings(suspicious_file_threshold_bytes=600 * 1024 * 1024)
    service.save(update)

    settings = service.read()
    # 已存字段
    assert settings.suspicious_file_threshold_bytes == 600 * 1024 * 1024
    # 未存字段回退默认值
    assert settings.enabled_metadata_profiles == ["tmdb_movie", "tmdb_show", "tpdb_adult_movie"]
    assert settings.enabled_library_formats == ["jellyfin"]


@pytest.mark.parametrize("policy", ["keep", "ask", "trash"])
def test_source_cleanup_policy_round_trip(config_dir: Path, policy: str) -> None:
    """keep/ask/trash 三个枚举值都能保存并读取一致"""
    from media_pilot.services.app_settings import AppSettings, AppSettingsService

    app_config = _build_app_config(config_dir)
    initialize_database(app_config)
    session_factory = create_session_factory(app_config)

    service = AppSettingsService(session_factory)
    service.save(AppSettings(source_cleanup_policy=policy))

    assert service.read().source_cleanup_policy == policy


def test_source_cleanup_policy_rejects_unknown_value(config_dir: Path) -> None:
    """不支持的清理策略应当被校验拒绝并回滚"""
    from media_pilot.services.app_settings import AppSettings, AppSettingsService, SettingsValidationError

    app_config = _build_app_config(config_dir)
    initialize_database(app_config)
    session_factory = create_session_factory(app_config)

    service = AppSettingsService(session_factory)

    with pytest.raises(SettingsValidationError):
        service.save(AppSettings(source_cleanup_policy="auto_delete"))

    # 失败后数据库保持上次成功的值
    assert service.read().source_cleanup_policy == "keep"


def test_source_cleanup_policy_trash_allowed_without_trash_dir(config_dir: Path) -> None:
    """回收区未配置时仍允许保存 trash 策略（运行时降级由 Agent 处理）"""
    from media_pilot.services.app_settings import AppSettings, AppSettingsService

    app_config = _build_app_config(config_dir)
    initialize_database(app_config)
    session_factory = create_session_factory(app_config)

    service = AppSettingsService(session_factory)
    # 不配置 trash_dir
    assert app_config.trash_dir is None

    service.save(AppSettings(source_cleanup_policy="trash"))

    assert service.read().source_cleanup_policy == "trash"
