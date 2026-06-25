"""Settings API 成人影片库根门禁测试.

锁定 (route-adult-movie-library-root 收口):
- ``_build_profile_options`` 中 ``tpdb_adult_movie.supported`` 必须同时要求
  ``config.tpdb_api_key`` 和 ``config.adult_movies_dir is not None``.
- ``tpdb_adult_movie.enabled`` 只能在 supported 为 True 时为 True.
- ``update_settings`` 在请求启用 ``tpdb_adult_movie`` 时:
  - 缺 TPDB API Key: 返回现有 validation_error.
  - 缺 ``adult_movies_dir``: 返回 validation_error, 文案说明未配置
    成人影片库根, 不能启用 TPDB 成人影片档案.

不改 EnvConfigStatusDto, 不展示 movies/shows/adult 路径状态.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from media_pilot.app import create_app
from media_pilot.config.settings import AppConfig
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.services.app_settings import AppSettings


def _make_config(
    tmp_path: Path,
    *,
    with_tpdb_key: bool = True,
    with_adult_dir: bool = True,
    adult_dir_mode: str = "valid",
) -> AppConfig:
    """构造 AppConfig; adult_dir_mode 决定成人影片库根的落点状态.

    - ``valid`` (默认): 路径在磁盘上是一个目录.
    - ``not_exists``: 路径被显式配为一个**不存在**的路径, 不自动建.
    - ``is_file``: 路径被显式配为一个**已存在但不是目录**的普通文件.
    - ``missing``: adult_movies_dir=None, 视同未配置 (需配合 with_adult_dir=False).
    """
    if not with_adult_dir:
        adult_path = None
    elif adult_dir_mode == "is_file":
        adult_path = tmp_path / "library" / "adult"
        adult_path.parent.mkdir(parents=True, exist_ok=True)
        adult_path.write_bytes(b"not a dir")
    elif adult_dir_mode == "not_exists":
        adult_path = tmp_path / "library" / "adult-does-not-exist"
    elif adult_dir_mode == "valid":
        adult_path = tmp_path / "library" / "adult"
        adult_path.mkdir(parents=True, exist_ok=True)
    else:
        raise ValueError(f"unknown adult_dir_mode: {adult_dir_mode}")

    return AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        adult_movies_dir=adult_path,
        tpdb_api_key="test-tpdb" if with_tpdb_key else None,
    )


def _make_client(tmp_path: Path, *, config: AppConfig) -> TestClient:
    """初始化数据库并建出常规目录; adult_movies_dir 的状态在 _make_config
    里已经按 adult_dir_mode 落定, 这里不再动它."""
    for d in (
        config.downloads_dir, config.watch_dir, config.workspace_dir,
        config.movies_dir, config.shows_dir, config.database_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    sf = create_session_factory(config)
    return TestClient(create_app(config=config, session_factory=sf))


# ── _build_profile_options 单元覆盖 ──


class TestBuildProfileOptionsAdultGate:
    def _options(self, config: AppConfig):
        from media_pilot.api.settings_routes import _build_profile_options

        return _build_profile_options(
            settings=AppSettings(), config=config,
        )

    def test_supported_requires_tpdb_key_and_adult_movies_dir(
        self, tmp_path: Path,
    ) -> None:
        """两个条件都满足时 supported=True; 默认 settings 包含 tpdb_adult_movie
        时 enabled 跟随 supported=True."""
        config = _make_config(tmp_path, with_tpdb_key=True, with_adult_dir=True)
        opts = self._options(config)
        tpdb_opt = next(o for o in opts if o.value == "tpdb_adult_movie")
        assert tpdb_opt.supported is True
        # 默认 AppSettings 包含 tpdb_adult_movie, supported=True 时 enabled 跟随
        assert tpdb_opt.enabled is True

    def test_unsupported_when_tpdb_key_missing(
        self, tmp_path: Path,
    ) -> None:
        """缺 TPDB API Key: supported=False."""
        config = _make_config(tmp_path, with_tpdb_key=False, with_adult_dir=True)
        opts = self._options(config)
        tpdb_opt = next(o for o in opts if o.value == "tpdb_adult_movie")
        assert tpdb_opt.supported is False
        assert tpdb_opt.enabled is False

    def test_unsupported_when_adult_movies_dir_missing(
        self, tmp_path: Path,
    ) -> None:
        """缺 adult_movies_dir: supported=False (route-adult-movie-library-root
        收口 — 即便 TPDB Key 已配, 没有显式库根也不能启用)."""
        config = _make_config(tmp_path, with_tpdb_key=True, with_adult_dir=False)
        opts = self._options(config)
        tpdb_opt = next(o for o in opts if o.value == "tpdb_adult_movie")
        assert tpdb_opt.supported is False, (
            "缺少 adult_movies_dir 时 tpdb_adult_movie 必须标记为不支持; "
            "当前 supported=True 会让前端误以为可以启用, 实际跑起来后会因 "
            "validate_startup_config 失败或 resolve_library_root ValueError 阻塞."
        )
        assert tpdb_opt.enabled is False

    def test_enabled_blocked_when_unsupported(
        self, tmp_path: Path,
    ) -> None:
        """当 supported=False 时, 即便用户在数据库里把 enabled_metadata_profiles
        写成包含 'tpdb_adult_movie', 也不能在响应里 reported 为 enabled=True.
        这是 _build_profile_options 的现有行为, 收口要求继续保留.

        注意: _build_profile_options 的入参是 AppSettings, 这里构造一个
        '假装' 启用了 tpdb_adult_movie 的 settings 验证 enabled 与 supported 联动."""
        config = _make_config(tmp_path, with_tpdb_key=False, with_adult_dir=False)
        settings = AppSettings(
            enabled_metadata_profiles=["tmdb_movie", "tpdb_adult_movie"],
        )
        from media_pilot.api.settings_routes import _build_profile_options
        opts = _build_profile_options(settings=settings, config=config)
        tpdb_opt = next(o for o in opts if o.value == "tpdb_adult_movie")
        assert tpdb_opt.supported is False
        # 即便 settings 里 enabled, 响应里也必须 reported 为 False
        assert tpdb_opt.enabled is False

    def test_tmdb_movie_supported_unaffected(
        self, tmp_path: Path,
    ) -> None:
        """回归: tmdb_movie 的 supported/enabled 不受成人影片库根缺失影响."""
        config = _make_config(tmp_path, with_tpdb_key=False, with_adult_dir=False)
        from media_pilot.api.settings_routes import _build_profile_options
        opts = _build_profile_options(settings=AppSettings(), config=config)
        tmdb_opt = next(o for o in opts if o.value == "tmdb_movie")
        assert tmdb_opt.supported is True

    def test_unsupported_when_adult_movies_dir_does_not_exist(
        self, tmp_path: Path,
    ) -> None:
        """adult_movies_dir 被配成不存在路径时 supported=False.

        与 validate_startup_config 一致 — 启动校验在路径不存在时会报
        ``adult_movies_dir does not exist``, 设置页必须同步展示为不可用,
        否则用户启用 → 启动失败, 出现"看似可启用但实际跑不起来"状态."""
        config = _make_config(
            tmp_path, with_tpdb_key=True,
            with_adult_dir=True, adult_dir_mode="not_exists",
        )
        # 不能走 _make_client (它会尝试建库), 仅做单元断言:
        from media_pilot.api.settings_routes import _build_profile_options
        opts = _build_profile_options(settings=AppSettings(), config=config)
        tpdb_opt = next(o for o in opts if o.value == "tpdb_adult_movie")
        assert tpdb_opt.supported is False, (
            "adult_movies_dir 指向不存在路径时 supported 必须为 False; "
            "实际 supported=True 会让前端误以为可启用, 但 worker 启动时 "
            "validate_startup_config 会因 'adult_movies_dir does not exist' 失败."
        )
        assert tpdb_opt.enabled is False

    def test_unsupported_when_adult_movies_dir_is_a_file(
        self, tmp_path: Path,
    ) -> None:
        """adult_movies_dir 路径存在但是普通文件时 supported=False.

        与 validate_startup_config 一致 — 启动校验在路径不是目录时报
        ``adult_movies_dir is not a directory``. 行为同 not_exists 模式."""
        config = _make_config(
            tmp_path, with_tpdb_key=True,
            with_adult_dir=True, adult_dir_mode="is_file",
        )
        from media_pilot.api.settings_routes import _build_profile_options
        opts = _build_profile_options(settings=AppSettings(), config=config)
        tpdb_opt = next(o for o in opts if o.value == "tpdb_adult_movie")
        assert tpdb_opt.supported is False, (
            "adult_movies_dir 指向普通文件时 supported 必须为 False; "
            "实际 supported=True 会让前端误以为可启用, 但 worker 启动时 "
            "validate_startup_config 会因 'adult_movies_dir is not a directory' 失败."
        )
        assert tpdb_opt.enabled is False
        tmdb_opt = next(o for o in opts if o.value == "tmdb_movie")
        assert tmdb_opt.supported is True


class TestBuildProfileOptionsTmdbShow:
    """TMDB 剧集档案 (tmdb_show) 必须在 _build_profile_options 中暴露,
    且 supported / enabled 行为与 tmdb_movie 对齐 (不依赖额外库根).

    锁定 backend 收口 (simplify-docker-onboarding-and-diagnostics 第 12
    节): settings profile selector 必须能向用户显示 tmdb_show 并
    把 enabled 状态正确反映到前端.
    """

    def test_tmdb_show_present_in_options(self, tmp_path: Path) -> None:
        """_build_profile_options 必须输出 value=tmdb_show 的 ProfileOption."""
        from media_pilot.api.settings_routes import _build_profile_options

        config = _make_config(tmp_path, with_tpdb_key=False, with_adult_dir=False)
        opts = _build_profile_options(settings=AppSettings(), config=config)
        show_opt = next((o for o in opts if o.value == "tmdb_show"), None)
        assert show_opt is not None, (
            f"_build_profile_options 必须输出 tmdb_show 选项; 实际 options="
            f"{[o.value for o in opts]!r}"
        )

    def test_tmdb_show_supported_independent_of_adult_dir(self, tmp_path: Path) -> None:
        """tmdb_show 不依赖 TPDB API Key / 成人影片库根, supported 恒为 True.

        与 tpdb_adult_movie 不同: 即使没配 TPDB 凭据 + 库根缺失,
        tmdb_show 仍应 supported=True. 只复用 TMDB 凭据门禁 (与
        tmdb_movie 一致).
        """
        from media_pilot.api.settings_routes import _build_profile_options

        config = _make_config(tmp_path, with_tpdb_key=False, with_adult_dir=False)
        opts = _build_profile_options(settings=AppSettings(), config=config)
        show_opt = next(o for o in opts if o.value == "tmdb_show")
        assert show_opt.supported is True
        # enabled 跟随 settings.enabled_metadata_profiles: AppSettings() 默认
        # 同时包含 tmdb_movie 和 tmdb_show, 因此 enabled=True.
        assert show_opt.enabled is True

    def test_tmdb_show_enabled_follows_settings(self, tmp_path: Path) -> None:
        """enabled 字段必须正确反映 settings.enabled_metadata_profiles.

        用户在设置页关闭 tmdb_show 后, _build_profile_options 必须把
        enabled 报为 False; 重新开启则报为 True. 与 tmdb_movie 同形.
        """
        from media_pilot.api.settings_routes import _build_profile_options

        config = _make_config(tmp_path)

        # 关闭 tmdb_show
        settings_off = AppSettings(enabled_metadata_profiles=["tmdb_movie"])
        opts = _build_profile_options(settings=settings_off, config=config)
        show_opt = next(o for o in opts if o.value == "tmdb_show")
        assert show_opt.enabled is False

        # 重新开启
        settings_on = AppSettings(
            enabled_metadata_profiles=["tmdb_movie", "tmdb_show"]
        )
        opts = _build_profile_options(settings=settings_on, config=config)
        show_opt = next(o for o in opts if o.value == "tmdb_show")
        assert show_opt.enabled is True


# ── PUT /api/v1/settings 端到端覆盖 ──


class TestUpdateSettingsAdultGate:
    def test_enable_tpdb_adult_movie_rejected_when_tpdb_key_missing(
        self, tmp_path: Path,
    ) -> None:
        """缺 TPDB API Key 时启用 tpdb_adult_movie → 422 validation_error."""
        config = _make_config(tmp_path, with_tpdb_key=False, with_adult_dir=True)
        client = _make_client(tmp_path, config=config)

        resp = client.put(
            "/api/v1/settings",
            json={"enabled_metadata_profiles": ["tmdb_movie", "tpdb_adult_movie"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"
        msg = body["messages"][0]
        assert msg["code"] == "validation_error"
        assert "TPDB" in msg["text"]

    def test_enable_tpdb_adult_movie_rejected_when_adult_movies_dir_missing(
        self, tmp_path: Path,
    ) -> None:
        """缺 adult_movies_dir 时启用 tpdb_adult_movie → 422 validation_error,
        文案明确指出"未配置成人影片库根"."""
        config = _make_config(tmp_path, with_tpdb_key=True, with_adult_dir=False)
        client = _make_client(tmp_path, config=config)

        resp = client.put(
            "/api/v1/settings",
            json={"enabled_metadata_profiles": ["tmdb_movie", "tpdb_adult_movie"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error", (
            f"缺 adult_movies_dir 时启用 tpdb_adult_movie 必须拒绝, got: {body!r}"
        )
        msg = body["messages"][0]
        assert msg["code"] == "validation_error"
        assert "成人影片库根" in msg["text"] or "adult_movies_dir" in msg["text"], (
            f"validation_error 文案必须说明未配置成人影片库根, got: {msg!r}"
        )

    def test_enable_tpdb_adult_movie_rejected_when_adult_movies_dir_not_exists(
        self, tmp_path: Path,
    ) -> None:
        """adult_movies_dir 指向不存在路径时启用 tpdb_adult_movie → 422.

        与 validate_startup_config 的 ``adult_movies_dir does not exist`` 错误
        同步 — 设置页必须在前端可启用的层面就拒绝, 不应等 worker 启动失败.
        """
        config = _make_config(
            tmp_path, with_tpdb_key=True,
            with_adult_dir=True, adult_dir_mode="not_exists",
        )
        client = _make_client(tmp_path, config=config)

        resp = client.put(
            "/api/v1/settings",
            json={"enabled_metadata_profiles": ["tmdb_movie", "tpdb_adult_movie"]},
        )
        body = resp.json()
        assert body["status"] == "error", (
            f"adult_movies_dir 指向不存在路径时启用 tpdb_adult_movie 必须拒绝, "
            f"got: {body!r}"
        )
        msg = body["messages"][0]
        assert msg["code"] == "validation_error"
        # 文案需提到"不可用"或"不存在", 提示用户库根本身有问题.
        assert (
            "不可用" in msg["text"]
            or "不存在" in msg["text"]
            or "成人影片库根" in msg["text"]
        ), f"validation_error 文案应说明成人影片库根不可用, got: {msg!r}"

    def test_enable_tpdb_adult_movie_rejected_when_adult_movies_dir_is_file(
        self, tmp_path: Path,
    ) -> None:
        """adult_movies_dir 指向普通文件时启用 tpdb_adult_movie → 422.

        与 validate_startup_config 的 ``adult_movies_dir is not a directory``
        错误同步 — 同上, 设置页必须在 worker 启动前就拒绝."""
        config = _make_config(
            tmp_path, with_tpdb_key=True,
            with_adult_dir=True, adult_dir_mode="is_file",
        )
        client = _make_client(tmp_path, config=config)

        resp = client.put(
            "/api/v1/settings",
            json={"enabled_metadata_profiles": ["tmdb_movie", "tpdb_adult_movie"]},
        )
        body = resp.json()
        assert body["status"] == "error", (
            f"adult_movies_dir 指向普通文件时启用 tpdb_adult_movie 必须拒绝, "
            f"got: {body!r}"
        )
        msg = body["messages"][0]
        assert msg["code"] == "validation_error"
        assert "不可用" in msg["text"] or "成人影片库根" in msg["text"], (
            f"validation_error 文案应说明成人影片库根不可用, got: {msg!r}"
        )

    def test_enable_tpdb_adult_movie_accepted_when_both_configured(
        self, tmp_path: Path,
    ) -> None:
        """TPDB Key + adult_movies_dir 都已配: 启用成功."""
        config = _make_config(tmp_path, with_tpdb_key=True, with_adult_dir=True)
        client = _make_client(tmp_path, config=config)

        resp = client.put(
            "/api/v1/settings",
            json={"enabled_metadata_profiles": ["tmdb_movie", "tpdb_adult_movie"]},
        )
        body = resp.json()
        assert body["status"] == "success", (
            f"两个条件都满足时启用 tpdb_adult_movie 应成功, got: {body!r}"
        )
        assert "tpdb_adult_movie" in body["data"]["enabled_metadata_profiles"]

    def test_get_settings_reports_tpdb_adult_unsupported_when_adult_dir_missing(
        self, tmp_path: Path,
    ) -> None:
        """GET /api/v1/settings: 缺 adult_movies_dir 时, available_profiles
        中 tpdb_adult_movie.supported=False."""
        config = _make_config(tmp_path, with_tpdb_key=True, with_adult_dir=False)
        client = _make_client(tmp_path, config=config)

        resp = client.get("/api/v1/settings")
        body = resp.json()
        assert body["status"] == "success"
        profiles = body["data"]["available_profiles"]
        tpdb_opt = next(o for o in profiles if o["value"] == "tpdb_adult_movie")
        assert tpdb_opt["supported"] is False
        assert tpdb_opt["enabled"] is False

    def test_env_status_does_not_leak_library_paths(
        self, tmp_path: Path,
    ) -> None:
        """回归: EnvConfigStatusDto 不暴露 movies/shows/adult 路径状态 (本次
        即便用户配置了 adult_movies_dir, 也不应在 env_status
        中新增字段)."""
        config = _make_config(tmp_path, with_tpdb_key=True, with_adult_dir=True)
        client = _make_client(tmp_path, config=config)

        resp = client.get("/api/v1/settings")
        env_status = resp.json()["data"]["env_status"]
        # 锁死: 不出现路径类键名
        for forbidden in ("movies_dir", "shows_dir", "adult_movies_dir"):
            assert forbidden not in env_status, (
                f"env_status 不应暴露 {forbidden!r} 路径状态, got: {env_status!r}"
            )
