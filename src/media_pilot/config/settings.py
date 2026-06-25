from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class AdapterMode(StrEnum):
    NONE = "none"
    REAL = "real"

    # 废弃值
    FAKE = "fake"


class MetadataProviderMode(StrEnum):
    TMDB = "tmdb"

    # 废弃值 — 生产配置不允许
    FAKE = "fake"


class LibraryFormat(StrEnum):
    JELLYFIN = "jellyfin"


class LLMPromptProfile(StrEnum):
    TMDB_MOVIE = "tmdb_movie"
    TMDB_SHOW = "tmdb_show"


@dataclass(frozen=True, kw_only=True)
class AppConfig:
    downloads_dir: Path
    watch_dir: Path
    workspace_dir: Path
    movies_dir: Path
    shows_dir: Path
    database_dir: Path
    # 成人影片库根 — 用于发布 `tpdb_adult_movie` 元数据档案结果.
    # 缺失时启用 TPDB 成人影片能力必须报错, 不得隐式 fallback 到
    # movies_dir. 用户可以显式把成人影片库根配成与常规电影库根相同.
    # 详见 route-adult-movie-library-root change.
    adult_movies_dir: Path | None = None
    ai_adapter: AdapterMode = AdapterMode.NONE
    metadata_provider: MetadataProviderMode = MetadataProviderMode.TMDB
    library_format: LibraryFormat = LibraryFormat.JELLYFIN
    ai_url: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = 30.0
    llm_prompt_profile: LLMPromptProfile = LLMPromptProfile.TMDB_MOVIE
    # LLM 回复语言偏好 — 面向 auto_ingest Agent 的对话回复与最终总结.
    # "auto" 时从 tmdb_language_priority[0] 推断 (zh*/en*); 显式值用于
    # 强制覆盖元数据语言与对话语言解耦的场景. 不会影响元数据搜索 / 候选 / 库产物语言.
    llm_reply_language: str = "auto"
    tmdb_api_key: str | None = None
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_language_priority: tuple[str, ...] = ("zh-CN", "en-US")
    tmdb_timeout_seconds: float = 10.0
    tmdb_image_base_url: str = "https://image.tmdb.org/t/p"
    tmdb_poster_size: str = "w780"
    tmdb_backdrop_size: str = "w1280"
    tmdb_logo_size: str = "w500"
    tmdb_profile_size: str = "w185"
    metadata_auto_confirm_confidence: float = 0.9
    metadata_auto_confirm_margin: float = 0.08
    trash_dir: Path | None = None
    tpdb_api_key: str | None = None
    tpdb_base_url: str = "https://api.theporndb.net"
    # Prowlarr 资源搜索
    prowlarr_url: str = ""
    prowlarr_api_key: str | None = None
    prowlarr_timeout_seconds: float = 15.0
    # qBittorrent 下载提交
    qbittorrent_url: str = ""
    qbittorrent_username: str = "admin"
    qbittorrent_password: str = ""
    qbittorrent_save_path: str = "/data/downloads"
    qbittorrent_category: str = "media-pilot"
    qbittorrent_timeout_seconds: float = 15.0
    # watch 扫描稳定窗口（秒）：顶层文件 / 顶层目录的快照必须连续保持不变
    # `watch_stable_window_seconds` 秒后才进入 IngestTask 候选。重启进程后
    # 进程内稳定缓存清空, 重新等待窗口。
    # `<= 0` 视为关闭稳定窗口：首次扫描即创建候选，恢复无 detector 的旧行为。
    # 不推荐生产使用（无法避免半成品被接管），仅供测试与回退场景。
    watch_stable_window_seconds: int = 120


@dataclass(frozen=True)
class ConfigValidationResult:
    errors: list[str]

    @property
    def can_start_worker(self) -> bool:
        return not self.errors


def validate_startup_config(config: AppConfig) -> ConfigValidationResult:
    errors: list[str] = []
    required_directories = {
        "downloads_dir": config.downloads_dir,
        "watch_dir": config.watch_dir,
        "workspace_dir": config.workspace_dir,
        "movies_dir": config.movies_dir,
        "shows_dir": config.shows_dir,
        "database_dir": config.database_dir,
    }

    for name, directory in required_directories.items():
        if not directory.exists():
            errors.append(f"{name} does not exist")
            continue
        if not directory.is_dir():
            errors.append(f"{name} is not a directory")

    if config.library_format != LibraryFormat.JELLYFIN:
        errors.append(f"library_format is not supported: {config.library_format}")
    if config.metadata_provider == MetadataProviderMode.FAKE:
        errors.append("metadata_provider=fake is not supported in production, use tmdb")
    if config.metadata_provider not in (
        MetadataProviderMode.FAKE,
        MetadataProviderMode.TMDB,
    ):
        errors.append(f"metadata_provider is not supported: {config.metadata_provider}")
    if config.metadata_provider == MetadataProviderMode.TMDB and not config.tmdb_api_key:
        errors.append("tmdb_api_key is required when metadata_provider=tmdb")
    if config.ai_adapter == AdapterMode.FAKE:
        errors.append("ai_adapter=fake is not supported in production, use none or real")
    # Agent 主线是当前唯一可推进的执行入口。后台 worker 启动条件
    # (`can_start_worker`) 不再受 `ai_adapter` 模式影响：即使选择
    # `ai_adapter=none` 也必须配置好 LLM, 否则后台 worker 不应启动。
    if not config.llm_api_key:
        errors.append("llm_api_key is required for the Agent mainline")
    if not config.llm_base_url:
        errors.append("llm_base_url is required for the Agent mainline")
    if not config.llm_model:
        errors.append("llm_model is required for the Agent mainline")
    if config.llm_timeout_seconds <= 0:
        errors.append("llm_timeout_seconds must be greater than 0")
    if config.llm_prompt_profile not in LLMPromptProfile:
        errors.append(f"unsupported llm_prompt_profile: {config.llm_prompt_profile}")
    if not config.llm_reply_language or not config.llm_reply_language.strip():
        errors.append("llm_reply_language must not be empty")
    if not config.tmdb_language_priority:
        errors.append("tmdb_language_priority must not be empty")
    if config.tmdb_timeout_seconds <= 0:
        errors.append("tmdb_timeout_seconds must be greater than 0")
    if config.metadata_auto_confirm_confidence < 0 or config.metadata_auto_confirm_confidence > 1:
        errors.append("metadata_auto_confirm_confidence must be between 0 and 1")
    if config.watch_stable_window_seconds < 0:
        errors.append("watch_stable_window_seconds must not be negative")

    # 成人影片库根 (route-adult-movie-library-root):
    # 1. 显式配置时必须存在且是目录 — 不论是否启用 TPDB, 配错路径都应报错.
    # 2. 启用 TPDB 能力 (tpdb_api_key 已配) 时, 系统承诺能跑 TPDB 成人
    #    影片入库, 必须给出显式成人影片库根; 反之未启用 TPDB 时, 缺失
    #    adult_movies_dir 不阻断启动.
    if config.adult_movies_dir is not None:
        if not config.adult_movies_dir.exists():
            errors.append("adult_movies_dir does not exist")
        elif not config.adult_movies_dir.is_dir():
            errors.append("adult_movies_dir is not a directory")
    elif config.tpdb_api_key:
        errors.append(
            "adult_movies_dir is required when tpdb_api_key is configured; "
            "set MEDIA_PILOT_ADULT_MOVIES_DIR explicitly (it may equal "
            "MEDIA_PILOT_MOVIES_DIR if you intentionally want the same root)"
        )

    return ConfigValidationResult(errors=errors)