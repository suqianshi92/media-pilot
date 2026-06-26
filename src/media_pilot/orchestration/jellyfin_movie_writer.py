import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Comment, Element, SubElement, tostring

import httpx
from sqlalchemy.orm import Session

from media_pilot.adapters.metadata import MetadataDetail
from media_pilot.orchestration.safe_naming import (
    movie_directory_name,
    movie_path_identifier,
    safe_file_stem,
)
from media_pilot.orchestration.staging_cleanup import cleanup_empty_staging_task_dir
from media_pilot.repository.audit import record_file_operation, record_generated_file_operation
from media_pilot.repository.models import FileAsset
from media_pilot.repository.repositories import WritePlanRepository, WriteResultRepository

logger = logging.getLogger(__name__)

CANONICAL_QUALITY_TOKENS = {
    "480p": "480p",
    "720p": "720p",
    "1080p": "1080p",
    "2160p": "2160p",
    "4k": "4K",
    "web-dl": "WEB-DL",
    "webrip": "WEBRip",
    "bluray": "BluRay",
    "bdrip": "BDRip",
    "remux": "REMUX",
    "dts": "DTS",
}


@dataclass(frozen=True)
class MovieWritePlanDraft:
    target_dir: Path
    target_file: Path
    final_target_dir: Path
    final_target_file: Path
    nfo_path: Path
    poster_path: Path
    fanart_path: Path
    clearlogo_path: Path


@dataclass(frozen=True)
class MovieWriteResult:
    status: str
    warnings: list[str]


def build_movie_write_plan(
    *,
    movies_dir: Path,
    source_path: Path,
    detail: MetadataDetail,
    task_id: str,
    provider: str = "tmdb",
    identifier: str | None = None,
) -> MovieWritePlanDraft:
    # Sanity check: 目录型 source_path 会在 ``source_path.suffix`` 上拿到
    # 目录名后缀 (e.g. `[YTS.MX]` → `.MX]`), 之后 ``shutil.copy2`` 会抛
    # ``IsADirectoryError``. services/video_source_resolver.py 应在调用
    # 本函数前过滤掉目录, 这里只是开发期兜底.
    #
    # 注意: 只查 "是不是目录", 不查 is_file() — 测试可能用不存在的路径
    # (mock 场景), 让 plan 计算可以先跑, 真正的 fs 错误留给 execute_movie_write.
    assert not source_path.is_dir(), (
        f"build_movie_write_plan 收到目录路径, 应通过 "
        f"services/video_source_resolver.py 解析为文件: {source_path}"
    )
    path_identifier = identifier or movie_path_identifier(
        provider=provider,
        title=detail.title,
        original_title=detail.original_title,
        provider_id=detail.provider_id,
        payload=detail.payload,
    )
    directory_name = _movie_directory_name(
        detail.title, detail.year, identifier=path_identifier,
    )
    quality_suffix = _quality_suffix_from_source_name(
        source_stem=source_path.stem,
        title=detail.title,
        year=detail.year,
    )
    target_dir = movies_dir / ".media-pilot-staging" / task_id / directory_name
    final_target_dir = movies_dir / directory_name
    raw_file_stem = directory_name if quality_suffix == "" else f"{directory_name} - {quality_suffix}"
    file_stem = safe_file_stem(raw_file_stem, extension=source_path.suffix)
    target_file = target_dir / f"{file_stem}{source_path.suffix}"
    final_target_file = final_target_dir / f"{file_stem}{source_path.suffix}"
    nfo_path = target_dir / f"{directory_name}.nfo"
    poster_path = target_dir / f"{directory_name}-poster.jpg"
    fanart_path = target_dir / f"{directory_name}-fanart.jpg"
    clearlogo_path = target_dir / f"{directory_name}-clearlogo.png"
    return MovieWritePlanDraft(
        target_dir=target_dir,
        target_file=target_file,
        final_target_dir=final_target_dir,
        final_target_file=final_target_file,
        nfo_path=nfo_path,
        poster_path=poster_path,
        fanart_path=fanart_path,
        clearlogo_path=clearlogo_path,
    )


def detect_movie_write_conflict(plan: MovieWritePlanDraft) -> str | None:
    if plan.final_target_file.exists():
        return "final_target_file_exists"
    if plan.final_target_dir.exists():
        return "final_target_dir_exists"
    if plan.target_file.exists():
        return "target_file_exists"
    if plan.nfo_path.exists():
        return "nfo_path_exists"
    if plan.target_dir.exists():
        return "target_dir_exists"
    return None


def execute_movie_write(
    session: Session,
    *,
    task_id: str,
    source_path: Path,
    detail: MetadataDetail,
    plan: MovieWritePlanDraft,
    client: httpx.Client,
    progress_callback: Callable[[str], None] | None = None,
    provider: str = "tmdb",
    identifier: str | None = None,
    force_overwrite: bool = False,
) -> MovieWriteResult:
    # ── 字幕预校验: 必须在任何 staging 写入之前完成 ──────────────────────
    # 解析任务输入节点, 校验 MediaSourceSelection.payload.selected_subtitles
    # 中所有路径存在且在 input 节点内. 越界或缺失 → 立即拒绝本次发布,
    # 不创建 staging 目录、不写 NFO / 海报 / 视频, 避免留下半截 staging
    # 残留. 仅写 WriteResult failed 留痕.
    pre_warnings: list[str] = []
    pre_task_input_root = _resolve_task_input_root(session, task_id, source_path)
    pre_user_selected = _load_user_selected_subtitles(
        session, task_id=task_id, source_root=pre_task_input_root,
    )
    if pre_user_selected is _USER_SUBS_UNSAFE:
        WriteResultRepository(session).save(
            task_id,
            status="failed",
            payload={
                "failure_reason": "unsafe_user_selected_subtitles",
                "warnings": pre_warnings + [
                    "unsafe_user_selected_subtitles:path_outside_input_or_missing"
                ],
            },
        )
        return MovieWriteResult(
            status="failed",
            warnings=pre_warnings + [
                "unsafe_user_selected_subtitles:path_outside_input_or_missing"
            ],
        )

    conflict = detect_movie_write_conflict(plan)
    WritePlanRepository(session).save(
        task_id,
        target_dir=str(plan.target_dir),
        target_file=str(plan.target_file),
        nfo_path=str(plan.nfo_path),
        payload={
            "poster_path": str(plan.poster_path),
            "fanart_path": str(plan.fanart_path),
            "clearlogo_path": str(plan.clearlogo_path),
            "conflict": conflict,
            "force_overwrite": force_overwrite,
        },
    )
    if conflict is not None and not force_overwrite:
        WriteResultRepository(session).save(
            task_id,
            status="target_conflict",
            payload={"conflict": conflict, "warnings": []},
        )
        return MovieWriteResult(status="target_conflict", warnings=[])

    if force_overwrite and conflict is not None:
        # 在用户已显式同意覆盖的前提下，先删除 final_target_dir / final_target_file，
        # 再进入正常发布流程；安全硬门禁（路径必须在 movies_dir 内）由调用方
        # 的 movies_dir 边界保护。
        if plan.final_target_file.exists() and plan.final_target_file.is_file():
            plan.final_target_file.unlink()
        if plan.final_target_dir.exists() and plan.final_target_dir.is_dir():
            shutil.rmtree(plan.final_target_dir)
        # 重新检测一次，conflict 应该已经被清空；保留作为安全网
        conflict = detect_movie_write_conflict(plan)

    plan.target_dir.mkdir(parents=True, exist_ok=True)
    dir_operation = record_generated_file_operation(
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
        progress_callback("write_metadata_assets")
    nfo_bytes = render_movie_nfo(
        detail, provider=provider, identifier=identifier,
    ).encode("utf-8")
    _write_generated_file(
        session,
        task_id=task_id,
        path=plan.nfo_path,
        content=nfo_bytes,
        role="library_nfo",
        operation_type="write_nfo",
    )

    required_image = _download_image(client, detail.images.poster_url, plan.poster_path)
    if required_image is not None:
        _record_generated_asset(
            session,
            task_id=task_id,
            path=plan.poster_path,
            role="library_poster",
            operation_type="download_poster",
        )
    else:
        WriteResultRepository(session).save(
            task_id,
            status="failed",
            payload={"failure_reason": "poster_download_failed", "warnings": warnings},
        )
        return MovieWriteResult(status="failed", warnings=warnings)

    for url, path, role, operation_type, warning_code in (
        (
            detail.images.backdrop_url,
            plan.fanart_path,
            "library_fanart",
            "download_fanart",
            "fanart_download_failed",
        ),
        (
            detail.images.logo_url,
            plan.clearlogo_path,
            "library_clearlogo",
            "download_clearlogo",
            "clearlogo_download_failed",
        ),
    ):
        if _download_image(client, url, path) is None:
            warnings.append(warning_code)
            continue
        _record_generated_asset(
            session,
            task_id=task_id,
            path=path,
            role=role,
            operation_type=operation_type,
        )

    plan.target_file.parent.mkdir(parents=True, exist_ok=True)
    if progress_callback is not None:
        progress_callback("copy_to_staging")
    duration_ms = _copy_video_to_staging(source_path, plan.target_file)
    video_asset = FileAsset(
        task_id=task_id,
        role="library_video",
        path=str(plan.target_file),
        size_bytes=plan.target_file.stat().st_size,
    )
    session.add(video_asset)
    session.flush()
    move_operation = record_file_operation(
        session,
        task_id=task_id,
        operation_type="copy_to_staging",
        permission_level="safe_write",
        source_path=source_path,
        target_path=plan.target_file,
        status="succeeded",
        actor="system",
        file_asset_id=video_asset.id,
        extra_details={
            "transfer_method": "copy",
            "duration_ms": duration_ms,
        },
    )

    # ── subtitle copy: 优先消费用户明确选择的字幕, 否则 same-stem 自动带入 ─
    # 字幕安全已经在函数开头预校验 (unSafe 路径在创建 staging 之前就
    # 失败返回), 此处只处理 _USER_SUBS_NOT_FOUND (input 节点无法解析
    # → same-stem fallback) 和复制本身.
    from media_pilot.repository.models import MediaSourceSelection
    from media_pilot.repository.repositories import (
        MediaSourceSelectionRepository,
    )
    from media_pilot.services.task_input_analysis import _find_same_stem_subtitles

    final_stem = plan.target_file.stem
    task_input_root = _resolve_task_input_root(session, task_id, source_path)
    user_selected_subs = _load_user_selected_subtitles(
        session, task_id=task_id, source_root=task_input_root,
    )
    if user_selected_subs is _USER_SUBS_NOT_FOUND:
        # 任务输入节点无法解析, 不能安全复制 — 走 same-stem fallback 让发布继续,
        # 同时在 warnings 里记录拒绝原因.
        warnings.append("subtitle_user_selection_skipped:input_node_unresolved")
        user_selected_subs = None

    if user_selected_subs is not None:
        subtitle_specs = _build_user_subtitle_specs(user_selected_subs, final_stem, source_path)
    else:
        subtitle_specs = [
            (Path(sub.path), sub.name, sub.matched_by == "same_stem")
            for sub in _find_same_stem_subtitles(source_path)
        ]
    for sub_source, sub_name, sub_matched_same_stem in subtitle_specs:
        try:
            if not sub_source.exists():
                warnings.append(f"subtitle_copy_failed:{sub_name}:source_missing")
                continue
            sub_ext = sub_source.suffix
            video_stem = source_path.stem
            if sub_matched_same_stem and sub_source.stem != video_stem:
                kept = sub_source.stem[len(video_stem):]
                target_name = f"{final_stem}{kept}{sub_ext}"
            else:
                target_name = f"{final_stem}{sub_ext}"

            sub_target = plan.target_dir / target_name
            shutil.copy2(sub_source, sub_target)

            sub_asset = FileAsset(
                task_id=task_id,
                role="library_subtitle",
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
            warnings.append(f"subtitle_copy_failed:{sub_name}:{exc}")

    try:
        if progress_callback is not None:
            progress_callback("publish_to_library")
        publish_operation = _publish_staging_directory(
            session,
            task_id=task_id,
            staging_dir=plan.target_dir,
            final_dir=plan.final_target_dir,
        )
    except OSError as error:
        record_file_operation(
            session,
            task_id=task_id,
            operation_type="publish_to_library",
            permission_level="safe_write",
            source_path=plan.target_dir,
            target_path=plan.final_target_dir,
            status="failed",
            actor="system",
            error_message=str(error),
        )
        WriteResultRepository(session).save(
            task_id,
            status="failed",
            payload={
                "failure_reason": "publish_to_library_failed",
                "warnings": warnings,
                "target_dir": str(plan.target_dir),
                "target_file": str(plan.target_file),
                "nfo_path": str(plan.nfo_path),
            },
        )
        return MovieWriteResult(status="failed", warnings=warnings)

    _repoint_file_assets(
        session,
        task_id=task_id,
        from_dir=plan.target_dir,
        to_dir=plan.final_target_dir,
    )

    result_status = "warning" if warnings else "succeeded"
    WriteResultRepository(session).save(
        task_id,
        status=result_status,
        payload={
            "target_dir": str(plan.final_target_dir),
            "target_file": str(plan.final_target_file),
            "nfo_path": str(plan.final_target_dir / plan.nfo_path.name),
            "warnings": warnings,
        },
    )

    # ── 清空 staging 任务子目录 ────────────────────────────────────────────
    # 非关键路径: 任何异常 / 越界 / 清理失败都不得影响 WriteResult.status 或
    # task.status. helper 内部已经会写 OperationRecord; 此处只兜底防御性
    # except, 避免异常冒泡改写 result_status.
    _cleanup_movie_staging(plan.target_dir, task_id, session)

    return MovieWriteResult(status=result_status, warnings=warnings)


def _cleanup_movie_staging(target_dir: Path, task_id: str, session: Session) -> None:
    """从 plan.target_dir 反推 media_root 并清理 staging/<task_id>/.

    plan.target_dir = <media_root>/.media-pilot-staging/<task_id>/<dir_name>.
    任意一步异常 (越界 / 非法 task_id / OSError) 只 log warning, 不抛.
    """
    try:
        media_root = target_dir.parent.parent.parent
        cleanup_empty_staging_task_dir(media_root, task_id, session)
    except ValueError as exc:
        # 越界 — helper 抛 ValueError, 不写库, 仅 log warning.
        logger.warning(
            "staging 清理越界 (movie, task_id=%s): %s", task_id, exc,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "staging 清理失败 (movie, task_id=%s): %s", task_id, exc,
        )


def _copy_video_to_staging(source_path: Path, target_path: Path) -> int:
    started_at = time.perf_counter()
    shutil.copy2(source_path, target_path)
    return int((time.perf_counter() - started_at) * 1000)


def _publish_staging_directory(
    session: Session,
    *,
    task_id: str,
    staging_dir: Path,
    final_dir: Path,
):
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging_dir), str(final_dir))
    return record_file_operation(
        session,
        task_id=task_id,
        operation_type="publish_to_library",
        permission_level="safe_write",
        source_path=staging_dir,
        target_path=final_dir,
        status="succeeded",
        actor="system",
    )


def _repoint_file_assets(
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


def render_movie_nfo(
    detail: MetadataDetail,
    *,
    provider: str = "tmdb",
    identifier: str | None = None,
) -> str:
    root = Element("movie")
    root.append(Comment(" Generated by Media Pilot "))
    _append_text(root, "title", detail.title)
    _append_text(root, "originaltitle", detail.original_title)
    _append_text(root, "year", None if detail.year is None else str(detail.year))
    _append_text(root, "plot", detail.plot)
    _append_text(root, "outline", detail.plot)
    runtime = None if detail.runtime_minutes is None else str(detail.runtime_minutes)
    _append_text(root, "runtime", runtime)
    _append_text(root, "premiered", detail.premiered)
    _append_text(root, "rating", None if detail.rating is None else str(detail.rating))
    _append_text(root, "source", "Media Pilot")
    _append_repeated_text(root, "genre", detail.genres)
    _append_repeated_text(root, "country", detail.countries)
    _append_repeated_text(root, "studio", detail.studios)

    if provider == "tpdb":
        # TPDB: 番号/sorttitle，不写入 tmdbid/imdbid
        if identifier:
            _append_text(root, "serial", identifier)
            _append_text(root, "sorttitle", identifier)
        _append_unique_id(root, "tpdb", detail.provider_id, is_default=True)
        _append_text(root, "label", detail.studios[0] if detail.studios else "")
    else:
        _append_text(root, "tmdbid", detail.provider_id)
        _append_text(root, "imdbid", detail.external_ids.imdb_id)
        _append_unique_id(root, "tmdb", detail.provider_id, is_default=True)
        _append_unique_id(root, "imdb", detail.external_ids.imdb_id, is_default=False)

    for director in detail.credits.directors:
        _append_text(root, "director", director.name)
        _append_text(root, "credits", director.name)
    for actor in detail.credits.actors:
        actor_element = SubElement(root, "actor")
        _append_text(actor_element, "name", actor.name)
        _append_text(actor_element, "role", actor.role)
        _append_text(actor_element, "thumb", actor.image_url)
        _append_text(actor_element, "profile", actor.profile_url)
        if provider != "tpdb":
            _append_text(actor_element, "tmdbid", actor.provider_id)
    return tostring(root, encoding="unicode")


def _movie_directory_name(
    title: str, year: int | None, *, identifier: str | None = None
) -> str:
    return movie_directory_name(title, year, identifier=identifier)


def _quality_suffix_from_source_name(
    *,
    source_stem: str,
    title: str,
    year: int | None,
) -> str:
    source_tokens = _tokenize(source_stem)
    title_tokens = _tokenize(title)
    remaining_tokens = list(source_tokens)
    if title_tokens:
        remaining_tokens = _remove_first_subsequence(remaining_tokens, title_tokens)
    if year is not None:
        year_token = str(year)
        remaining_tokens = [token for token in remaining_tokens if token != year_token]
    quality_tokens: list[str] = []
    for token in remaining_tokens:
        canonical = _canonical_quality_token(token)
        if canonical is None or canonical in quality_tokens:
            continue
        quality_tokens.append(canonical)
    return " ".join(quality_tokens)


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
    _record_generated_asset(
        session,
        task_id=task_id,
        path=path,
        role=role,
        operation_type=operation_type,
    )


def _record_generated_asset(
    session: Session,
    *,
    task_id: str,
    path: Path,
    role: str,
    operation_type: str,
) -> None:
    asset = FileAsset(
        task_id=task_id,
        role=role,
        path=str(path),
        size_bytes=path.stat().st_size if path.exists() and path.is_file() else None,
    )
    session.add(asset)
    session.flush()
    operation = record_generated_file_operation(
        session,
        task_id=task_id,
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
    parent: Element,
    id_type: str,
    value: str | None,
    *,
    is_default: bool,
) -> None:
    if value is None or value == "":
        return
    child = SubElement(parent, "uniqueid")
    child.set("type", id_type)
    child.set("default", "true" if is_default else "false")
    child.text = value


def _tokenize(value: str) -> list[str]:
    normalized = value
    for separator in (".", "_", "(", ")"):
        normalized = normalized.replace(separator, " ")
    tokens: list[str] = []
    for segment in normalized.split():
        tokens.extend(_split_hyphenated_segment(segment))
    return tokens


def _remove_first_subsequence(tokens: list[str], pattern: list[str]) -> list[str]:
    if not pattern or len(pattern) > len(tokens):
        return tokens
    for index in range(len(tokens) - len(pattern) + 1):
        if tokens[index : index + len(pattern)] == pattern:
            return tokens[:index] + tokens[index + len(pattern) :]
    return tokens


def _split_hyphenated_segment(segment: str) -> list[str]:
    if "-" not in segment:
        return [segment]

    pieces = [piece for piece in segment.split("-") if piece]
    tokens: list[str] = []
    index = 0
    while index < len(pieces):
        if index + 1 < len(pieces):
            joined = f"{pieces[index]}-{pieces[index + 1]}"
            if _canonical_quality_token(joined) is not None:
                tokens.append(joined)
                index += 2
                continue
        tokens.append(pieces[index])
        index += 1
    return tokens


# ── User-selected subtitle consumption ───────────────────────────────
# MediaSourceSelection.payload.selected_subtitles 表达了用户对字幕的明确选择;
# 发布工具在复制字幕前必须校验路径属于任务输入节点, 否则拒绝本次发布.
# 不在用户选择里的非同源字幕不得自动带入.

_USER_SUBS_NOT_FOUND: object = object()
_USER_SUBS_UNSAFE: object = object()


def _resolve_task_input_root(session: Session, task_id: str, source_path: Path) -> Path | None:
    """优先 MediaSourceSelection.input_path, 退回 source_path.parent / source_path.

    任务输入节点 (input_node) 必须是一个目录 — 文件本身不是节点.
    当 MediaSourceSelection.input_path 指向一个文件 (用户选了主视频后
    把 selected_path 写入) 时, 取其父目录作为输入根.
    """
    from media_pilot.repository.repositories import MediaSourceSelectionRepository

    selection = MediaSourceSelectionRepository(session).get_for_task(task_id)
    candidate: str | None = None
    if selection is not None and selection.input_path:
        candidate = selection.input_path
    elif source_path.is_file():
        candidate = str(source_path.parent)
    else:
        candidate = str(source_path)
    if not candidate:
        return None
    try:
        resolved = Path(candidate).resolve()
    except OSError:
        return None
    if resolved.is_file():
        return resolved.parent
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def _load_user_selected_subtitles(
    session: Session,
    *,
    task_id: str,
    source_root: Path | None,
) -> list[str] | object:
    """读 MediaSourceSelection.payload.selected_subtitles, 校验安全.

    - 没有 MediaSourceSelection 或没有 selected_subtitles key → 返回 None
      (让 same-stem 自动带入生效).
    - 有 selected_subtitles 但 source_root 解析失败 → 返回 _USER_SUBS_NOT_FOUND
      (让 same-stem fallback, 附 warning).
    - 路径越界或不存在 → 返回 _USER_SUBS_UNSAFE (拒绝本次发布).
    """
    from media_pilot.repository.repositories import MediaSourceSelectionRepository

    selection = MediaSourceSelectionRepository(session).get_for_task(task_id)
    if selection is None:
        return None
    payload = selection.payload if isinstance(selection.payload, dict) else {}
    if "selected_subtitles" not in payload:
        return None
    selected = payload.get("selected_subtitles")
    if not isinstance(selected, list):
        return None
    if source_root is None:
        return _USER_SUBS_NOT_FOUND
    for sub_path in selected:
        if not isinstance(sub_path, str) or not sub_path:
            return _USER_SUBS_UNSAFE
        sub = Path(sub_path)
        if not sub.exists() or not sub.is_file():
            return _USER_SUBS_UNSAFE
        if not _is_within(sub, source_root):
            return _USER_SUBS_UNSAFE
    return [str(p) for p in selected]


def _build_user_subtitle_specs(
    user_selected_subs: list[str],
    final_stem: str,
    source_path: Path,
) -> list[tuple[Path, str, bool]]:
    """把用户选择的字幕路径转成 (source, name, matched_same_stem) 三元组.

    同源判定: 如果字幕 stem 以 video stem 开头, 当作 same_stem 处理,
    保留 .zh / .zh-hans 等后缀; 否则整段 stem 替换.
    final_stem 仅为同源命名一致性预留 (保持与 same-stem 路径相同的
    命名约定, 即 final_stem + sub_stem_suffix + ext).
    """
    video_stem = source_path.stem
    specs: list[tuple[Path, str, bool]] = []
    for sub_str in user_selected_subs:
        sub = Path(sub_str)
        name = sub.name
        matched = sub.stem.startswith(video_stem)
        specs.append((sub, name, matched))
    return specs



def _canonical_quality_token(token: str) -> str | None:
    return CANONICAL_QUALITY_TOKENS.get(token.strip().lower())
