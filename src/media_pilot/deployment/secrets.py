"""部署 secrets 模块 — 共享 secrets 文件 + Prowlarr / qBittorrent 配置读写.

锁定 (simplify-docker-onboarding-and-diagnostics):
- 共享 secrets 文件路径: /data/shared-secrets.env (chmod 0o600, 不在错误信息中暴露值).
- Prowlarr `config.xml` 读写, 缺失 ApiKey 时返回 None, 写入保留其它字段.
- qBittorrent `qBittorrent.conf` 读写, PBKDF2 hash 通过 hashlib.pbkdf2_hmac 构造.
- qBittorrent WebUI 用户名同步: media-pilot 默认 ``admin``; env / shared
  secrets 显式覆盖时 bootstrap 与主应用都用覆盖值.
- fallback 读取: env 优先, env 缺失时回退到 shared secrets; secrets 缺失时
  返回 None (非 Docker 场景继续按现状运行).
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import string
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# ── 路径默认值 (Docker 容器内, 与 docker-compose.yml 保持一致) ──

# /data 在容器内是统一持久化目录 (由 docker-compose volume 挂载).
# 共享 secrets 文件与 Prowlarr/qB 配置不在同一卷下, init 容器必须同时
# 挂载 /data/shared-secrets.env 与第三方服务的 /config.
DEFAULT_SHARED_SECRETS_PATH = Path("/data/shared-secrets.env")
DEFAULT_PROWLARR_CONFIG_PATH = Path("/config/config.xml")
DEFAULT_QBITTORRENT_CONFIG_PATH = Path("/config/qBittorrent/qBittorrent.conf")

# qB 镜像 (linuxserver/qbittorrent) 在 bind mount 根下创建 ``qBittorrent/``
# 子目录, 把 ``qBittorrent.conf`` 放在里面. init 容器必须知道这个固定子
# 目录, 才能在 fresh bind mount (只有 mount 根, 没有 ``qBittorrent/`` 子目
# 录) 时安全创建子目录. 但不允许在挂载根本身缺失时瞎建嵌套目录 — 那意味
# 着 compose 挂载错误, 必须 abort 让用户看到提示.
KNOWN_QBITTORRENT_SUBDIR = "qBittorrent"

# init 容器内 Prowlarr / qB 配置目录的环境变量覆盖, 允许 compose 把
# 第三方配置卷挂到非 /config 路径 (避免与 Prowlarr 镜像默认路径冲突).
ENV_PROWLARR_CONFIG_DIR = "MEDIA_PILOT_BOOTSTRAP_PROWLARR_CONFIG_DIR"
ENV_QBITTORRENT_CONFIG_DIR = "MEDIA_PILOT_BOOTSTRAP_QBITTORRENT_CONFIG_DIR"
ENV_SHARED_SECRETS_PATH = "MEDIA_PILOT_SHARED_SECRETS_PATH"


def resolve_prowlarr_config_path() -> Path:
    """解析 Prowlarr config.xml 实际路径, 允许 env 覆盖.

    优先级: env 显式目录 → 默认 /config/config.xml.
    """
    override_dir = os.getenv(ENV_PROWLARR_CONFIG_DIR)
    if override_dir:
        return Path(override_dir) / "config.xml"
    return DEFAULT_PROWLARR_CONFIG_PATH


def resolve_qbittorrent_config_path() -> Path:
    override_dir = os.getenv(ENV_QBITTORRENT_CONFIG_DIR)
    if override_dir:
        return Path(override_dir) / "qBittorrent" / "qBittorrent.conf"
    return DEFAULT_QBITTORRENT_CONFIG_PATH


def resolve_shared_secrets_path() -> Path:
    override = os.getenv(ENV_SHARED_SECRETS_PATH)
    if override:
        return Path(override)
    return DEFAULT_SHARED_SECRETS_PATH

# ── 凭据生成规则 ──

# Prowlarr 内部使用 32+ 字节 hex 串作为 API Key, 这里固定 32 字节 (64 hex)
# 足以满足 Prowlarr 校验且符合 design.md 最低要求.
PROWLARR_API_KEY_BYTES = 32
# qB 密码: 24 字符 (字母 + 数字) 提供高熵, 满足 design.md 长度与随机性要求.
QBITTORRENT_PASSWORD_LENGTH = 24
# qBittorrent PBKDF2 格式: 16 bytes salt + 64 bytes digest, 100000 轮 SHA-512.
# qBittorrent 5.x (linuxserver 镜像 >=5.0) 改用 PBKDF2-HMAC-SHA512 + 100000
# iterations, 与早期 SHA-1/10000 不兼容. 用错算法写入的 WebUI\Password_PBKDF2
# 字段会让 qB 5.2 启动后 WebUI 登录全部失败 ("Bad credentials"), 因此
# 算法常量与 hash/verify 实现必须保持一致.
QBITTORRENT_PBKDF2_ALGO = "sha512"
QBITTORRENT_PBKDF2_ITERATIONS = 100000
QBITTORRENT_PBKDF2_SALT_BYTES = 16
QBITTORRENT_PBKDF2_DKLEN = 64

# qBittorrent WebUI 默认用户名. 镜像首次启动会生成一个临时 admin 密码, 但
# 用户名始终是 ``admin``; 我们在 shared secrets 中也以 ``admin`` 为默认
# 候选, 与 ``MEDIA_PILOT_QBITTORRENT_USERNAME`` 不填写时一致.
DEFAULT_QBITTORRENT_USERNAME = "admin"


def generate_prowlarr_api_key() -> str:
    """生成 32 字节 hex Prowlarr API Key.

    Prowlarr 默认使用 32+ 字节十六进制 token; 这里直接用 32 字节熵,
    64 hex 字符足以满足 Prowlarr 校验.
    """
    return secrets.token_hex(PROWLARR_API_KEY_BYTES)


def generate_qbittorrent_password() -> str:
    """生成 24 字符高熵 qBittorrent WebUI 密码 (字母 + 数字)."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(QBITTORRENT_PASSWORD_LENGTH))


def hash_qbittorrent_password(plaintext: str) -> str:
    r"""生成 qBittorrent WebUI\Password_PBKDF2 字段值 (PBKDF2-HMAC-SHA512, 100000 轮).

    输出格式: ``@ByteArray(<base64 salt>:<base64 hash>)``.

    qBittorrent 5.x 内部以 PBKDF2-HMAC-SHA512 + 100000 iterations 计算
    WebUI 密码摘要, 旧版 SHA-1/10000 算法写入的字段会导致 5.x 启动后
    登录全部失败. 必须用与 qBittorrent 镜像一致的算法常量.
    """
    salt = secrets.token_bytes(QBITTORRENT_PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        QBITTORRENT_PBKDF2_ALGO,
        plaintext.encode("utf-8"),
        salt,
        QBITTORRENT_PBKDF2_ITERATIONS,
        dklen=QBITTORRENT_PBKDF2_DKLEN,
    )
    return f"@ByteArray({base64.b64encode(salt).decode('ascii')}:{base64.b64encode(digest).decode('ascii')})"


def verify_qbittorrent_password_hash(plaintext: str, hash_value: str) -> bool:
    """校验 plaintext 是否匹配 qBittorrent `WebUI\\Password_PBKDF2` 字段值.

    通过 base64 解码 salt, 用相同 plaintext 重新 PBKDF2 (SHA-512 + 100000),
    与 hash 比对. 格式异常或参数不合法时返回 False, 不抛错.

    注意: 这里不兼容旧 SHA-1/10000 算法生成的 hash. qBittorrent 5.x
    WebUI 也不会接受旧 hash (登录失败), 因此 verify 拒绝旧 hash 与
    qBittorrent 自身行为一致. 已写入旧 hash 的环境必须用 force init
    (MEDIA_PILOT_BOOTSTRAP_FORCE=1) 重新覆盖 qBittorrent.conf 并重启
    qB 容器, 否则 5.x WebUI 永远无法登录.
    """
    if not hash_value.startswith("@ByteArray(") or not hash_value.endswith(")"):
        return False
    inner = hash_value[len("@ByteArray(") : -1]
    if ":" not in inner:
        return False
    salt_b64, hash_b64 = inner.split(":", 1)
    try:
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
    except (ValueError, base64.binascii.Error):
        return False
    if len(salt) != QBITTORRENT_PBKDF2_SALT_BYTES:
        return False
    if len(expected) != QBITTORRENT_PBKDF2_DKLEN:
        return False
    actual = hashlib.pbkdf2_hmac(
        QBITTORRENT_PBKDF2_ALGO,
        plaintext.encode("utf-8"),
        salt,
        QBITTORRENT_PBKDF2_ITERATIONS,
        dklen=QBITTORRENT_PBKDF2_DKLEN,
    )
    return secrets.compare_digest(actual, expected)


# ── 共享 secrets 文件读写 ──


def write_shared_secrets(
    path: Path,
    *,
    prowlarr_api_key: str,
    qbittorrent_username: str,
    qbittorrent_password: str,
) -> None:
    """原子写入共享 secrets 文件 (mode 0o600).

    字段名固定, 顺序稳定, 方便人工 cat 检查 (但 chmod 0o600 限制读取).
    qbittorrent_username 是必填参数, 强制调用方显式提供 — username 不像
    password 是高熵随机值, 写错 (例如写成空串) 会让 qB 登录失败, 因此不
    允许隐式默认.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# Auto-generated by media-pilot deployment bootstrap. Do not edit by hand.\n"
        f"MEDIA_PILOT_PROWLARR_API_KEY={prowlarr_api_key}\n"
        f"MEDIA_PILOT_QBITTORRENT_USERNAME={qbittorrent_username}\n"
        f"MEDIA_PILOT_QBITTORRENT_PASSWORD={qbittorrent_password}\n"
    )
    # Write then chmod (file may not exist; os.open with O_CREAT | O_WRONLY | O_TRUNC).
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, body.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


_SHARED_SECRETS_KEYS: frozenset[str] = frozenset({
    "MEDIA_PILOT_PROWLARR_API_KEY",
    "MEDIA_PILOT_QBITTORRENT_USERNAME",
    "MEDIA_PILOT_QBITTORRENT_PASSWORD",
})


def read_shared_secrets(path: Path) -> dict[str, str]:
    """读取共享 secrets 文件. 缺失或不可读时返回空 dict."""
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in _SHARED_SECRETS_KEYS:
            result[key] = value.strip()
    return result


# ── Prowlarr config.xml 读写 ──


def read_prowlarr_api_key_from_config(path: Path) -> str | None:
    """读取 Prowlarr `<ApiKey>` 字段值. 缺失或文件不存在时返回 None.

    格式异常时抛 ValueError, 由调用方决定如何处理.
    """
    if not path.is_file():
        return None
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except ET.ParseError as exc:
        raise ValueError(f"Prowlarr 配置 XML 解析失败 ({path}): {exc}") from exc
    elem = root.find("ApiKey")
    if elem is None or elem.text is None:
        return None
    value = elem.text.strip()
    return value or None


def write_prowlarr_api_key_to_config(path: Path, api_key: str) -> None:
    """写入 Prowlarr `<ApiKey>`, 已有则替换; 其它字段保留.

    写入顺序: 把 ApiKey 节点放到与现有节点同样的父节点 (root) 中. 若文件
    不存在则新建最小 config. 写入使用 indent 保持可读.
    """
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        root = ET.Element("Config")
        ET.SubElement(root, "ApiKey").text = api_key
    else:
        try:
            root = ET.fromstring(path.read_text(encoding="utf-8"))
        except ET.ParseError as exc:
            raise ValueError(f"Prowlarr 配置 XML 解析失败 ({path}): {exc}") from exc
        elem = root.find("ApiKey")
        if elem is None:
            # 把 ApiKey 插到首位, 保持与原始 Prowlarr config 风格一致.
            new_elem = ET.Element("ApiKey")
            new_elem.text = api_key
            root.insert(0, new_elem)
        else:
            elem.text = api_key

    _write_xml(path, root)


def _write_xml(path: Path, root: ET.Element) -> None:
    """写入 ElementTree, 保持与 Prowlarr 原始风格 (单行 tag, 无命名空间)."""
    body = ET.tostring(root, encoding="unicode")
    # 去掉 ET 默认 namespace prefix, 简单还原 Prowlarr 风格 <Config>...</Config>.
    path.write_text(body, encoding="utf-8")


# ── qBittorrent config 读写 ──


_QB_PASSWORD_LINE_RE = re.compile(r"^WebUI\\Password_PBKDF2\s*=\s*(.*)$")
_QB_USERNAME_LINE_RE = re.compile(r"^WebUI\\Username\s*=\s*(.*)$")


def _qb_read_field(path: Path, line_re: re.Pattern[str]) -> str | None:
    """读取 qB conf 中第一个匹配 regex 的字段值. 文件不存在或字段缺失返回 None.

    qB 配置文件为 INI 风格, 我们按行扫描, 不引入额外 INI 解析依赖.
    """
    if not path.is_file():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        m = line_re.match(raw_line)
        if m is None:
            continue
        value = m.group(1).strip()
        return value or None
    return None


def _qb_write_field(path: Path, line_re: re.Pattern[str], new_line: str) -> None:
    """写入 qB conf 字段. 已有匹配行 → 替换; [Preferences] 段尾 → 插入;
    无 [Preferences] 段 → 追加新段. 其它行原样保留.

    把 username / password 抽成同一个 helper, 保证多次写同一文件时不会
    重复插入 [Preferences] 段.
    """
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"[Preferences]\n{new_line}\n",
            encoding="utf-8",
        )
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    # 1) 已有匹配行 → 替换
    for i, line in enumerate(lines):
        if line_re.match(line):
            lines[i] = new_line
            break
    else:
        # 2) 找 [Preferences] 段, 把字段插到段尾; 段尾定义为本段最后一个
        #    非 [Section] 行的下一行 (即下一个 [Section] 之前).
        prefs_start = -1
        prefs_end = len(lines)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "[Preferences]":
                prefs_start = i
            elif prefs_start >= 0 and stripped.startswith("[") and stripped.endswith("]"):
                prefs_end = i
                break
        if prefs_start >= 0:
            lines.insert(prefs_end, new_line)
        else:
            # 3) 文件里没有 [Preferences] 段: 追加新段.
            if lines and lines[-1].strip() != "":
                lines.append("")
            lines.append("[Preferences]")
            lines.append(new_line)
    # 保留末尾换行
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_qbittorrent_password_hash(path: Path) -> str | None:
    """读取 qBittorrent `WebUI\\Password_PBKDF2` 字段值.

    文件不存在或字段缺失时返回 None. 文件存在但格式异常时返回 None
    (允许空文件, 不抛错, 因为 qB 配置存在多种合法格式).
    """
    return _qb_read_field(path, _QB_PASSWORD_LINE_RE)


def write_qbittorrent_password_hash(path: Path, hash_value: str) -> None:
    """写入 `WebUI\\Password_PBKDF2`, 已有则替换; 其它行原样保留."""
    _qb_write_field(
        path, _QB_PASSWORD_LINE_RE, f"WebUI\\Password_PBKDF2={hash_value}"
    )


def read_qbittorrent_username(path: Path) -> str | None:
    """读取 qBittorrent `WebUI\\Username` 字段值. 字段缺失返回 None."""
    return _qb_read_field(path, _QB_USERNAME_LINE_RE)


def write_qbittorrent_username(path: Path, username: str) -> None:
    """写入 `WebUI\\Username`, 已有则替换; 其它行原样保留.

    qB 镜像默认用户名 ``admin``; 我们只在字段缺失或与候选不同时写, 由
    bootstrap 的 ``_check_or_apply_service`` 决定具体行为.
    """
    _qb_write_field(path, _QB_USERNAME_LINE_RE, f"WebUI\\Username={username}")


# ── 主应用 fallback 读取 (env 优先, 缺失时回退到共享 secrets) ──


def read_prowlarr_api_key_with_fallback(
    secrets_path: Path | None = None,
) -> str | None:
    """主应用读取 Prowlarr API Key: env 优先, 缺失时回退到共享 secrets.

    共享 secrets 文件也不存在时返回 None (非 Docker 场景保持现有行为).
    """
    env_value = os.getenv("MEDIA_PILOT_PROWLARR_API_KEY")
    if env_value:
        return env_value
    path = secrets_path or resolve_shared_secrets_path()
    data = read_shared_secrets(path)
    return data.get("MEDIA_PILOT_PROWLARR_API_KEY")


def read_qbittorrent_password_with_fallback(
    secrets_path: Path | None = None,
) -> str | None:
    """主应用读取 qBittorrent 密码: env 优先, 缺失时回退到共享 secrets."""
    env_value = os.getenv("MEDIA_PILOT_QBITTORRENT_PASSWORD")
    if env_value:
        return env_value
    path = secrets_path or resolve_shared_secrets_path()
    data = read_shared_secrets(path)
    return data.get("MEDIA_PILOT_QBITTORRENT_PASSWORD")


def read_qbittorrent_username_with_fallback(
    secrets_path: Path | None = None,
) -> str:
    """主应用读取 qBittorrent WebUI 用户名: env 优先 → 共享 secrets → admin 默认.

    与 password fallback 不同, 这里永远返回一个非空字符串, 因为 qB 镜像
    默认用户名始终是 ``admin``, 而下载提交逻辑依赖具体字符串来构造登录
    请求. 把缺失也视为 ``admin`` 避免下游再次 fallback, 减少出错面.
    """
    env_value = os.getenv("MEDIA_PILOT_QBITTORRENT_USERNAME")
    if env_value:
        return env_value
    path = secrets_path or resolve_shared_secrets_path()
    data = read_shared_secrets(path)
    return data.get("MEDIA_PILOT_QBITTORRENT_USERNAME") or DEFAULT_QBITTORRENT_USERNAME
