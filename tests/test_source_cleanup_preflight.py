"""源文件清理预检服务测试 — 文件/目录输入、根目录拒绝、越界、缺失、目标冲突."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_pilot.config.settings import AppConfig
from media_pilot.repository.models import IngestTask, MediaSourceSelection
from media_pilot.services.source_cleanup_preflight import (
    PreflightResult,
    check_source_cleanup_preflight,
    execute_source_cleanup,
    resolve_task_input_node,
)


# ── 测试 fixture ──────────────────────────────────────────────────────


def _build_config(tmp_path: Path, *, with_trash: bool = True) -> AppConfig:
    downloads_dir = tmp_path / "downloads"
    watch_dir = tmp_path / "watch"
    workspace_dir = tmp_path / "workspace"
    movies_dir = tmp_path / "library" / "movies"
    shows_dir = tmp_path / "library" / "shows"
    trash_dir = tmp_path / "trash" if with_trash else None
    return AppConfig(
        downloads_dir=downloads_dir,
        watch_dir=watch_dir,
        workspace_dir=workspace_dir,
        movies_dir=movies_dir,
        shows_dir=shows_dir,
        database_dir=tmp_path / "db",
        trash_dir=trash_dir,
    )


def _make_task(source_path: str | None) -> IngestTask:
    """构造一个不入库的轻量 IngestTask (仅供预检用)."""
    return IngestTask(source_path=source_path or "")


def _make_selection(
    input_path: str | None = None,
    selected_path: str | None = None,
) -> MediaSourceSelection:
    return MediaSourceSelection(
        task_id="task",
        input_path=input_path or "",
        selected_path=selected_path,
    )


# ── 解析任务输入节点 ──────────────────────────────────────────────────


def test_resolve_task_input_node_prefers_selection_input_path(tmp_path: Path) -> None:
    """MediaSourceSelection.input_path 优先于 IngestTask.source_path.

    input_path 代表扫描/导入时记录的"任务接收的整块输入" — 单文件就是
    该文件路径, 目录就是该目录路径. selected_path 不应被当成输入节点.
    """
    file_a = tmp_path / "from_selection.mkv"
    file_b = tmp_path / "from_task.mkv"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")

    task = _make_task(str(file_b))
    selection = _make_selection(input_path=str(file_a), selected_path=str(file_a))

    resolved = resolve_task_input_node(task=task, selection=selection)
    assert resolved == Path(str(file_a))


def test_resolve_task_input_node_uses_input_path_not_selected_path_for_directory(
    tmp_path: Path,
) -> None:
    """目录输入: input_path=目录, selected_path=目录/movie.mkv → 解析为整个目录.

    清理对象是整个任务输入节点, 不能只移 selected_path 那个主视频文件.
    """
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    movie_dir = downloads / "Movie.Name.2026"
    movie_dir.mkdir()
    main_video = movie_dir / "movie.mkv"
    main_video.write_bytes(b"video")
    sidecar = movie_dir / "subtitle.srt"
    sidecar.write_bytes(b"sub")

    task = _make_task(str(movie_dir))
    selection = _make_selection(
        input_path=str(movie_dir),
        selected_path=str(main_video),
    )

    resolved = resolve_task_input_node(task=task, selection=selection)
    assert resolved == Path(str(movie_dir)), (
        "解析结果必须是整个目录, 而非 selected_path 指向的单个视频文件"
    )
    # 主视频和旁白文件都必须能在该目录下被找到
    assert (resolved / "movie.mkv").exists()
    assert (resolved / "subtitle.srt").exists()


def test_resolve_task_input_node_falls_back_to_task_source(tmp_path: Path) -> None:
    """无 selection 或 selection.input_path 为空时退回 task.source_path."""
    file_b = tmp_path / "from_task.mkv"
    file_b.write_bytes(b"b")

    task = _make_task(str(file_b))

    assert resolve_task_input_node(task=task, selection=None) == Path(str(file_b))
    assert resolve_task_input_node(
        task=task, selection=_make_selection(input_path=None)
    ) == Path(str(file_b))
    assert resolve_task_input_node(
        task=task, selection=_make_selection(input_path="")
    ) == Path(str(file_b))


def test_resolve_task_input_node_returns_none_when_missing() -> None:
    """任务和 selection 都缺失路径时返回 None."""
    task = _make_task("")
    assert resolve_task_input_node(task=task, selection=None) is None
    assert resolve_task_input_node(
        task=task, selection=_make_selection(input_path=None)
    ) is None
    assert resolve_task_input_node(
        task=task, selection=_make_selection(input_path="")
    ) is None


# ── trash_dir 缺失 / 任务输入缺失 ──────────────────────────────────────


def test_preflight_refuses_when_trash_dir_not_configured(tmp_path: Path) -> None:
    config = _build_config(tmp_path, with_trash=False)
    file = tmp_path / "downloads" / "movie.mkv"
    file.parent.mkdir()
    file.write_bytes(b"m")

    task = _make_task(str(file))
    result = check_source_cleanup_preflight(config=config, task=task, selection=None)

    assert result.allowed is False
    assert result.reason == "trash_dir_not_configured"


def test_preflight_refuses_missing_input_node(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    (tmp_path / "trash").mkdir()

    task = _make_task("")
    result = check_source_cleanup_preflight(config=config, task=task, selection=None)

    assert result.allowed is False
    assert result.reason == "missing_input_node"


# ── 受控根目录拒绝 ────────────────────────────────────────────────────


@pytest.mark.parametrize("root_name", ["downloads", "watch", "workspace", "movies", "shows"])
def test_preflight_refuses_each_protected_root_as_itself(
    tmp_path: Path, root_name: str
) -> None:
    """downloads/watch/workspace/movies/shows 根本身不能被作为清理源."""
    config = _build_config(tmp_path)
    (tmp_path / "trash").mkdir()
    protected_root = {
        "downloads": config.downloads_dir,
        "watch": config.watch_dir,
        "workspace": config.workspace_dir,
        "movies": config.movies_dir,
        "shows": config.shows_dir,
    }[root_name]
    protected_root.mkdir(parents=True, exist_ok=True)

    task = _make_task(str(protected_root))
    result = check_source_cleanup_preflight(config=config, task=task, selection=None)

    assert result.allowed is False
    assert result.reason == "refuse_protected_root"
    assert result.source_path == protected_root.resolve(strict=False)


def test_preflight_refuses_source_inside_trash_dir(tmp_path: Path) -> None:
    """任务输入节点已经位于 trash_dir 内 — 不允许再次移动."""
    config = _build_config(tmp_path)
    trash = tmp_path / "trash"
    trash.mkdir()
    nested = trash / "abandoned.mkv"
    nested.write_bytes(b"old")

    task = _make_task(str(nested))
    result = check_source_cleanup_preflight(config=config, task=task, selection=None)

    assert result.allowed is False
    assert result.reason == "source_inside_trash_dir"


# ── 越界与不存在 ────────────────────────────────────────────────────


def test_preflight_refuses_path_outside_input_roots(tmp_path: Path) -> None:
    """任务输入节点位于受控输入根之外 → 越界拒绝."""
    config = _build_config(tmp_path)
    (tmp_path / "trash").mkdir()
    rogue = tmp_path / "external" / "movie.mkv"
    rogue.parent.mkdir(parents=True)
    rogue.write_bytes(b"x")

    task = _make_task(str(rogue))
    result = check_source_cleanup_preflight(config=config, task=task, selection=None)

    assert result.allowed is False
    assert result.reason == "outside_input_roots"
    assert result.source_path == rogue.resolve(strict=False)


def test_preflight_refuses_source_not_found(tmp_path: Path) -> None:
    """任务输入节点路径在数据库有记录但已不存在 → 拒绝."""
    config = _build_config(tmp_path)
    (tmp_path / "trash").mkdir()
    missing = config.downloads_dir / "vanished.mkv"
    # 不创建文件

    task = _make_task(str(missing))
    result = check_source_cleanup_preflight(config=config, task=task, selection=None)

    assert result.allowed is False
    assert result.reason == "source_not_found"
    assert result.source_path == missing.resolve(strict=False)


# ── 文件 / 目录输入允许 ───────────────────────────────────────────────


def test_preflight_allows_file_inside_downloads(tmp_path: Path) -> None:
    """downloads 内的单文件任务输入节点允许 trash 移动."""
    config = _build_config(tmp_path)
    (tmp_path / "trash").mkdir()
    config.downloads_dir.mkdir(parents=True, exist_ok=True)
    file = config.downloads_dir / "movie.mkv"
    file.write_bytes(b"m")

    task = _make_task(str(file))
    result = check_source_cleanup_preflight(config=config, task=task, selection=None)

    assert result.allowed is True
    assert result.source_path == file.resolve(strict=False)
    assert result.trash_target is not None
    assert result.trash_target.parent == (tmp_path / "trash").resolve(strict=False)
    assert result.trash_target.name == "movie.mkv"


def test_preflight_allows_directory_inside_workspace(tmp_path: Path) -> None:
    """workspace 内的目录任务输入节点允许 trash 移动."""
    config = _build_config(tmp_path)
    (tmp_path / "trash").mkdir()
    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    source_dir = config.workspace_dir / "Movie Pack"
    source_dir.mkdir()
    (source_dir / "movie.mkv").write_bytes(b"m")

    task = _make_task(str(source_dir))
    result = check_source_cleanup_preflight(config=config, task=task, selection=None)

    assert result.allowed is True
    assert result.source_path == source_dir.resolve(strict=False)
    assert result.trash_target is not None
    assert result.trash_target.name == "Movie_Pack"
    assert result.trash_target.parent == (tmp_path / "trash").resolve(strict=False)


def test_preflight_target_uniqueness_appends_suffix(tmp_path: Path) -> None:
    """回收区目标已存在时, 自动追加 -2, -3 后缀避免冲突."""
    config = _build_config(tmp_path)
    trash = tmp_path / "trash"
    trash.mkdir()
    config.downloads_dir.mkdir(parents=True, exist_ok=True)

    # 回收区里已经存在 movie.mkv
    (trash / "movie.mkv").write_bytes(b"existing")

    file = config.downloads_dir / "movie.mkv"
    file.write_bytes(b"new")

    task = _make_task(str(file))
    result = check_source_cleanup_preflight(config=config, task=task, selection=None)

    assert result.allowed is True
    assert result.trash_target is not None
    assert result.trash_target.name == "movie-2.mkv"


# ── 执行 trash 移动 ──────────────────────────────────────────────────


def test_execute_source_cleanup_moves_file_into_trash(tmp_path: Path) -> None:
    """通过预检后, 执行阶段将整个任务输入节点移入 trash_dir."""
    config = _build_config(tmp_path)
    trash = tmp_path / "trash"
    trash.mkdir()
    config.downloads_dir.mkdir(parents=True, exist_ok=True)
    file = config.downloads_dir / "movie.mkv"
    file.write_bytes(b"payload")

    task = _make_task(str(file))
    preflight = check_source_cleanup_preflight(config=config, task=task, selection=None)
    assert preflight.allowed is True

    result = execute_source_cleanup(config=config, preflight=preflight)

    assert result.success is True
    assert result.source_path == file.resolve(strict=False)
    assert result.trash_target is not None
    assert result.trash_target.exists()
    assert result.trash_target.read_bytes() == b"payload"
    assert not file.exists()


def test_execute_source_cleanup_moves_directory_into_trash(tmp_path: Path) -> None:
    """目录输入节点也被整体移动."""
    config = _build_config(tmp_path)
    trash = tmp_path / "trash"
    trash.mkdir()
    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    source_dir = config.workspace_dir / "Movie"
    source_dir.mkdir()
    (source_dir / "a.mkv").write_bytes(b"a")
    (source_dir / "b.mkv").write_bytes(b"b")

    task = _make_task(str(source_dir))
    preflight = check_source_cleanup_preflight(config=config, task=task, selection=None)
    assert preflight.allowed is True

    result = execute_source_cleanup(config=config, preflight=preflight)

    assert result.success is True
    assert not source_dir.exists()
    moved = result.trash_target
    assert moved is not None
    assert (moved / "a.mkv").read_bytes() == b"a"
    assert (moved / "b.mkv").read_bytes() == b"b"


def test_preflight_and_execute_use_directory_when_input_path_is_directory(
    tmp_path: Path,
) -> None:
    """目录输入端到端: input_path=目录, selected_path=目录/movie.mkv → 移动整个目录.

    关键: 预检 source_path 必须是整个目录 (而非 selected_path 指向的
    单个视频), trash 移动后所有文件 (含旁白) 都跟着走.
    """
    config = _build_config(tmp_path)
    (tmp_path / "trash").mkdir()
    config.downloads_dir.mkdir(parents=True, exist_ok=True)
    movie_dir = config.downloads_dir / "Movie.Name.2026"
    movie_dir.mkdir()
    main_video = movie_dir / "movie.mkv"
    main_video.write_bytes(b"video")
    sidecar = movie_dir / "subtitle.srt"
    sidecar.write_bytes(b"sub")

    task = _make_task(str(movie_dir))
    selection = _make_selection(
        input_path=str(movie_dir),
        selected_path=str(main_video),
    )

    preflight = check_source_cleanup_preflight(
        config=config, task=task, selection=selection,
    )
    assert preflight.allowed is True, (
        f"预检应通过, 实际 reason={preflight.reason!r}"
    )
    # 关键: source_path 必须是整个目录, 不是 selected_path
    assert preflight.source_path == movie_dir.resolve(strict=False)
    assert preflight.source_path.is_dir()
    assert preflight.trash_target is not None
    assert preflight.trash_target.parent == (tmp_path / "trash").resolve(strict=False)

    result = execute_source_cleanup(config=config, preflight=preflight)
    assert result.success is True
    # 整个目录被移动, 不只是 selected_path 那个视频
    assert not movie_dir.exists()
    assert not main_video.exists()
    moved = result.trash_target
    assert moved is not None
    assert (moved / "movie.mkv").read_bytes() == b"video"
    assert (moved / "subtitle.srt").read_bytes() == b"sub"


def test_execute_source_cleanup_refuses_when_preflight_not_passed(tmp_path: Path) -> None:
    """不允许在不通过预检的情况下执行."""
    config = _build_config(tmp_path)
    fake_preflight = PreflightResult(allowed=False, reason="missing_input_node")

    result = execute_source_cleanup(config=config, preflight=fake_preflight)
    assert result.success is False
    assert result.reason == "preflight_not_passed"


def test_execute_source_cleanup_refuses_target_outside_trash_dir(tmp_path: Path) -> None:
    """伪造的 trash_target 不在 trash_dir 内 → 拒绝执行."""
    config = _build_config(tmp_path)
    (tmp_path / "trash").mkdir()
    fake_source = tmp_path / "downloads" / "movie.mkv"
    fake_source.parent.mkdir(parents=True, exist_ok=True)
    fake_source.write_bytes(b"x")
    fake_target = tmp_path / "library" / "movies" / "fake.mkv"

    fake_preflight = PreflightResult(
        allowed=True,
        source_path=fake_source.resolve(strict=False),
        trash_target=fake_target,
    )

    result = execute_source_cleanup(config=config, preflight=fake_preflight)
    assert result.success is False
    assert result.reason == "target_outside_trash_dir"
    # 原文件未被移动
    assert fake_source.exists()
