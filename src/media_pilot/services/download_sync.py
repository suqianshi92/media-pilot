"""下载状态同步服务 — 查询非终态下载任务 → qBittorrent Adapter → 更新 DB"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.config.settings import AppConfig
from media_pilot.repository.models import OperationRecord
from media_pilot.repository.repositories import (
    DownloadTaskRepository,
    IngestTaskCreate,
    IngestTaskRepository,
)
from media_pilot.resource_discovery.qbittorrent_adapter import QBittorrentAdapter
from media_pilot.resource_discovery.types import is_qb_torrent_completed

logger = logging.getLogger(__name__)


@dataclass
class DownloadSyncResult:
    synced: int = 0
    failed: int = 0
    skipped: int = 0
    ingested: int = 0


class DownloadSyncService:
    """下载状态同步服务：轮询非终态下载任务并更新 qB 状态。"""

    def __init__(
        self, config: AppConfig, adapter: QBittorrentAdapter | None = None
    ) -> None:
        self._config = config
        self._adapter: QBittorrentAdapter = adapter or QBittorrentAdapter(config)

    def sync_once(self, session_factory: sessionmaker[Session]) -> DownloadSyncResult:
        """单轮同步：查询所有非终态下载任务，调用 qB API 更新状态。"""
        result = DownloadSyncResult()

        if not self._config.qbittorrent_url:
            return result

        with session_factory() as session:
            repo = DownloadTaskRepository(session)
            tasks = repo.list_non_terminal()

            for task in tasks:
                if not task.qb_hash:
                    # 尝试从 qB 补齐 hash
                    if not self._backfill_hash(repo, task):
                        result.skipped += 1
                        continue
                try:
                    infos = self._adapter.get_torrent_info([task.qb_hash])
                # SQLite OperationalError (database is locked) — 后台 qB
                # 同步 5s 一次可能与 Agent 长事务争写锁. WAL + busy_timeout
                # 已经把大部分冲突消化, 真出现 locked 时记 warning, 跳过
                # 本轮, 下一轮再试. 不更新 task 状态, 避免在 lock 期间
                # 再触发 nested OperationalError 让 task 永远 sync_failed.
                except OperationalError as exc:
                    logger.warning(
                        "下载状态同步遇到 database is locked, 跳过本轮 hash=%s: %s",
                        task.qb_hash[:12], exc,
                    )
                    result.skipped += 1
                    continue
                except Exception:
                    logger.exception(
                        "qBittorrent 状态同步失败: hash=%s", task.qb_hash[:12]
                    )
                    repo.update_sync_status(
                        task,
                        status="sync_failed",
                        error_message="qBittorrent API 调用失败",
                    )
                    result.failed += 1
                    continue

                if not infos:
                    repo.update_sync_status(
                        task,
                        status="sync_failed",
                        error_message=(
                            f"qBittorrent 中找不到 hash: {task.qb_hash[:12]}..."
                        ),
                    )
                    result.failed += 1
                    continue

                info = infos[0]
                repo.update_sync_status(
                    task,
                    progress=info.progress,
                    download_speed_bytes_per_second=info.dlspeed,
                    upload_speed_bytes_per_second=info.upspeed,
                    seeders=info.num_seeds,
                    leechers=info.num_leechs,
                    connections=info.connections,
                    qb_state=info.state,
                    content_path=info.content_path,
                    qb_name=info.name or None,
                    status="downloading",
                    error_message=None,
                )
                result.synced += 1

                # ── 下载完成 → 转入库 ──
                self._maybe_ingest(session, task, info, result)

            session.commit()

        return result

    def retry_sync_one(
        self, session_factory: sessionmaker[Session], download_id: str,
    ) -> DownloadSyncResult:
        """手动重试单个下载任务的状态同步。

        只对没有 ingest_task_id 的 download-only 任务可用。
        不重新提交 magnet/torrent，只重新与 qB 对账。
        """
        result = DownloadSyncResult()

        if not self._config.qbittorrent_url:
            return result

        with session_factory() as session:
            repo = DownloadTaskRepository(session)
            task = repo.get(download_id)

            if task is None:
                result.skipped += 1
                return result

            # 已关联入库任务的走入库链路，不再暴露下载重试
            if task.ingest_task_id is not None:
                result.skipped += 1
                return result

            if not task.qb_hash:
                if not self._backfill_hash(repo, task):
                    result.skipped += 1
                    session.commit()
                    return result

            try:
                infos = self._adapter.get_torrent_info([task.qb_hash])
            except Exception:
                logger.exception(
                    "手动重试同步失败: hash=%s", task.qb_hash[:12]
                )
                repo.update_sync_status(
                    task,
                    status="sync_failed",
                    error_message="qBittorrent API 调用失败",
                )
                result.failed += 1
                session.commit()
                return result

            if not infos:
                repo.update_sync_status(
                    task,
                    status="sync_failed",
                    error_message=(
                        f"qBittorrent 中找不到 hash: {task.qb_hash[:12]}..."
                    ),
                )
                result.failed += 1
                session.commit()
                return result

            info = infos[0]
            repo.update_sync_status(
                task,
                progress=info.progress,
                download_speed_bytes_per_second=info.dlspeed,
                upload_speed_bytes_per_second=info.upspeed,
                seeders=info.num_seeds,
                leechers=info.num_leechs,
                connections=info.connections,
                qb_state=info.state,
                content_path=info.content_path,
                qb_name=info.name or None,
                status="downloading",
                error_message=None,
            )
            result.synced += 1

            self._maybe_ingest(session, task, info, result)

            session.commit()

        return result

    def _maybe_ingest(
        self,
        session: Session,
        task,  # noqa: F821  # DownloadTask
        info,  # noqa: F821  # QBTorrentInfo
        result: DownloadSyncResult,
    ) -> None:
        """下载完成判定 + 创建入库任务 + 幂等保护"""
        # 已完成绑定 → 跳过
        if task.ingest_task_id is not None:
            return

        # 判定是否完成
        if not is_qb_torrent_completed(info):
            return

        # 路径越界检查：使用 Path 语义而非字符串 startswith
        content_path = info.content_path
        if not content_path:
            return
        downloads_dir = self._config.downloads_dir.resolve()
        try:
            content_path_obj = Path(content_path).resolve(strict=False)
        except Exception:
            logger.warning("下载完成但无法解析内容路径: %s", content_path)
            return
        if not content_path_obj.is_relative_to(downloads_dir):
            logger.warning(
                "下载完成但内容路径越界: hash=%s path=%s downloads=%s",
                task.qb_hash[:12] if task.qb_hash else "?",
                content_path,
                downloads_dir,
            )
            return

        # 5.2: 路径可访问性检查 — 暂不可访问则等待重试
        if not content_path_obj.exists():
            logger.info(
                "下载完成但路径暂不可访问: hash=%s path=%s",
                task.qb_hash[:12] if task.qb_hash else "?", content_path,
            )
            repo = DownloadTaskRepository(session)
            repo.update_sync_status(task, status="completed_pending_ingest")
            return

        # 创建入库任务
        ingest_repo = IngestTaskRepository(session)
        ingest_task = ingest_repo.create(
            IngestTaskCreate(
                owner_user_id=task.owner_user_id,
                is_adult=task.is_adult,
                source_path=content_path,
                source_download_task_id=task.id,
                status="discovered",
                current_step="download_scan",
                # DownloadTask 上的 preselected 元数据事实必须随 IngestTask
                # 一起持久化. Agent 链路 (prepare_select_metadata_candidate
                # _decision / check_eligibility) 看到这三个字段都存在时,
                # 把 preselected 当强事实 — 不得走 search 候选 / 向用户
                # 确认同一个元数据, 直接进入 fetch + publish.
                preselected_metadata_profile=task.preselected_metadata_profile,
                preselected_metadata_provider=task.preselected_metadata_provider,
                preselected_metadata_external_id=task.preselected_metadata_external_id,
            )
        )

        # 回写绑定
        repo = DownloadTaskRepository(session)
        repo.bind_ingest_task(task, ingest_task.id)
        repo.update_sync_status(task, status="completed")

        # ── 时间线事件：下载请求 + 下载完成 ──
        now = datetime.now(UTC)
        session.add(
            OperationRecord(
                task_id=ingest_task.id,
                operation_type="download_requested",
                permission_level="read_only",
                status="succeeded",
                details={
                    "download_task_id": task.id,
                    "title": task.title,
                    "source": task.source,
                    "indexer": task.indexer or "",
                },
                created_at=task.created_at,
            )
        )
        session.add(
            OperationRecord(
                task_id=ingest_task.id,
                operation_type="download_completed",
                permission_level="read_only",
                status="succeeded",
                details={
                    "download_task_id": task.id,
                    "content_path": content_path,
                },
                created_at=now,
            )
        )

        result.ingested += 1
        logger.info(
            "下载完成转入库: hash=%s → ingest=%s",
            task.qb_hash[:12] if task.qb_hash else "?",
            ingest_task.id,
        )

    def _backfill_hash(self, repo, task) -> bool:  # noqa: F821
        """从 qBittorrent 回查 torrent 并补齐 hash。

        优先通过下载关联标签（media-pilot:<task_id>）回查，
        失败时降级到标题标准化匹配。
        返回 True 表示补齐成功，False 表示本次未匹配。

        多命中保护：先收集全部匹配，单一命中才绑定。
        多命中时优先精确标准化匹配；仍歧义则保持 awaiting_sync。
        """
        # ── 4.3: 优先按标签回查 ──
        tag = f"media-pilot:{task.id}"
        try:
            tagged_torrents = self._adapter.get_torrent_info([], tag=tag)
        except Exception:
            logger.exception("标签回查失败: tag=%s", tag)
            tagged_torrents = []

        if tagged_torrents:
            info = tagged_torrents[0]
            repo.update_sync_status(
                task,
                qb_hash=info.hash,
                qb_name=info.name,
                status="downloading",
                error_message=None,
            )
            logger.info(
                "hash 补齐成功（标签回查）: id=%s tag=%s → hash=%s",
                task.id, tag, info.hash[:12],
            )
            return True

        # ── 降级: 标题标准化匹配（保留为兜底路径） ──
        try:
            all_torrents = self._adapter.get_torrent_info([])
        except Exception:
            logger.exception("hash 补齐失败: 无法获取 qB torrent 列表")
            return False

        task_title = (task.title or "").strip()
        if not task_title:
            return False

        task_norm = _normalize_for_matching(task_title)

        # 第一轮：收集所有标准化匹配的 torrent
        matches: list = []
        for info in all_torrents:
            info_name = (info.name or "").strip()
            if not info_name:
                continue
            info_norm = _normalize_for_matching(info_name)
            if task_norm and info_norm and (
                task_norm == info_norm or task_norm in info_norm
            ):
                matches.append((info, info_norm))

        if not matches:
            if task.status != "awaiting_sync":
                repo.update_sync_status(
                    task,
                    status="awaiting_sync",
                    error_message="等待 qBittorrent hash 补齐",
                )
            return False

        # 单一命中 → 直接绑定
        if len(matches) == 1:
            info, _ = matches[0]
            repo.update_sync_status(
                task,
                qb_hash=info.hash,
                qb_name=info.name,
                status="downloading",
                error_message=None,
            )
            logger.info(
                "hash 补齐成功: id=%s title=%r → hash=%s",
                task.id, task_title, info.hash[:12],
            )
            return True

        # 多命中 → 优先精确标准化匹配
        exact_matches = [
            (info, norm) for info, norm in matches if norm == task_norm
        ]
        if len(exact_matches) == 1:
            info, _ = exact_matches[0]
            repo.update_sync_status(
                task,
                qb_hash=info.hash,
                qb_name=info.name,
                status="downloading",
                error_message=None,
            )
            logger.info(
                "hash 补齐成功（精确匹配）: id=%s title=%r → hash=%s",
                task.id, task_title, info.hash[:12],
            )
            return True

        # 仍有歧义 → 保守保留 awaiting_sync
        logger.warning(
            "hash 补齐多命中: id=%s title=%r candidates=%d exact=%d",
            task.id, task_title, len(matches), len(exact_matches),
        )
        if task.status != "awaiting_sync":
            repo.update_sync_status(
                task,
                status="awaiting_sync",
                error_message=(
                    f"hash 补齐多命中 ({len(matches)} 候选)"
                ),
            )
        return False


def _normalize_for_matching(name: str) -> str:
    """标准化 torrent 名称用于匹配对比。

    去扩展名 → 小写 → 统一分隔符为空格 → 去重复空格 → strip。
    例如 "Weathering.With.You.2019.1080p.BluRay.x264.mkv"
      → "weathering with you 2019 1080p bluray x264"
    """
    import re

    # 去扩展名（.mkv, .mp4, .avi 等）
    name = re.sub(r"\.(mkv|mp4|avi|ts|m2ts|iso|wmv|mov|flv|webm)$", "", name, flags=re.IGNORECASE)

    # 统一分隔符：点、下划线、连字符 → 空格
    name = re.sub(r"[._-]", " ", name)

    # 去多个空格
    name = re.sub(r"\s+", " ", name)

    return name.strip().lower()
