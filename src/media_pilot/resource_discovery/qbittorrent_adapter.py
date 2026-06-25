"""
qBittorrent DownloadAdapter — HTTP 客户端封装

使用 httpx 进行 Cookie-based 认证，提交 magnet/URL 下载。
保存路径从后端配置读取，不接受客户端指定。
"""

from __future__ import annotations

import logging
from typing import Literal
from urllib.parse import urlparse, urlunparse

import httpx

from media_pilot.config.settings import AppConfig
from media_pilot.resource_discovery.types import (
    DownloadRequest,
    DownloadSubmitResult,
    ToolConnectionStatus,
)

logger = logging.getLogger(__name__)


def _sanitize_log_url(url: str) -> str:
    """移除 URL 中的敏感 token，仅保留 scheme+host 用于日志"""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, "/***", "", "", ""))


class QBittorrentAdapter:
    """qBittorrent 下载 Adapter — 实现 DownloadAdapter 协议"""

    def __init__(self, config: AppConfig) -> None:
        self._url = config.qbittorrent_url.rstrip("/") if config.qbittorrent_url else ""
        self._username = config.qbittorrent_username
        self._password = config.qbittorrent_password
        self._save_path = config.qbittorrent_save_path
        self._category = config.qbittorrent_category
        self._timeout = httpx.Timeout(config.qbittorrent_timeout_seconds)
        self._sid: str | None = None
        self._sid_cookie_name = "SID"

    # ── DownloadAdapter 协议方法 ──

    def add_download(
        self, request: DownloadRequest, *, tag: str | None = None
    ) -> DownloadSubmitResult:
        """提交下载到 qBittorrent。保存路径来自配置，不接受客户端指定。

        tag 为可选的下载关联标签，用于后续按标签回查 torrent 状态。
        格式如 "media-pilot:<download_task_id>"。
        """
        if not self._url:
            return DownloadSubmitResult(
                status="failed",
                title=request.title,
                source=request.source,
                message="qBittorrent 未配置：缺少 URL",
            )

        url_to_add = request.download_url or request.magnet_url
        if not url_to_add:
            return DownloadSubmitResult(
                status="failed",
                title=request.title,
                source=request.source,
                message="无可下载链接（magnet 或 URL）",
            )

        sid = self._login()
        if not sid:
            return DownloadSubmitResult(
                status="failed",
                title=request.title,
                source=request.source,
                message="qBittorrent 登录失败 — 请检查用户名/密码",
            )

        result = self._try_add_download(url_to_add, sid, request, tag=tag)
        if result.status == "submitted":
            return result

        # 认证失败时清除缓存 SID，重新登录再试一次
        if result.message and ("403" in result.message or "401" in result.message):
            logger.info("qBittorrent SID 过期，重新登录")
            self._sid = None
            sid = self._login(force=True)
            if not sid:
                return result
            return self._try_add_download(url_to_add, sid, request, tag=tag)

        return result

    def _try_add_download(
        self, url_to_add: str, sid: str, request: DownloadRequest,
        *, tag: str | None = None,
    ) -> DownloadSubmitResult:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                data = {
                    "urls": url_to_add,
                    "savepath": self._save_path,
                    "category": self._category,
                }
                if tag:
                    data["tags"] = tag
                resp = client.post(
                    f"{self._url}/api/v2/torrents/add",
                    data=data,
                    cookies={self._sid_cookie_name: sid},
                )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "qBittorrent add_download HTTP %d: %s",
                exc.response.status_code,
                _sanitize_log_url(str(exc.request.url)),
            )
            return DownloadSubmitResult(
                status="failed",
                title=request.title,
                source=request.source,
                message=f"下载提交失败（HTTP {exc.response.status_code}）",
            )
        except Exception as exc:
            logger.warning("qBittorrent add_download 异常: %s", exc)
            return DownloadSubmitResult(
                status="failed",
                title=request.title,
                source=request.source,
                message=f"下载提交失败：{exc}",
            )

        return DownloadSubmitResult(
            status="submitted",
            title=request.title,
            source=request.source,
            message=f'已提交到 qBittorrent："{request.title}"',
        )

    def test_connection(self) -> ToolConnectionStatus:
        """探测 qBittorrent 连通性与认证"""
        if not self._url:
            return ToolConnectionStatus(
                tool="qbittorrent",
                configured=False,
                reachable=False,
                authenticated=False,
                message="qBittorrent 未配置 URL",
            )

        sid = self._login()
        if sid is None:
            return ToolConnectionStatus(
                tool="qbittorrent",
                configured=True,
                reachable=False,
                authenticated=False,
                message="qBittorrent 连接超时",
            )
        if sid is False:
            return ToolConnectionStatus(
                tool="qbittorrent",
                configured=True,
                reachable=True,
                authenticated=False,
                message="qBittorrent 认证失败 — 请检查用户名/密码",
            )

        # 验证已认证会话可用
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(
                    f"{self._url}/api/v2/app/version",
                    cookies={self._sid_cookie_name: sid},
                )
            resp.raise_for_status()
        except Exception:
            return ToolConnectionStatus(
                tool="qbittorrent",
                configured=True,
                reachable=True,
                authenticated=True,
                message="qBittorrent 认证成功但 API 调用失败",
            )

        return ToolConnectionStatus(
            tool="qbittorrent",
            configured=True,
            reachable=True,
            authenticated=True,
            message="连接正常",
        )

    def add_torrent_file(
        self, torrent_data: bytes, *, tag: str | None = None
    ) -> DownloadSubmitResult:
        """通过 .torrent 文件提交下载到 qBittorrent。"""
        if not self._url:
            return DownloadSubmitResult(
                status="failed",
                title="",
                source="manual_upload",
                message="qBittorrent 未配置：缺少 URL",
            )

        sid = self._login()
        if not sid:
            return DownloadSubmitResult(
                status="failed",
                title="",
                source="manual_upload",
                message="qBittorrent 登录失败 — 请检查用户名/密码",
            )

        result = self._try_add_torrent_file(torrent_data, sid, tag=tag)
        if result.status == "submitted":
            return result

        if result.message and ("403" in result.message or "401" in result.message):
            logger.info("qBittorrent SID 过期，重新登录")
            self._sid = None
            sid = self._login(force=True)
            if not sid:
                return result
            return self._try_add_torrent_file(torrent_data, sid, tag=tag)

        return result

    def _try_add_torrent_file(
        self, torrent_data: bytes, sid: str, *, tag: str | None = None,
    ) -> DownloadSubmitResult:
        title = "torrent"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                files = {
                    "torrents": (
                        "upload.torrent",
                        torrent_data,
                        "application/x-bittorrent",
                    )
                }
                data = {
                    "savepath": self._save_path,
                    "category": self._category,
                }
                if tag:
                    data["tags"] = tag
                resp = client.post(
                    f"{self._url}/api/v2/torrents/add",
                    data=data,
                    files=files,
                    cookies={self._sid_cookie_name: sid},
                )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "qBittorrent add_torrent_file HTTP %d",
                exc.response.status_code,
            )
            return DownloadSubmitResult(
                status="failed",
                title=title,
                source="manual_upload",
                message=f"torrent 文件提交失败（HTTP {exc.response.status_code}）",
            )
        except Exception as exc:
            logger.warning("qBittorrent add_torrent_file 异常: %s", exc)
            return DownloadSubmitResult(
                status="failed",
                title=title,
                source="manual_upload",
                message=f"torrent 文件提交失败：{exc}",
            )

        return DownloadSubmitResult(
            status="submitted",
            title=title,
            source="manual_upload",
            message="已提交到 qBittorrent",
        )

    # ── 状态读取 ──

    def get_torrent_info(
        self, hashes: list[str], *, tag: str | None = None
    ) -> list:  # noqa: F821
        """通过 qBittorrent API v2 读取 torrent 状态。

        - hashes: 指定 hash 列表（传空列表获取全部）
        - tag: 按标签过滤（如 "media-pilot:dt-001"）
        二者至少提供一个，否则不查。
        """
        from media_pilot.resource_discovery.types import QBTorrentInfo as QBTI

        if not self._url:
            return []

        sid = self._login()
        if not sid:
            return []

        try:
            with httpx.Client(timeout=self._timeout) as client:
                params: dict[str, str] = {}
                if hashes:
                    params["hashes"] = "|".join(hashes)
                if tag:
                    params["tag"] = tag
                resp = client.get(
                    f"{self._url}/api/v2/torrents/info",
                    params=params if params else None,
                    cookies={self._sid_cookie_name: sid},
                )
            resp.raise_for_status()
            data: list[dict] = resp.json()
        except Exception as exc:
            logger.warning("qBittorrent get_torrent_info 失败: %s", exc)
            return []

        return [
            QBTI(
                hash=item.get("hash", ""),
                name=item.get("name", ""),
                save_path=item.get("save_path", ""),
                content_path=item.get("content_path"),
                progress=float(item.get("progress", 0)),
                size_bytes=int(item.get("size", 0)),
                dlspeed=int(item.get("dlspeed", 0)),
                upspeed=int(item.get("upspeed", 0)),
                num_seeds=int(item.get("num_seeds", 0)),
                num_leechs=int(item.get("num_leechs", 0)),
                num_complete=int(item.get("num_complete", 0)),
                connections=int(item.get("connections", 0)),
                state=str(item.get("state", "")),
            )
            for item in data
        ]

    # ── 暂停 / 恢复 ──

    def pause_torrent(self, qb_hash: str) -> bool:
        """暂停指定 torrent"""
        return self._torrent_action(qb_hash, "pause")

    def resume_torrent(self, qb_hash: str) -> bool:
        """恢复指定 torrent"""
        return self._torrent_action(qb_hash, "resume")

    def set_global_rate_limits(
        self,
        *,
        download_rate_limit_bytes_per_second: int,
        upload_rate_limit_bytes_per_second: int,
    ) -> bool:
        """设置 qBittorrent 实例级全局下载 / 上传限速。

        qB Web API 的 transfer 限速接口使用 bytes/s，0 表示不限速。
        该方法作用于整个 qBittorrent 实例，不区分 category 或单个种子。
        """
        if not self._url:
            return False
        if download_rate_limit_bytes_per_second < 0 or upload_rate_limit_bytes_per_second < 0:
            return False

        sid = self._login()
        if not sid:
            return False

        try:
            with httpx.Client(timeout=self._timeout) as client:
                dl_resp = client.post(
                    f"{self._url}/api/v2/transfer/setDownloadLimit",
                    data={"limit": str(download_rate_limit_bytes_per_second)},
                    cookies={self._sid_cookie_name: sid},
                )
                dl_resp.raise_for_status()
                up_resp = client.post(
                    f"{self._url}/api/v2/transfer/setUploadLimit",
                    data={"limit": str(upload_rate_limit_bytes_per_second)},
                    cookies={self._sid_cookie_name: sid},
                )
                up_resp.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("qBittorrent 全局限速同步失败: %s", exc)
            return False

    def _torrent_action(self, qb_hash: str, action: str) -> bool:
        sid = self._login()
        if not sid:
            return False
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._url}/api/v2/torrents/{action}",
                    data={"hashes": qb_hash},
                    cookies={self._sid_cookie_name: sid},
                )
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.warning(
                "qBittorrent %s 失败: hash=%s err=%s",
                action, qb_hash[:12] if qb_hash else "", exc,
            )
            return False

    # ── 删除 ──

    def delete_torrent(
        self, qb_hash: str, *, delete_files: bool = True,
    ) -> Literal["deleted", "not_found", "error"]:
        """通知 qBittorrent 删除 torrent 及数据。

        3 态返回, 让上层区分:
        - "deleted":   qB 删除请求成功 (HTTP 200), 或 qB 上本来就没有
                       这个 torrent (404 视为幂等成功, 让"qB 已删但 DB
                       残留" 的半完成状态可以靠重试收敛).
        - "not_found": qB 明确返回 404 (torrent 不存在).
        - "error":     登录失败 / 网络异常 / 5xx 等, 调用方决定本地清理策略.
        """
        sid = self._login()
        if not sid:
            return "error"

        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._url}/api/v2/torrents/delete",
                    data={
                        "hashes": qb_hash,
                        "deleteFiles": str(delete_files).lower(),
                    },
                    cookies={self._sid_cookie_name: sid},
                )
            if resp.status_code == 404:
                return "not_found"
            resp.raise_for_status()
            return "deleted"
        except Exception as exc:
            logger.warning(
                "qBittorrent delete_torrent 失败: hash=%s err=%s",
                qb_hash[:12] if qb_hash else "", exc,
            )
            return "error"

    # ── 内部方法 ──

    def _login(self, *, force: bool = False) -> str | None | bool:
        """登录 qBittorrent，返回 SID 字符串。复用缓存 SID，force=True 时重新登录。

        返回:
            str: 登录成功，返回 SID
            None: 网络/超时失败
            False: 认证失败（HTTP 403/401）
        """
        if not force and self._sid is not None:
            return self._sid

        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._url}/api/v2/auth/login",
                    data={
                        "username": self._username,
                        "password": self._password,
                    },
                    headers={"Referer": self._url},
                )
            resp.raise_for_status()
            sid_cookie = _extract_sid_cookie(resp.cookies)
            sid = sid_cookie[1] if sid_cookie else ""
            if not sid:
                logger.warning("qBittorrent 登录响应无 SID cookie")
                self._sid = None
                return False
            self._sid_cookie_name = sid_cookie[0] if sid_cookie else "SID"
            self._sid = sid
            return sid
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                logger.warning("qBittorrent 认证失败: HTTP %d", exc.response.status_code)
                self._sid = None
                return False
            logger.warning(
                "qBittorrent 登录 HTTP %d: %s",
                exc.response.status_code,
                _sanitize_log_url(self._url),
            )
            return False
        except httpx.TimeoutException:
            logger.warning("qBittorrent 登录超时: %s", _sanitize_log_url(self._url))
            return None
        except Exception as exc:
            logger.warning("qBittorrent 登录异常: %s — %s", _sanitize_log_url(self._url), exc)
            return None


def _extract_sid_cookie(cookies: httpx.Cookies) -> tuple[str, str] | None:
    """提取 qBittorrent 登录 cookie。

    qBittorrent 4.x 常见 cookie 名为 SID；qBittorrent 5.x / linuxserver 镜像
    可能返回 QBT_SID_<port>，后续 API 必须使用同一个 cookie 名称。
    """
    sid = cookies.get("SID")
    if sid:
        return "SID", sid
    for name, value in cookies.items():
        if name.startswith("QBT_SID") and value:
            return name, value
    return None
