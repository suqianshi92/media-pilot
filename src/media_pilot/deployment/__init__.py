"""部署初始化 (Docker Compose one-shot 入口) — 简化首次部署诊断.

锁定 (simplify-docker-onboarding-and-diagnostics):
- 共享 secrets 文件读写, 字段值域 (Prowlarr API Key / qB 密码 / qB 用户名) 校验.
- 凭据生成规则: 不使用固定公开默认值 (username 例外, 默认 ``admin``).
- 主应用 fallback 读取: 共享 secrets 仅在 env 缺失时使用.
"""

from media_pilot.deployment.bootstrap import (
    BootstrapError,
    BootstrapResult,
    run_bootstrap,
)
from media_pilot.deployment.secrets import (
    DEFAULT_PROWLARR_CONFIG_PATH,
    DEFAULT_QBITTORRENT_CONFIG_PATH,
    DEFAULT_QBITTORRENT_USERNAME,
    DEFAULT_SHARED_SECRETS_PATH,
    generate_prowlarr_api_key,
    generate_qbittorrent_password,
    hash_qbittorrent_password,
    read_prowlarr_api_key_from_config,
    read_prowlarr_api_key_with_fallback,
    read_qbittorrent_password_hash,
    read_qbittorrent_password_with_fallback,
    read_qbittorrent_username,
    read_qbittorrent_username_with_fallback,
    read_shared_secrets,
    verify_qbittorrent_password_hash,
    write_prowlarr_api_key_to_config,
    write_qbittorrent_password_hash,
    write_qbittorrent_username,
    write_shared_secrets,
)

__all__ = [
    "BootstrapError",
    "BootstrapResult",
    "DEFAULT_PROWLARR_CONFIG_PATH",
    "DEFAULT_QBITTORRENT_CONFIG_PATH",
    "DEFAULT_QBITTORRENT_USERNAME",
    "DEFAULT_SHARED_SECRETS_PATH",
    "generate_prowlarr_api_key",
    "generate_qbittorrent_password",
    "hash_qbittorrent_password",
    "read_prowlarr_api_key_from_config",
    "read_prowlarr_api_key_with_fallback",
    "read_qbittorrent_password_hash",
    "read_qbittorrent_password_with_fallback",
    "read_qbittorrent_username",
    "read_qbittorrent_username_with_fallback",
    "read_shared_secrets",
    "run_bootstrap",
    "verify_qbittorrent_password_hash",
    "write_prowlarr_api_key_to_config",
    "write_qbittorrent_password_hash",
    "write_qbittorrent_username",
    "write_shared_secrets",
]
