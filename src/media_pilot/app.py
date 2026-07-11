import logging
import os
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.accounts.csrf import csrf_middleware
from media_pilot.api.agent_background_routes import router as agent_background_router
from media_pilot.api.auth_dependencies import get_current_admin, get_current_auth
from media_pilot.api.auth_routes import router as auth_router
from media_pilot.api.content_discovery_routes import router as content_discovery_router
from media_pilot.api.manual_upload_routes import router as manual_upload_router
from media_pilot.api.public_routes import router as public_router
from media_pilot.api.resource_discovery_routes import router as resource_discovery_router
from media_pilot.api.settings_routes import router as settings_router
from media_pilot.api.user_routes import router as user_router
from media_pilot.api.v1 import router as api_v1_router
from media_pilot.config import (
    AdapterMode,
    AppConfig,
    LibraryFormat,
    LLMPromptProfile,
    MetadataProviderMode,
    validate_startup_config,
)
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.services.agent_background_status import (
    get_default_background_status_service,
)
from media_pilot.worker import Worker


def _config_from_environment() -> AppConfig:
    # 凭据读取: env 优先, 缺失时回退到 media-pilot-init 写入的共享 secrets.
    # 共享 secrets 文件不存在时保持现状 (非 Docker 场景继续按环境变量运行).
    prowlarr_api_key = _read_prowlarr_api_key()
    qbittorrent_password = _read_qbittorrent_password()

    return AppConfig(
        downloads_dir=_path_env("MEDIA_PILOT_DOWNLOADS_DIR", "/data/downloads"),
        watch_dir=_path_env("MEDIA_PILOT_WATCH_DIR", "/data/watch"),
        workspace_dir=_path_env("MEDIA_PILOT_WORKSPACE_DIR", "/data/workspace"),
        movies_dir=_path_env("MEDIA_PILOT_MOVIES_DIR", "/data/library/movies"),
        shows_dir=_path_env("MEDIA_PILOT_SHOWS_DIR", "/data/library/shows"),
        database_dir=_path_env("MEDIA_PILOT_DATABASE_DIR", "/data/db"),
        database_url=os.getenv("MEDIA_PILOT_DATABASE_URL") or None,
        ai_adapter=AdapterMode(os.getenv("MEDIA_PILOT_AI_ADAPTER", AdapterMode.NONE.value)),
        metadata_provider=MetadataProviderMode(
            os.getenv("MEDIA_PILOT_METADATA_PROVIDER", MetadataProviderMode.TMDB.value)
        ),
        library_format=LibraryFormat(
            os.getenv("MEDIA_PILOT_LIBRARY_FORMAT", LibraryFormat.JELLYFIN.value)
        ),
        prowlarr_url=os.getenv("MEDIA_PILOT_PROWLARR_URL", "http://media-pilot-prowlarr:9696"),
        prowlarr_api_key=prowlarr_api_key,
        prowlarr_timeout_seconds=float(os.getenv("MEDIA_PILOT_PROWLARR_TIMEOUT_SECONDS", "15")),
        qbittorrent_url=os.getenv("MEDIA_PILOT_QBITTORRENT_URL", "http://media-pilot-qbittorrent:8080"),
        qbittorrent_username=_read_qbittorrent_username(),
        qbittorrent_password=qbittorrent_password,
        qbittorrent_save_path=os.getenv("MEDIA_PILOT_QBITTORRENT_SAVE_PATH", "/data/downloads"),
        qbittorrent_category=os.getenv("MEDIA_PILOT_QBITTORRENT_CATEGORY", "media-pilot"),
        qbittorrent_timeout_seconds=float(
            os.getenv("MEDIA_PILOT_QBITTORRENT_TIMEOUT_SECONDS", "15")
        ),
        watch_stable_window_seconds=int(
            os.getenv("MEDIA_PILOT_WATCH_STABLE_SECONDS", "120")
        ),
        ai_url=os.getenv("MEDIA_PILOT_AI_URL"),
        llm_api_key=os.getenv("MEDIA_PILOT_LLM_API_KEY"),
        llm_base_url=os.getenv("MEDIA_PILOT_LLM_BASE_URL"),
        llm_model=os.getenv("MEDIA_PILOT_LLM_MODEL"),
        llm_timeout_seconds=float(
            os.getenv("MEDIA_PILOT_LLM_TIMEOUT_SECONDS", "30")
        ),
        llm_prompt_profile=LLMPromptProfile(
            os.getenv("MEDIA_PILOT_LLM_PROMPT_PROFILE", LLMPromptProfile.TMDB_MOVIE.value)
        ),
        llm_reply_language=os.getenv("MEDIA_PILOT_LLM_REPLY_LANGUAGE", "auto"),
        tmdb_api_key=os.getenv("MEDIA_PILOT_TMDB_API_KEY"),
        tmdb_base_url=os.getenv("MEDIA_PILOT_TMDB_BASE_URL", "https://api.themoviedb.org/3"),
        tmdb_language_priority=_csv_env("MEDIA_PILOT_TMDB_LANGUAGE_PRIORITY", ("zh-CN", "en-US")),
        tmdb_timeout_seconds=float(os.getenv("MEDIA_PILOT_TMDB_TIMEOUT_SECONDS", "10")),
        tmdb_image_base_url=os.getenv(
            "MEDIA_PILOT_TMDB_IMAGE_BASE_URL", "https://image.tmdb.org/t/p"
        ),
        tmdb_poster_size=os.getenv("MEDIA_PILOT_TMDB_POSTER_SIZE", "w780"),
        tmdb_backdrop_size=os.getenv("MEDIA_PILOT_TMDB_BACKDROP_SIZE", "w1280"),
        tmdb_logo_size=os.getenv("MEDIA_PILOT_TMDB_LOGO_SIZE", "w500"),
        tmdb_profile_size=os.getenv("MEDIA_PILOT_TMDB_PROFILE_SIZE", "w185"),
        metadata_auto_confirm_confidence=float(
            os.getenv("MEDIA_PILOT_METADATA_AUTO_CONFIRM_CONFIDENCE", "0.9")
        ),
        metadata_auto_confirm_margin=float(
            os.getenv("MEDIA_PILOT_METADATA_AUTO_CONFIRM_MARGIN", "0.08")
        ),
        trash_dir=_optional_path_env("MEDIA_PILOT_TRASH_DIR", "/data/trash"),
        tpdb_api_key=os.getenv("MEDIA_PILOT_TPDB_API_KEY"),
        tpdb_base_url=os.getenv("MEDIA_PILOT_TPDB_BASE_URL", "https://api.theporndb.net"),
        # 成人影片库根 (route-adult-movie-library-root). 默认使用 Docker
        # 容器内固定路径; 用户可显式把两个库根配成同一路径表示"故意混放".
        adult_movies_dir=_optional_path_env(
            "MEDIA_PILOT_ADULT_MOVIES_DIR", "/data/library/adult"
        ),
    )


def _path_env(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))


def _optional_path_env(name: str, default: str | None = None) -> Path | None:
    value = os.getenv(name)
    if value:
        return Path(value)
    return Path(default) if default is not None else None


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    parsed = tuple(item.strip() for item in value.split(",") if item.strip())
    return parsed or default


def _read_prowlarr_api_key() -> str | None:
    """Prowlarr API Key 读取: env 优先, 缺失时回退到共享 secrets.

    非 Docker 场景 (共享 secrets 不存在) 继续返回 None, 与既有行为一致.
    """
    from media_pilot.deployment.secrets import read_prowlarr_api_key_with_fallback

    return read_prowlarr_api_key_with_fallback()


def _read_qbittorrent_password() -> str:
    """qBittorrent WebUI 密码读取: env 优先, 缺失时回退到共享 secrets.

    非 Docker 场景保持现状 (空字符串, 与原默认值一致).
    """
    from media_pilot.deployment.secrets import read_qbittorrent_password_with_fallback

    return read_qbittorrent_password_with_fallback() or ""


def _read_qbittorrent_username() -> str:
    """qBittorrent WebUI 用户名读取: env 优先 → 共享 secrets → admin 默认.

    用户名在 qB 镜像里始终是 ``admin``, 因此 fallback 必须返回非空
    字符串, 否则下载提交会用空用户名构造登录请求, 一定失败. 共享
    secrets 与 fallback 默认值与 bootstrap 的 ``DEFAULT_QBITTORRENT_USERNAME``
    保持一致, 避免主应用和 init 容器对"用户名应当是什么"产生分歧.
    """
    from media_pilot.deployment.secrets import read_qbittorrent_username_with_fallback

    return read_qbittorrent_username_with_fallback()


def create_runtime_app() -> FastAPI:
    config = _config_from_environment()
    initialize_database(config)
    session_factory = create_session_factory(config)

    # 下载器全局限速是应用配置的期望状态。启动时只在期望值尚未成功
    # 同步过时补同步；该动作不能阻塞 API 或 worker 启动。
    _start_rate_limit_sync(config=config, session_factory=session_factory)

    # 恢复服务重启前的 stale AgentRun
    from media_pilot.orchestration.agent_recovery import recover_stale_agent_runs
    recover_stale_agent_runs(session_factory)

    return create_app(
        config=config,
        session_factory=session_factory,
        enable_background_processor=True,
    )


def create_app(
    *,
    config: AppConfig | None = None,
    session_factory: sessionmaker[Session] | None = None,
    enable_background_processor: bool = False,
) -> FastAPI:
    app = FastAPI(
        title="Media Pilot",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    worker = Worker(config)
    app.state.worker = worker
    app.state.session_factory = session_factory
    app.state.config = config
    app.middleware("http")(csrf_middleware)

    # 同步启动配置校验结果到后台状态服务 — 进程内单例, 不持久化.
    # 校验失败时 disabled 状态会暴露具体原因, 但不泄漏密钥值或路径凭据
    # (validate_startup_config 的 errors 列表只含字段/状态名).
    if config is not None:
        validation = validate_startup_config(config)
        if not validation.can_start_worker:
            get_default_background_status_service().set_disabled_reasons(
                list(validation.errors)
            )

    # 注册 JSON API 路由
    app.include_router(public_router)
    app.include_router(auth_router)
    # 认证会话只用于进入路由前的校验。function scope 确保 SSE/文件响应
    # 不会在整个发送周期内占用数据库连接。
    authenticated = [Depends(get_current_auth, scope="function")]
    admin_only = [Depends(get_current_admin)]
    app.include_router(api_v1_router, dependencies=authenticated)
    app.include_router(resource_discovery_router, dependencies=authenticated)
    app.include_router(content_discovery_router, dependencies=authenticated)
    app.include_router(manual_upload_router, dependencies=authenticated)
    app.include_router(settings_router, dependencies=admin_only)
    app.include_router(agent_background_router, dependencies=admin_only)
    app.include_router(user_router, dependencies=admin_only)

    # 启动后台任务处理线程，定期扫描并处理 discovered/queued 任务
    if enable_background_processor:
        _start_background_processor(app, worker, session_factory)

    # 挂载前端 SPA 静态产物（/app/ 路径）
    frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend-dist"
    if frontend_dist.is_dir():
        app.mount("/app", StaticFiles(directory=str(frontend_dist), html=True), name="spa")

        # SPA 路由回退：StaticFiles.html=True 处理子路由 404
        # 需要在 mount 外部额外捕获静态文件未命中的 SPA 路由
        index_html = frontend_dist / "index.html"
        @app.middleware("http")
        async def spa_fallback(request: Request, call_next):
            response = await call_next(request)
            if response.status_code == 404 and request.url.path.startswith("/app/"):
                return FileResponse(index_html)
            return response

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/", response_class=RedirectResponse)
    def web_root() -> RedirectResponse:
        return RedirectResponse(url="/app/", status_code=303)

    return app


_bg_thread_started: bool = False


def _start_rate_limit_sync(
    *,
    config: AppConfig,
    session_factory: sessionmaker[Session],
) -> None:
    """后台补同步下载器全局限速，不阻塞应用启动。"""
    if not config.qbittorrent_url or not config.qbittorrent_password:
        return

    from media_pilot.services.app_settings import AppSettingsService

    settings = AppSettingsService(session_factory).read()
    if (
        settings.download_rate_limit_bytes_per_second == 0
        and settings.upload_rate_limit_bytes_per_second == 0
        and settings.synced_download_rate_limit_bytes_per_second is None
        and settings.synced_upload_rate_limit_bytes_per_second is None
    ):
        return
    if (
        settings.synced_download_rate_limit_bytes_per_second
        == settings.download_rate_limit_bytes_per_second
        and settings.synced_upload_rate_limit_bytes_per_second
        == settings.upload_rate_limit_bytes_per_second
    ):
        return

    logger = logging.getLogger(__name__)

    def _sync() -> None:
        try:
            from media_pilot.services.download_rate_limits import (
                sync_download_rate_limits_on_startup,
            )

            sync_download_rate_limits_on_startup(
                config=config,
                session_factory=session_factory,
            )
        except Exception:
            logger.exception("启动时同步下载器全局限速失败")

    thread = threading.Thread(
        target=_sync,
        daemon=True,
        name="media-pilot-rate-limit-sync",
    )
    thread.start()


def _start_background_processor(
    app: FastAPI,
    worker: Worker,
    session_factory: sessionmaker[Session] | None,
) -> None:
    """启动后台线程，定期调用 BackgroundProcessor.run_once()"""
    global _bg_thread_started

    if _bg_thread_started:
        return
    if session_factory is None:
        return
    # Agent 主线未就绪 (LLM 未配置 / 目录缺失) 时不启动后台线程,
    # 避免空转。`can_start_worker=False` 时 API 仍可访问, 用户可继续
    # 补齐配置; 下一轮重启或补齐后会重新启动。
    if not worker.is_enabled():
        logger = logging.getLogger(__name__)
        logger.info(
            "Worker 未就绪 (LLM 缺失或目录配置不全), 跳过启动后台处理线程"
        )
        return

    _bg_thread_started = True
    logger = logging.getLogger(__name__)

    from media_pilot.orchestration.background_processor import BackgroundProcessor

    processor = BackgroundProcessor(worker)
    logger.info("启动后台任务处理线程")

    def _loop() -> None:
        logger.info("后台处理线程已启动")
        time.sleep(3)
        logger.info("开始后台处理循环")
        while True:
            try:
                result = processor.run_once(session_factory)
                logger.debug(
                    "后台处理完成 scanned=%d pending=%d succeeded=%d failed=%d",
                    result.scanned, result.pending,
                    result.succeeded, result.failed,
                )
            except Exception:
                logger.exception("后台处理循环异常")
            time.sleep(5)

    thread = threading.Thread(target=_loop, daemon=True, name="media-pilot-bg")
    thread.start()


app = create_app()
