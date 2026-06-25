"""源文件清理预检服务 — 解析任务输入节点, 校验路径安全, 生成唯一回收区目标.

设计要点 (来自 design.md):
- 清理对象是整个任务输入节点 — 优先 MediaSourceSelection.input_path
  (代表"用户/扫描器交给系统的整块输入"), 退回 IngestTask.source_path.
  不能用 MediaSourceSelection.selected_path (那是预判的主媒体文件, 在
  目录输入中只代表单个视频, 不能代表整个输入节点).
- 安全根 = {downloads, watch, workspace, movies, shows}; 任务输入节点必须
  位于受控输入根内, 且不能等于任何受控根本身.
- 拒绝在 trash 根下的任务输入节点 (避免回收区内移动).
- 路径无法解析 / 已不存在 / 越界 → 返回失败, 由调用方决定创建 ask 决策.
- 目标必须唯一落在 trash_dir 内, 已存在目标自动追加 N 后缀.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from media_pilot.config import AppConfig
from media_pilot.repository.models import IngestTask, MediaSourceSelection


# 任务输入节点是系统级"受控"目录; 这些根本身绝不能成为清理源.
# "adult" 映射到 config.adult_movies_dir — TPDB 成人影片库根 (route-adult-
# movie-library-root). 缺失时不追加, 不影响未启用 TPDB 的部署.
_PROTECTED_ROOTS: tuple[tuple[str, str], ...] = (
    ("downloads", "downloads_dir"),
    ("watch", "watch_dir"),
    ("workspace", "workspace_dir"),
    ("movies", "movies_dir"),
    ("shows", "shows_dir"),
    ("adult", "adult_movies_dir"),
)


@dataclass(frozen=True, kw_only=True)
class PreflightResult:
    """预检结果 — 调用方根据 allowed 决定是否继续 trash / 退回 ask."""

    allowed: bool
    source_path: Path | None = None
    trash_target: Path | None = None
    reason: str | None = None
    details: dict = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ExecuteResult:
    """执行 trash 移动的结果."""

    success: bool
    source_path: Path | None = None
    trash_target: Path | None = None
    reason: str | None = None


def _protected_root_set(config: AppConfig) -> list[Path]:
    roots: list[Path] = []
    for _label, attr in _PROTECTED_ROOTS:
        path = getattr(config, attr, None)
        if path is not None:
            roots.append(path.resolve(strict=False))
    if config.trash_dir is not None:
        roots.append(config.trash_dir.resolve(strict=False))
    return roots


def _allowed_source_roots(config: AppConfig) -> list[Path]:
    """受控输入根: downloads / watch / workspace. 这些是任务输入节点可能落点的位置."""
    roots: list[Path] = []
    for name in ("downloads_dir", "watch_dir", "workspace_dir"):
        path = getattr(config, name, None)
        if path is not None:
            roots.append(path.resolve(strict=False))
    return roots


def _is_descendant(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_same(path: Path, root: Path) -> bool:
    return path == root


def _sanitize_basename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or "source"


def _unique_trash_target(trash_dir: Path, source: Path) -> Path:
    """在 trash_dir 内生成不冲突的目录/文件目标.

    目录输入 → trash_dir/<sanitized>/ ; 文件输入 → trash_dir/<sanitized>.
    已存在则追加 -2, -3, ... 后缀.
    """
    base_name = _sanitize_basename(source.name)
    if source.is_dir():
        candidate = trash_dir / base_name
        suffix = 2
        while candidate.exists():
            candidate = trash_dir / f"{base_name}-{suffix}"
            suffix += 1
        return candidate
    # 文件: 用 stem + suffix 模式, 避免覆盖同名历史
    stem = _sanitize_basename(source.stem)
    suffix = source.suffix
    candidate = trash_dir / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = trash_dir / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def resolve_task_input_node(
    *,
    task: IngestTask,
    selection: MediaSourceSelection | None,
) -> Path | None:
    """从任务和 source selection 解析出"任务输入节点"路径.

    优先使用 MediaSourceSelection.input_path — 这是扫描/导入时记录的"任务
    接收的整块输入" (单文件 = 该文件路径, 目录 = 该目录路径). 对于目录
    输入, 它就是整个目录; selected_path 只是 source 预判选中的主媒体
    文件, 不能代表整个任务输入节点.

    退回 IngestTask.source_path. 当 MediaSourceSelection 缺失或 input_path
    为空时才走这条.
    """
    if selection is not None and selection.input_path:
        return Path(selection.input_path)
    if task.source_path:
        return Path(task.source_path)
    return None


def check_source_cleanup_preflight(
    *,
    config: AppConfig,
    task: IngestTask,
    selection: MediaSourceSelection | None,
) -> PreflightResult:
    """校验任务输入节点是否可被自动 trash 移动.

    - 任务输入节点缺失 → not allowed (reason=missing_input_node)
    - 受控根本身 → not allowed (reason=refuse_protected_root)
    - 不在受控输入根内 → not allowed (reason=outside_input_roots)
    - 已不存在 → not allowed (reason=source_not_found)
    - trash_dir 未配置 → not allowed (reason=trash_dir_not_configured)
    - 通过 → allowed=True, trash_target 唯一生成
    """
    if config.trash_dir is None:
        return PreflightResult(
            allowed=False,
            reason="trash_dir_not_configured",
        )

    source = resolve_task_input_node(task=task, selection=selection)
    if source is None:
        return PreflightResult(
            allowed=False,
            reason="missing_input_node",
        )

    resolved_source = source.resolve(strict=False)

    protected_roots = _protected_root_set(config)
    for root in protected_roots:
        if _is_same(resolved_source, root):
            return PreflightResult(
                allowed=False,
                source_path=resolved_source,
                reason="refuse_protected_root",
            )

    # 已位于 trash_dir 之内 → 不允许再次移动
    if (
        config.trash_dir is not None
        and _is_descendant(
            resolved_source, config.trash_dir.resolve(strict=False)
        )
    ):
        return PreflightResult(
            allowed=False,
            source_path=resolved_source,
            reason="source_inside_trash_dir",
        )

    allowed_roots = _allowed_source_roots(config)
    if not any(_is_descendant(resolved_source, root) for root in allowed_roots):
        return PreflightResult(
            allowed=False,
            source_path=resolved_source,
            reason="outside_input_roots",
        )

    if not resolved_source.exists():
        return PreflightResult(
            allowed=False,
            source_path=resolved_source,
            reason="source_not_found",
        )

    trash_target = _unique_trash_target(config.trash_dir, resolved_source)
    return PreflightResult(
        allowed=True,
        source_path=resolved_source,
        trash_target=trash_target,
    )


def execute_source_cleanup(
    *,
    config: AppConfig,
    preflight: PreflightResult,
) -> ExecuteResult:
    """在预检通过的前提下执行 trash 移动; 不可恢复, 失败时返回 success=False.

    调用前必须确认 preflight.allowed=True 且 trash_target 在 config.trash_dir 内.
    """
    if not preflight.allowed or preflight.source_path is None or preflight.trash_target is None:
        return ExecuteResult(
            success=False,
            reason="preflight_not_passed",
        )
    if config.trash_dir is None:
        return ExecuteResult(
            success=False,
            reason="trash_dir_not_configured",
        )

    source = preflight.source_path
    target = preflight.trash_target

    # 二次校验目标确实在 trash_dir 内 (防调用方传入伪造 PreflightResult)
    trash_root = config.trash_dir.resolve(strict=False)
    if not _is_descendant(target.resolve(strict=False), trash_root):
        return ExecuteResult(
            success=False,
            source_path=source,
            trash_target=target,
            reason="target_outside_trash_dir",
        )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    except Exception as exc:  # noqa: BLE001 — 移动失败需保留上下文
        return ExecuteResult(
            success=False,
            source_path=source,
            trash_target=target,
            reason=f"move_failed:{type(exc).__name__}:{exc}",
        )

    return ExecuteResult(
        success=True,
        source_path=source,
        trash_target=target,
    )


__all__ = [
    "PreflightResult",
    "ExecuteResult",
    "check_source_cleanup_preflight",
    "execute_source_cleanup",
    "resolve_task_input_node",
]
