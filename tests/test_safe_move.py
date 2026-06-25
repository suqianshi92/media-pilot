from pathlib import Path

from media_pilot.file_tools.safe_move import MoveMethod, safe_move


def test_safe_move_workspace_file_to_staging_dir(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    staging_dir = tmp_path / "staging"
    workspace_dir.mkdir()
    staging_dir.mkdir()
    source = workspace_dir / "Movie.2026.mkv"
    source.write_bytes(b"movie")
    target = staging_dir / "Movie.2026.mkv"

    result = safe_move(
        source,
        target,
        allowed_source_roots=(workspace_dir,),
        allowed_target_roots=(staging_dir,),
    )

    assert result.method == MoveMethod.MOVE
    assert result.source_path == source.resolve(strict=False)
    assert result.target_path == target.resolve()
    assert result.reason is None
    assert not source.exists()
    assert target.read_bytes() == b"movie"


def test_safe_move_staging_output_directory_to_library_target(tmp_path: Path) -> None:
    staging_dir = tmp_path / "staging"
    library_dir = tmp_path / "library" / "movies"
    output_dir = staging_dir / "Example Movie (2026)"
    output_dir.mkdir(parents=True)
    media_file = output_dir / "Example Movie (2026).mkv"
    media_file.write_bytes(b"movie")
    target_dir = library_dir / output_dir.name

    result = safe_move(
        output_dir,
        target_dir,
        allowed_source_roots=(staging_dir,),
        allowed_target_roots=(library_dir,),
    )

    assert result.method == MoveMethod.MOVE
    assert not output_dir.exists()
    assert (target_dir / media_file.name).read_bytes() == b"movie"


def test_safe_move_rejects_existing_target_without_overwrite(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    staging_dir = tmp_path / "staging"
    workspace_dir.mkdir()
    staging_dir.mkdir()
    source = workspace_dir / "Movie.2026.mkv"
    target = staging_dir / "Movie.2026.mkv"
    source.write_bytes(b"movie")
    target.write_bytes(b"existing")

    result = safe_move(
        source,
        target,
        allowed_source_roots=(workspace_dir,),
        allowed_target_roots=(staging_dir,),
    )

    assert result.method is None
    assert result.reason == "target_already_exists"
    assert source.read_bytes() == b"movie"
    assert target.read_bytes() == b"existing"


def test_safe_move_rejects_source_outside_allowed_roots(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    workspace_dir = tmp_path / "workspace"
    staging_dir = tmp_path / "staging"
    downloads_dir.mkdir()
    workspace_dir.mkdir()
    staging_dir.mkdir()
    source = downloads_dir / "Movie.2026.mkv"
    source.write_bytes(b"movie")
    target = staging_dir / source.name

    result = safe_move(
        source,
        target,
        allowed_source_roots=(workspace_dir,),
        allowed_target_roots=(staging_dir,),
    )

    assert result.method is None
    assert result.reason == "path_outside_allowed_roots"
    assert source.read_bytes() == b"movie"
    assert not target.exists()
