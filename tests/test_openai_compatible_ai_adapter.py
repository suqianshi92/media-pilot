"""OpenAI-compatible LLM adapter 测试 — 使用 mock client，不发送真实请求"""

from unittest.mock import MagicMock, patch

from media_pilot.adapters.ai import (
    AiParseRequest,
    AiParseResult,
    AiSearchKeywordRequest,
    AiSearchKeywordResult,
    MediaType,
)
from media_pilot.adapters.openai_compatible_ai import OpenAICompatibleAiAdapter

VALID_KEYWORD_JSON = (
    '{"keyword": "天气之子", "candidate_title": "天気の子", '
    '"candidate_year": 2019, "confidence": 0.85, '
    '"reason": "LLM 辅助清洗", '
    '"explanation": "移除了网站前缀和编码信息", '
    '"removed_tokens": ["www.test.com", "1080p"]}'
)


def _make_request() -> AiSearchKeywordRequest:
    return AiSearchKeywordRequest(
        input_path="/downloads/www.test.com@天气之子.mkv",
        input_name="www.test.com@天气之子.mkv",
        selected_path="/workspace/www.test.com@天气之子.mkv",
        selected_name="www.test.com@天气之子.mkv",
        selected_parent_name="weathering-with-you",
        rule_keyword="www.test.com 天气之子",
        rule_confidence=0.3,
        quality_tokens=[],
        removed_tokens=["www.test.com", "@"],
    )


def _make_mock_completion(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    completion = MagicMock()
    completion.choices = [choice]
    return completion


def test_adapter_parse_filename_returns_minimal_result() -> None:
    adapter = OpenAICompatibleAiAdapter(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        timeout_seconds=10,
    )
    result = adapter.parse_filename(
        AiParseRequest(filename="www.test.com@天气之子.mkv")
    )
    assert isinstance(result, AiParseResult)
    assert result.media_type == MediaType.UNKNOWN
    assert result.confidence == 0.2


def test_generate_search_keyword_success() -> None:
    adapter = OpenAICompatibleAiAdapter(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        timeout_seconds=10,
    )
    mock_completion = _make_mock_completion(VALID_KEYWORD_JSON)

    with patch.object(adapter._client.chat.completions, "create", return_value=mock_completion):
        result = adapter.generate_search_keyword(_make_request())

    assert isinstance(result, AiSearchKeywordResult)
    assert result.keyword == "天气之子"
    assert result.candidate_title == "天気の子"
    assert result.candidate_year == 2019
    assert result.confidence == 0.85
    assert result.removed_tokens == ["www.test.com", "1080p"]


def test_generate_search_keyword_api_error_raises() -> None:
    adapter = OpenAICompatibleAiAdapter(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        timeout_seconds=10,
    )

    with patch.object(
        adapter._client.chat.completions, "create",
        side_effect=RuntimeError("connection failed"),
    ):
        try:
            adapter.generate_search_keyword(_make_request())
            raise AssertionError("should have raised")
        except RuntimeError:
            pass


def test_generate_search_keyword_empty_response_raises() -> None:
    adapter = OpenAICompatibleAiAdapter(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        timeout_seconds=10,
    )
    mock_completion = _make_mock_completion("")

    with patch.object(adapter._client.chat.completions, "create", return_value=mock_completion):
        try:
            adapter.generate_search_keyword(_make_request())
            raise AssertionError("should have raised")
        except RuntimeError as exc:
            assert "空" in str(exc)


def test_generate_search_keyword_invalid_json_raises() -> None:
    adapter = OpenAICompatibleAiAdapter(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        timeout_seconds=10,
    )
    mock_completion = _make_mock_completion("not valid json")

    with patch.object(adapter._client.chat.completions, "create", return_value=mock_completion):
        try:
            adapter.generate_search_keyword(_make_request())
            raise AssertionError("should have raised")
        except RuntimeError as exc:
            assert "JSON" in str(exc)


def test_api_key_not_in_logs() -> None:
    adapter = OpenAICompatibleAiAdapter(
        api_key="sk-secret-12345",
        base_url="https://api.example.com/v1",
        model="test-model",
        timeout_seconds=10,
    )
    assert "sk-secret" not in adapter._safe_host()
    assert "api.example.com" in adapter._safe_host()
