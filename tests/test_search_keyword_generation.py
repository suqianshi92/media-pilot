from pathlib import Path

from media_pilot.orchestration.search_keyword_generation import generate_search_keyword


def test_generate_search_keyword_cleans_movie_filename_to_title_and_year() -> None:
    result = generate_search_keyword(Path("Example.Movie.2026.1080p.WEB-DL-GROUP.mkv"))

    assert result.keyword == "Example Movie 2026"
    assert result.source == "rule"
    assert result.confidence == 0.95
    assert result.reason == "filename_rule_cleanup"


def test_generate_search_keyword_preserves_quality_tokens_separately() -> None:
    result = generate_search_keyword(Path("Movie.Name.2024.2160p.DTS.WEB-DL.mkv"))

    assert result.keyword == "Movie Name 2024"
    assert result.payload["quality_tokens"] == ["2160p", "DTS", "WEB-DL"]


def test_generate_search_keyword_supports_single_token_chinese_title() -> None:
    result = generate_search_keyword(Path("流浪地球2.2023.2160p.WEB-DL.mkv"))

    assert result.keyword == "流浪地球2 2023"
    assert result.confidence == 0.95
    assert result.payload["quality_tokens"] == ["2160p", "WEB-DL"]


def test_generate_search_keyword_removes_advertising_and_release_noise() -> None:
    result = generate_search_keyword(
        Path("[site] Movie.Name.2024.1080p.x265.AAC-GROUP-sample.mkv")
    )

    assert result.keyword == "Movie Name 2024"
    assert result.payload["tokens_removed"] == ["site", "x265", "AAC", "GROUP", "sample"]


def test_generate_search_keyword_returns_low_confidence_when_title_signals_are_missing() -> None:
    result = generate_search_keyword(Path("2160p-WEB-DL-GROUP.mkv"))

    assert result.keyword == "2160p WEB-DL"
    assert result.confidence == 0.3
    assert result.reason == "insufficient_title_signals"


def test_generate_search_keyword_detects_www_url_noise() -> None:
    """www.test.com@天气之子.mkv → URL 噪声，低置信度"""
    result = generate_search_keyword(Path("www.test.com@天气之子.mkv"))

    assert result.confidence == 0.3
    assert result.reason == "url_noise_detected"


def test_generate_search_keyword_detects_com_domain_suffix() -> None:
    """example.com.电影名.mkv → 域名后缀噪声"""
    result = generate_search_keyword(Path("example.com.电影名.mkv"))

    assert result.confidence == 0.3
    assert result.reason == "url_noise_detected"


def test_generate_search_keyword_detects_ip_address() -> None:
    """192.168.1.1-电影名.mkv → IP 噪声"""
    result = generate_search_keyword(Path("192.168.1.1-电影名.mkv"))

    assert result.confidence == 0.3
    assert result.reason == "url_noise_detected"


def test_generate_search_keyword_detects_org_domain() -> None:
    """site.org@电影名.mkv → .org 域名后缀"""
    result = generate_search_keyword(Path("site.org@电影名.mkv"))

    assert result.confidence == 0.3
    assert result.reason == "url_noise_detected"


def test_generate_search_keyword_detects_pure_numeric() -> None:
    """1080 → 纯数字"""
    result = generate_search_keyword(Path("1080.mkv"))

    assert result.confidence == 0.3
    assert result.reason == "url_noise_detected"


def test_generate_search_keyword_clean_name_still_high_confidence() -> None:
    """正常文件名仍然高置信度"""
    result = generate_search_keyword(Path("天气之子.2019.1080p.mkv"))

    assert result.confidence == 0.95
    assert result.reason == "filename_rule_cleanup"
