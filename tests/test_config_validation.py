from pathlib import Path

from media_pilot.config import (
    AdapterMode,
    AppConfig,
    LibraryFormat,
    MetadataProviderMode,
    validate_startup_config,
)
from media_pilot.worker import Worker


def make_config(base: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=base / "downloads",
        watch_dir=base / "watch",
        workspace_dir=base / "workspace",
        movies_dir=base / "library" / "movies",
        shows_dir=base / "library" / "shows",
        database_dir=base / "db",
        tmdb_api_key="test-key",
    )


def create_required_dirs(config: AppConfig) -> None:
    for directory in (
        config.downloads_dir,
        config.watch_dir,
        config.workspace_dir,
        config.movies_dir,
        config.shows_dir,
        config.database_dir,
    ):
        directory.mkdir(parents=True)


def test_valid_startup_config_allows_worker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    # Agent 主线要求 LLM 已配置；向 valid config 注入 LLM 配置
    config = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        tmdb_api_key=config.tmdb_api_key,
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
    )
    create_required_dirs(config)

    result = validate_startup_config(config)

    assert result.can_start_worker is True
    assert result.errors == []
    assert Worker(config).is_enabled() is True


def test_missing_required_directory_blocks_worker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.downloads_dir.mkdir(parents=True)

    result = validate_startup_config(config)

    assert result.can_start_worker is False
    assert "workspace_dir does not exist" in result.errors
    assert Worker(config).is_enabled() is False


def test_unsupported_library_format_blocks_worker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    create_required_dirs(config)
    # 配齐 LLM, 让"非支持 library_format"成为唯一阻断因素, 便于断言。
    unsupported_config = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        library_format="plex",
        tmdb_api_key="test-key",
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
    )

    result = validate_startup_config(unsupported_config)

    assert result.can_start_worker is False
    assert result.errors == ["library_format is not supported: plex"]
    assert LibraryFormat.JELLYFIN.value == "jellyfin"


def test_invalid_tmdb_settings_block_worker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    create_required_dirs(config)
    invalid_config = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        tmdb_language_priority=(),
        tmdb_timeout_seconds=0,
        metadata_auto_confirm_confidence=1.2,
        tmdb_api_key="test-key",
    )

    result = validate_startup_config(invalid_config)

    assert result.can_start_worker is False
    assert "tmdb_language_priority must not be empty" in result.errors
    assert "tmdb_timeout_seconds must be greater than 0" in result.errors
    assert "metadata_auto_confirm_confidence must be between 0 and 1" in result.errors


def test_real_ai_mode_requires_llm_config(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    create_required_dirs(config)
    real_config = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        ai_adapter="real",
        llm_api_key=None,
        llm_base_url=None,
        llm_model=None,
        tmdb_api_key="test-key",
    )

    result = validate_startup_config(real_config)

    assert result.can_start_worker is False
    assert "llm_api_key is required for the Agent mainline" in result.errors
    assert "llm_base_url is required for the Agent mainline" in result.errors
    assert "llm_model is required for the Agent mainline" in result.errors


def test_real_ai_mode_with_complete_llm_config_is_valid(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    create_required_dirs(config)
    real_config = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        ai_adapter="real",
        llm_api_key="test-key",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
        llm_timeout_seconds=15.0,
        llm_prompt_profile="tmdb_movie",
        tmdb_api_key="test-key",
    )

    result = validate_startup_config(real_config)

    assert result.can_start_worker is True
    assert result.errors == []


def test_fake_ai_mode_is_deprecated(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    create_required_dirs(config)
    fake_config = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        ai_adapter="fake",
        tmdb_api_key="test-key",
    )

    result = validate_startup_config(fake_config)

    assert "not supported" in " ".join(result.errors)


def test_none_ai_mode_without_llm_blocks_worker(tmp_path: Path) -> None:
    """Agent 主线要求 LLM 配置; `ai_adapter=none` 同样不能豁免。
    这条回归锁定 `can_start_worker` 不会仅在 `ai_adapter=real` 时才
    校验 LLM, 而是只要缺失 `llm_api_key`/`llm_base_url`/`llm_model`,
    后台 worker 就不应启动。"""
    config = make_config(tmp_path)
    create_required_dirs(config)
    none_config = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        ai_adapter="none",
        tmdb_api_key="test-key",
    )

    result = validate_startup_config(none_config)

    assert result.can_start_worker is False
    assert "llm_api_key is required for the Agent mainline" in result.errors
    assert "llm_base_url is required for the Agent mainline" in result.errors
    assert "llm_model is required for the Agent mainline" in result.errors


def test_none_ai_mode_with_llm_config_is_valid(tmp_path: Path) -> None:
    """`ai_adapter=none` 模式下, 配齐 LLM 后 `can_start_worker` 应为 True。"""
    config = make_config(tmp_path)
    create_required_dirs(config)
    none_config = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        ai_adapter="none",
        llm_api_key="test-key",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
        tmdb_api_key="test-key",
    )

    result = validate_startup_config(none_config)

    assert result.can_start_worker is True
    assert result.errors == []


def test_fake_metadata_provider_rejected_at_startup(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    create_required_dirs(config)
    fake_config = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        metadata_provider=MetadataProviderMode.FAKE,
        tmdb_api_key="test-key",
    )

    result = validate_startup_config(fake_config)
    assert result.can_start_worker is False
    assert any("fake is not supported" in e for e in result.errors)


def test_tmdb_provider_requires_api_key(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    create_required_dirs(config)
    invalid_config = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        metadata_provider=MetadataProviderMode.TMDB,
        tmdb_api_key=None,
    )

    result = validate_startup_config(invalid_config)

    assert result.can_start_worker is False
    assert "tmdb_api_key is required when metadata_provider=tmdb" in result.errors


def test_fake_ai_adapter_rejected_at_startup(tmp_path: Path) -> None:
    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
    )
    dirs = (
        config.downloads_dir, config.watch_dir, config.workspace_dir,
        config.movies_dir, config.shows_dir, config.database_dir,
    )
    for d in dirs:
        d.mkdir(parents=True)

    fake_config = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        ai_adapter=AdapterMode.FAKE,
    )

    result = validate_startup_config(fake_config)
    assert result.can_start_worker is False
    assert any("fake is not supported" in e for e in result.errors)


# ═══════════════════════════════════════════════════════════════
# watch_dir 校验 (Task 2.3 — TDD RED 阶段)
# ═══════════════════════════════════════════════════════════════

def test_missing_watch_dir_blocks_worker(tmp_path: Path) -> None:
    """watch_dir 不存在时阻塞 worker"""
    config = make_config(tmp_path)
    # 创建除 watch_dir 外的所有目录
    config.downloads_dir.mkdir(parents=True)
    config.workspace_dir.mkdir(parents=True)
    config.movies_dir.mkdir(parents=True)
    config.shows_dir.mkdir(parents=True)
    config.database_dir.mkdir(parents=True)
    # watch_dir 未创建

    result = validate_startup_config(config)

    assert result.can_start_worker is False
    assert "watch_dir does not exist" in result.errors


def test_watch_dir_not_directory_blocks_worker(tmp_path: Path) -> None:
    """watch_dir 存在但是文件而非目录时阻塞 worker"""
    config = make_config(tmp_path)
    create_required_dirs(config)
    # 删除 watch_dir 目录，替换为文件
    import shutil
    shutil.rmtree(config.watch_dir)
    config.watch_dir.write_text("not a dir")

    result = validate_startup_config(config)

    assert result.can_start_worker is False
    assert "watch_dir is not a directory" in result.errors


# ═══════════════════════════════════════════════════════════════
# 成人影片库根 adult_movies_dir 校验 (route-adult-movie-library-root 1.x)
# ═══════════════════════════════════════════════════════════════


def test_appconfig_accepts_adult_movies_dir_field(tmp_path: Path) -> None:
    """AppConfig 显式支持 adult_movies_dir: Path | None 字段."""
    cfg = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        adult_movies_dir=tmp_path / "library" / "adult",
    )
    # 字段存在且类型是 Path
    assert cfg.adult_movies_dir == tmp_path / "library" / "adult"


def test_appconfig_adult_movies_dir_defaults_to_none(tmp_path: Path) -> None:
    """未配置 adult_movies_dir 时, 字段默认为 None (非隐式 fallback)."""
    cfg = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
    )
    assert cfg.adult_movies_dir is None


def test_tpdb_api_key_without_adult_movies_dir_blocks_worker(tmp_path: Path) -> None:
    """TPDB API Key 已配置但成人影片库根缺失 → 启动失败 (不隐式 fallback)."""
    config = make_config(tmp_path)
    create_required_dirs(config)
    cfg = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        tmdb_api_key="test-key",
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        tpdb_api_key="tpdb-key",
        # adult_movies_dir 缺失
    )

    result = validate_startup_config(cfg)

    assert result.can_start_worker is False
    assert any("adult_movies_dir" in e for e in result.errors), (
        f"tpdb_api_key 已配但 adult_movies_dir 缺失必须报错, got errors={result.errors!r}"
    )


def test_adult_movies_dir_does_not_exist_blocks_worker(tmp_path: Path) -> None:
    """成人影片库根目录不存在 → 启动失败."""
    config = make_config(tmp_path)
    create_required_dirs(config)
    cfg = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        tmdb_api_key="test-key",
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        adult_movies_dir=tmp_path / "library" / "adult_missing",
    )

    result = validate_startup_config(cfg)

    assert result.can_start_worker is False
    assert any("adult_movies_dir" in e and "does not exist" in e for e in result.errors), (
        f"adult_movies_dir 目录不存在必须报错, got errors={result.errors!r}"
    )


def test_adult_movies_dir_is_file_blocks_worker(tmp_path: Path) -> None:
    """成人影片库根路径存在但不是目录 → 启动失败."""
    config = make_config(tmp_path)
    create_required_dirs(config)
    bad_path = tmp_path / "adult_is_file"
    bad_path.write_text("not a dir")
    cfg = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        tmdb_api_key="test-key",
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        adult_movies_dir=bad_path,
    )

    result = validate_startup_config(cfg)

    assert result.can_start_worker is False
    assert any("adult_movies_dir" in e and "not a directory" in e for e in result.errors)


def test_adult_movies_dir_configured_and_exists_passes(tmp_path: Path) -> None:
    """成人影片库根存在且显式配置 → 启动通过."""
    config = make_config(tmp_path)
    create_required_dirs(config)
    adult_dir = tmp_path / "library" / "adult"
    adult_dir.mkdir(parents=True)
    cfg = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        tmdb_api_key="test-key",
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        adult_movies_dir=adult_dir,
    )

    result = validate_startup_config(cfg)

    assert result.can_start_worker is True, (
        f"adult_movies_dir 显式配置且存在应通过, errors={result.errors!r}"
    )


def test_adult_movies_dir_explicitly_equal_to_movies_dir_passes(tmp_path: Path) -> None:
    """用户显式把成人影片库根配成与常规电影库根相同 → 启动通过.

    这条不同于"缺失时自动 fallback". 显式同路径是用户选择, 系统不应拒绝.
    """
    config = make_config(tmp_path)
    create_required_dirs(config)
    cfg = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        tmdb_api_key="test-key",
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        # 显式把 adult_movies_dir 设成跟 movies_dir 相同
        adult_movies_dir=config.movies_dir,
    )

    result = validate_startup_config(cfg)

    assert result.can_start_worker is True, (
        f"显式同路径应通过, errors={result.errors!r}"
    )


def test_no_tpdb_api_key_and_no_adult_movies_dir_is_valid(tmp_path: Path) -> None:
    """未启用 TPDB (无 tpdb_api_key) 时, adult_movies_dir 缺失不应阻断启动.

    系统不强制要求所有部署都配成人影片库根, 只在启用 TPDB 能力时
    才强制要求. 这是因为非成人影片库部署完全没有相关需求.
    """
    config = make_config(tmp_path)
    create_required_dirs(config)
    cfg = AppConfig(
        downloads_dir=config.downloads_dir,
        watch_dir=config.watch_dir,
        workspace_dir=config.workspace_dir,
        movies_dir=config.movies_dir,
        shows_dir=config.shows_dir,
        database_dir=config.database_dir,
        tmdb_api_key="test-key",
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        # tpdb_api_key 默认 None, adult_movies_dir 默认 None
    )

    result = validate_startup_config(cfg)

    assert result.can_start_worker is True, (
        f"未启用 TPDB 时缺失 adult_movies_dir 应通过, errors={result.errors!r}"
    )
