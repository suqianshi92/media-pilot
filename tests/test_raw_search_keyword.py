"""原始搜索词构造测试"""

from pathlib import Path

from media_pilot.orchestration.raw_search_keyword import build_raw_search_keyword


def test_single_file_uses_stem() -> None:
    result = build_raw_search_keyword(Path("/downloads/天气之子.2019.1080p.mkv"))
    assert "天气之子" in result
    assert "2019" in result


def test_directory_prefers_parent_name() -> None:
    result = build_raw_search_keyword(
        Path("/workspace/天气之子/BDMV/STREAM/00001.m2ts"),
    )
    assert "天气之子" in result


def test_generic_parent_falls_back_to_stem() -> None:
    result = build_raw_search_keyword(
        Path("/data/downloads/www.test.com@天气之子.mkv"),
    )
    # downloads 是通用目录，跳过；最终回退到文件 stem
    assert "天气之子" in result
    assert "downloads" not in result


def test_underscore_separators_become_spaces() -> None:
    result = build_raw_search_keyword(
        Path("/downloads/The.Matrix.1999.mkv"),
    )
    assert "The Matrix 1999" in result


def test_chinese_title_preserved() -> None:
    result = build_raw_search_keyword(
        Path("/downloads/你的名字.2016.BluRay.mkv"),
    )
    assert "你的名字" in result


def test_whitespace_compressed() -> None:
    result = build_raw_search_keyword(
        Path("/downloads/The---Matrix___1999.mkv"),
    )
    # 多余分隔符被压缩为单个空格
    assert "The Matrix 1999" == result
