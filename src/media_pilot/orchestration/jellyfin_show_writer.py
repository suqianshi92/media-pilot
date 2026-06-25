import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree.ElementTree import Comment, Element, SubElement, tostring

import httpx
from sqlalchemy.orm import Session

from media_pilot.adapters.metadata import MetadataDetail
from media_pilot.orchestration.staging_cleanup import cleanup_empty_staging_task_dir
from media_pilot.repository.audit import record_file_operation, record_generated_file_operation
from media_pilot.repository.models import FileAsset
from media_pilot.repository.repositories import WritePlanRepository, WriteResultRepository
from media_pilot.services.task_input_analysis import (
    SUBTITLE_EXTENSIONS,
    is_same_stem_subtitle,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EpisodeTarget:
    """单个 episode 的目标信息"""
    episode: int
    season: int
    source_file: Path
    target_file: Path


@dataclass(frozen=True)
class ShowWritePlanDraft:
    target_dir: Path
    final_target_dir: Path
    show_dir_name: str
    season_dir_name: str
    episodes: list[EpisodeTarget]
    tvshow_nfo_path: Path
    season_nfo_path: Path

    @property
    def episode_nfo_paths(self) -> dict[int, Path]:
        """episode number → episode.nfo path"""
        return {
            ep.episode: ep.target_file.parent / f"{self.show_dir_name} - S{ep.season:02d}E{ep.episode:02d}.nfo"
            for ep in self.episodes
        }


@dataclass(frozen=True)
class ShowWriteResult:
    status: str
    warnings: list[str]


def build_show_write_plan(
    *,
    shows_dir: Path,
    episodes: list[EpisodeTarget],
    detail: MetadataDetail,
    task_id: str,
    provider: str = "tmdb",
) -> ShowWritePlanDraft:
    show_dir_name = _show_directory_name(detail.title, detail.year)
    seasons = {ep.season for ep in episodes}
    if len(seasons) != 1:
        raise ValueError(f"跨季 episode 不支持自动发布: seasons={seasons}")
    season = next(iter(seasons))
    season_dir_name = f"Season {season:02d}"
    target_dir = shows_dir / ".media-pilot-staging" / task_id / show_dir_name / season_dir_name
    final_target_dir = shows_dir / show_dir_name / season_dir_name
    tvshow_nfo_path = target_dir.parent / "tvshow.nfo"
    season_nfo_path = target_dir / "season.nfo"

    episode_targets: list[EpisodeTarget] = []
    for ep in episodes:
        file_stem = f"{show_dir_name} - S{ep.season:02d}E{ep.episode:02d}"
        target_file = target_dir / f"{file_stem}{ep.source_file.suffix}"
        episode_targets.append(EpisodeTarget(
            episode=ep.episode,
            season=ep.season,
            source_file=ep.source_file,
            target_file=target_file,
        ))

    return ShowWritePlanDraft(
        target_dir=target_dir,
        final_target_dir=final_target_dir,
        show_dir_name=show_dir_name,
        season_dir_name=season_dir_name,
        episodes=episode_targets,
        tvshow_nfo_path=tvshow_nfo_path,
        season_nfo_path=season_nfo_path,
    )


def detect_show_write_conflict(plan: ShowWritePlanDraft) -> str | None:
    """Detect show write plan conflict.

    新的语义 (fix-show-absolute-episode-ingest-and-agent-search-loop):

    - 已存在 show 根目录 / season 目录本身不再是冲突 — 允许追加
      新 episodes 到现有 show, 这是常见追加发布路径 (后续季 / 动漫
      绝对集数追加入库).
    - 冲突只在具体 episode 产物或 provider 身份不一致时触发.
    - 冲突检查针对 **final target path** (而非 staging 路径) —
      ``ep.target_file`` 指向 ``.media-pilot-staging/{task_id}/...``,
      这条路径每次 task 唯一, 不可能预先存在, 因此拿它检查不出"剧集
      已经发布过 S01E05 的视频". 真正应该检查的是
      ``plan.final_target_dir / "<show_dir_name> - SxxExx.<ext>"``,
      那才是 final published 文件位置.
    """
    # 已存在的 show 目录本身不算冲突.
    # 仅在 tvshow.nfo 已存在但身份不一致时返回 identity_conflict (供
    # 调用方单独提示). 本函数只覆盖文件系统存在性冲突.
    for ep in plan.episodes:
        final_episode_file = _final_episode_target_file(plan, ep)
        if final_episode_file.exists():
            return f"target_episode_file_exists:S{ep.season:02d}E{ep.episode:02d}"
    # episode NFO 也算 episode 产物冲突.
    for ep in plan.episodes:
        final_episode_file = _final_episode_target_file(plan, ep)
        ep_nfo = final_episode_file.with_suffix(".nfo")
        if ep_nfo.exists():
            return (
                f"target_episode_nfo_exists:"
                f"S{ep.season:02d}E{ep.episode:02d}"
            )
    # 已有 season 目录时, 若本 season 目录里已存在任何与当前 episode 同源
    # 的字幕 (包括 `.chs.srt` / `.sc.ass` 等语言后缀), 也算冲突.
    if plan.final_target_dir.exists():
        for ep in plan.episodes:
            final_episode_file = _final_episode_target_file(plan, ep)
            if any(_iter_final_same_stem_subtitles(plan, final_episode_file)):
                return (
                    f"target_episode_subtitle_exists:"
                    f"S{ep.season:02d}E{ep.episode:02d}"
                )
    return None


def _final_episode_target_file(
    plan: ShowWritePlanDraft, ep: EpisodeTarget,
) -> Path:
    """final target 路径下的 episode 文件 — 用于 conflict 检测.

    与 staging 路径不同, 这是已发布 / 待发布的 Jellyfin 实际位置.
    通过 plan.show_dir_name + season/episode + 源文件后缀重建, 与
    ``build_show_write_plan`` 中 ``file_stem`` 的命名约定保持一致.
    """
    file_stem = (
        f"{plan.show_dir_name} - "
        f"S{ep.season:02d}E{ep.episode:02d}"
    )
    return plan.final_target_dir / f"{file_stem}{ep.source_file.suffix}"


def detect_show_identity_conflict(
    plan: ShowWritePlanDraft,
    detail: MetadataDetail,
) -> str | None:
    """检查 show 目录已存在的 tvshow.nfo 是否与当前 task 的 provider 身份一致.

    仅在 tvshow.nfo 已存在时调用; 不存在 → 返回 None (无冲突).
    provider_id 不一致 (e.g. 同一标题但 TMDB id 不同) → 返回
    "show_identity_mismatch", 由 show publish 路径作为 target_conflict 处理.

    注意: 这是字符串级别的不严谨判定 (XML tmdbid 节点), 但足以区分
    "之前发布的 show" 与 "新发布的同名 show". 严格 schema 校验留给未来.
    """
    tvshow_nfo = plan.final_target_dir.parent / "tvshow.nfo"
    if not tvshow_nfo.exists():
        return None
    try:
        content = tvshow_nfo.read_text(encoding="utf-8")
    except OSError:
        return None
    existing_provider_id = detail.provider_id
    if not existing_provider_id:
        return None
    if existing_provider_id not in content:
        return "show_identity_mismatch"
    return None


def execute_show_write(
    session: Session,
    *,
    task_id: str,
    detail: MetadataDetail,
    plan: ShowWritePlanDraft,
    client: httpx.Client,
    progress_callback: Callable[[str], None] | None = None,
    provider: str = "tmdb",
    force_overwrite: bool = False,
) -> ShowWriteResult:
    conflict = detect_show_write_conflict(plan)
    identity_conflict = detect_show_identity_conflict(plan, detail)
    effective_conflict = conflict or identity_conflict
    WritePlanRepository(session).save(
        task_id,
        target_dir=str(plan.target_dir.parent),
        target_file=str(plan.target_dir),
        nfo_path=str(plan.tvshow_nfo_path),
        payload={
            "poster_path": str(plan.target_dir.parent / f"{plan.show_dir_name}-poster.jpg"),
            "fanart_path": str(plan.target_dir.parent / f"{plan.show_dir_name}-fanart.jpg"),
            "clearlogo_path": str(plan.target_dir.parent / f"{plan.show_dir_name}-clearlogo.png"),
            "episodes": [
                {"season": ep.season, "episode": ep.episode, "target_file": str(ep.target_file)}
                for ep in plan.episodes
            ],
            "conflict": effective_conflict,
            "identity_conflict": identity_conflict,
        },
    )
    if effective_conflict is not None and not force_overwrite:
        WriteResultRepository(session).save(
            task_id,
            status="target_conflict",
            payload={"conflict": effective_conflict, "warnings": []},
        )
        return ShowWriteResult(status="target_conflict", warnings=[])

    if force_overwrite:
        # overwrite 路径: 仅清理本次 EpisodeMapping 涉及的 episode 文件
        # / NFO / 同源字幕. show 目录 / season 目录其它 episode / NFO 全部
        # 保留. 这是 show overwrite 的安全边界, 与 movie overwrite (替换整
        # 个目录) 不同.
        _cleanup_show_overwrite_targets(plan=plan)

    plan.target_dir.mkdir(parents=True, exist_ok=True)
    record_generated_file_operation(
        session,
        task_id=task_id,
        operation_type="create_directory",
        permission_level="safe_write",
        target_path=plan.target_dir,
        status="succeeded",
        actor="system",
    )

    warnings: list[str] = []
    if progress_callback is not None:
        progress_callback("write_show_metadata_assets")

    # tvshow.nfo
    tvshow_nfo_bytes = render_tvshow_nfo(detail, provider=provider).encode("utf-8")
    _write_generated_file(
        session, task_id=task_id, path=plan.tvshow_nfo_path,
        content=tvshow_nfo_bytes, role="library_tvshow_nfo", operation_type="write_tvshow_nfo",
    )

    # season.nfo
    season_nfo_bytes = render_season_nfo(
        detail, season_number=plan.episodes[0].season,
        episodes=plan.episodes,
    ).encode("utf-8")
    _write_generated_file(
        session, task_id=task_id, path=plan.season_nfo_path,
        content=season_nfo_bytes, role="library_season_nfo", operation_type="write_season_nfo",
    )

    # 下载图像到 show 目录
    required_poster = _download_image(client, detail.images.poster_url,
                                      plan.target_dir.parent / f"{plan.show_dir_name}-poster.jpg")
    if required_poster is not None:
        _record_generated_asset(session, task_id=task_id,
                                path=plan.target_dir.parent / f"{plan.show_dir_name}-poster.jpg",
                                role="library_poster", operation_type="download_poster")
    else:
        WriteResultRepository(session).save(
            task_id, status="failed",
            payload={"failure_reason": "poster_download_failed", "warnings": warnings},
        )
        return ShowWriteResult(status="failed", warnings=warnings)

    for url, fname, role, op_type, warn_code in (
        (detail.images.backdrop_url, f"{plan.show_dir_name}-fanart.jpg",
         "library_fanart", "download_fanart", "fanart_download_failed"),
        (detail.images.logo_url, f"{plan.show_dir_name}-clearlogo.png",
         "library_clearlogo", "download_clearlogo", "clearlogo_download_failed"),
    ):
        path = plan.target_dir.parent / fname
        if _download_image(client, url, path) is None:
            warnings.append(warn_code)
            continue
        _record_generated_asset(session, task_id=task_id, path=path, role=role,
                                operation_type=op_type)

    # 复制 episode 文件
    for ep in plan.episodes:
        if progress_callback is not None:
            progress_callback(f"copy_episode_S{ep.season:02d}E{ep.episode:02d}")
        ep.target_file.parent.mkdir(parents=True, exist_ok=True)
        duration_ms = _copy_video_to_staging(ep.source_file, ep.target_file)
        video_asset = FileAsset(
            task_id=task_id, role="library_video",
            path=str(ep.target_file),
            size_bytes=ep.target_file.stat().st_size,
        )
        session.add(video_asset)
        session.flush()
        record_file_operation(
            session, task_id=task_id,
            operation_type="copy_to_staging",
            permission_level="safe_write",
            source_path=ep.source_file,
            target_path=ep.target_file,
            status="succeeded",
            actor="system",
            file_asset_id=video_asset.id,
            extra_details={"transfer_method": "copy", "duration_ms": duration_ms},
        )

        # ── same-stem 字幕: 每个 episode 自动携带同源字幕, 不明确字幕
        #    不阻塞 (用 warnings 记录但继续发布).
        _copy_same_stem_subtitles_for_episode(
            session=session,
            task_id=task_id,
            episode=ep,
            warnings=warnings,
        )

    # 写入 episode NFO
    for ep in plan.episodes:
        ep_nfo_path = plan.episode_nfo_paths[ep.episode]
        ep_nfo_bytes = render_episode_nfo(
            detail, season=ep.season, episode=ep.episode,
        ).encode("utf-8")
        _write_generated_file(
            session, task_id=task_id, path=ep_nfo_path,
            content=ep_nfo_bytes, role="library_episode_nfo",
            operation_type="write_episode_nfo",
        )

    # Publish
    try:
        if progress_callback is not None:
            progress_callback("publish_show_to_library")
        _publish_show_staging(
            session, task_id=task_id,
            staging_dir=plan.target_dir.parent,
            final_dir=plan.final_target_dir.parent,
        )
    except OSError as error:
        record_file_operation(
            session, task_id=task_id,
            operation_type="publish_to_library",
            permission_level="safe_write",
            source_path=plan.target_dir.parent,
            target_path=plan.final_target_dir.parent,
            status="failed", actor="system",
            error_message=str(error),
        )
        WriteResultRepository(session).save(
            task_id, status="failed",
            payload={
                "failure_reason": "publish_to_library_failed",
                "warnings": warnings,
            },
        )
        return ShowWriteResult(status="failed", warnings=warnings)

    _repoint_show_assets(session, task_id=task_id,
                         from_dir=plan.target_dir.parent,
                         to_dir=plan.final_target_dir.parent)

    result_status = "warning" if warnings else "succeeded"
    WriteResultRepository(session).save(
        task_id, status=result_status,
        payload={
            "target_dir": str(plan.final_target_dir),
            "tvshow_nfo": str(plan.final_target_dir.parent / "tvshow.nfo"),
            "season_nfo": str(plan.final_target_dir / "season.nfo"),
            "warnings": warnings,
        },
    )

    # ── 清空 staging 任务子目录 ────────────────────────────────────────────
    # 非关键路径: 任何异常 / 越界 / 清理失败都不得影响 WriteResult.status 或
    # task.status. helper 内部已经会写 OperationRecord; 此处只兜底防御性
    # except, 避免异常冒泡改写 result_status.
    _cleanup_show_staging(plan.target_dir, task_id, session)

    return ShowWriteResult(status=result_status, warnings=warnings)


def _cleanup_show_staging(target_dir: Path, task_id: str, session: Session) -> None:
    """从 plan.target_dir 反推 media_root 并清理 staging/<task_id>/.

    plan.target_dir = <media_root>/.media-pilot-staging/<task_id>/<show>/<season>.
    任意一步异常 (越界 / 非法 task_id / OSError) 只 log warning, 不抛.
    """
    try:
        media_root = target_dir.parent.parent.parent.parent
        cleanup_empty_staging_task_dir(media_root, task_id, session)
    except ValueError as exc:
        logger.warning(
            "staging 清理越界 (show, task_id=%s): %s", task_id, exc,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "staging 清理失败 (show, task_id=%s): %s", task_id, exc,
        )


def render_tvshow_nfo(detail: MetadataDetail, *, provider: str = "tmdb") -> str:
    root = Element("tvshow")
    root.append(Comment(" Generated by Media Pilot "))
    _append_text(root, "title", detail.title)
    _append_text(root, "originaltitle", detail.original_title)
    _append_text(root, "year", None if detail.year is None else str(detail.year))
    _append_text(root, "plot", detail.plot)
    _append_text(root, "premiered", detail.premiered)
    _append_text(root, "rating", None if detail.rating is None else str(detail.rating))
    _append_text(root, "source", "Media Pilot")
    _append_repeated_text(root, "genre", detail.genres)
    _append_repeated_text(root, "country", detail.countries)
    _append_repeated_text(root, "studio", detail.studios)
    _append_text(root, "tmdbid", detail.provider_id)
    _append_text(root, "imdbid", detail.external_ids.imdb_id)
    _append_unique_id(root, "tmdb", detail.provider_id, is_default=True)
    _append_unique_id(root, "imdb", detail.external_ids.imdb_id, is_default=False)
    for director in detail.credits.directors:
        _append_text(root, "director", director.name)
    for actor in detail.credits.actors:
        actor_element = SubElement(root, "actor")
        _append_text(actor_element, "name", actor.name)
        _append_text(actor_element, "role", actor.role)
        _append_text(actor_element, "thumb", actor.image_url)
        _append_text(actor_element, "profile", actor.profile_url)
        _append_text(actor_element, "tmdbid", actor.provider_id)
    return tostring(root, encoding="unicode")


def render_season_nfo(
    detail: MetadataDetail,
    *,
    season_number: int,
    episodes: list[EpisodeTarget],
) -> str:
    root = Element("season")
    root.append(Comment(" Generated by Media Pilot "))
    _append_text(root, "title", detail.title)
    _append_text(root, "originaltitle", detail.original_title)
    _append_text(root, "seasonnumber", str(season_number))
    _append_text(root, "plot", detail.plot)
    _append_text(root, "year", None if detail.year is None else str(detail.year))
    return tostring(root, encoding="unicode")


def render_episode_nfo(
    detail: MetadataDetail,
    *,
    season: int,
    episode: int,
) -> str:
    root = Element("episodedetails")
    root.append(Comment(" Generated by Media Pilot "))
    _append_text(root, "title", detail.title)
    _append_text(root, "showtitle", detail.title)
    _append_text(root, "season", str(season))
    _append_text(root, "episode", str(episode))
    _append_text(root, "plot", detail.plot)
    _append_text(root, "premiered", detail.premiered)
    _append_text(root, "rating", None if detail.rating is None else str(detail.rating))
    _append_text(root, "tmdbid", detail.provider_id)
    _append_text(root, "imdbid", detail.external_ids.imdb_id)
    for director in detail.credits.directors:
        _append_text(root, "director", director.name)
    for actor in detail.credits.actors:
        actor_element = SubElement(root, "actor")
        _append_text(actor_element, "name", actor.name)
        _append_text(actor_element, "role", actor.role)
        _append_text(actor_element, "thumb", actor.image_url)
    return tostring(root, encoding="unicode")


def _show_directory_name(title: str, year: int | None) -> str:
    if year is None:
        return title
    return f"{title} ({year})"


def _copy_video_to_staging(source_path: Path, target_path: Path) -> int:
    started_at = time.perf_counter()
    shutil.copy2(source_path, target_path)
    return int((time.perf_counter() - started_at) * 1000)


def _copy_same_stem_subtitles_for_episode(
    *,
    session: Session,
    task_id: str,
    episode: EpisodeTarget,
    warnings: list[str],
) -> None:
    """复制单个 episode 的 same-stem 字幕到目标 season 目录.

    - 同源字幕: 与 episode 视频文件 stem 完全一致 (或以 video_stem + '.'
      开头, 例如 ``Show.S01E01.chs.srt``).
    - 复制到 ``episode.target_file.parent`` (即 Season 目录).
    - 字幕 stem 与 target_file stem 不一致时, 保留差异部分; 一致时
      直接拼 .srt / .ass 等后缀.
    - 复制失败 / 源文件丢失 → 写 warning, 不阻塞发布.
    - 不明确字幕 (与其它 episode 共享的非同源字幕) 不在 episode 维度
      处理 — 留给用户后续手动选.
    """
    from media_pilot.services.task_input_analysis import _find_same_stem_subtitles

    target_stem = episode.target_file.stem
    source_video_stem = episode.source_file.stem

    try:
        same_stem_subs = _find_same_stem_subtitles(episode.source_file)
    except Exception as exc:
        warnings.append(
            f"subtitle_scan_failed:S{episode.season:02d}E{episode.episode:02d}:{exc}"
        )
        return

    for sub in same_stem_subs:
        sub_source = Path(sub.path)
        if not sub_source.exists() or not sub_source.is_file():
            warnings.append(
                f"subtitle_copy_failed:S{episode.season:02d}E{episode.episode:02d}:"
                f"{sub.name}:source_missing"
            )
            continue
        if sub_source == episode.source_file:
            continue
        sub_ext = sub_source.suffix
        try:
            # 保留同源字幕的 stem 差异 (例如 chs / eng)
            if sub_source.stem == source_video_stem:
                target_name = f"{target_stem}{sub_ext}"
            elif sub_source.stem.startswith(source_video_stem + "."):
                kept = sub_source.stem[len(source_video_stem):]
                target_name = f"{target_stem}{kept}{sub_ext}"
            else:
                # same_stem 来自 _find_same_stem_subtitles 的严格匹配, 但
                # 防御性处理: 直接按 video_stem+sub_ext 输出.
                target_name = f"{target_stem}{sub_ext}"

            sub_target = episode.target_file.parent / target_name
            shutil.copy2(sub_source, sub_target)

            sub_asset = FileAsset(
                task_id=task_id, role="library_subtitle",
                path=str(sub_target),
                size_bytes=sub_target.stat().st_size,
            )
            session.add(sub_asset)
            session.flush()
            record_file_operation(
                session,
                task_id=task_id,
                operation_type="copy_subtitle_to_staging",
                permission_level="safe_write",
                source_path=sub_source,
                target_path=sub_target,
                status="succeeded",
                actor="system",
                file_asset_id=sub_asset.id,
            )
        except Exception as exc:
            warnings.append(
                f"subtitle_copy_failed:S{episode.season:02d}E{episode.episode:02d}:"
                f"{sub.name}:{exc}"
            )


def _publish_show_staging(
    session: Session,
    *,
    task_id: str,
    staging_dir: Path,
    final_dir: Path,
):
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_dir.exists():
        # 已存在的 final show 目录: 递归合并 staging show root.
        # root 资产 (tvshow.nfo / poster / fanart / clearlogo) 写入 final
        # show root, season 内容只合并到对应 final season 目录.
        _merge_staging_show_root(staging_dir, final_dir)
    else:
        shutil.move(str(staging_dir), str(final_dir))
    return record_file_operation(
        session, task_id=task_id,
        operation_type="publish_to_library",
        permission_level="safe_write",
        source_path=staging_dir,
        target_path=final_dir,
        status="succeeded",
        actor="system",
    )


def _merge_staging_show_root(staging_dir: Path, final_dir: Path) -> None:
    """把 staging show root 递归合并到已存在的 final show root.

    - root 级文件进入 final show root (tvshow.nfo / poster / fanart / clearlogo)
    - season 子目录递归合并到对应 final season 目录
    - 同名文件以 staging 覆盖 final, 但不删除无关 season / episode
    """
    final_dir.mkdir(parents=True, exist_ok=True)
    for src in staging_dir.iterdir():
        dst = final_dir / src.name
        if src.is_dir():
            if dst.exists():
                _merge_staging_show_root(src, dst)
                try:
                    src.rmdir()
                except OSError:
                    pass
                continue
            shutil.move(str(src), str(dst))
            continue

        if dst.exists():
            if dst.is_dir():
                raise OSError(
                    f"Cannot merge show asset file onto directory target: {dst}"
                )
            dst.unlink()
        shutil.move(str(src), str(dst))
    try:
        staging_dir.rmdir()
    except OSError:
        pass


def _cleanup_show_overwrite_targets(plan: ShowWritePlanDraft) -> None:
    """Overwrite 路径: 仅清理本次 EpisodeMapping 涉及的产物.

    删除范围严格限定 (final target path, 不是 staging):
    - 本次每个 episode 的 final video 路径 (e.g. S01E05.mkv)
    - 本次每个 episode 的 final episode NFO
    - 与本次每个 episode 同源的字幕 (.srt / .ass 同 stem)
    - tvshow.nfo / season.nfo 不删除 (共享 NFO, 复用)
    - 其它已存在的 episode 文件不动

    这是 show overwrite 的安全边界 — 与 movie overwrite (替换整个目录)
    不同, show 必须保留其它 episode.
    """
    for ep in plan.episodes:
        final_episode_file = _final_episode_target_file(plan, ep)
        if final_episode_file.exists():
            try:
                final_episode_file.unlink()
            except OSError:
                pass
        ep_nfo = final_episode_file.with_suffix(".nfo")
        if ep_nfo.exists():
            try:
                ep_nfo.unlink()
            except OSError:
                pass
        # 同源字幕: 仅清理当前 episode final target stem 对应的字幕产物,
        # 包括 `.chs.srt` / `.sc.ass` 等语言后缀, 不删其它 episode.
        for sibling in _iter_final_same_stem_subtitles(plan, final_episode_file):
            try:
                sibling.unlink()
            except OSError:
                pass


def _repoint_show_assets(
    session: Session,
    *,
    task_id: str,
    from_dir: Path,
    to_dir: Path,
) -> None:
    from_prefix = str(from_dir)
    to_prefix = str(to_dir)
    assets = session.query(FileAsset).filter(FileAsset.task_id == task_id).all()
    for asset in assets:
        if asset.path.startswith(from_prefix):
            asset.path = asset.path.replace(from_prefix, to_prefix, 1)


def _iter_final_same_stem_subtitles(
    plan: ShowWritePlanDraft,
    final_episode_file: Path,
):
    if not plan.final_target_dir.exists():
        return
    target_stem = final_episode_file.stem
    for sibling in plan.final_target_dir.iterdir():
        if not sibling.is_file():
            continue
        if sibling.suffix.lower() not in SUBTITLE_EXTENSIONS:
            continue
        if is_same_stem_subtitle(sibling.stem, target_stem):
            yield sibling


def _write_generated_file(
    session: Session,
    *,
    task_id: str,
    path: Path,
    content: bytes,
    role: str,
    operation_type: str,
) -> None:
    path.write_bytes(content)
    _record_generated_asset(session, task_id=task_id, path=path, role=role,
                            operation_type=operation_type)


def _record_generated_asset(
    session: Session,
    *,
    task_id: str,
    path: Path,
    role: str,
    operation_type: str,
) -> None:
    asset = FileAsset(
        task_id=task_id, role=role, path=str(path),
        size_bytes=path.stat().st_size if path.exists() and path.is_file() else None,
    )
    session.add(asset)
    session.flush()
    record_generated_file_operation(
        session, task_id=task_id,
        operation_type=operation_type,
        permission_level="safe_write",
        target_path=path,
        status="succeeded",
        actor="system",
        file_asset_id=asset.id,
    )


def _download_image(client: httpx.Client, url: str | None, path: Path) -> bytes | None:
    if not url:
        return None
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    path.write_bytes(response.content)
    return response.content


def _append_text(parent: Element, name: str, value: str | None) -> None:
    if value is None or value == "":
        return
    child = SubElement(parent, name)
    child.text = value


def _append_repeated_text(parent: Element, name: str, values: list[str]) -> None:
    for value in values:
        _append_text(parent, name, value)


def _append_unique_id(
    parent: Element, id_type: str, value: str | None, *, is_default: bool,
) -> None:
    if value is None or value == "":
        return
    child = SubElement(parent, "uniqueid")
    child.set("type", id_type)
    child.set("default", "true" if is_default else "false")
    child.text = value
