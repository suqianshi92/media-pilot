"""torrent 最小元数据解析器单元测试"""

from __future__ import annotations

import pytest

from media_pilot.services.torrent_parser import parse_torrent_meta, TorrentMeta


def _make_single_file_torrent(name: str, length: int) -> bytes:
    """构造最小单文件 torrent（bencode）。"""
    info = f"4:infod4:name{len(name)}:{name}6:lengthi{length}e12:piece lengthi262144e6:pieces20:aaaaaaaaaaaaaaaaaaaae"
    return f"d{info}e".encode()


def _make_multi_file_torrent(name: str, files: list[tuple[str, int]]) -> bytes:
    """构造最小多文件 torrent。"""
    file_entries = ""
    for fname, flen in files:
        file_entries += f"d6:lengthi{flen}e4:pathl{len(fname)}:{fname}ee"
    info = f"4:infod4:name{len(name)}:{name}5:filesl{file_entries}e12:piece lengthi262144e6:pieces20:aaaaaaaaaaaaaaaaaaaae"
    return f"d{info}e".encode()


class TestParseTorrentMeta:
    def test_single_file_torrent(self):
        data = _make_single_file_torrent("test.mkv", 1234567890)
        meta = parse_torrent_meta(data)
        assert meta is not None
        assert meta.display_name == "test.mkv"
        assert meta.total_size_bytes == 1234567890

    def test_multi_file_torrent(self):
        data = _make_multi_file_torrent("MyMovie", [
            ("movie.mkv", 1000000),
            ("poster.jpg", 500000),
        ])
        meta = parse_torrent_meta(data)
        assert meta is not None
        assert meta.display_name == "MyMovie"
        assert meta.total_size_bytes == 1500000

    def test_single_file_with_zero_size(self):
        data = _make_single_file_torrent("empty.dat", 0)
        meta = parse_torrent_meta(data)
        assert meta is not None
        assert meta.total_size_bytes == 0

    def test_invalid_data_returns_none(self):
        assert parse_torrent_meta(b"not a torrent") is None
        assert parse_torrent_meta(b"") is None

    def test_no_info_key_returns_none(self):
        data = b"d4:infod3:abci1ee3:othere"
        meta = parse_torrent_meta(data)
        assert meta is None

    def test_info_without_name_returns_none(self):
        data = b"d4:infod6:lengthi100eee"
        meta = parse_torrent_meta(data)
        assert meta is None
