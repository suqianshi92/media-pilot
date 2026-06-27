"""主视频源解析 — publish / overwrite 共用入口.

背景: watch 目录型单电影输入 (e.g. `Warcraft ... [YTS.MX]/foo.mkv` +
jpg/txt), prepare_complex_input_decision 在 single_video_ready 路径
未持久化 `MediaSourceSelection`, 导致 publish_movie_to_library 与
target_conflict_handler.handle_overwrite_target 都回退到 `task.source_path`
(目录), 触发 `IsADirectoryError: [Errno 21]`. `build_movie_write_plan`
还会拿 `source_path.suffix` 得到目录名后缀 (`.MX]`), 而不是 .mkv.

本模块提供统一的"解析实际主视频文件"入口, 三个职责:
1. 优先复用已存在的 `MediaSourceSelection.selected_path` (事实一致);
2. 单视频目录时自动补写 `MediaSourceSelection` (自愈);
3. 0 / 多个主视频时返回结构化错误 (留给上层创建决策或失败).

文件电影不会返回目录路径给 `build_movie_write_plan` / `execute_movie_write`。
BDMV 电影目录作为显式 `source_kind="bdmv"` 例外，由 writer 按目录树发布。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from media_pilot.config import AppConfig
from media_pilot.services.disc_input import resolve_bdmv_movie_source
from media_pilot.services.task_input_analysis import (
    FileInfo,
    analyze_task_input,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class VideoSourceResolveResult:
    """主视频解析结果.

    - video_path: 文件电影为实际主视频文件; BDMV 为任务输入目录.
    - error_code: 失败时的稳定字符串; 成功时为 None.
      已知值: "no_main_video" | "multiple_videos" | "source_missing" | "source_outside_safe_roots"
    - error_message: 人类可读的错误信息.
    - created_selection: 是否在本调用内自动补写了 MediaSourceSelection.
    """

    video_path: Path | None
    error_code: str | None = None
    error_message: str | None = None
    created_selection: bool = False
    source_kind: str = "file"


def _build_selection_payload(
    *,
    primary: FileInfo,
    auxiliary_videos: list[FileInfo],
    excluded: list[FileInfo],
    subtitle_candidates: list[FileInfo] | None = None,
) -> dict:
    """构造 MediaSourceSelection.payload — key 约定与
    services/complex_input.py:_persist_ready_selection 一致
    (auxiliary_videos / excluded / subtitle_candidates), 供前端
    任务工作台展示 auto_single_video / user_decision 两种来源.
    """
    payload: dict = {
        "selection_source": "auto_single_video",
        "auxiliary_videos": [
            {"path": f.path, "name": f.name, "size_bytes": f.size_bytes}
            for f in auxiliary_videos
        ],
        "excluded": [
            {
                "path": f.path, "name": f.name,
                "size_bytes": f.size_bytes,
                "excluded_reason": f.excluded_reason or "sample/trailer/auxiliary",
            }
            for f in excluded
        ],
    }
    if subtitle_candidates is not None:
        payload["subtitle_candidates"] = [
            {"path": f.path, "name": f.name, "size_bytes": f.size_bytes}
            for f in subtitle_candidates
        ]
    payload["primary"] = {
        "path": primary.path, "name": primary.name,
        "size_bytes": primary.size_bytes,
    }
    return payload


def resolve_main_video_for_publish(
    session: Session,
    task,
    *,
    config: AppConfig,
) -> VideoSourceResolveResult:
    """解析电影发布源.

    优先级:
    1. BDMV 目录 → 返回 source_kind="bdmv" 的目录型 movie source.
    2. MediaSourceSelection 存在 + selected_path 是文件 → 用它.
    3. task.source_path 是文件 → 用它.
    4. task.source_path 是目录 → analyze_task_input:
       - 0 主视频 → error "no_main_video".
       - 1 主视频 → 自动补写 MediaSourceSelection (input_path=task.source_path,
         selected_path=primary.path, payload=auxiliary/excluded/subtitles 事实),
         created_selection=True.
       - 2+ 主视频 → error "multiple_videos" (让用户重访 complex_input_decision).

    被 publish_movie_to_library 与 target_conflict_handler.handle_overwrite_target
    共同使用, 杜绝把普通目录路径喂给 build_movie_write_plan / execute_movie_write.
    """
    from media_pilot.repository.repositories import MediaSourceSelectionRepository

    if task is None:
        return VideoSourceResolveResult(
            video_path=None,
            error_code="source_missing",
            error_message="任务不存在, 无法解析主视频",
        )

    source_path = Path(task.source_path) if task.source_path else None
    if source_path is None or not source_path.exists():
        return VideoSourceResolveResult(
            video_path=None,
            error_code="source_missing",
            error_message=f"任务输入路径不存在: {task.source_path}",
        )

    sel_repo = MediaSourceSelectionRepository(session)
    existing = sel_repo.get_for_task(task.id)

    # BDMV movie directory: preserve the disc structure as an opaque movie
    # source. Do this before single-video directory analysis because
    # BDMV/STREAM may contain many .m2ts files that must not become candidates.
    bdmv_source = resolve_bdmv_movie_source(source_path)
    if bdmv_source is not None:
        created_selection = False
        if existing is None or not (
            isinstance(existing.payload, dict)
            and existing.payload.get("source_kind") == "bdmv"
        ):
            try:
                sel_repo.save(
                    task_id=task.id,
                    input_path=str(source_path),
                    selected_path=None,
                    confidence=1.0,
                    reason="auto_bdmv_movie_dir",
                    payload={
                        "selection_source": "auto_bdmv_movie_dir",
                        "source_kind": "bdmv",
                        "bdmv_dir": str(bdmv_source.bdmv_dir),
                        "certificate_dir": (
                            str(bdmv_source.certificate_dir)
                            if bdmv_source.certificate_dir is not None else None
                        ),
                    },
                )
                created_selection = True
            except Exception:
                logger.exception("自动补写 BDMV MediaSourceSelection 失败: task=%s", task.id)
        return VideoSourceResolveResult(
            video_path=source_path,
            created_selection=created_selection,
            source_kind="bdmv",
        )

    # 1. 已存在 selection 且 selected_path 是真实文件 → 优先复用.
    if existing and existing.selected_path:
        selected = Path(existing.selected_path)
        if selected.is_file():
            return VideoSourceResolveResult(video_path=selected)

    # 2. source_path 本身是文件 → 直接用.
    if source_path.is_file():
        return VideoSourceResolveResult(video_path=source_path)

    # 3. source_path 是目录 → 重新扫描.
    if not source_path.is_dir():
        # 兜底: 不是文件也不是目录 (symlink 悬空等), 视为 missing.
        return VideoSourceResolveResult(
            video_path=None,
            error_code="source_missing",
            error_message=f"任务输入路径既非文件也非目录: {source_path}",
        )

    try:
        analysis = analyze_task_input(source_path)
    except Exception as exc:
        logger.exception("analyze_task_input 失败: %s", exc)
        return VideoSourceResolveResult(
            video_path=None,
            error_code="source_missing",
            error_message=f"扫描任务输入失败: {type(exc).__name__}: {exc}",
        )

    main_videos = [f for f in analysis.files if f.type == "video"]
    if len(main_videos) == 0:
        return VideoSourceResolveResult(
            video_path=None,
            error_code="no_main_video",
            error_message=(
                f"任务输入目录 {source_path} 中没有可识别的主视频文件"
            ),
        )
    if len(main_videos) > 1:
        return VideoSourceResolveResult(
            video_path=None,
            error_code="multiple_videos",
            error_message=(
                f"任务输入目录 {source_path} 中有 {len(main_videos)} "
                "个主视频文件, 请先通过 complex_input_decision 选择主视频"
            ),
        )

    primary = main_videos[0]
    primary_path = Path(primary.path)
    if not primary_path.is_file():
        return VideoSourceResolveResult(
            video_path=None,
            error_code="source_missing",
            error_message=f"识别的主视频文件不存在: {primary_path}",
        )

    # 1 主视频 → 自动补写 MediaSourceSelection (自愈, 让后续 publish / overwrite
    # 链路读到 selected_path 而不是目录).
    payload = _build_selection_payload(
        primary=primary,
        auxiliary_videos=[
            f for f in analysis.excluded if f.type == "video"
        ],
        excluded=analysis.excluded,
        subtitle_candidates=[
            f for f in analysis.files if f.type == "subtitle"
        ],
    )
    try:
        sel_repo.save(
            task_id=task.id,
            input_path=str(source_path),
            selected_path=str(primary_path),
            confidence=1.0,
            reason="auto_single_video_dir",
            payload=payload,
        )
        logger.info(
            "video_source_resolver 自动补写 MediaSourceSelection: task=%s selected=%s",
            task.id, primary_path,
        )
    except Exception:
        # 自动补写失败不影响返回主视频路径 — publish 仍可继续, 仅 audit 缺一行.
        logger.exception("自动补写 MediaSourceSelection 失败: task=%s", task.id)

    return VideoSourceResolveResult(
        video_path=primary_path,
        created_selection=True,
    )
