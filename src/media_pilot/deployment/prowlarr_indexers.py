"""Prowlarr 公共 indexer 初始化 — Compose one-shot 入口.

锁定 (simplify-docker-onboarding-and-diagnostics):
- 默认公共 indexer 集合固定: YTS, The Pirate Bay, LimeTorrents, Nyaa.si,
  Mikan, dmhy, ACG.RIP.
- 不默认创建 sukebei.nyaa.si 或任何需要账号的私有 / 邀请站.
- 幂等: 通过 Prowlarr API 检查现有 indexer 名称, 跳过同名项, 不覆盖用户
  已修改配置.
- 单个 indexer 创建失败只 warning, 不阻塞主服务; 仅当 Prowlarr API 完全
  不可达时 abort.
- Prowlarr API 启动期间可能尚未就绪: 对连接类错误做 bounded retry,
  任何 HTTP 4xx/5xx 视为"API 已就绪但拒绝请求", 立即报错, 不重试.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol, TypeVar

import httpx

logger = logging.getLogger(__name__)


# ── API ready 重试策略 ──

# 默认总等待上限: 60 秒. Prowlarr 在 init 容器紧跟其后启动时通常 5~30 秒
# 就绪, 60 秒足以覆盖慢启动而不让 init 容器无限阻塞.
PROWLARR_API_READY_TIMEOUT_SECONDS = 60.0
# 每次重试间隔.
PROWLARR_API_READY_RETRY_DELAY = 2.0


class ProwlarrInitError(RuntimeError):
    """Prowlarr API 不可达 / 配置缺失 — 必须 abort init 容器."""


@dataclass(frozen=True)
class IndexerEntry:
    """默认公共 indexer 描述.

    ``name`` 与 Prowlarr GET /api/v1/indexer/schema 返回的 ``name`` 字段
    一致, 用于 schema 查找.
    """

    name: str
    # 备注, 人类可读, 不参与 Prowlarr API 调用.
    note: str = ""


# ── 默认公共 indexer 集合 (锁定 — 见 design.md Decision 4) ──

# 常规电影 / 剧集
DEFAULT_PUBLIC_INDEXERS: tuple[IndexerEntry, ...] = (
    IndexerEntry("YTS", "公共 YIFY 镜像, 适合电影"),
    IndexerEntry("The Pirate Bay", "公共 PT 索引, 通用资源"),
    IndexerEntry("LimeTorrents", "公共 PT 索引, 通用资源"),
    # 动漫 / 中文资源
    IndexerEntry("Nyaa.si", "Nyaa 主站 (非成人)"),
    IndexerEntry("Mikan", "Mikan Project 蜜柑计划, 动画 BT"),
    IndexerEntry("dmhy", "动漫花园, 中文字幕新番"),
    IndexerEntry("ACG.RIP", "ACG 资源整合站"),
)
# 注意: 故意不包含:
# - sukebei.nyaa.si (成人源, 默认关闭)
# - 1337x / EZTV / Anidex (Cloudflare 易失败, 当前不稳定)
# - HDChina / OurBits / MTeam 等 (需账号, 私有/邀请站)


# ── Prowlarr API 客户端协议 ──


class ProwlarrAPIClient(Protocol):
    def list_indexer_schema(self) -> list[dict]: ...
    def list_app_profiles(self) -> list[dict]: ...
    def list_indexers(self) -> list[dict]: ...
    def add_indexer(self, definition: dict) -> dict: ...


# ── API ready 重试 ──

T = TypeVar("T")


def with_api_retry(
    fn: Callable[[], T],
    *,
    label: str,
    timeout_seconds: float = PROWLARR_API_READY_TIMEOUT_SECONDS,
    delay_seconds: float = PROWLARR_API_READY_RETRY_DELAY,
) -> T:
    """对连接类错误做 bounded retry, 其它错误立即抛.

    Prowlarr 启动时 (容器刚起, 端口 listen 但 HTTP 还没接受请求) 通常会
    抛 ``httpx.ConnectError`` 或 ``httpx.TimeoutException``. 这两类是网络
    层面的瞬时错误, 适合短间隔重试. ``httpx.HTTPStatusError`` (4xx/5xx)
    表明 Prowlarr 已响应但拒绝了请求 (例如 API Key 错), 立即报错让用户
    看到, 不应被静默重试吞掉.
    """
    deadline = time.monotonic() + timeout_seconds
    attempt = 0
    last_exc: Exception | None = None
    while True:
        attempt += 1
        try:
            return fn()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exc = exc
            if time.monotonic() >= deadline:
                break
            logger.info(
                "[prowlarr-init] %s 尚未就绪 (attempt %d): %s; %.1fs 后重试",
                label,
                attempt,
                exc,
                delay_seconds,
            )
            time.sleep(delay_seconds)
    raise ProwlarrInitError(
        f"Prowlarr API 在 {timeout_seconds:.0f}s 内未就绪 ({label}): {last_exc!r}. "
        f"请检查 Prowlarr 服务是否启动并监听 9696 端口."
    ) from last_exc


# ── 工具函数 ──


def find_indexer_definition(
    schema: list[dict], name: str
) -> dict | None:
    """从 Prowlarr schema 中按 name 找到对应 indexer 定义.

    Prowlarr schema 列表元素 ``name`` 不区分大小写; 这里 normalize 后
    比对, 减少与 Prowlarr 版本迭代间大小写不一致的脆弱性.
    """
    target = name.strip().lower()
    for entry in schema:
        entry_name = str(entry.get("name", "")).strip().lower()
        if entry_name == target:
            return entry
    return None


def is_indexer_installed(installed: list[dict], name: str) -> bool:
    target = name.strip().lower()
    for entry in installed:
        entry_name = str(entry.get("name", "")).strip().lower()
        if entry_name == target:
            return True
    return False


def resolve_default_app_profile_id(profiles: list[dict]) -> int:
    """Return the first usable Prowlarr app profile id.

    Fresh Prowlarr installs create the built-in "Standard" app profile with id
    1, while indexer schema entries still carry ``appProfileId=0``. Current
    Prowlarr rejects POST /api/v1/indexer when that 0 is sent back unchanged.
    """
    for profile in profiles:
        profile_id = profile.get("id")
        if isinstance(profile_id, int) and profile_id > 0:
            return profile_id
    raise ProwlarrInitError(
        "Prowlarr 未返回可用 App Profile (id > 0), 无法创建默认 indexer."
    )


# ── Bootstrap 编排 ──


@dataclass(frozen=True)
class BootstrapResult:
    status: str  # "ok" | "noop"
    api_unreachable: bool
    created_count: int
    skipped_count: int
    failed_count: int
    created: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    missing_from_schema: list[dict] = field(default_factory=list)


def bootstrap_public_indexers(
    api: ProwlarrAPIClient,
    *,
    default_set: tuple[IndexerEntry, ...] = DEFAULT_PUBLIC_INDEXERS,
    ready_timeout_seconds: float | None = None,
    ready_retry_delay: float | None = None,
) -> BootstrapResult:
    """Prowlarr 公共 indexer 幂等创建.

    Prowlarr API 启动期间调用会抛 ConnectError/TimeoutException, 这里用
    ``with_api_retry`` 做 bounded retry. 单个 indexer 失败 (例如 schema 缺
    失或 POST 错误) 只 warning 继续, 不阻塞主服务.

    ready_timeout_seconds / ready_retry_delay 在测试中可以被 monkeypatch
    覆盖 (因为 None 时函数会重新读模块级常量, 而非绑定到定义时值).

    Returns:
        BootstrapResult: 单个 indexer 失败时 failed_count > 0, 但 status
        仍为 "ok" (主服务可继续启动). 只有 Prowlarr API 完全不可达时
        抛 ProwlarrInitError, 由 CLI 入口把容器 exit 1.
    """
    if ready_timeout_seconds is None:
        ready_timeout_seconds = PROWLARR_API_READY_TIMEOUT_SECONDS
    if ready_retry_delay is None:
        ready_retry_delay = PROWLARR_API_READY_RETRY_DELAY

    schema = with_api_retry(
        api.list_indexer_schema,
        label="list_indexer_schema",
        timeout_seconds=ready_timeout_seconds,
        delay_seconds=ready_retry_delay,
    )
    installed = with_api_retry(
        api.list_indexers,
        label="list_indexers",
        timeout_seconds=ready_timeout_seconds,
        delay_seconds=ready_retry_delay,
    )
    app_profile_id = resolve_default_app_profile_id(
        with_api_retry(
            api.list_app_profiles,
            label="list_app_profiles",
            timeout_seconds=ready_timeout_seconds,
            delay_seconds=ready_retry_delay,
        )
    )

    created: list[dict] = []
    skipped: list[str] = []
    failed: list[dict] = []
    missing: list[dict] = []

    for entry in default_set:
        if is_indexer_installed(installed, entry.name):
            skipped.append(entry.name)
            logger.info("[prowlarr-init] 跳过已存在 indexer: %s", entry.name)
            continue
        definition = find_indexer_definition(schema, entry.name)
        if definition is None:
            missing.append({"name": entry.name, "note": entry.note})
            logger.warning(
                "[prowlarr-init] Prowlarr schema 找不到默认 indexer, 跳过: %s",
                entry.name,
            )
            continue
        # 构造 POST payload: schema 元素直接使用, 强制 enable=True
        payload = dict(definition)
        payload["enable"] = True
        payload["appProfileId"] = app_profile_id
        # 不携带 schema 自带的 id 字段, 避免 Prowlarr 误以为是更新
        payload.pop("id", None)
        try:
            api.add_indexer(payload)
        except Exception as exc:
            failed.append({"name": entry.name, "error": repr(exc)})
            logger.warning(
                "[prowlarr-init] 创建 %s 失败, 继续: %s", entry.name, exc
            )
            continue
        created.append(payload)
        logger.info("[prowlarr-init] 创建 indexer: %s", entry.name)

    return BootstrapResult(
        status="ok",
        api_unreachable=False,
        created_count=len(created),
        skipped_count=len(skipped),
        failed_count=len(failed),
        created=created,
        skipped=skipped,
        failed=failed,
        missing_from_schema=missing,
    )


# ── httpx 实现 (Docker 容器内使用) ──


class HttpxProwlarrClient:
    """通过 Prowlarr HTTP API 工作的客户端.

    Prowlarr 所有 API 端点都接受 ``X-Api-Key`` header; query string 上的
    ``apikey=`` 同样有效, 这里统一用 header.
    """

    def __init__(
        self, base_url: str, *, api_key: str, timeout_seconds: float = 30.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_seconds)

    def list_indexer_schema(self) -> list[dict]:
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                f"{self._base_url}/api/v1/indexer/schema",
                headers={"X-Api-Key": self._api_key},
            )
            resp.raise_for_status()
            return list(resp.json())

    def list_indexers(self) -> list[dict]:
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                f"{self._base_url}/api/v1/indexer",
                headers={"X-Api-Key": self._api_key},
            )
            resp.raise_for_status()
            return list(resp.json())

    def list_app_profiles(self) -> list[dict]:
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                f"{self._base_url}/api/v1/appProfile",
                headers={"X-Api-Key": self._api_key},
            )
            resp.raise_for_status()
            return list(resp.json())

    def add_indexer(self, definition: dict) -> dict:
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                f"{self._base_url}/api/v1/indexer",
                json=definition,
                headers={"X-Api-Key": self._api_key},
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}


def build_default_client() -> ProwlarrAPIClient:
    """构造 Prowlarr API 客户端 — 读 env + 共享 secrets.

    Priority: env (MEDIA_PILOT_PROWLARR_URL/API_KEY) → 共享 secrets fallback.
    """
    from media_pilot.deployment.secrets import (
        read_prowlarr_api_key_with_fallback,
    )

    url = os.getenv(
        "MEDIA_PILOT_PROWLARR_INIT_URL"
    ) or os.getenv("MEDIA_PILOT_PROWLARR_URL", "")
    api_key = read_prowlarr_api_key_with_fallback()
    if not url:
        raise ProwlarrInitError(
            "MEDIA_PILOT_PROWLARR_INIT_URL 未配置, 无法初始化 Prowlarr indexer."
        )
    if not api_key:
        raise ProwlarrInitError(
            "Prowlarr API Key 未配置 (env 与共享 secrets 均缺失). "
            "请确认 media-pilot-init 已成功写入 /data/shared-secrets.env."
        )
    return HttpxProwlarrClient(url, api_key=api_key)


# ── CLI 入口 ──


def main(argv: list[str] | None = None) -> int:
    """`python -m media_pilot.deployment.bootstrap_prowlarr_indexers` 入口."""
    try:
        client = build_default_client()
    except ProwlarrInitError as exc:
        print(f"[prowlarr-init] ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        result = bootstrap_public_indexers(client)
    except ProwlarrInitError as exc:
        print(f"[prowlarr-init] ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"[prowlarr-init] ok "
        f"created={result.created_count} "
        f"skipped={result.skipped_count} "
        f"failed={result.failed_count}"
    )
    if result.failed:
        for f in result.failed:
            print(f"[prowlarr-init] WARN failed: {f['name']} ({f['error']})", file=sys.stderr)
    if result.missing_from_schema:
        for m in result.missing_from_schema:
            print(
                f"[prowlarr-init] WARN schema-missing: {m['name']}",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
