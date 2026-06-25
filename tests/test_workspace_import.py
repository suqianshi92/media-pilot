from pathlib import Path

from media_pilot.file_tools.workspace_import import ImportMethod, import_download_to_workspace


def test_import_download_prepares_task_workspace_without_copying_main_video(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    workspace_dir = tmp_path / "workspace"
    downloads_dir.mkdir()
    workspace_dir.mkdir()
    source = downloads_dir / "Movie.2026.mkv"
    source.write_bytes(b"movie")
    original_mtime = source.stat().st_mtime_ns

    result = import_download_to_workspace(
        source,
        downloads_dir=downloads_dir,
        workspace_dir=workspace_dir,
        task_id="task-1",
    )

    assert result.method == ImportMethod.PREPARE
    assert result.target_path == workspace_dir / "task-1"
    assert result.target_path.is_dir()
    assert (result.target_path / "metadata").is_dir()
    assert not (result.target_path / source.name).exists()
    assert source.exists()
    assert source.read_bytes() == b"movie"
    assert source.stat().st_mtime_ns == original_mtime


def test_import_download_is_idempotent_for_existing_task_workspace(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    workspace_dir = tmp_path / "workspace"
    downloads_dir.mkdir()
    workspace_dir.mkdir()
    source = downloads_dir / "Movie.2026.mkv"
    source.write_bytes(b"movie")

    result = import_download_to_workspace(
        source,
        downloads_dir=downloads_dir,
        workspace_dir=workspace_dir,
        task_id="task-1",
    )

    second_result = import_download_to_workspace(
        source,
        downloads_dir=downloads_dir,
        workspace_dir=workspace_dir,
        task_id="task-1",
    )

    assert result.method == ImportMethod.PREPARE
    assert second_result.method == ImportMethod.PREPARE
    assert second_result.target_path == workspace_dir / "task-1"
    assert (second_result.target_path / "metadata").is_dir()


def test_import_download_rejects_outside_downloads_root(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    workspace_dir = tmp_path / "workspace"
    downloads_dir.mkdir()
    workspace_dir.mkdir()
    source = tmp_path / "Movie.2026.mkv"
    source.write_bytes(b"movie")

    result = import_download_to_workspace(
        source,
        downloads_dir=downloads_dir,
        workspace_dir=workspace_dir,
        task_id="task-1",
    )

    assert result.method is None
    assert result.target_path == workspace_dir / "task-1"
    assert result.reason == "path_outside_allowed_roots"
    assert source.read_bytes() == b"movie"


def test_import_download_prepares_workspace_for_directory_source_without_copying_contents(
    tmp_path: Path,
) -> None:
    downloads_dir = tmp_path / "downloads"
    workspace_dir = tmp_path / "workspace"
    downloads_dir.mkdir()
    workspace_dir.mkdir()
    source_dir = downloads_dir / "Movie.2026"
    source_dir.mkdir()
    movie = source_dir / "Movie.2026.mkv"
    note = source_dir / "readme.txt"
    movie.write_bytes(b"movie")
    note.write_text("keep", encoding="utf-8")

    result = import_download_to_workspace(
        source_dir,
        downloads_dir=downloads_dir,
        workspace_dir=workspace_dir,
        task_id="task-2",
    )

    assert result.method == ImportMethod.PREPARE
    assert result.target_path == workspace_dir / "task-2"
    assert result.target_path.is_dir()
    assert (result.target_path / "metadata").is_dir()
    assert not (result.target_path / movie.name).exists()
    assert not (result.target_path / note.name).exists()
    assert source_dir.is_dir()


def test_import_download_does_not_materialize_main_video_in_workspace(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    workspace_dir = tmp_path / "workspace"
    downloads_dir.mkdir()
    workspace_dir.mkdir()
    source = downloads_dir / "Movie.2026.mkv"
    source.write_bytes(b"movie")

    result = import_download_to_workspace(
        source,
        downloads_dir=downloads_dir,
        workspace_dir=workspace_dir,
        task_id="task-3",
    )

    assert result.method == ImportMethod.PREPARE
    assert result.target_path == workspace_dir / "task-3"
    assert not (result.target_path / source.name).exists()
    assert source.read_bytes() == b"movie"


def test_import_download_never_uses_hardlink_for_main_video(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    workspace_dir = tmp_path / "workspace"
    downloads_dir.mkdir()
    workspace_dir.mkdir()
    source = downloads_dir / "Movie.2026.mkv"
    source.write_bytes(b"movie")

    result = import_download_to_workspace(
        source,
        downloads_dir=downloads_dir,
        workspace_dir=workspace_dir,
        task_id="task-4",
    )

    assert result.method == ImportMethod.PREPARE
