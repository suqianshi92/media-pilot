"""最小 .torrent 文件解析 — 只提取显示名和总大小。

不引入第三方 bencode 库依赖，用内联递归下降解析器。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, kw_only=True)
class TorrentMeta:
    display_name: str
    total_size_bytes: int


def parse_torrent_meta(data: bytes) -> TorrentMeta | None:
    """从 .torrent 文件的原始字节中提取最小元数据。"""
    try:
        root = _bdecode(data)
        if not isinstance(root, dict):
            return None
        info = root.get(b"info")
        if not isinstance(info, dict):
            return None
        name = _bstr(info.get(b"name"))
        if not name:
            return None

        files = info.get(b"files")
        if isinstance(files, list):
            total = sum(
                _bint(f.get(b"length")) for f in files if isinstance(f, dict)
            )
        else:
            total = _bint(info.get(b"length"))

        return TorrentMeta(display_name=name, total_size_bytes=total)
    except Exception:
        return None


# ── minimal bencode parser ──

def _bint(v: Any) -> int:
    if isinstance(v, int):
        return v
    return 0


def _bstr(v: Any) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return ""


def _bdecode(data: bytes) -> Any:
    """递归下降 bencode 解析。只接受 bytes 输入。"""
    return _parse_value(data, 0)[0]


def _parse_value(data: bytes, pos: int) -> tuple[Any, int]:
    if pos >= len(data):
        raise ValueError("unexpected end of bencode data")
    ch = data[pos : pos + 1]
    if ch == b"i":
        return _parse_int(data, pos + 1)
    if ch == b"l":
        return _parse_list(data, pos + 1)
    if ch == b"d":
        return _parse_dict(data, pos + 1)
    if ch in b"0123456789":
        return _parse_string(data, pos)
    raise ValueError(f"unexpected bencode prefix at {pos}: {chr(data[pos])!r}")


def _parse_int(data: bytes, pos: int) -> tuple[int, int]:
    end = data.index(b"e", pos)
    return int(data[pos:end]), end + 1


def _parse_string(data: bytes, pos: int) -> tuple[bytes, int]:
    colon = data.index(b":", pos)
    length = int(data[pos:colon])
    start = colon + 1
    end = start + length
    return data[start:end], end


def _parse_list(data: bytes, pos: int) -> tuple[list, int]:
    items: list = []
    while pos < len(data) and data[pos : pos + 1] != b"e":
        value, pos = _parse_value(data, pos)
        items.append(value)
    return items, pos + 1


def _parse_dict(data: bytes, pos: int) -> tuple[dict, int]:
    d: dict = {}
    while pos < len(data) and data[pos : pos + 1] != b"e":
        key, pos = _parse_string(data, pos)
        value, pos = _parse_value(data, pos)
        d[key] = value
    return d, pos + 1
