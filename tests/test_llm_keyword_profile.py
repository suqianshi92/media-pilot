"""LLM 关键词 profile — 结构化响应校验测试"""


from media_pilot.adapters.llm_prompts import (
    LLMKeywordParseError,
    LLMKeywordResult,
    parse_llm_keyword_response,
)

VALID_JSON = (
    '{"keyword": "天气之子", "candidate_title": "天気の子", '
    '"candidate_year": 2019, "confidence": 0.90, '
    '"reason": "规则清洗+LLM辅助", '
    '"explanation": "去除了网站前缀和发布组后缀", '
    '"removed_tokens": ["www.test.com", "1080p", "BluRay"]}'
)


def test_valid_keyword_response() -> None:
    result = parse_llm_keyword_response(VALID_JSON)
    assert isinstance(result, LLMKeywordResult)
    assert result.keyword == "天气之子"
    assert result.candidate_title == "天気の子"
    assert result.candidate_year == 2019
    assert result.confidence == 0.90
    assert result.reason == "规则清洗+LLM辅助"
    assert result.explanation == "去除了网站前缀和发布组后缀"
    assert result.removed_tokens == ["www.test.com", "1080p", "BluRay"]


def test_minimal_valid_response() -> None:
    json_str = (
        '{"keyword": "天气之子", "candidate_title": null, '
        '"candidate_year": null, "confidence": 0.5, '
        '"reason": "仅规则提取", "explanation": "无法识别更多信息", '
        '"removed_tokens": []}'
    )
    result = parse_llm_keyword_response(json_str)
    assert isinstance(result, LLMKeywordResult)
    assert result.candidate_title is None
    assert result.candidate_year is None
    assert result.removed_tokens == []


def test_markdown_code_block_wrapping() -> None:
    raw = "```json\n" + VALID_JSON + "\n```"
    result = parse_llm_keyword_response(raw)
    assert isinstance(result, LLMKeywordResult)
    assert result.keyword == "天气之子"


def test_plain_code_block_wrapping() -> None:
    raw = "```\n" + VALID_JSON + "\n```"
    result = parse_llm_keyword_response(raw)
    assert isinstance(result, LLMKeywordResult)


def test_non_json_response() -> None:
    result = parse_llm_keyword_response("这是一段中文文本，不是JSON")
    assert isinstance(result, LLMKeywordParseError)
    assert "JSON" in result.message


def test_json_array_not_object() -> None:
    result = parse_llm_keyword_response('[{"keyword": "test"}]')
    assert isinstance(result, LLMKeywordParseError)
    assert "对象" in result.message


def test_missing_keyword() -> None:
    result = parse_llm_keyword_response(
        '{"candidate_title": "test", "candidate_year": 2020, '
        '"confidence": 0.8, "reason": "test", '
        '"explanation": "test", "removed_tokens": []}'
    )
    assert isinstance(result, LLMKeywordParseError)
    assert "keyword" in result.message


def test_empty_keyword() -> None:
    result = parse_llm_keyword_response(
        '{"keyword": "", "candidate_title": null, '
        '"candidate_year": null, "confidence": 0.5, '
        '"reason": "test", "explanation": "test", "removed_tokens": []}'
    )
    assert isinstance(result, LLMKeywordParseError)
    assert "keyword" in result.message


def test_whitespace_keyword() -> None:
    result = parse_llm_keyword_response(
        '{"keyword": "   ", "candidate_title": null, '
        '"candidate_year": null, "confidence": 0.5, '
        '"reason": "test", "explanation": "test", "removed_tokens": []}'
    )
    assert isinstance(result, LLMKeywordParseError)
    assert "keyword" in result.message


def test_confidence_out_of_bounds_high() -> None:
    result = parse_llm_keyword_response(
        '{"keyword": "test", "candidate_title": null, '
        '"candidate_year": null, "confidence": 1.5, '
        '"reason": "test", "explanation": "test", "removed_tokens": []}'
    )
    assert isinstance(result, LLMKeywordParseError)
    assert "confidence" in result.message


def test_confidence_out_of_bounds_low() -> None:
    result = parse_llm_keyword_response(
        '{"keyword": "test", "candidate_title": null, '
        '"candidate_year": null, "confidence": -0.1, '
        '"reason": "test", "explanation": "test", "removed_tokens": []}'
    )
    assert isinstance(result, LLMKeywordParseError)
    assert "confidence" in result.message


def test_removed_tokens_not_array() -> None:
    result = parse_llm_keyword_response(
        '{"keyword": "test", "candidate_title": null, '
        '"candidate_year": null, "confidence": 0.5, '
        '"reason": "test", "explanation": "test", "removed_tokens": "not_array"}'
    )
    assert isinstance(result, LLMKeywordParseError)
    assert "removed_tokens" in result.message


def test_removed_tokens_not_all_strings() -> None:
    result = parse_llm_keyword_response(
        '{"keyword": "test", "candidate_title": null, '
        '"candidate_year": null, "confidence": 0.5, '
        '"reason": "test", "explanation": "test", "removed_tokens": [1, 2, 3]}'
    )
    assert isinstance(result, LLMKeywordParseError)
    assert "removed_tokens" in result.message


def test_candidate_year_not_int() -> None:
    result = parse_llm_keyword_response(
        '{"keyword": "test", "candidate_title": null, '
        '"candidate_year": "2020", "confidence": 0.5, '
        '"reason": "test", "explanation": "test", "removed_tokens": []}'
    )
    assert isinstance(result, LLMKeywordParseError)
    assert "candidate_year" in result.message


def test_scenario_www_prefix_cleaning() -> None:
    """模拟 www.test.com@天气之子.mkv 场景"""
    result = parse_llm_keyword_response(
        '{"keyword": "天气之子", "candidate_title": "天気の子", '
        '"candidate_year": 2019, "confidence": 0.85, '
        '"reason": "去除了网站前缀", '
        '"explanation": "移除了 www.test.com 前缀和 .mkv 扩展名", '
        '"removed_tokens": ["www.test.com", "@", ".mkv"]}'
    )
    assert isinstance(result, LLMKeywordResult)
    assert result.keyword == "天气之子"
    assert result.confidence == 0.85
