from pathlib import Path

from media_pilot.file_tools.protection import check_allowed_path


def test_allows_path_inside_allowed_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    media_file = workspace / "Movie.2026.mkv"
    media_file.write_bytes(b"movie")

    result = check_allowed_path(media_file, allowed_roots=(workspace,))

    assert result.allowed is True
    assert result.resolved_path == media_file.resolve()
    assert result.reason is None


def test_rejects_path_outside_allowed_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    outside_file = outside / "Movie.2026.mkv"
    outside_file.write_bytes(b"movie")

    result = check_allowed_path(outside_file, allowed_roots=(workspace,))

    assert result.allowed is False
    assert result.resolved_path == outside_file.resolve()
    assert result.reason == "path_outside_allowed_roots"


def test_rejects_symlink_escape_from_allowed_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    outside_file = outside / "Movie.2026.mkv"
    outside_file.write_bytes(b"movie")
    symlink = workspace / "escape.mkv"
    symlink.symlink_to(outside_file)

    result = check_allowed_path(symlink, allowed_roots=(workspace,))

    assert result.allowed is False
    assert result.resolved_path == outside_file.resolve()
    assert result.reason == "path_outside_allowed_roots"
