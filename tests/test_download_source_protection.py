from pathlib import Path

from media_pilot.file_tools.protection import (
    FileOperation,
    check_download_source_operation,
)


def test_rejects_mutating_original_download_file(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    media_file = downloads_dir / "Movie.2024.mkv"
    downloads_dir.mkdir()
    media_file.write_bytes(b"movie")

    for operation in (
        FileOperation.MOVE,
        FileOperation.DELETE,
        FileOperation.OVERWRITE,
        FileOperation.RENAME,
    ):
        result = check_download_source_operation(downloads_dir, media_file, operation)

        assert result.allowed is False
        assert result.reason == "original_download_file_is_read_only"


def test_allows_reading_original_download_file(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    media_file = downloads_dir / "Movie.2024.mkv"
    downloads_dir.mkdir()
    media_file.write_bytes(b"movie")

    result = check_download_source_operation(downloads_dir, media_file, FileOperation.READ)

    assert result.allowed is True
    assert result.reason is None


def test_does_not_apply_download_protection_outside_download_root(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    workspace_file = tmp_path / "workspace" / "Movie.2024.mkv"
    downloads_dir.mkdir()
    workspace_file.parent.mkdir()
    workspace_file.write_bytes(b"movie")

    result = check_download_source_operation(downloads_dir, workspace_file, FileOperation.MOVE)

    assert result.allowed is True
    assert result.reason is None


def test_rejects_mutating_watch_dir_file(tmp_path: Path) -> None:
    """watch_dir 中的外部导入文件同样受只读保护"""
    downloads_dir = tmp_path / "downloads"
    watch_dir = tmp_path / "watch"
    downloads_dir.mkdir()
    watch_dir.mkdir()
    media_file = watch_dir / "External.Movie.2024.mkv"
    media_file.write_bytes(b"movie")

    result = check_download_source_operation(
        downloads_dir, media_file, FileOperation.MOVE, watch_dir=watch_dir,
    )
    assert result.allowed is False
    assert result.reason == "original_download_file_is_read_only"


def test_watch_dir_protection_is_optional(tmp_path: Path) -> None:
    """不传 watch_dir 时，watch 中的文件不受保护（向后兼容）"""
    downloads_dir = tmp_path / "downloads"
    watch_dir = tmp_path / "watch"
    downloads_dir.mkdir()
    watch_dir.mkdir()
    media_file = watch_dir / "External.mkv"
    media_file.write_bytes(b"movie")

    result = check_download_source_operation(
        downloads_dir, media_file, FileOperation.MOVE,
    )
    # watch_dir 未传入，文件不在保护范围内
    assert result.allowed is True
