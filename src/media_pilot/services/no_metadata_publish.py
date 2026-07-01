"""无元数据入库服务.

该能力只绕过元数据缺失/不明确, 不绕过输入结构、路径、目标冲突等
安全硬门禁。首版支持单文件电影和 BDMV 电影目录, 不支持剧集。
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from media_pilot.config import AppConfig
from media_pilot.orchestration.safe_naming import safe_file_stem, safe_path_component
from media_pilot.orchestration.staging_cleanup import cleanup_empty_staging_task_dir
from media_pilot.repository.audit import record_file_operation, record_generated_file_operation
from media_pilot.repository.models import FileAsset
from media_pilot.repository.repositories import (
    IngestTaskRepository,
    MediaSourceSelectionRepository,
    WritePlanRepository,
    WriteResultRepository,
)
from media_pilot.services.disc_input import resolve_bdmv_movie_source
from media_pilot.services.library_root_resolver import resolve_library_root
from media_pilot.services.task_input_analysis import _find_same_stem_subtitles
from media_pilot.services.video_source_resolver import resolve_main_video_for_publish

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class NoMetadataPublishResult:
    status: str
    summary: str
    final_target_dir: str | None = None
    final_target_file: str | None = None
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def publish_without_metadata(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
    force_overwrite: bool = False,
    allow_agent_running: bool = False,
    library_target: str | None = None,
) -> NoMetadataPublishResult:
    """把已确认发布对象以无元数据方式发布到电影库.

    不创建 MetadataDetail / NFO / 图片资产。发布成功后把
    task.metadata_status 置为 ``none``。
    """

    task_repo = IngestTaskRepository(session)
    task = task_repo.get(task_id)
    if task is None:
        return NoMetadataPublishResult(
            status="rejected", summary="任务不存在", blocking_reasons=["task_not_found"],
        )
    if task.status == "agent_running" and not allow_agent_running:
        return NoMetadataPublishResult(
            status="rejected",
            summary="任务正在 Agent 处理中，请等待完成或先恢复卡住运行",
            blocking_reasons=["agent_running"],
        )
    if task.media_type == "show":
        return NoMetadataPublishResult(
            status="rejected",
            summary="无元数据入库首版不支持剧集",
            blocking_reasons=["show_not_supported"],
        )

    resolve_result = resolve_main_video_for_publish(session, task, config=config)
    if resolve_result.error_code is not None or resolve_result.video_path is None:
        return NoMetadataPublishResult(
            status="rejected",
            summary=resolve_result.error_message or "无法解析发布对象",
            blocking_reasons=[resolve_result.error_code or "invalid_video_source"],
        )

    source_path = resolve_result.video_path
    source_kind = resolve_result.source_kind
    if source_kind not in ("file", "bdmv"):
        return NoMetadataPublishResult(
            status="rejected",
            summary=f"无元数据入库不支持 source_kind={source_kind}",
            blocking_reasons=["unsupported_source_kind"],
        )

    try:
        library_root = _resolve_no_metadata_library_root(
            config, task=task, library_target=library_target,
        )
    except ValueError as exc:
        reason = (
            "library_target_required"
            if "library_target is required" in str(exc)
            else "invalid_library_target"
        )
        return NoMetadataPublishResult(
            status="rejected",
            summary=str(exc),
            blocking_reasons=[reason],
        )
    plan = build_no_metadata_plan(
        library_root=library_root,
        source_path=source_path,
        task_id=task.id,
        source_kind=source_kind,
    )

    conflict = detect_no_metadata_conflict(plan)
    if conflict is not None and not force_overwrite:
        WritePlanRepository(session).save(
            task_id,
            target_dir=str(plan.target_dir),
            target_file=str(plan.target_file),
            nfo_path=None,
            payload={
                "publish_mode": "no_metadata",
                "source_kind": source_kind,
                "conflict": conflict,
                "final_target_dir": str(plan.final_target_dir),
                "final_target_file": str(plan.final_target_file),
            },
        )
        WriteResultRepository(session).save(
            task_id,
            status="target_conflict",
            payload={"publish_mode": "no_metadata", "conflict": conflict},
        )
        return NoMetadataPublishResult(
            status="target_conflict",
            summary=f"目标已存在: {conflict}",
            final_target_dir=str(plan.final_target_dir),
            final_target_file=str(plan.final_target_file),
            blocking_reasons=["target_conflict"],
        )

    if force_overwrite and conflict is not None:
        _remove_existing_target(plan)

    try:
        _execute_no_metadata_write(
            session=session, task_id=task.id, source_path=source_path, plan=plan,
        )
    except Exception as exc:  # noqa: BLE001
        WriteResultRepository(session).save(
            task_id,
            status="failed",
            payload={
                "publish_mode": "no_metadata",
                "failure_reason": f"no_metadata_publish_failed:{type(exc).__name__}:{exc}",
            },
        )
        return NoMetadataPublishResult(
            status="failed",
            summary=f"无元数据入库失败: {exc}",
            blocking_reasons=["write_failed"],
        )

    task.status = "library_import_complete"
    task.current_step = "library_import_complete"
    task.metadata_status = "none"
    task.failure_reason = None
    session.flush()
    return NoMetadataPublishResult(
        status="published",
        summary="已按无元数据方式入库",
        final_target_dir=str(plan.final_target_dir),
        final_target_file=str(plan.final_target_file),
    )


@dataclass(frozen=True, kw_only=True)
class NoMetadataPlan:
    source_kind: str
    target_dir: Path
    target_file: Path
    final_target_dir: Path
    final_target_file: Path


def build_no_metadata_plan(
    *,
    library_root: Path,
    source_path: Path,
    task_id: str,
    source_kind: str,
) -> NoMetadataPlan:
    base_name = safe_path_component(source_path.stem if source_path.is_file() else source_path.name)
    target_dir = library_root / ".media-pilot-staging" / task_id / base_name
    final_target_dir = library_root / base_name
    if source_kind == "bdmv":
        target_file = target_dir / "BDMV" / "index.bdmv"
        final_target_file = final_target_dir / "BDMV" / "index.bdmv"
    else:
        stem = safe_file_stem(base_name, extension=source_path.suffix)
        target_file = target_dir / f"{stem}{source_path.suffix}"
        final_target_file = final_target_dir / f"{stem}{source_path.suffix}"
    return NoMetadataPlan(
        source_kind=source_kind,
        target_dir=target_dir,
        target_file=target_file,
        final_target_dir=final_target_dir,
        final_target_file=final_target_file,
    )


def detect_no_metadata_conflict(plan: NoMetadataPlan) -> str | None:
    if plan.final_target_dir.exists():
        return "final_target_dir_exists"
    if plan.target_dir.exists():
        return "target_dir_exists"
    return None


def _execute_no_metadata_write(
    *,
    session: Session,
    task_id: str,
    source_path: Path,
    plan: NoMetadataPlan,
) -> None:
    WritePlanRepository(session).save(
        task_id,
        target_dir=str(plan.target_dir),
        target_file=str(plan.target_file),
        nfo_path=None,
        payload={
            "publish_mode": "no_metadata",
            "source_kind": plan.source_kind,
            "final_target_dir": str(plan.final_target_dir),
            "final_target_file": str(plan.final_target_file),
        },
    )
    plan.target_dir.mkdir(parents=True, exist_ok=True)
    record_generated_file_operation(
        session,
        task_id=task_id,
        operation_type="create_no_metadata_directory",
        permission_level="safe_write",
        target_path=plan.target_dir,
        status="succeeded",
        actor="system",
    )

    if plan.source_kind == "bdmv":
        _copy_bdmv_no_metadata(session, task_id=task_id, source_path=source_path, plan=plan)
    else:
        _copy_file_no_metadata(session, task_id=task_id, source_path=source_path, plan=plan)

    record_file_operation(
        session,
        task_id=task_id,
        operation_type="publish_no_metadata_to_library",
        permission_level="safe_write",
        source_path=plan.target_dir,
        target_path=plan.final_target_dir,
        status="succeeded",
        actor="system",
    )
    plan.final_target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(plan.target_dir), str(plan.final_target_dir))
    _repoint_file_assets(session, task_id=task_id, from_dir=plan.target_dir, to_dir=plan.final_target_dir)
    WriteResultRepository(session).save(
        task_id,
        status="succeeded",
        payload={
            "publish_mode": "no_metadata",
            "target_dir": str(plan.final_target_dir),
            "target_file": str(plan.final_target_file),
            "source_kind": plan.source_kind,
            "warnings": [],
        },
    )
    _cleanup_no_metadata_staging(plan.target_dir, task_id, session)


def _copy_file_no_metadata(
    session: Session, *, task_id: str, source_path: Path, plan: NoMetadataPlan,
) -> None:
    started_at = time.perf_counter()
    shutil.copy2(source_path, plan.target_file)
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    asset = FileAsset(
        task_id=task_id,
        role="library_video",
        path=str(plan.target_file),
        size_bytes=plan.target_file.stat().st_size,
    )
    session.add(asset)
    session.flush()
    record_file_operation(
        session,
        task_id=task_id,
        operation_type="copy_no_metadata_video_to_staging",
        permission_level="safe_write",
        source_path=source_path,
        target_path=plan.target_file,
        status="succeeded",
        actor="system",
        file_asset_id=asset.id,
        extra_details={"transfer_method": "copy", "duration_ms": duration_ms},
    )
    _copy_same_stem_subtitles(session, task_id=task_id, source_path=source_path, plan=plan)


def _copy_same_stem_subtitles(
    session: Session, *, task_id: str, source_path: Path, plan: NoMetadataPlan,
) -> None:
    final_stem = plan.target_file.stem
    for sub in _find_same_stem_subtitles(source_path):
        sub_source = Path(sub.path)
        if not sub_source.exists():
            continue
        kept = ""
        if sub_source.stem.startswith(source_path.stem):
            kept = sub_source.stem[len(source_path.stem):]
        sub_target = plan.target_dir / f"{final_stem}{kept}{sub_source.suffix}"
        shutil.copy2(sub_source, sub_target)
        asset = FileAsset(
            task_id=task_id,
            role="library_subtitle",
            path=str(sub_target),
            size_bytes=sub_target.stat().st_size,
        )
        session.add(asset)
        session.flush()
        record_file_operation(
            session,
            task_id=task_id,
            operation_type="copy_no_metadata_subtitle_to_staging",
            permission_level="safe_write",
            source_path=sub_source,
            target_path=sub_target,
            status="succeeded",
            actor="system",
            file_asset_id=asset.id,
        )


def _copy_bdmv_no_metadata(
    session: Session, *, task_id: str, source_path: Path, plan: NoMetadataPlan,
) -> None:
    bdmv_source = resolve_bdmv_movie_source(source_path)
    if bdmv_source is None:
        raise ValueError(f"Not a supported BDMV movie source: {source_path}")
    started_at = time.perf_counter()
    shutil.copytree(bdmv_source.bdmv_dir, plan.target_dir / "BDMV", dirs_exist_ok=True)
    if bdmv_source.certificate_dir is not None:
        shutil.copytree(
            bdmv_source.certificate_dir,
            plan.target_dir / "CERTIFICATE",
            dirs_exist_ok=True,
        )
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    asset = FileAsset(
        task_id=task_id,
        role="library_bdmv",
        path=str(plan.target_dir / "BDMV"),
        size_bytes=None,
    )
    session.add(asset)
    session.flush()
    record_file_operation(
        session,
        task_id=task_id,
        operation_type="copy_no_metadata_bdmv_to_staging",
        permission_level="safe_write",
        source_path=source_path,
        target_path=plan.target_dir / "BDMV",
        status="succeeded",
        actor="system",
        file_asset_id=asset.id,
        extra_details={"transfer_method": "copytree", "duration_ms": duration_ms},
    )


def _remove_existing_target(plan: NoMetadataPlan) -> None:
    if plan.final_target_dir.exists() and plan.final_target_dir.is_dir():
        shutil.rmtree(plan.final_target_dir)
    if plan.target_dir.exists() and plan.target_dir.is_dir():
        shutil.rmtree(plan.target_dir)


def _repoint_file_assets(
    session: Session, *, task_id: str, from_dir: Path, to_dir: Path,
) -> None:
    from_prefix = str(from_dir)
    to_prefix = str(to_dir)
    assets = session.query(FileAsset).filter(FileAsset.task_id == task_id).all()
    for asset in assets:
        if asset.path.startswith(from_prefix):
            asset.path = asset.path.replace(from_prefix, to_prefix, 1)


def _cleanup_no_metadata_staging(target_dir: Path, task_id: str, session: Session) -> None:
    """清理 ``.media-pilot-staging/<task_id>`` 空目录, 不影响发布结果。"""
    try:
        media_root = target_dir.parent.parent.parent
        cleanup_empty_staging_task_dir(media_root, task_id, session)
    except ValueError as exc:
        logger.warning("staging 清理越界 (no_metadata, task_id=%s): %s", task_id, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("staging 清理失败 (no_metadata, task_id=%s): %s", task_id, exc)


def _provider_hint_for_task(task) -> str | None:
    # 已预选成人影片时, 即使没有 MetadataDetail 也应发布到成人库根。
    if task.preselected_metadata_provider:
        return task.preselected_metadata_provider
    if task.preselected_metadata_profile == "tpdb_adult_movie":
        return "tpdb"
    return None


def _resolve_no_metadata_library_root(
    config: AppConfig, *, task, library_target: str | None,
) -> Path:
    """解析无元数据入库目标库。

    无元数据任务没有可靠 provider 事实，不能静默把未知电影放进普通电影库。
    UI/API 显式选择 ``movie`` / ``adult`` 时按选择路由；Agent 决策路径可
    继续消费任务已有的 preselected provider/profile hint。
    """
    if library_target == "movie":
        return resolve_library_root(config, media_type="movie", provider="tmdb")
    if library_target == "adult":
        return resolve_library_root(config, media_type="movie", provider="tpdb")
    if library_target not in (None, ""):
        raise ValueError(f"Unsupported no-metadata library target: {library_target}")

    provider_hint = _provider_hint_for_task(task)
    if provider_hint is None:
        raise ValueError(
            "library_target is required for no-metadata publish when no "
            "metadata provider hint exists"
        )
    return resolve_library_root(config, media_type="movie", provider=provider_hint)
