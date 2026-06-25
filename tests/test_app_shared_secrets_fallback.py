"""主应用配置读取 — 共享 secrets fallback 测试.

锁定 (simplify-docker-onboarding-and-diagnostics):
- env 优先: 配置项同时由 env 提供时, 主应用使用 env 值.
- env 缺失时回退到 media-pilot-init 写入的共享 secrets.
- 共享 secrets 文件不存在时保持现有行为 (None / 空字符串), 不报错.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from media_pilot.app import _config_from_environment


def _clear_credentials(monkeypatch) -> None:
    monkeypatch.delenv("MEDIA_PILOT_PROWLARR_API_KEY", raising=False)
    monkeypatch.delenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", raising=False)
    monkeypatch.delenv("MEDIA_PILOT_QBITTORRENT_USERNAME", raising=False)


def _required_env(monkeypatch, *, tmp_path: Path) -> None:
    """保证 app.py 创建 AppConfig 时不报缺目录, 不触发 validate 报错."""
    monkeypatch.setenv("MEDIA_PILOT_DOWNLOADS_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("MEDIA_PILOT_WATCH_DIR", str(tmp_path / "watch"))
    monkeypatch.setenv("MEDIA_PILOT_WORKSPACE_DIR", str(tmp_path / "ws"))
    monkeypatch.setenv("MEDIA_PILOT_MOVIES_DIR", str(tmp_path / "movies"))
    monkeypatch.setenv("MEDIA_PILOT_SHOWS_DIR", str(tmp_path / "shows"))
    monkeypatch.setenv("MEDIA_PILOT_DATABASE_DIR", str(tmp_path / "db"))
    for d in ("dl", "watch", "ws", "movies", "shows", "db"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    # LLM / TMDB 必填, 用占位符即可, 本测试不依赖其行为
    monkeypatch.setenv("MEDIA_PILOT_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("MEDIA_PILOT_LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("MEDIA_PILOT_LLM_MODEL", "test-model")


class TestAppConfigSharedSecretsFallback:
    def test_env_only(self, monkeypatch, tmp_path: Path):
        _required_env(monkeypatch, tmp_path=tmp_path)
        _clear_credentials(monkeypatch)
        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "env-prowlarr")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "env-qb")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_USERNAME", "env-qb-user")
        # 共享 secrets 路径设到不存在的目录
        monkeypatch.setenv("MEDIA_PILOT_SHARED_SECRETS_PATH", str(tmp_path / "no-such" / "secrets.env"))

        cfg = _config_from_environment()
        assert cfg.prowlarr_api_key == "env-prowlarr"
        assert cfg.qbittorrent_password == "env-qb"
        assert cfg.qbittorrent_username == "env-qb-user"

    def test_secrets_fallback_when_env_missing(self, monkeypatch, tmp_path: Path):
        from media_pilot.deployment.secrets import write_shared_secrets

        _required_env(monkeypatch, tmp_path=tmp_path)
        _clear_credentials(monkeypatch)
        secrets = tmp_path / "secrets.env"
        write_shared_secrets(
            secrets,
            prowlarr_api_key="secrets-prowlarr",
            qbittorrent_username="secrets-qb-user",
            qbittorrent_password="secrets-qb",
        )
        monkeypatch.setenv("MEDIA_PILOT_SHARED_SECRETS_PATH", str(secrets))

        cfg = _config_from_environment()
        assert cfg.prowlarr_api_key == "secrets-prowlarr"
        assert cfg.qbittorrent_password == "secrets-qb"
        assert cfg.qbittorrent_username == "secrets-qb-user"

    def test_env_overrides_secrets(self, monkeypatch, tmp_path: Path):
        from media_pilot.deployment.secrets import write_shared_secrets

        _required_env(monkeypatch, tmp_path=tmp_path)
        secrets = tmp_path / "secrets.env"
        write_shared_secrets(
            secrets,
            prowlarr_api_key="secrets-prowlarr",
            qbittorrent_username="secrets-qb-user",
            qbittorrent_password="secrets-qb",
        )
        monkeypatch.setenv("MEDIA_PILOT_SHARED_SECRETS_PATH", str(secrets))
        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "env-prowlarr")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "env-qb")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_USERNAME", "env-qb-user")

        cfg = _config_from_environment()
        # env 必须赢
        assert cfg.prowlarr_api_key == "env-prowlarr"
        assert cfg.qbittorrent_password == "env-qb"
        assert cfg.qbittorrent_username == "env-qb-user"

    def test_missing_secrets_no_docker_no_error(self, monkeypatch, tmp_path: Path):
        """非 Docker 场景: 共享 secrets 不存在, 配置仍然成功创建.

        这是从外部启动 (uv run python -m media_pilot) 必须保持的兼容性.
        username 在 env 与 secrets 都缺失时回退到镜像默认 ``admin``.
        """
        _required_env(monkeypatch, tmp_path=tmp_path)
        _clear_credentials(monkeypatch)
        # secrets 路径指向不存在的文件
        monkeypatch.setenv(
            "MEDIA_PILOT_SHARED_SECRETS_PATH", str(tmp_path / "missing-secrets.env")
        )
        # 不应抛错
        cfg = _config_from_environment()
        assert cfg.prowlarr_api_key is None
        assert cfg.qbittorrent_password == ""
        assert cfg.qbittorrent_username == "admin"
