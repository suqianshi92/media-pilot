"""Disc-style movie input detection.

BDMV support intentionally treats the Blu-ray directory as an opaque movie
source. We do not inspect playlists or rename STREAM/*.m2ts files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ISO_EXTENSIONS = frozenset({".iso", ".img"})


@dataclass(frozen=True, kw_only=True)
class BdmvMovieSource:
    """Resolved BDMV source.

    - input_path is the task input node.
    - bdmv_dir is the actual directory to copy into target/BDMV.
    - certificate_dir is copied only when it is a child of input_path.
    """

    input_path: Path
    bdmv_dir: Path
    certificate_dir: Path | None = None


def is_iso_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in ISO_EXTENSIONS


def resolve_bdmv_movie_source(path: Path) -> BdmvMovieSource | None:
    """Return BDMV movie source info if ``path`` is an unpacked BDMV input.

    Supported shapes:
    - ``Movie Folder/BDMV/...`` with optional ``Movie Folder/CERTIFICATE``.
    - ``BDMV/...`` where the task input node itself is the BDMV directory.
    """
    if not path.exists() or not path.is_dir():
        return None

    nested_bdmv = path / "BDMV"
    if _looks_like_bdmv_dir(nested_bdmv):
        certificate = path / "CERTIFICATE"
        return BdmvMovieSource(
            input_path=path,
            bdmv_dir=nested_bdmv,
            certificate_dir=certificate if certificate.is_dir() else None,
        )

    if path.name.upper() == "BDMV" and _looks_like_bdmv_dir(path):
        return BdmvMovieSource(input_path=path, bdmv_dir=path)

    return None


def _looks_like_bdmv_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    return (path / "index.bdmv").is_file() or (path / "STREAM").is_dir()

