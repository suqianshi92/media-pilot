"""部署 bootstrap 编排 — 串联凭据生成 / 共享 secrets 同步 / 第三方配置写入.

锁定 (simplify-docker-onboarding-and-diagnostics):
- 仅在 Docker Compose one-shot 容器内执行, 不要求宿主机 Python.
- 第一次运行: env 缺失时生成凭据, 同步到共享 secrets + Prowlarr/qB 配置.
- 反复运行: 凭据一致时 no-op; 不一致时默认不覆盖且不阻塞启动,
  显式 force=True 时以 shared secrets 为准覆盖第三方配置.
- 错误信息只含字段名 / 路径 / 同步方向 / username, 不得打印 secret 值
  (password / api key).
- qB 用户名同步: 与密码同等地位, 缺失时写入 ``admin``; 已有且与候选不
  同时默认跳过并 warning, force 时覆盖.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from media_pilot.deployment.secrets import (
    DEFAULT_QBITTORRENT_USERNAME,
    DEFAULT_SHARED_SECRETS_PATH,
    KNOWN_QBITTORRENT_SUBDIR,
    generate_prowlarr_api_key,
    generate_qbittorrent_password,
    hash_qbittorrent_password,
    read_prowlarr_api_key_from_config,
    read_qbittorrent_password_hash,
    read_qbittorrent_username,
    read_shared_secrets,
    resolve_prowlarr_config_path,
    resolve_qbittorrent_config_path,
    resolve_shared_secrets_path,
    verify_qbittorrent_password_hash,
    write_prowlarr_api_key_to_config,
    write_qbittorrent_password_hash,
    write_qbittorrent_username,
    write_shared_secrets,
)


class BootstrapError(RuntimeError):
    """Bootstrap 失败 — 必须 abort 容器启动, 让用户看到清晰错误."""


@dataclass(frozen=True)
class BootstrapResult:
    status: str  # "ok" | "noop" | "warning"
    created: bool  # True 表示本次运行生成了新凭据
    prowlarr_api_key: str  # 注意: 不直接 log 输出, 仅作结果对象
    qbittorrent_username: str  # username 不是高熵随机值, 错误信息可以带
    qbittorrent_password: str  # 同上, 任何日志路径都不能 print 这个对象
    warnings: tuple[str, ...] = ()


def run_bootstrap(
    *,
    force: bool = False,
    shared_secrets_path: Path | None = None,
    prowlarr_config_path: Path | None = None,
    qbittorrent_config_path: Path | None = None,
) -> BootstrapResult:
    """执行凭据同步.

    策略:
    1. 收集 candidate 凭据 = env (优先) 或 secrets (回退), 缺失则生成/默认.
    2. 读 Prowlarr / qB 服务配置中现存值.
    3. 若 Prowlarr/qB 现存值缺失 → 写入.
       若现存值 = candidate → no-op.
       若现存值 ≠ candidate 且 force=False → 保留服务配置, 记录 warning.
       若 force=True → 以 candidate 为准覆盖第三方配置.
    4. 把 candidate 同步到 shared secrets (始终以 candidate 为准). 若有
       mismatch warning, 共享 secrets 可能仍与服务配置不一致; 用户需显式
       force 才能修复, 但 init 不阻塞主应用启动.
    """
    secrets_path = shared_secrets_path or resolve_shared_secrets_path()
    prowlarr_cfg = prowlarr_config_path or resolve_prowlarr_config_path()
    qb_cfg = qbittorrent_config_path or resolve_qbittorrent_config_path()

    existing = read_shared_secrets(secrets_path)

    candidate_prowlarr = _resolve_prowlarr_candidate(
        existing.get("MEDIA_PILOT_PROWLARR_API_KEY")
    )
    candidate_qb_username = _resolve_qb_username_candidate(
        existing.get("MEDIA_PILOT_QBITTORRENT_USERNAME")
    )
    candidate_qb_password = _resolve_qb_password_candidate(
        existing.get("MEDIA_PILOT_QBITTORRENT_PASSWORD")
    )

    # 检查第三方服务配置路径是否存在
    _ensure_prowlarr_path_writable(prowlarr_cfg, label="prowlarr")
    _ensure_qbittorrent_path_writable(qb_cfg, label="qbittorrent")

    prowlarr_service = read_prowlarr_api_key_from_config(prowlarr_cfg)
    qb_password_service = read_qbittorrent_password_hash(qb_cfg)
    qb_username_service = read_qbittorrent_username(qb_cfg)

    # 决定是否首次创建: 共享 secrets 与第三方服务配置都为空才算首次.
    prowlarr_matches = (
        prowlarr_service is not None and prowlarr_service == candidate_prowlarr
    )
    qb_password_matches = (
        qb_password_service is not None
        and verify_qbittorrent_password_hash(candidate_qb_password, qb_password_service)
    )
    qb_username_matches = (
        qb_username_service is not None
        and qb_username_service == candidate_qb_username
    )
    created = (
        existing == {}
        and prowlarr_service is None
        and qb_password_service is None
        and qb_username_service is None
    )
    is_noop = (
        not created
        and prowlarr_matches
        and qb_password_matches
        and qb_username_matches
    )

    # 一致性校验 / 覆盖
    warnings: list[str] = []

    warning = _check_or_apply_service(
        label="prowlarr_api_key",
        candidate=candidate_prowlarr,
        service_value=prowlarr_service,
        service_path=prowlarr_cfg,
        write_service=write_prowlarr_api_key_to_config,
        force=force,
        service_matches_candidate=lambda cand, svc: cand == svc,
    )
    if warning:
        warnings.append(warning)
    # qB 服务配置存的是 PBKDF2 hash, 比较时用 verify 还原候选 plaintext 是否
    # 能导出与现存 hash 相同的摘要. 持久化 qB hash 时, 写入永远以新 salt 重
    # 哈希, 所以"严格相等"在反复运行时会误报不一致.
    warning = _check_or_apply_service(
        label="qbittorrent_password",
        candidate=candidate_qb_password,
        service_value=qb_password_service,
        service_path=qb_cfg,
        write_service=lambda p, v: write_qbittorrent_password_hash(p, hash_qbittorrent_password(v)),
        force=force,
        service_matches_candidate=lambda cand, svc: bool(svc)
        and verify_qbittorrent_password_hash(cand, svc),
    )
    if warning:
        warnings.append(warning)
    # qB username: 明文, 严格相等即一致. 错误信息允许带 username, 因其
    # 不是高熵 secret.
    warning = _check_or_apply_service(
        label="qbittorrent_username",
        candidate=candidate_qb_username,
        service_value=qb_username_service,
        service_path=qb_cfg,
        write_service=write_qbittorrent_username,
        force=force,
        service_matches_candidate=lambda cand, svc: cand == svc,
        show_value=True,
    )
    if warning:
        warnings.append(warning)

    # 同步到 shared secrets (始终以 candidate 为准)
    write_shared_secrets(
        secrets_path,
        prowlarr_api_key=candidate_prowlarr,
        qbittorrent_username=candidate_qb_username,
        qbittorrent_password=candidate_qb_password,
    )

    # no-op 判定: 全部已就绪且未触发任何写入
    if not created:
        # 若本次没创建 (已有 candidate) 且第三方服务值已与 candidate 对齐,
        # 报告 no-op; 但 secrets 写盘可能已经发生 (允许), status 仍记 ok.
        pass

    return BootstrapResult(
        status="warning" if warnings else ("noop" if is_noop else "ok"),
        created=created,
        warnings=tuple(warnings),
        prowlarr_api_key=candidate_prowlarr,
        qbittorrent_username=candidate_qb_username,
        qbittorrent_password=candidate_qb_password,
    )


def _resolve_prowlarr_candidate(existing_secrets_value: str | None) -> str:
    """从 env → 已有 secrets → 生成新值."""
    env_value = os.getenv("MEDIA_PILOT_PROWLARR_API_KEY", "").strip()
    if env_value:
        return env_value
    if existing_secrets_value:
        return existing_secrets_value
    return generate_prowlarr_api_key()


def _resolve_qb_password_candidate(existing_secrets_value: str | None) -> str:
    """从 env → 已有 secrets → 生成新值.

    注意 qB 服务配置里存的是 PBKDF2 hash, 不能直接用作 candidate plaintext.
    """
    env_value = os.getenv("MEDIA_PILOT_QBITTORRENT_PASSWORD", "").strip()
    if env_value:
        return env_value
    if existing_secrets_value:
        return existing_secrets_value
    return generate_qbittorrent_password()


def _resolve_qb_username_candidate(existing_secrets_value: str | None) -> str:
    """从 env → 已有 secrets → 默认 ``admin``.

    username 不是高熵随机值, 因此不生成, 缺失时回退到镜像默认 ``admin``,
    以保证 qB 登录凭据有效.
    """
    env_value = os.getenv("MEDIA_PILOT_QBITTORRENT_USERNAME", "").strip()
    if env_value:
        return env_value
    if existing_secrets_value:
        return existing_secrets_value
    return DEFAULT_QBITTORRENT_USERNAME


def _ensure_prowlarr_path_writable(path: Path, *, label: str) -> None:
    """检查 Prowlarr config 父目录 (即 bind mount 根) 存在.

    Prowlarr 把 config.xml 直接放在 bind mount 根 (/config/config.xml),
    没有中间子目录. 父目录缺失意味着 compose 挂载丢失, 必须 abort.
    """
    parent = path.parent
    if not parent.exists():
        raise BootstrapError(
            f"{label} 配置目录不存在: {parent}. "
            f"请检查 docker-compose.yml 中 {label} 服务的 volume 挂载."
        )


def _ensure_qbittorrent_path_writable(path: Path, *, label: str) -> None:
    """检查 qBittorrent config 可写; 安全创建已知子目录.

    qB 镜像 (linuxserver/qbittorrent) 把 qBittorrent.conf 放在 bind mount 根
    下的 ``qBittorrent/`` 子目录里. 策略:
    - bind mount 根 (path.parent.parent) 缺失 → 视为挂载错误, abort.
    - 已知子目录 ``qBittorrent/`` 缺失但 bind mount 根存在 → 允许创建
      (这是 fresh bind mount 的合法场景).
    - 允许在子目录下创建 conf 文件 (由 write_qbittorrent_password_hash 处理).
    """
    grandparent = path.parent.parent  # bind mount 根
    parent = path.parent  # 已知子目录 (例如 qBittorrent/)

    if not grandparent.exists():
        raise BootstrapError(
            f"{label} 配置目录不存在: {grandparent}. "
            f"请检查 docker-compose.yml 中 {label} 服务的 volume 挂载."
        )

    # 已知子目录缺失时, 安全创建. parent.name 必须是 KNOWN_QBITTORRENT_SUBDIR,
    # 否则说明路径不是默认布局, 我们仍然只允许在白名单子目录名上自动建.
    if not parent.exists():
        if parent.name == KNOWN_QBITTORRENT_SUBDIR:
            parent.mkdir(parents=False, exist_ok=False)
        else:
            raise BootstrapError(
                f"{label} 配置子目录 {parent} 不存在, 且不是已知的 "
                f"{KNOWN_QBITTORRENT_SUBDIR!r}. "
                f"拒绝自动创建非白名单嵌套目录, 请检查挂载布局."
            )


def _check_or_apply_service(
    *,
    label: str,
    candidate: str,
    service_value: str | None,
    service_path: Path,
    write_service,
    force: bool,
    service_matches_candidate,
    show_value: bool = False,
) -> str | None:
    """一致性校验 / 强制覆盖.

    service_value = None  → 视为缺失, 直接写入.
    service_matches_candidate(candidate, service_value) → 已对齐, no-op.
    否则: 默认跳过并返回 warning, force=True 时覆盖.

    show_value=True 时 (仅用于 username / 非敏感字段) 在错误信息中带
    candidate 与 service 实际值, 方便用户排障. 敏感字段 (password / api
    key) 必须保持默认 False, 错误信息只含字段名 + 同步方向.
    """
    if service_value is None:
        write_service(service_path, candidate)
        return None
    if service_matches_candidate(candidate, service_value):
        return None
    if not force:
        if show_value:
            return (
                f"{label} 不一致 (candidate={candidate!r}, service={service_value!r}); "
                f"默认不覆盖且不阻塞启动. 显式 force 模式 "
                f"(MEDIA_PILOT_BOOTSTRAP_FORCE=1) 允许以 candidate 为准覆盖 "
                f"{service_path.name}."
            )
        return (
            f"{label} 在 shared secrets 与 {service_path.name} 之间不一致, "
            f"默认不覆盖且不阻塞启动. 显式 force 模式 "
            f"(MEDIA_PILOT_BOOTSTRAP_FORCE=1) 允许以 shared secrets 为准覆盖 "
            f"{service_path.name}."
        )
    # force 模式: 以 candidate 为准
    write_service(service_path, candidate)
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI 入口 — `python -m media_pilot.deployment.bootstrap`."""
    argv = list(sys.argv[1:] if argv is None else argv)
    force = False
    if "--force" in argv:
        force = True
    if os.getenv("MEDIA_PILOT_BOOTSTRAP_FORCE", "").strip() in {"1", "true", "yes"}:
        force = True
    try:
        result = run_bootstrap(force=force)
    except BootstrapError as exc:
        print(f"[bootstrap] ERROR: {exc}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"[bootstrap] WARNING: {warning}", file=sys.stderr)
    print(
        f"[bootstrap] {result.status} created={result.created} "
        f"secrets={DEFAULT_SHARED_SECRETS_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
