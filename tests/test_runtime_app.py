from pathlib import Path

import media_pilot.app as app_module
from media_pilot.app import create_app
from media_pilot.repository.database import DATABASE_FILE_NAME


def _make_ready_dirs(root: Path) -> tuple[Path, ...]:
    downloads_dir = root / "downloads"
    watch_dir = root / "watch"
    workspace_dir = root / "workspace"
    movies_dir = root / "library" / "movies"
    shows_dir = root / "library" / "shows"
    database_dir = root / "db"
    for directory in (downloads_dir, watch_dir, workspace_dir,
                      movies_dir, shows_dir, database_dir):
        directory.mkdir(parents=True)
    return (downloads_dir, watch_dir, workspace_dir, movies_dir,
            shows_dir, database_dir)


def test_runtime_app_configures_database_from_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    downloads_dir, watch_dir, workspace_dir, movies_dir, shows_dir, database_dir = _make_ready_dirs(tmp_path)

    monkeypatch.setenv("MEDIA_PILOT_DOWNLOADS_DIR", str(downloads_dir))
    monkeypatch.setenv("MEDIA_PILOT_WATCH_DIR", str(watch_dir))
    monkeypatch.setenv("MEDIA_PILOT_WORKSPACE_DIR", str(workspace_dir))
    monkeypatch.setenv("MEDIA_PILOT_MOVIES_DIR", str(movies_dir))
    monkeypatch.setenv("MEDIA_PILOT_SHOWS_DIR", str(shows_dir))
    monkeypatch.setenv("MEDIA_PILOT_DATABASE_DIR", str(database_dir))
    monkeypatch.delenv("MEDIA_PILOT_DATABASE_URL", raising=False)
    monkeypatch.setenv("MEDIA_PILOT_METADATA_PROVIDER", "tmdb")
    monkeypatch.setenv("MEDIA_PILOT_TMDB_API_KEY", "test-key")
    monkeypatch.setenv("MEDIA_PILOT_TMDB_LANGUAGE_PRIORITY", "zh-CN,en-US")
    monkeypatch.setenv("MEDIA_PILOT_TMDB_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("MEDIA_PILOT_METADATA_AUTO_CONFIRM_CONFIDENCE", "0.92")

    config = app_module._config_from_environment()
    app_module.initialize_database(config)

    assert (database_dir / DATABASE_FILE_NAME).is_file()


def test_create_app_does_not_start_background_thread_when_worker_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """没有 LLM 配置时, `create_app(enable_background_processor=True)`
    不应启动后台处理线程。`can_start_worker` 受 LLM 缺失阻断
    (`ai_adapter` 模式无关)。"""
    from media_pilot.config import AppConfig
    from media_pilot.repository.database import create_session_factory, initialize_database

    _make_ready_dirs(tmp_path)
    config = AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        tmdb_api_key="test-key",
    )
    initialize_database(config)
    session_factory = create_session_factory(config)

    # 显式重置模块级 _bg_thread_started 状态, 避免上一次测试的状态
    # 影响本次断言。
    monkeypatch.setattr(app_module, "_bg_thread_started", False)

    create_app(
        config=config,
        session_factory=session_factory,
        enable_background_processor=True,
    )

    # 关键不变量: 当 worker 未就绪 (LLM 缺失) 时, `_bg_thread_started`
    # 必须保持 False, 表示没有后台线程被启动。
    assert app_module._bg_thread_started is False


def test_runtime_app_reads_llm_reply_language_from_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """`MEDIA_PILOT_LLM_REPLY_LANGUAGE` env 必须映射到 `AppConfig.llm_reply_language`,
    默认值 `auto`. 这样运维侧可在不改代码的前提下覆盖 Agent 对话回复语言."""
    from media_pilot.app import _config_from_environment

    # 显式 unset, 避免被外部环境干扰
    monkeypatch.delenv("MEDIA_PILOT_LLM_REPLY_LANGUAGE", raising=False)

    config = _config_from_environment()
    assert config.llm_reply_language == "auto"

    monkeypatch.setenv("MEDIA_PILOT_LLM_REPLY_LANGUAGE", "zh-CN")
    config = _config_from_environment()
    assert config.llm_reply_language == "zh-CN"

    monkeypatch.setenv("MEDIA_PILOT_LLM_REPLY_LANGUAGE", "en-US")
    config = _config_from_environment()
    assert config.llm_reply_language == "en-US"


def test_runtime_app_empty_llm_reply_language_fails_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """空字符串 `MEDIA_PILOT_LLM_REPLY_LANGUAGE=` 应被 `validate_startup_config`
    拒绝, 与直接传 `AppConfig(llm_reply_language='')` 行为一致."""
    from media_pilot.app import _config_from_environment
    from media_pilot.config import validate_startup_config

    downloads_dir, watch_dir, workspace_dir, movies_dir, shows_dir, database_dir = _make_ready_dirs(tmp_path)
    monkeypatch.setenv("MEDIA_PILOT_DOWNLOADS_DIR", str(downloads_dir))
    monkeypatch.setenv("MEDIA_PILOT_WATCH_DIR", str(watch_dir))
    monkeypatch.setenv("MEDIA_PILOT_WORKSPACE_DIR", str(workspace_dir))
    monkeypatch.setenv("MEDIA_PILOT_MOVIES_DIR", str(movies_dir))
    monkeypatch.setenv("MEDIA_PILOT_SHOWS_DIR", str(shows_dir))
    monkeypatch.setenv("MEDIA_PILOT_DATABASE_DIR", str(database_dir))
    monkeypatch.setenv("MEDIA_PILOT_LLM_REPLY_LANGUAGE", "")

    config = _config_from_environment()
    result = validate_startup_config(config)

    assert any("llm_reply_language" in err for err in result.errors)


def test_runtime_app_watch_stable_seconds_default(monkeypatch) -> None:
    """未设置 `MEDIA_PILOT_WATCH_STABLE_SECONDS` 时默认 120 秒。"""
    from media_pilot.app import _config_from_environment

    monkeypatch.delenv("MEDIA_PILOT_WATCH_STABLE_SECONDS", raising=False)
    config = _config_from_environment()
    assert config.watch_stable_window_seconds == 120


def test_runtime_app_watch_stable_seconds_explicit(monkeypatch) -> None:
    """设置 `MEDIA_PILOT_WATCH_STABLE_SECONDS=30` 时必须解析为 30。"""
    from media_pilot.app import _config_from_environment

    monkeypatch.setenv("MEDIA_PILOT_WATCH_STABLE_SECONDS", "30")
    config = _config_from_environment()
    assert config.watch_stable_window_seconds == 30


def test_runtime_app_uses_container_path_defaults(monkeypatch) -> None:
    """Docker compose 不需要重复注入固定容器内路径; 应用自身提供默认值."""
    from media_pilot.app import _config_from_environment

    for name in (
        "MEDIA_PILOT_DOWNLOADS_DIR",
        "MEDIA_PILOT_WATCH_DIR",
        "MEDIA_PILOT_WORKSPACE_DIR",
        "MEDIA_PILOT_MOVIES_DIR",
        "MEDIA_PILOT_SHOWS_DIR",
        "MEDIA_PILOT_DATABASE_DIR",
        "MEDIA_PILOT_ADULT_MOVIES_DIR",
        "MEDIA_PILOT_TRASH_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    config = _config_from_environment()

    assert str(config.downloads_dir) == "/data/downloads"
    assert str(config.watch_dir) == "/data/watch"
    assert str(config.workspace_dir) == "/data/workspace"
    assert str(config.movies_dir) == "/data/library/movies"
    assert str(config.shows_dir) == "/data/library/shows"
    assert str(config.database_dir) == "/data/db"
    assert str(config.adult_movies_dir) == "/data/library/adult"
    assert str(config.trash_dir) == "/data/trash"


def test_runtime_app_reads_database_url_from_environment(monkeypatch) -> None:
    """`MEDIA_PILOT_DATABASE_URL` 存在时应进入 AppConfig, 由数据库层优先使用。"""
    from media_pilot.app import _config_from_environment

    monkeypatch.setenv(
        "MEDIA_PILOT_DATABASE_URL",
        "postgresql+psycopg://media_pilot:secret@media-pilot-postgres:5432/media_pilot",
    )

    config = _config_from_environment()

    assert config.database_url == (
        "postgresql+psycopg://media_pilot:secret@media-pilot-postgres:5432/media_pilot"
    )


def test_appconfig_default_watch_stable_window_seconds() -> None:
    """`AppConfig()` 不传 `watch_stable_window_seconds` 时默认 120。"""
    from pathlib import Path

    from media_pilot.config import AppConfig

    config = AppConfig(
        downloads_dir=Path("/tmp/dl"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp/ws"),
        movies_dir=Path("/tmp/movies"),
        shows_dir=Path("/tmp/shows"),
        database_dir=Path("/tmp/db"),
    )
    assert config.watch_stable_window_seconds == 120
