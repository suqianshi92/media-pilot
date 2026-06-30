"""Prepare a published task for metadata correction and republish.

This service is intentionally separate from the normal "revoke publish"
operation. A user correction means "remove old library output, then publish
again"; when the original source was already cleaned, the current library
output is copied into controlled staging and becomes a temporary input node.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_pilot.config import AppConfig


@dataclass(frozen=True, kw_only=True)
class RepublishSourceResult:
    status: str  # "prepared" | "failure"
    summary: str
    data: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "prepared"


def prepare_republish_source(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
) -> RepublishSourceResult:
    """Revoke current output while keeping the task ready for republish."""
    from media_pilot.orchestration.revoke_publish import (
        _cleanup_publish_context,
        check_revoke_publish,
    )
    from media_pilot.orchestration.state_machine import IngestTaskStatus
    from media_pilot.repository.models import IngestTask

    check = check_revoke_publish(session, task_id=task_id)
    if not check.allowed:
        return RepublishSourceResult(
            status="failure",
            summary=f"撤销旧发布失败，无法重新入库：{check.outcome_description}",
            data={"reason": "revoke_not_allowed"},
        )

    if not check.source_file_exists:
        prepared = _prepare_from_published_output(
            session=session,
            config=config,
            task_id=task_id,
            publish_dir=check.publish_dir,
        )
        if not prepared.ok:
            return prepared

    _remove_publish_dir(check.publish_dir)
    _cleanup_publish_context(session, task_id)

    task = session.get(IngestTask, task_id)
    if task is not None:
        task.status = IngestTaskStatus.PROCESSING
        task.current_step = "post_revoke_reingest"
    session.flush()
    return RepublishSourceResult(
        status="prepared",
        summary="旧发布已撤销，任务已准备重新入库",
        data={
            "publish_dir": check.publish_dir,
            "source_file_exists": check.source_file_exists,
            "is_complex_structure": check.is_complex_structure,
        },
    )


def cleanup_temporary_republish_source(session: Session, task_id: str) -> bool:
    from media_pilot.repository.repositories import MediaSourceSelectionRepository

    selection = MediaSourceSelectionRepository(session).get_for_task(task_id)
    payload = selection.payload if selection is not None else {}
    if not isinstance(payload, dict):
        return False
    if payload.get("selection_source") != "published_output_reselect":
        return False
    source_dir = Path(selection.input_path)
    if source_dir.exists() and source_dir.is_dir():
        shutil.rmtree(source_dir)
    return True


def _prepare_from_published_output(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
    publish_dir: str | None,
) -> RepublishSourceResult:
    if publish_dir is None:
        return RepublishSourceResult(
            status="failure",
            summary="旧发布记录缺少发布目录，无法从已发布产物重新入库",
            data={"reason": "missing_publish_dir"},
        )

    publish_path = Path(publish_dir)
    if not publish_path.exists() or not publish_path.is_dir():
        trashed = _latest_trashed_input_path(session=session, task_id=task_id)
        if trashed is not None:
            return _prepare_from_existing_input(
                session=session,
                config=config,
                task_id=task_id,
                input_path=trashed,
                staging_parent=publish_path.parent,
                original_publish_dir=publish_path,
                reason="trashed_input_recovery",
            )
        return RepublishSourceResult(
            status="failure",
            summary=f"旧发布目录不存在，无法从已发布产物重新入库：{publish_path}",
            data={"reason": "publish_dir_not_found"},
        )
    if not _is_within_library_root(config, publish_path):
        return RepublishSourceResult(
            status="failure",
            summary=f"旧发布目录不在受控媒体库根目录内，拒绝重新入库：{publish_path}",
            data={"reason": "publish_dir_outside_library_root"},
        )

    from media_pilot.services.disc_input import resolve_bdmv_movie_source

    if resolve_bdmv_movie_source(publish_path) is not None:
        return _prepare_bdmv_source(
            session=session,
            task_id=task_id,
            publish_path=publish_path,
        )
    return _prepare_single_file_source(
        session=session,
        config=config,
        task_id=task_id,
        publish_path=publish_path,
    )


def _prepare_single_file_source(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
    publish_path: Path,
) -> RepublishSourceResult:
    from media_pilot.repository.models import FileAsset, IngestTask
    from media_pilot.repository.repositories import MediaSourceSelectionRepository

    source_file = session.scalars(
        select(FileAsset)
        .where(FileAsset.task_id == task_id)
        .where(FileAsset.role == "library_video")
        .order_by(FileAsset.created_at.desc())
    ).first()
    source_path = Path(source_file.path) if source_file is not None else None
    if source_path is None or not source_path.exists() or not source_path.is_file():
        source_path = _find_single_video_in_published_dir(publish_path)
    if source_path is None:
        return RepublishSourceResult(
            status="failure",
            summary="旧发布目录中没有可复用的单文件主视频，无法重新入库",
            data={"reason": "no_single_video_in_publish_dir"},
        )
    if not _is_within_library_root(config, source_path):
        return RepublishSourceResult(
            status="failure",
            summary=f"旧发布主视频不在受控媒体库根目录内，拒绝重新入库：{source_path}",
            data={"reason": "published_video_outside_library_root"},
        )

    staging_dir = _republish_staging_dir(publish_path, task_id)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_source = staging_dir / source_path.name
    shutil.copy2(source_path, staging_source)
    for subtitle in _same_stem_subtitles(source_path):
        shutil.copy2(subtitle, staging_dir / subtitle.name)

    task = session.get(IngestTask, task_id)
    if task is not None:
        task.source_path = str(staging_dir)
    MediaSourceSelectionRepository(session).save(
        task_id=task_id,
        input_path=str(staging_dir),
        selected_path=str(staging_source),
        confidence=1.0,
        reason="published_output_reselect",
        payload={
            "selection_source": "published_output_reselect",
            "source_kind": "file",
            "temporary": True,
            "original_publish_dir": str(publish_path),
            "original_source_path_missing": True,
        },
    )
    session.flush()
    return RepublishSourceResult(
        status="prepared",
        summary="已从发布目录复制单文件主视频作为临时重入库来源",
        data={"temporary_source": str(staging_dir), "source_kind": "file"},
    )


def _prepare_bdmv_source(
    *,
    session: Session,
    task_id: str,
    publish_path: Path,
) -> RepublishSourceResult:
    from media_pilot.repository.models import IngestTask
    from media_pilot.repository.repositories import MediaSourceSelectionRepository
    from media_pilot.services.disc_input import resolve_bdmv_movie_source

    bdmv_source = resolve_bdmv_movie_source(publish_path)
    if bdmv_source is None:
        return RepublishSourceResult(
            status="failure",
            summary="旧发布目录不是可复用的 BDMV 结构",
            data={"reason": "published_output_not_bdmv"},
        )

    staging_dir = _republish_staging_dir(publish_path, task_id)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bdmv_source.bdmv_dir, staging_dir / "BDMV", dirs_exist_ok=True)
    if bdmv_source.certificate_dir is not None:
        shutil.copytree(
            bdmv_source.certificate_dir,
            staging_dir / "CERTIFICATE",
            dirs_exist_ok=True,
        )

    task = session.get(IngestTask, task_id)
    if task is not None:
        task.source_path = str(staging_dir)
    MediaSourceSelectionRepository(session).save(
        task_id=task_id,
        input_path=str(staging_dir),
        selected_path=None,
        confidence=1.0,
        reason="published_output_reselect",
        payload={
            "selection_source": "published_output_reselect",
            "source_kind": "bdmv",
            "temporary": True,
            "bdmv_dir": str(staging_dir / "BDMV"),
            "certificate_dir": (
                str(staging_dir / "CERTIFICATE")
                if (staging_dir / "CERTIFICATE").is_dir() else None
            ),
            "original_publish_dir": str(publish_path),
            "original_source_path_missing": True,
        },
    )
    session.flush()
    return RepublishSourceResult(
        status="prepared",
        summary="已从发布目录复制 BDMV 结构作为临时重入库来源",
        data={"temporary_source": str(staging_dir), "source_kind": "bdmv"},
    )


def _prepare_from_existing_input(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
    input_path: Path,
    staging_parent: Path,
    original_publish_dir: Path,
    reason: str,
) -> RepublishSourceResult:
    from media_pilot.services.disc_input import resolve_bdmv_movie_source

    if not input_path.exists():
        return RepublishSourceResult(
            status="failure",
            summary=f"记录的回收区源文件不存在，无法重新入库：{input_path}",
            data={"reason": "trashed_input_not_found"},
        )
    if resolve_bdmv_movie_source(input_path) is not None:
        return _copy_existing_bdmv_input(
            session=session,
            task_id=task_id,
            input_path=input_path,
            staging_parent=staging_parent,
            original_publish_dir=original_publish_dir,
            reason=reason,
        )
    if input_path.is_file():
        return _copy_existing_file_input(
            session=session,
            task_id=task_id,
            input_path=input_path,
            staging_parent=staging_parent,
            original_publish_dir=original_publish_dir,
            reason=reason,
        )
    return RepublishSourceResult(
        status="failure",
        summary=f"记录的回收区输入不是单文件或 BDMV 结构，无法重新入库：{input_path}",
        data={"reason": "unsupported_trashed_input"},
    )


def _copy_existing_file_input(
    *,
    session: Session,
    task_id: str,
    input_path: Path,
    staging_parent: Path,
    original_publish_dir: Path,
    reason: str,
) -> RepublishSourceResult:
    from media_pilot.repository.models import IngestTask
    from media_pilot.repository.repositories import MediaSourceSelectionRepository

    staging_dir = _republish_staging_dir_from_parent(staging_parent, task_id)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_source = staging_dir / input_path.name
    shutil.copy2(input_path, staging_source)
    for subtitle in _same_stem_subtitles(input_path):
        shutil.copy2(subtitle, staging_dir / subtitle.name)

    task = session.get(IngestTask, task_id)
    if task is not None:
        task.source_path = str(staging_dir)
    MediaSourceSelectionRepository(session).save(
        task_id=task_id,
        input_path=str(staging_dir),
        selected_path=str(staging_source),
        confidence=1.0,
        reason="published_output_reselect",
        payload={
            "selection_source": "published_output_reselect",
            "source_kind": "file",
            "temporary": True,
            "recovery_source": reason,
            "original_publish_dir": str(original_publish_dir),
            "original_source_path_missing": True,
        },
    )
    session.flush()
    return RepublishSourceResult(
        status="prepared",
        summary="已从回收区复制单文件主视频作为临时重入库来源",
        data={"temporary_source": str(staging_dir), "source_kind": "file"},
    )


def _copy_existing_bdmv_input(
    *,
    session: Session,
    task_id: str,
    input_path: Path,
    staging_parent: Path,
    original_publish_dir: Path,
    reason: str,
) -> RepublishSourceResult:
    from media_pilot.repository.models import IngestTask
    from media_pilot.repository.repositories import MediaSourceSelectionRepository
    from media_pilot.services.disc_input import resolve_bdmv_movie_source

    bdmv_source = resolve_bdmv_movie_source(input_path)
    if bdmv_source is None:
        return RepublishSourceResult(
            status="failure",
            summary=f"记录的 BDMV 回收区输入不可用：{input_path}",
            data={"reason": "trashed_bdmv_not_found"},
        )
    staging_dir = _republish_staging_dir_from_parent(staging_parent, task_id)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bdmv_source.bdmv_dir, staging_dir / "BDMV", dirs_exist_ok=True)
    if bdmv_source.certificate_dir is not None:
        shutil.copytree(
            bdmv_source.certificate_dir,
            staging_dir / "CERTIFICATE",
            dirs_exist_ok=True,
        )

    task = session.get(IngestTask, task_id)
    if task is not None:
        task.source_path = str(staging_dir)
    MediaSourceSelectionRepository(session).save(
        task_id=task_id,
        input_path=str(staging_dir),
        selected_path=None,
        confidence=1.0,
        reason="published_output_reselect",
        payload={
            "selection_source": "published_output_reselect",
            "source_kind": "bdmv",
            "temporary": True,
            "recovery_source": reason,
            "bdmv_dir": str(staging_dir / "BDMV"),
            "certificate_dir": (
                str(staging_dir / "CERTIFICATE")
                if (staging_dir / "CERTIFICATE").is_dir() else None
            ),
            "original_publish_dir": str(original_publish_dir),
            "original_source_path_missing": True,
        },
    )
    session.flush()
    return RepublishSourceResult(
        status="prepared",
        summary="已从回收区复制 BDMV 结构作为临时重入库来源",
        data={"temporary_source": str(staging_dir), "source_kind": "bdmv"},
    )


def _remove_publish_dir(publish_dir: str | None) -> None:
    if not publish_dir:
        return
    publish_path = Path(publish_dir)
    if not publish_path.exists():
        return
    if publish_path.is_dir():
        shutil.rmtree(publish_path)
    else:
        publish_path.unlink()


def _republish_staging_dir(publish_path: Path, task_id: str) -> Path:
    return publish_path.parent / ".media-pilot-staging" / task_id / "republish-source"


def _republish_staging_dir_from_parent(parent: Path, task_id: str) -> Path:
    return parent / ".media-pilot-staging" / task_id / "republish-source"


def _latest_trashed_input_path(*, session: Session, task_id: str) -> Path | None:
    from media_pilot.repository.models import OperationRecord

    record = session.scalars(
        select(OperationRecord)
        .where(OperationRecord.task_id == task_id)
        .where(OperationRecord.operation_type == "source_input_trashed")
        .where(OperationRecord.status == "succeeded")
        .order_by(OperationRecord.created_at.desc())
    ).first()
    if record is None or not record.target_path:
        return None
    return Path(record.target_path)


def _is_within_library_root(config: AppConfig, path: Path) -> bool:
    candidate = path.resolve(strict=False)
    roots = [
        config.movies_dir,
        config.shows_dir,
        getattr(config, "adult_movies_dir", None),
    ]
    for root in roots:
        if root is None:
            continue
        try:
            candidate.relative_to(root.resolve(strict=False))
        except ValueError:
            continue
        return True
    return False


def _find_single_video_in_published_dir(publish_path: Path) -> Path | None:
    from media_pilot.orchestration.ingestion import MEDIA_EXTENSIONS

    videos = [
        path for path in publish_path.rglob("*")
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
    ]
    if len(videos) != 1:
        return None
    return videos[0]


def _same_stem_subtitles(source_path: Path) -> list[Path]:
    subtitle_exts = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
    parent = source_path.parent
    return [
        path for path in parent.iterdir()
        if (
            path.is_file()
            and path.stem == source_path.stem
            and path.suffix.lower() in subtitle_exts
        )
    ]
