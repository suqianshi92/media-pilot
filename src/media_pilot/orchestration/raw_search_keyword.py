"""原始搜索词构造 — 最小可读化，不维护噪声黑名单"""

import re
from pathlib import Path

GENERIC_PARENT_NAMES = {
    "workspace", "downloads", "download", "library", "movies", "shows",
    "bdmv", "stream", "certificate", "video_ts", "audio_ts", "hvdvd_ts",
    "data", "media", "mnt", "volumes", "vol", "root",
}
STREAM_LIKE_EXTENSIONS = {".m2ts", ".mts", ".ts", ".vob"}


def build_raw_search_keyword(
    selected_path: Path,
    *,
    input_path: Path | None = None,
) -> str:
    """从选中文件路径构造原始搜索词。

    单文件：用文件 stem。目录：向上遍历找有意义的父目录名。
    只做最小可读化：去扩展名、压缩空白。
    """
    # 单文件默认直接使用文件 stem，避免把临时目录名或下载根目录名误当标题。
    if selected_path.suffix and selected_path.suffix.lower() not in STREAM_LIKE_EXTENSIONS:
        stem = selected_path.stem
    else:
        # 从目录路径向上遍历，找到第一个非通用目录名
        meaningful_parent = _find_meaningful_parent(selected_path)
        if meaningful_parent:
            stem = meaningful_parent
        else:
            stem = selected_path.stem

    return _minimal_clean(stem)


def _find_meaningful_parent(path: Path) -> str | None:
    """向上遍历目录树，返回第一个有意义的父目录名"""
    for parent in path.parents:
        name = parent.name.strip()
        if not name or name == ".":
            continue
        if name.lower() not in GENERIC_PARENT_NAMES and len(name) >= 2:
            return name
    return None


def _minimal_clean(stem: str) -> str:
    """最小可读化：压缩空白，保留原始标题信号"""
    # 将常见的分隔符替换为空格
    cleaned = re.sub(r"[._\-@]+", " ", stem)
    # 压缩连续空白
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
