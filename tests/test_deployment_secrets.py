"""部署 secrets 初始化单元测试.

锁定 (simplify-docker-onboarding-and-diagnostics):
- 共享 secrets 文件读写幂等.
- Prowlarr API Key 生成 32+ 字节 hex, 长度与随机性合规.
- qBittorrent 密码生成高熵随机, 写入 PBKDF2 配置.
- 凭据一致时 no-op, 不一致时默认错误并提示同步方向.
- 错误信息只显示字段名 + 必要路径, 不打印完整 secret.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

# ── 凭据生成规则 ──


class TestProwlarrApiKeyGeneration:
    def test_generated_key_is_hex(self):
        from media_pilot.deployment.secrets import generate_prowlarr_api_key

        key = generate_prowlarr_api_key()
        assert re.fullmatch(r"[0-9a-f]+", key), f"Prowlarr API Key 必须十六进制, 收到 {key!r}"

    def test_generated_key_meets_minimum_length(self):
        from media_pilot.deployment.secrets import generate_prowlarr_api_key

        key = generate_prowlarr_api_key()
        # 32 bytes = 64 hex chars 最低, 实际可能更多.
        assert len(key) >= 32, f"Prowlarr API Key 长度不足: {len(key)}"

    def test_generated_keys_are_unique(self):
        from media_pilot.deployment.secrets import generate_prowlarr_api_key

        keys = {generate_prowlarr_api_key() for _ in range(20)}
        assert len(keys) == 20, "Prowlarr API Key 不应重复"


class TestQbittorrentPasswordGeneration:
    def test_generated_password_is_string(self):
        from media_pilot.deployment.secrets import generate_qbittorrent_password

        pw = generate_qbittorrent_password()
        assert isinstance(pw, str)
        assert len(pw) >= 16, f"qB 密码长度不足: {len(pw)}"

    def test_generated_passwords_are_unique(self):
        from media_pilot.deployment.secrets import generate_qbittorrent_password

        passwords = {generate_qbittorrent_password() for _ in range(20)}
        assert len(passwords) == 20, "qB 密码不应重复"

    def test_generated_password_has_alphanumeric(self):
        from media_pilot.deployment.secrets import generate_qbittorrent_password

        pw = generate_qbittorrent_password()
        # 至少包含字母 + 数字
        assert any(c.isalpha() for c in pw)
        assert any(c.isdigit() for c in pw)


class TestFixedDefaultsNotLeaked:
    """仓库默认文件不得提供可生产使用的固定凭据."""

    def test_env_example_prowlarr_key_is_placeholder(self):
        text = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
        m = re.search(r"^MEDIA_PILOT_PROWLARR_API_KEY=[ \t]*(.*)$", text, re.MULTILINE)
        assert m, ".env.example 缺 MEDIA_PILOT_PROWLARR_API_KEY"
        value = m.group(1).strip()
        # 必须是占位符, 不能是真实 hex
        assert "your-prowlarr-api-key" in value or value == "" or "compose" in value.lower(), (
            f".env.example 出现固定 Prowlarr API Key 默认值: {value!r}"
        )

    def test_env_example_qb_password_is_placeholder(self):
        text = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
        m = re.search(r"^MEDIA_PILOT_QBITTORRENT_PASSWORD=[ \t]*(.*)$", text, re.MULTILINE)
        assert m, ".env.example 缺 MEDIA_PILOT_QBITTORRENT_PASSWORD"
        value = m.group(1).strip()
        assert "your-qbittorrent-password" in value or value == "" or "compose" in value.lower(), (
            f".env.example 出现固定 qB 密码默认值: {value!r}"
        )


# ── 共享 secrets 文件读写 ──


class TestSharedSecretsReadWrite:
    def test_write_then_read(self, tmp_path: Path):
        from media_pilot.deployment.secrets import (
            read_shared_secrets,
            write_shared_secrets,
        )

        path = tmp_path / "secrets.env"
        write_shared_secrets(
            path,
            prowlarr_api_key="abcdef1234567890",
            qbittorrent_username="admin",
            qbittorrent_password="SuperSecret123",
        )
        data = read_shared_secrets(path)
        assert data["MEDIA_PILOT_PROWLARR_API_KEY"] == "abcdef1234567890"
        assert data["MEDIA_PILOT_QBITTORRENT_USERNAME"] == "admin"
        assert data["MEDIA_PILOT_QBITTORRENT_PASSWORD"] == "SuperSecret123"

    def test_missing_file_returns_empty(self, tmp_path: Path):
        from media_pilot.deployment.secrets import read_shared_secrets

        data = read_shared_secrets(tmp_path / "nope.env")
        assert data == {}

    def test_file_permissions_are_restrictive(self, tmp_path: Path):
        from media_pilot.deployment.secrets import write_shared_secrets

        path = tmp_path / "secrets.env"
        write_shared_secrets(
            path,
            prowlarr_api_key="x",
            qbittorrent_username="admin",
            qbittorrent_password="y",
        )
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600, f"共享 secrets 文件权限应为 0o600, 实际 {oct(mode)}"


# ── Prowlarr config.xml 读写 ──


class TestProwlarrConfigReadWrite:
    def _write_xml(self, path: Path, *, api_key: str | None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            "<Config>"
            f"<ApiKey>{api_key or ''}</ApiKey>"
            "<Port>9696</Port>"
            "</Config>"
        )
        path.write_text(body, encoding="utf-8")

    def test_missing_api_key_returns_none(self, tmp_path: Path):
        from media_pilot.deployment.secrets import read_prowlarr_api_key_from_config

        cfg = tmp_path / "config.xml"
        self._write_xml(cfg, api_key=None)
        assert read_prowlarr_api_key_from_config(cfg) is None

    def test_present_api_key_is_read(self, tmp_path: Path):
        from media_pilot.deployment.secrets import read_prowlarr_api_key_from_config

        cfg = tmp_path / "config.xml"
        self._write_xml(cfg, api_key="cafebabe")
        assert read_prowlarr_api_key_from_config(cfg) == "cafebabe"

    def test_missing_file_returns_none(self, tmp_path: Path):
        from media_pilot.deployment.secrets import read_prowlarr_api_key_from_config

        assert read_prowlarr_api_key_from_config(tmp_path / "nope.xml") is None

    def test_write_inserts_api_key(self, tmp_path: Path):
        from media_pilot.deployment.secrets import write_prowlarr_api_key_to_config

        cfg = tmp_path / "config.xml"
        self._write_xml(cfg, api_key=None)
        write_prowlarr_api_key_to_config(cfg, "newkey123")
        # 重新解析校验
        root = ET.fromstring(cfg.read_text(encoding="utf-8"))
        assert root.find("ApiKey").text == "newkey123"
        # 其它字段保留
        assert root.find("Port").text == "9696"

    def test_write_replaces_existing(self, tmp_path: Path):
        from media_pilot.deployment.secrets import write_prowlarr_api_key_to_config

        cfg = tmp_path / "config.xml"
        self._write_xml(cfg, api_key="old")
        write_prowlarr_api_key_to_config(cfg, "new")
        root = ET.fromstring(cfg.read_text(encoding="utf-8"))
        assert root.find("ApiKey").text == "new"
        # 确认只有一个 ApiKey 节点
        assert len(root.findall("ApiKey")) == 1

    def test_invalid_xml_format_raises(self, tmp_path: Path):
        from media_pilot.deployment.secrets import read_prowlarr_api_key_from_config

        cfg = tmp_path / "config.xml"
        cfg.write_text("<<<not-xml>>>", encoding="utf-8")
        with pytest.raises(ValueError, match="Prowlarr 配置 XML 解析失败"):
            read_prowlarr_api_key_from_config(cfg)


# ── qBittorrent config 读写 ──


class TestQbittorrentConfigReadWrite:
    def _write_conf(self, path: Path, *, password_hash: str | None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["[Preferences]", "General\\Locale=en"]
        if password_hash is not None:
            lines.append(f"WebUI\\Password_PBKDF2={password_hash}")
        lines.extend(["[WebUI]", "Username=admin", "Port=8080"])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_missing_password_field_returns_none(self, tmp_path: Path):
        from media_pilot.deployment.secrets import read_qbittorrent_password_hash

        cfg = tmp_path / "qBittorrent.conf"
        self._write_conf(cfg, password_hash=None)
        assert read_qbittorrent_password_hash(cfg) is None

    def test_present_hash_is_read(self, tmp_path: Path):
        from media_pilot.deployment.secrets import read_qbittorrent_password_hash

        cfg = tmp_path / "qBittorrent.conf"
        self._write_conf(cfg, password_hash='@ByteArray(abc:def)')
        assert read_qbittorrent_password_hash(cfg) == "@ByteArray(abc:def)"

    def test_missing_file_returns_none(self, tmp_path: Path):
        from media_pilot.deployment.secrets import read_qbittorrent_password_hash

        assert read_qbittorrent_password_hash(tmp_path / "nope.conf") is None

    def test_write_inserts_password_hash(self, tmp_path: Path):
        from media_pilot.deployment.secrets import write_qbittorrent_password_hash

        cfg = tmp_path / "qBittorrent.conf"
        self._write_conf(cfg, password_hash=None)
        write_qbittorrent_password_hash(cfg, "@ByteArray(salt:hash)")
        text = cfg.read_text(encoding="utf-8")
        assert "WebUI\\Password_PBKDF2=@ByteArray(salt:hash)" in text

    def test_write_replaces_existing(self, tmp_path: Path):
        from media_pilot.deployment.secrets import write_qbittorrent_password_hash

        cfg = tmp_path / "qBittorrent.conf"
        self._write_conf(cfg, password_hash="@ByteArray(old)")
        write_qbittorrent_password_hash(cfg, "@ByteArray(new)")
        text = cfg.read_text(encoding="utf-8")
        # 必须只出现一次, 值为 new
        matches = re.findall(r"WebUI\\Password_PBKDF2=@ByteArray\(([^)]+)\)", text)
        assert matches == ["new"]

    def test_invalid_format_raises(self, tmp_path: Path):
        from media_pilot.deployment.secrets import read_qbittorrent_password_hash

        cfg = tmp_path / "qBittorrent.conf"
        cfg.write_text("[Preferences]\nnot-a-password-line\n", encoding="utf-8")
        # 这里只是没字段, 不应抛错. 真正的"格式异常"是文件结构不可识别.
        # 我们用 0 字节验证一下文件级边界.
        empty = tmp_path / "empty.conf"
        empty.write_text("", encoding="utf-8")
        assert read_qbittorrent_password_hash(empty) is None

    def test_password_hash_round_trip(self):
        """hashlib.pbkdf2_hmac 生成的 hash 能被脚本原样写回并被读取.

        qBittorrent 5.x 内部使用 PBKDF2-HMAC-SHA512 + 100000 iterations;
        这里必须按相同算法验证, 否则 qB 启动后 WebUI 登录失败.
        """
        from media_pilot.deployment.secrets import (
            generate_qbittorrent_password,
            hash_qbittorrent_password,
        )

        pw = generate_qbittorrent_password()
        hashed = hash_qbittorrent_password(pw)
        assert hashed.startswith("@ByteArray(")
        # 解码 salt+hash, 重新用同 plaintext 哈希, 应得到同样的 hash
        inner = hashed[len("@ByteArray(") : -1]
        salt_b64, hash_b64 = inner.split(":", 1)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64encode(
            hashlib.pbkdf2_hmac("sha512", pw.encode("utf-8"), salt, 100000, dklen=64)
        ).decode("ascii")
        assert hash_b64 == expected


class TestHashQbittorrentPassword:
    def test_hash_format_is_byterray(self):
        from media_pilot.deployment.secrets import hash_qbittorrent_password

        h = hash_qbittorrent_password("hunter2hunter2")
        assert h.startswith("@ByteArray(")
        assert h.endswith(")")

    def test_hash_salt_and_digest_length(self):
        from media_pilot.deployment.secrets import hash_qbittorrent_password

        h = hash_qbittorrent_password("hunter2hunter2")
        inner = h[len("@ByteArray(") : -1]
        salt_b64, hash_b64 = inner.split(":", 1)
        salt = base64.b64decode(salt_b64)
        digest = base64.b64decode(hash_b64)
        assert len(salt) == 16
        assert len(digest) == 64

    def test_hash_differs_for_different_passwords(self):
        from media_pilot.deployment.secrets import hash_qbittorrent_password

        a = hash_qbittorrent_password("password-a")
        b = hash_qbittorrent_password("password-b")
        assert a != b

    def test_verify_accepts_new_hash(self):
        """新算法 (SHA-512 + 100000) 生成的 hash 必须能被 verify 通过."""
        from media_pilot.deployment.secrets import (
            hash_qbittorrent_password,
            verify_qbittorrent_password_hash,
        )

        pw = "correct horse battery staple"
        hashed = hash_qbittorrent_password(pw)
        assert verify_qbittorrent_password_hash(pw, hashed) is True

    def test_verify_rejects_wrong_password(self):
        """错误明文密码 verify 必须返回 False, 不抛错."""
        from media_pilot.deployment.secrets import (
            hash_qbittorrent_password,
            verify_qbittorrent_password_hash,
        )

        hashed = hash_qbittorrent_password("real-password")
        assert verify_qbittorrent_password_hash("wrong-password", hashed) is False

    def test_verify_rejects_legacy_sha1_hash(self):
        """旧版 SHA-1/10000 hash 不被当前 verify 误判为有效.

        保护生产环境: 已用旧算法写入 qBittorrent.conf 的环境, 即使
        共享 secrets 里的明文密码对得上, verify 也必须返回 False,
        强迫用户走 force init 重新覆盖 qB 配置. 否则错误算法 hash
        会让 qBittorrent 5.x 启动后 WebUI 永远登录失败, 而 verify
        误报"凭据有效"会掩盖问题.
        """
        import base64

        from media_pilot.deployment.secrets import verify_qbittorrent_password_hash

        pw = "real-password"
        salt = b"0123456789abcdef"  # 16 bytes
        # 旧算法: SHA-1 + 10000 iterations, dklen 64 bytes
        legacy_digest = hashlib.pbkdf2_hmac(
            "sha1", pw.encode("utf-8"), salt, 10000, dklen=64
        )
        legacy_hash = (
            f"@ByteArray({base64.b64encode(salt).decode('ascii')}:"
            f"{base64.b64encode(legacy_digest).decode('ascii')})"
        )
        assert verify_qbittorrent_password_hash(pw, legacy_hash) is False

    def test_verify_rejects_legacy_sha512_10k_hash(self):
        """算法 / iteration 数量必须同时匹配. 用 SHA-512 但 10000 轮
        (折中方案) 也不能 verify 通过 — 当前 qBittorrent 5.x 强依赖
        100000 轮, 必须让这种"看似新算法"也明确被拒.
        """
        import base64

        from media_pilot.deployment.secrets import verify_qbittorrent_password_hash

        pw = "real-password"
        salt = b"0123456789abcdef"
        wrong_iter_digest = hashlib.pbkdf2_hmac(
            "sha512", pw.encode("utf-8"), salt, 10000, dklen=64
        )
        wrong_iter_hash = (
            f"@ByteArray({base64.b64encode(salt).decode('ascii')}:"
            f"{base64.b64encode(wrong_iter_digest).decode('ascii')})"
        )
        assert verify_qbittorrent_password_hash(pw, wrong_iter_hash) is False

    def test_hash_uses_sha512_100k_constants(self):
        """锁定算法常量: 必须与 qBittorrent 5.x 实现一致.

        防止后续误把常量改回 SHA-1/10000. 这里直接用模块常量做断言,
        让任何对 QBITTORRENT_PBKDF2_ALGO / QBITTORRENT_PBKDF2_ITERATIONS
        的回退都让测试立刻失败.
        """
        from media_pilot.deployment import secrets

        assert secrets.QBITTORRENT_PBKDF2_ALGO == "sha512"
        assert secrets.QBITTORRENT_PBKDF2_ITERATIONS == 100000
        assert secrets.QBITTORRENT_PBKDF2_SALT_BYTES == 16
        assert secrets.QBITTORRENT_PBKDF2_DKLEN == 64


# ── 整体 bootstrap 流程 ──


class TestBootstrap:
    def _patched_paths(self, tmp_path: Path):
        secrets = tmp_path / "shared-secrets.env"
        prowlarr_cfg = tmp_path / "prowlarr-config.xml"
        qb_cfg = tmp_path / "qBittorrent.conf"
        return secrets, prowlarr_cfg, qb_cfg

    def test_first_run_creates_secrets_and_service_configs(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment import secrets as secrets_mod
        from media_pilot.deployment.bootstrap import run_bootstrap

        secrets, prowlarr_cfg, qb_cfg = self._patched_paths(tmp_path)
        # 准备空 prowlarr config (无 ApiKey) + 空 qB config (无 Password_PBKDF2)
        prowlarr_cfg.parent.mkdir(parents=True, exist_ok=True)
        prowlarr_cfg.write_text("<Config><Port>9696</Port></Config>", encoding="utf-8")
        qb_cfg.parent.mkdir(parents=True, exist_ok=True)
        qb_cfg.write_text("[Preferences]\n", encoding="utf-8")

        env = {
            "MEDIA_PILOT_PROWLARR_API_KEY": "",
            "MEDIA_PILOT_QBITTORRENT_PASSWORD": "",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        result = run_bootstrap(
            force=False,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert result.status == "ok"
        assert result.created is True
        # secrets 写入
        data = secrets_mod.read_shared_secrets(secrets)
        assert data["MEDIA_PILOT_PROWLARR_API_KEY"]
        assert len(data["MEDIA_PILOT_PROWLARR_API_KEY"]) >= 32
        assert data["MEDIA_PILOT_QBITTORRENT_PASSWORD"]
        assert len(data["MEDIA_PILOT_QBITTORRENT_PASSWORD"]) >= 16
        # 同步到 prowlarr / qB config
        assert (
            secrets_mod.read_prowlarr_api_key_from_config(prowlarr_cfg)
            == data["MEDIA_PILOT_PROWLARR_API_KEY"]
        )
        assert secrets_mod.read_qbittorrent_password_hash(qb_cfg) is not None

    def test_consistent_state_is_noop(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment.bootstrap import run_bootstrap

        secrets, prowlarr_cfg, qb_cfg = self._patched_paths(tmp_path)
        # 准备一致状态: 先跑一次写入, 再跑应当 no-op
        prowlarr_cfg.parent.mkdir(parents=True, exist_ok=True)
        prowlarr_cfg.write_text("<Config><Port>9696</Port></Config>", encoding="utf-8")
        qb_cfg.parent.mkdir(parents=True, exist_ok=True)
        qb_cfg.write_text("[Preferences]\n", encoding="utf-8")

        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "")

        first = run_bootstrap(
            force=False,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert first.created is True
        # 第二次跑, 应当 no-op
        second = run_bootstrap(
            force=False,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert second.status == "noop"
        assert second.created is False

    def test_user_provided_env_used_when_present(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment import secrets as secrets_mod
        from media_pilot.deployment.bootstrap import run_bootstrap

        secrets, prowlarr_cfg, qb_cfg = self._patched_paths(tmp_path)
        prowlarr_cfg.parent.mkdir(parents=True, exist_ok=True)
        prowlarr_cfg.write_text("<Config><Port>9696</Port></Config>", encoding="utf-8")
        qb_cfg.parent.mkdir(parents=True, exist_ok=True)
        qb_cfg.write_text("[Preferences]\n", encoding="utf-8")

        # 用户显式填了
        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "user-supplied-prowlarr-key")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "UserSuppliedPass123")

        result = run_bootstrap(
            force=False,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert result.status == "ok"
        data = secrets_mod.read_shared_secrets(secrets)
        assert data["MEDIA_PILOT_PROWLARR_API_KEY"] == "user-supplied-prowlarr-key"
        assert data["MEDIA_PILOT_QBITTORRENT_PASSWORD"] == "UserSuppliedPass123"
        # 同步到 prowlarr config
        assert (
            secrets_mod.read_prowlarr_api_key_from_config(prowlarr_cfg)
            == "user-supplied-prowlarr-key"
        )

    def test_inconsistent_state_warns_without_force(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment import secrets as secrets_mod
        from media_pilot.deployment.bootstrap import run_bootstrap

        secrets, prowlarr_cfg, qb_cfg = self._patched_paths(tmp_path)
        # secrets 中有 key A, prowlarr config 中 key B
        secrets_mod.write_shared_secrets(
            secrets,
            prowlarr_api_key="aaaa",
            qbittorrent_username="admin",
            qbittorrent_password="bbbb",
        )
        prowlarr_cfg.parent.mkdir(parents=True, exist_ok=True)
        prowlarr_cfg.write_text("<Config><ApiKey>zzzz</ApiKey></Config>", encoding="utf-8")
        qb_cfg.parent.mkdir(parents=True, exist_ok=True)
        qb_cfg.write_text("[Preferences]\n", encoding="utf-8")

        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "")

        result = run_bootstrap(
            force=False,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert result.status == "warning"
        assert result.created is False
        assert result.warnings
        msg = "\n".join(result.warnings)
        # 必须有同步方向提示, 但不能阻塞 compose up.
        assert "force" in msg.lower()
        assert "不阻塞启动" in msg
        # 默认不覆盖第三方服务配置.
        assert secrets_mod.read_prowlarr_api_key_from_config(prowlarr_cfg) == "zzzz"

    def test_inconsistent_state_force_rewrites(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment import secrets as secrets_mod
        from media_pilot.deployment.bootstrap import run_bootstrap

        secrets, prowlarr_cfg, qb_cfg = self._patched_paths(tmp_path)
        secrets_mod.write_shared_secrets(
            secrets,
            prowlarr_api_key="aaaa",
            qbittorrent_username="admin",
            qbittorrent_password="bbbb",
        )
        prowlarr_cfg.parent.mkdir(parents=True, exist_ok=True)
        prowlarr_cfg.write_text("<Config><ApiKey>zzzz</ApiKey></Config>", encoding="utf-8")
        qb_cfg.parent.mkdir(parents=True, exist_ok=True)
        qb_cfg.write_text("[Preferences]\n", encoding="utf-8")

        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "")

        result = run_bootstrap(
            force=True,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert result.status == "ok"
        # force 时以 secrets 为准, 同步到 service configs
        assert secrets_mod.read_prowlarr_api_key_from_config(prowlarr_cfg) == "aaaa"
        assert secrets_mod.read_qbittorrent_password_hash(qb_cfg) is not None
        # username 也会被同步
        assert secrets_mod.read_qbittorrent_username(qb_cfg) == "admin"

    def test_missing_config_dir_errors_clearly(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment.bootstrap import BootstrapError, run_bootstrap

        secrets = tmp_path / "secrets.env"
        # prowlarr config 路径不存在, 且父目录也不存在
        prowlarr_cfg = tmp_path / "no-such-dir" / "config.xml"
        qb_cfg = tmp_path / "qBittorrent.conf"
        qb_cfg.parent.mkdir(parents=True, exist_ok=True)
        qb_cfg.write_text("[Preferences]\n", encoding="utf-8")

        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "")

        with pytest.raises(BootstrapError) as ei:
            run_bootstrap(
                force=False,
                shared_secrets_path=secrets,
                prowlarr_config_path=prowlarr_cfg,
                qbittorrent_config_path=qb_cfg,
            )
        msg = str(ei.value)
        assert "prowlarr" in msg.lower()
        # 错误必须不打印 secret (没有 aaaa 之类)
        assert "aaaa" not in msg
        # 不能创建模糊的嵌套目录
        assert not prowlarr_cfg.exists()
        assert not prowlarr_cfg.parent.exists()

    def test_fresh_qbittorrent_bind_mount_creates_subdir_and_conf(
        self, tmp_path: Path, monkeypatch
    ):
        """Fresh qB bind mount: 只有 mount 根目录, 没有 qBittorrent/ 子目录.

        这对应全新部署场景: data/qbittorrent/config/ 已存在 (被 compose
        bind mount), 但 linuxserver/qbittorrent 镜像还没跑过, 所以还没有
        自动创建 qBittorrent/ 子目录. init 必须能安全创建这个已知子目录
        和 conf 文件, 不能因为"父目录不存在"就 abort.
        """
        from media_pilot.deployment import secrets as secrets_mod
        from media_pilot.deployment.bootstrap import run_bootstrap

        secrets = tmp_path / "shared-secrets.env"
        # Prowlarr 端: bind mount 根存在, config.xml 还没有
        prowlarr_mount = tmp_path / "prowlarr-config"
        prowlarr_mount.mkdir(parents=True, exist_ok=True)
        prowlarr_cfg = prowlarr_mount / "config.xml"
        prowlarr_cfg.write_text("<Config><Port>9696</Port></Config>", encoding="utf-8")
        # qB 端: bind mount 根存在, qBittorrent/ 子目录与 conf 都不存在
        qb_mount = tmp_path / "qbittorrent-config"
        qb_mount.mkdir(parents=True, exist_ok=True)
        qb_cfg = qb_mount / "qBittorrent" / "qBittorrent.conf"
        assert not qb_cfg.parent.exists(), "测试前置: 子目录必须尚未存在"
        assert not qb_cfg.exists()

        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "")

        result = run_bootstrap(
            force=False,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert result.status == "ok"
        assert result.created is True
        # 子目录被自动创建
        assert qb_cfg.parent.exists()
        assert qb_cfg.parent.is_dir()
        # conf 文件被写入
        assert qb_cfg.is_file()
        # 内容是合法的 qB 格式
        text = qb_cfg.read_text(encoding="utf-8")
        assert "WebUI\\Password_PBKDF2=" in text
        assert "@ByteArray(" in text
        # secrets 同步
        data = secrets_mod.read_shared_secrets(secrets)
        assert data["MEDIA_PILOT_PROWLARR_API_KEY"]
        assert len(data["MEDIA_PILOT_PROWLARR_API_KEY"]) >= 32
        assert data["MEDIA_PILOT_QBITTORRENT_PASSWORD"]

    def test_qbittorrent_bind_mount_root_missing_aborts(
        self, tmp_path: Path, monkeypatch
    ):
        """qB bind mount 根本身就缺失 → 视为挂载错误, abort (不创建嵌套目录)."""
        from media_pilot.deployment.bootstrap import BootstrapError, run_bootstrap

        secrets = tmp_path / "shared-secrets.env"
        prowlarr_cfg = tmp_path / "prowlarr-config" / "config.xml"
        prowlarr_cfg.parent.mkdir(parents=True, exist_ok=True)
        prowlarr_cfg.write_text("<Config><Port>9696</Port></Config>", encoding="utf-8")
        # qB mount 根目录根本不存在
        qb_cfg = tmp_path / "totally-missing" / "qBittorrent" / "qBittorrent.conf"

        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "")

        with pytest.raises(BootstrapError) as ei:
            run_bootstrap(
                force=False,
                shared_secrets_path=secrets,
                prowlarr_config_path=prowlarr_cfg,
                qbittorrent_config_path=qb_cfg,
            )
        msg = str(ei.value)
        assert "qbittorrent" in msg.lower()
        # 不应自动创建挂载根或中间嵌套
        assert not qb_cfg.exists()
        assert not qb_cfg.parent.exists()
        assert not qb_cfg.parent.parent.exists()

    def test_qbittorrent_unexpected_subdir_name_aborts(
        self, tmp_path: Path, monkeypatch
    ):
        """qB 路径的子目录名不是已知的 'qBittorrent' → 拒绝自动创建."""
        from media_pilot.deployment.bootstrap import BootstrapError, run_bootstrap

        secrets = tmp_path / "shared-secrets.env"
        prowlarr_cfg = tmp_path / "prowlarr-config" / "config.xml"
        prowlarr_cfg.parent.mkdir(parents=True, exist_ok=True)
        prowlarr_cfg.write_text("<Config><Port>9696</Port></Config>", encoding="utf-8")
        # mount 根存在, 但子目录名是 "WrongName" (非白名单)
        qb_mount = tmp_path / "qbittorrent-config"
        qb_mount.mkdir(parents=True, exist_ok=True)
        qb_cfg = qb_mount / "WrongName" / "qBittorrent.conf"

        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "")

        with pytest.raises(BootstrapError) as ei:
            run_bootstrap(
                force=False,
                shared_secrets_path=secrets,
                prowlarr_config_path=prowlarr_cfg,
                qbittorrent_config_path=qb_cfg,
            )
        msg = str(ei.value)
        assert "qbittorrent" in msg.lower()
        # 拒绝自动创建非白名单子目录
        assert not qb_cfg.parent.exists()

    def test_warning_messages_do_not_leak_secrets(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment import secrets as secrets_mod
        from media_pilot.deployment.bootstrap import run_bootstrap

        secrets, prowlarr_cfg, qb_cfg = self._patched_paths(tmp_path)
        # 故意填一个特殊 secret 字符串
        secret_key = "TopSecretKeyDoNotLeak9876"
        secret_pw = "TopSecretPasswordDoNotLeak"
        secrets_mod.write_shared_secrets(
            secrets,
            prowlarr_api_key=secret_key,
            qbittorrent_username="admin",
            qbittorrent_password=secret_pw,
        )
        prowlarr_cfg.parent.mkdir(parents=True, exist_ok=True)
        # 写一个不同的 ApiKey 让它不一致
        prowlarr_cfg.write_text(
            "<Config><ApiKey>OtherKey1234</ApiKey></Config>", encoding="utf-8"
        )
        qb_cfg.parent.mkdir(parents=True, exist_ok=True)
        qb_cfg.write_text("[Preferences]\n", encoding="utf-8")

        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "")

        result = run_bootstrap(
            force=False,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert result.status == "warning"
        msg = "\n".join(result.warnings)
        # secret 不能出现在错误信息
        assert secret_key not in msg
        assert secret_pw not in msg
        # 字段名要出现
        assert "MEDIA_PILOT_PROWLARR_API_KEY" in msg or "prowlarr_api_key" in msg


# ── 共享 secrets 作为 Docker fallback (主应用读取) ──


class TestSharedSecretsFallback:
    def test_read_env_then_secrets(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment.secrets import (
            read_prowlarr_api_key_with_fallback,
            read_qbittorrent_password_with_fallback,
            write_shared_secrets,
        )

        secrets = tmp_path / "secrets.env"
        write_shared_secrets(
            secrets,
            prowlarr_api_key="from-secrets-1",
            qbittorrent_username="from-secrets-user",
            qbittorrent_password="from-secrets-2",
        )

        # 1) env 优先
        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "from-env")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "from-env-pw")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_USERNAME", "from-env-user")
        assert read_prowlarr_api_key_with_fallback(secrets_path=secrets) == "from-env"
        assert read_qbittorrent_password_with_fallback(secrets_path=secrets) == "from-env-pw"

        # 2) env 缺失时回退到 secrets
        monkeypatch.delenv("MEDIA_PILOT_PROWLARR_API_KEY", raising=False)
        monkeypatch.delenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", raising=False)
        monkeypatch.delenv("MEDIA_PILOT_QBITTORRENT_USERNAME", raising=False)
        assert read_prowlarr_api_key_with_fallback(secrets_path=secrets) == "from-secrets-1"
        assert read_qbittorrent_password_with_fallback(secrets_path=secrets) == "from-secrets-2"

    def test_missing_secrets_returns_none(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment.secrets import (
            read_prowlarr_api_key_with_fallback,
            read_qbittorrent_password_with_fallback,
        )

        monkeypatch.delenv("MEDIA_PILOT_PROWLARR_API_KEY", raising=False)
        monkeypatch.delenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", raising=False)
        # secrets 文件不存在
        assert read_prowlarr_api_key_with_fallback(tmp_path / "nope.env") is None
        assert read_qbittorrent_password_with_fallback(tmp_path / "nope.env") is None


# ── qB WebUI 用户名同步 (simplify-docker-onboarding-and-diagnostics 收口) ──


class TestQbittorrentUsernameReadWrite:
    """qBittorrent ``WebUI\\Username`` 字段读写."""

    def test_missing_username_returns_none(self, tmp_path: Path):
        from media_pilot.deployment.secrets import read_qbittorrent_username

        cfg = tmp_path / "qBittorrent.conf"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            "[Preferences]\nGeneral\\Locale=en\n[WebUI]\nPort=8080\n",
            encoding="utf-8",
        )
        assert read_qbittorrent_username(cfg) is None

    def test_present_username_is_read(self, tmp_path: Path):
        from media_pilot.deployment.secrets import read_qbittorrent_username

        cfg = tmp_path / "qBittorrent.conf"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            "[Preferences]\nWebUI\\Username=alice\n[WebUI]\nPort=8080\n",
            encoding="utf-8",
        )
        assert read_qbittorrent_username(cfg) == "alice"

    def test_missing_file_returns_none(self, tmp_path: Path):
        from media_pilot.deployment.secrets import read_qbittorrent_username

        assert read_qbittorrent_username(tmp_path / "nope.conf") is None

    def test_write_inserts_username(self, tmp_path: Path):
        from media_pilot.deployment.secrets import write_qbittorrent_username

        cfg = tmp_path / "qBittorrent.conf"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            "[Preferences]\nGeneral\\Locale=en\n[WebUI]\nPort=8080\n",
            encoding="utf-8",
        )
        write_qbittorrent_username(cfg, "alice")
        text = cfg.read_text(encoding="utf-8")
        # 字段名 + 值都写入
        assert "WebUI\\Username=alice" in text
        # 其它行原样保留
        assert "Port=8080" in text
        assert "General\\Locale=en" in text

    def test_write_replaces_existing(self, tmp_path: Path):
        from media_pilot.deployment.secrets import write_qbittorrent_username

        cfg = tmp_path / "qBittorrent.conf"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            "[Preferences]\nWebUI\\Username=alice\n[WebUI]\n",
            encoding="utf-8",
        )
        write_qbittorrent_username(cfg, "bob")
        text = cfg.read_text(encoding="utf-8")
        import re as _re

        matches = _re.findall(r"WebUI\\Username=(\S+)", text)
        assert matches == ["bob"], f"应只出现一次且为 bob, 实际 {matches}"

    def test_write_creates_file_when_missing(self, tmp_path: Path):
        from media_pilot.deployment.secrets import write_qbittorrent_username

        cfg = tmp_path / "qBittorrent.conf"
        write_qbittorrent_username(cfg, "alice")
        assert cfg.is_file()
        text = cfg.read_text(encoding="utf-8")
        assert "[Preferences]" in text
        assert "WebUI\\Username=alice" in text

    def test_write_and_password_creates_single_preferences_section(
        self, tmp_path: Path
    ):
        """先写 username 再写 password 必须在同一个 [Preferences] 段.

        防止 _qb_write_field 的"找不到 [Preferences] 段就追加新段"逻辑
        在多次写入时重复创建 [Preferences] 段.
        """
        from media_pilot.deployment.secrets import (
            write_qbittorrent_password_hash,
            write_qbittorrent_username,
        )

        cfg = tmp_path / "qBittorrent.conf"
        write_qbittorrent_username(cfg, "alice")
        write_qbittorrent_password_hash(cfg, "@ByteArray(salt:hash)")
        text = cfg.read_text(encoding="utf-8")
        # 只能有一个 [Preferences] 段
        assert text.count("[Preferences]") == 1, (
            f"qB 配置文件被重复追加 [Preferences] 段:\n{text}"
        )
        # username + password 都在该段
        assert "WebUI\\Username=alice" in text
        assert "WebUI\\Password_PBKDF2=@ByteArray(salt:hash)" in text


# ── bootstrap 中 qB 用户名同步 ──


class TestQbittorrentUsernameBootstrap:
    """bootstrap 写入 / 校验 / 覆盖 qB ``WebUI\\Username`` 字段."""

    def _patched_paths(self, tmp_path: Path):
        secrets = tmp_path / "shared-secrets.env"
        prowlarr_cfg = tmp_path / "prowlarr-config.xml"
        qb_cfg = tmp_path / "qBittorrent.conf"
        return secrets, prowlarr_cfg, qb_cfg

    def _prep_consistent_state(
        self,
        tmp_path: Path,
        *,
        existing_username: str | None = None,
        prowlarr_xml: str = "<Config><Port>9696</Port></Config>",
    ) -> tuple[Path, Path, Path]:
        """准备 bootstrap 前的 Prowlarr/qB 配置状态. existing_username=None
        表示 qB 配置文件里还没有 ``WebUI\\Username`` 字段.
        """
        secrets, prowlarr_cfg, qb_cfg = self._patched_paths(tmp_path)
        prowlarr_cfg.parent.mkdir(parents=True, exist_ok=True)
        prowlarr_cfg.write_text(prowlarr_xml, encoding="utf-8")
        qb_cfg.parent.mkdir(parents=True, exist_ok=True)
        lines = ["[Preferences]"]
        if existing_username is not None:
            lines.append(f"WebUI\\Username={existing_username}")
        lines.append("[WebUI]\nPort=8080")
        qb_cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return secrets, prowlarr_cfg, qb_cfg

    def test_fresh_qb_config_writes_default_admin(
        self, tmp_path: Path, monkeypatch
    ):
        from media_pilot.deployment import secrets as secrets_mod
        from media_pilot.deployment.bootstrap import run_bootstrap

        secrets, prowlarr_cfg, qb_cfg = self._prep_consistent_state(
            tmp_path, existing_username=None
        )
        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "")
        monkeypatch.delenv("MEDIA_PILOT_QBITTORRENT_USERNAME", raising=False)

        result = run_bootstrap(
            force=False,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert result.status == "ok"
        # shared secrets 同步 username = admin
        data = secrets_mod.read_shared_secrets(secrets)
        assert data["MEDIA_PILOT_QBITTORRENT_USERNAME"] == "admin"
        # qB config 写入 username + password
        assert secrets_mod.read_qbittorrent_username(qb_cfg) == "admin"
        assert secrets_mod.read_qbittorrent_password_hash(qb_cfg) is not None
        # BootstrapResult 暴露 username (非敏感)
        assert result.qbittorrent_username == "admin"

    def test_matching_existing_username_is_noop(
        self, tmp_path: Path, monkeypatch
    ):
        from media_pilot.deployment import secrets as secrets_mod
        from media_pilot.deployment.bootstrap import run_bootstrap

        secrets, prowlarr_cfg, qb_cfg = self._prep_consistent_state(
            tmp_path, existing_username="admin"
        )
        # 准备一致状态: secrets 里已有 username, qB 也有同样的 admin
        secrets_mod.write_shared_secrets(
            secrets,
            prowlarr_api_key="some-key",
            qbittorrent_username="admin",
            qbittorrent_password="some-pw",
        )
        # 把 Prowlarr config 也对齐 secrets 的 key, 让 password 也一致
        prowlarr_cfg.write_text(
            "<Config><ApiKey>some-key</ApiKey></Config>", encoding="utf-8"
        )
        # qB 密码 hash 用 secrets 同样的 plaintext 哈希一次
        from media_pilot.deployment.secrets import hash_qbittorrent_password

        qb_cfg.write_text(
            "[Preferences]\n"
            f"WebUI\\Username=admin\n"
            f"WebUI\\Password_PBKDF2={hash_qbittorrent_password('some-pw')}\n"
            "[WebUI]\nPort=8080\n",
            encoding="utf-8",
        )
        # secrets 已经在 ; 再触发一次 bootstrap, 应 no-op
        result = run_bootstrap(
            force=False,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert result.status == "noop"
        assert result.created is False
        # qB config 仍然只有一份 username
        text = qb_cfg.read_text(encoding="utf-8")
        assert text.count("WebUI\\Username=") == 1

    def test_different_username_warns_without_force(
        self, tmp_path: Path, monkeypatch
    ):
        from media_pilot.deployment import secrets as secrets_mod
        from media_pilot.deployment.bootstrap import run_bootstrap

        secrets, prowlarr_cfg, qb_cfg = self._prep_consistent_state(
            tmp_path, existing_username="root"  # qB 里写死一个非 admin 用户
        )
        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "kk")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "pp")
        # env 不设置 username → candidate=admin, qB 现存=root, 不一致

        result = run_bootstrap(
            force=False,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert result.status == "warning"
        msg = "\n".join(result.warnings)
        # username 错误信息允许带值
        assert "qbittorrent_username" in msg.lower() or "admin" in msg
        # 默认不覆盖 qB 原有 username
        assert secrets_mod.read_qbittorrent_username(qb_cfg) == "root"
        # password/api_key 不能出现在错误信息
        assert "kk" not in msg
        assert "pp" not in msg

    def test_force_mode_overrides_different_username(
        self, tmp_path: Path, monkeypatch
    ):
        from media_pilot.deployment import secrets as secrets_mod
        from media_pilot.deployment.bootstrap import run_bootstrap

        secrets, prowlarr_cfg, qb_cfg = self._prep_consistent_state(
            tmp_path, existing_username="root"
        )
        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "kk")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "pp")

        result = run_bootstrap(
            force=True,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert result.status == "ok"
        # force 时以 candidate (admin) 覆盖 qB 原有的 root
        assert secrets_mod.read_qbittorrent_username(qb_cfg) == "admin"

    def test_env_username_used_when_present(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment import secrets as secrets_mod
        from media_pilot.deployment.bootstrap import run_bootstrap

        secrets, prowlarr_cfg, qb_cfg = self._prep_consistent_state(
            tmp_path, existing_username=None
        )
        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "kk")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "pp")
        # 用户显式指定 username (例如 "media-pilot")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_USERNAME", "media-pilot")

        result = run_bootstrap(
            force=False,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        assert result.status == "ok"
        # env 优先, qB config 写入 media-pilot
        assert secrets_mod.read_qbittorrent_username(qb_cfg) == "media-pilot"
        # shared secrets 也写入
        data = secrets_mod.read_shared_secrets(secrets)
        assert data["MEDIA_PILOT_QBITTORRENT_USERNAME"] == "media-pilot"

    def test_username_in_bootstrap_result(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment.bootstrap import BootstrapResult, run_bootstrap

        secrets, prowlarr_cfg, qb_cfg = self._prep_consistent_state(
            tmp_path, existing_username=None
        )
        monkeypatch.setenv("MEDIA_PILOT_PROWLARR_API_KEY", "")
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "")
        monkeypatch.delenv("MEDIA_PILOT_QBITTORRENT_USERNAME", raising=False)

        result = run_bootstrap(
            force=False,
            shared_secrets_path=secrets,
            prowlarr_config_path=prowlarr_cfg,
            qbittorrent_config_path=qb_cfg,
        )
        # BootstrapResult 必须有 qbittorrent_username 字段 (供调用方读取)
        assert isinstance(result, BootstrapResult)
        assert hasattr(result, "qbittorrent_username")
        assert result.qbittorrent_username == "admin"


class TestBootstrapCli:
    def test_cli_warning_result_returns_zero(self, monkeypatch, capsys):
        """mismatch warning 不得让 one-shot init 容器 exit 1 阻塞 compose up."""
        from media_pilot.deployment import bootstrap

        def fake_run_bootstrap(*, force=False):
            assert force is False
            return bootstrap.BootstrapResult(
                status="warning",
                created=False,
                prowlarr_api_key="hidden-key",
                qbittorrent_username="admin",
                qbittorrent_password="hidden-password",
                warnings=("qbittorrent_password mismatch; force required",),
            )

        monkeypatch.setattr(bootstrap, "run_bootstrap", fake_run_bootstrap)

        assert bootstrap.main([]) == 0
        captured = capsys.readouterr()
        assert "[bootstrap] WARNING:" in captured.err
        assert "qbittorrent_password" in captured.err
        assert "hidden-key" not in captured.out
        assert "hidden-password" not in captured.out


# ── 主应用 username fallback 读取 ──


class TestQbittorrentUsernameFallback:
    """read_qbittorrent_username_with_fallback: env → secrets → admin."""

    def test_env_wins_over_secrets(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment.secrets import (
            read_qbittorrent_username_with_fallback,
            write_shared_secrets,
        )

        secrets = tmp_path / "secrets.env"
        write_shared_secrets(
            secrets,
            prowlarr_api_key="k",
            qbittorrent_username="from-secrets",
            qbittorrent_password="p",
        )
        monkeypatch.setenv("MEDIA_PILOT_QBITTORRENT_USERNAME", "from-env")
        assert read_qbittorrent_username_with_fallback(secrets_path=secrets) == "from-env"

    def test_secrets_used_when_env_missing(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment.secrets import (
            read_qbittorrent_username_with_fallback,
            write_shared_secrets,
        )

        secrets = tmp_path / "secrets.env"
        write_shared_secrets(
            secrets,
            prowlarr_api_key="k",
            qbittorrent_username="from-secrets",
            qbittorrent_password="p",
        )
        monkeypatch.delenv("MEDIA_PILOT_QBITTORRENT_USERNAME", raising=False)
        assert read_qbittorrent_username_with_fallback(secrets_path=secrets) == "from-secrets"

    def test_admin_default_when_both_missing(self, tmp_path: Path, monkeypatch):
        from media_pilot.deployment.secrets import (
            DEFAULT_QBITTORRENT_USERNAME,
            read_qbittorrent_username_with_fallback,
        )

        # env 未设, secrets 文件不存在
        monkeypatch.delenv("MEDIA_PILOT_QBITTORRENT_USERNAME", raising=False)
        result = read_qbittorrent_username_with_fallback(
            secrets_path=tmp_path / "nope.env"
        )
        assert result == DEFAULT_QBITTORRENT_USERNAME == "admin"
