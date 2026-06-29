"""docker-compose.yml ↔ .env.example 一致性 source-audit 测试.

锁定:
- .env.example 中披露的 ``MEDIA_PILOT_ADULT_MOVIES_DIR`` 必须在
  docker-compose.yml 中挂载到容器内一致路径 (默认 ``/data/library/adult``).
- 固定容器内路径默认值由应用配置提供, compose environment 不应重复注入.

防回归: 成人影片库根如果只在 .env.example 披露而未在
compose 中落地, 容器内 worker 将使用一个不存在的目录, 启动校验抛错
或运行时写入失败, 与设计目标"显式分流"相悖.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE_FILE = REPO_ROOT / ".env.example"
TOP_BAR_FILE = REPO_ROOT / "frontend" / "src" / "components" / "layout" / "top-bar.tsx"


def _parse_media_pilot_service() -> tuple[set[str], set[str]]:
    """返回 (compose 中 environment 的 key 集合, volumes 中容器路径集合)."""
    import yaml

    text = COMPOSE_FILE.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    service = (data.get("services") or {}).get("media-pilot") or {}

    env_keys: set[str] = set()
    env_section = service.get("environment") or {}
    if isinstance(env_section, dict):
        for key in env_section.keys():
            if isinstance(key, str):
                env_keys.add(key)
    elif isinstance(env_section, list):
        for entry in env_section:
            if isinstance(entry, str) and "=" in entry:
                env_keys.add(entry.split("=", 1)[0])
            elif isinstance(entry, dict) and "key" in entry:
                env_keys.add(entry["key"])

    container_paths: set[str] = set()
    for raw in service.get("volumes") or []:
        if not isinstance(raw, str):
            continue
        # 形如 ${HOST:-default}:/container[:ro|:rw]
        # 切分后, 容器路径总是以 "/" 开头且不是默认值 (-xxx}), 找
        # 第一个 parts[i].startswith("/") 的位置.
        parts = raw.split(":")
        container_path = next(
            (p.strip() for p in parts if p.startswith("/")), None,
        )
        if container_path:
            container_paths.add(container_path)
    return env_keys, container_paths


# ── 1.1: 固定容器路径默认值不应在 compose environment 重复注入 ──


class TestContainerPathDefaultsNotDuplicatedInComposeEnvironment:
    def test_container_path_env_vars_not_declared_in_compose_environment(self) -> None:
        """容器内固定路径由应用默认值提供, compose 只负责宿主机挂载."""
        env_keys, _ = _parse_media_pilot_service()
        duplicated = {
            "MEDIA_PILOT_DOWNLOADS_DIR",
            "MEDIA_PILOT_WATCH_DIR",
            "MEDIA_PILOT_WORKSPACE_DIR",
            "MEDIA_PILOT_MOVIES_DIR",
            "MEDIA_PILOT_SHOWS_DIR",
            "MEDIA_PILOT_DATABASE_DIR",
            "MEDIA_PILOT_ADULT_MOVIES_DIR",
            "MEDIA_PILOT_TRASH_DIR",
        } & env_keys
        assert duplicated == set(), (
            "docker-compose.yml 不应重复注入固定容器路径变量: "
            + ", ".join(sorted(duplicated))
        )


# ── 1.2: 容器内默认路径必须在 compose volumes 中挂载 ──


class TestAdultMoviesDirVolumeMounted:
    def test_adult_movies_dir_mounted_at_container_path(self) -> None:
        """``/data/library/adult`` 必须在 compose volumes 中作为容器内
        路径出现, 形式形如 ``${MEDIA_PILOT_ADULT_MOVIES_DIR}:/data/library/adult``.
        缺失会导致容器内该目录为空, 启动后写入失败."""
        _, container_paths = _parse_media_pilot_service()
        assert "/data/library/adult" in container_paths, (
            "docker-compose.yml 缺 ${MEDIA_PILOT_ADULT_MOVIES_DIR}:/data/library/adult "
            "卷挂载; 容器内成人影片库根将为空, 启动后无法写入."
        )


# ── 1.3: .env.example 必须披露该变量 (防 compose 与文档漂移反向) ──


class TestAdultMoviesDirDocumentedInEnvExample:
    def test_adult_movies_dir_documented_in_env_example(self) -> None:
        """防 compose 配了但 .env.example 漏披露. 任意一行 (active 或注释)
        包含 ``MEDIA_PILOT_ADULT_MOVIES_DIR=`` 即视为披露."""
        text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
        assert "MEDIA_PILOT_ADULT_MOVIES_DIR=" in text, (
            ".env.example 缺 MEDIA_PILOT_ADULT_MOVIES_DIR 披露; "
            "用户无法从 .env.example 看到该开关."
        )


class TestComposeUsesPublishedImageByDefault:
    def test_app_services_use_media_pilot_image_variable(self) -> None:
        """默认 compose 面向发布部署, 不应要求用户本地有源码 build 上下文."""
        import yaml

        data = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8")) or {}
        services = data.get("services") or {}
        for service_name in (
            "media-pilot",
            "media-pilot-init",
            "media-pilot-prowlarr-init",
        ):
            service = services.get(service_name) or {}
            assert service.get("image") == "${MEDIA_PILOT_IMAGE}"
            assert "build" not in service

    def test_media_pilot_image_documented_in_env_example(self) -> None:
        text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
        assert "MEDIA_PILOT_IMAGE=" in text


class TestPostgresComposeWiring:
    def test_postgres_service_exists_and_is_health_gated(self) -> None:
        import yaml

        data = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8")) or {}
        services = data.get("services") or {}
        postgres = services.get("media-pilot-postgres") or {}
        assert postgres.get("image") == "postgres:17-alpine"
        assert "healthcheck" in postgres
        depends_on = (services.get("media-pilot") or {}).get("depends_on") or {}
        assert depends_on["media-pilot-postgres"]["condition"] == "service_healthy"

    def test_media_pilot_receives_database_url(self) -> None:
        import yaml

        data = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8")) or {}
        service = (data.get("services") or {}).get("media-pilot") or {}
        env = service.get("environment") or {}
        assert env["MEDIA_PILOT_DATABASE_URL"] == "${MEDIA_PILOT_DATABASE_URL}"

    def test_postgres_variables_documented_in_env_example(self) -> None:
        text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
        for expected in (
            "POSTGRES_DATA_DIR=",
            "POSTGRES_DB=",
            "POSTGRES_USER=",
            "POSTGRES_PASSWORD=",
            "MEDIA_PILOT_DATABASE_URL=",
        ):
            assert expected in text


class TestFrontendAssetsUseAppBasePath:
    def test_top_bar_logo_does_not_hardcode_root_path(self) -> None:
        text = TOP_BAR_FILE.read_text(encoding="utf-8")
        assert 'src="/media-pilot-mark.svg"' not in text
        assert "import.meta.env.BASE_URL" in text
