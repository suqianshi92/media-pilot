
"""测试 intent_parser ResourceIntentParser 新字段透传 — Codex review fix"""

import json
from unittest.mock import MagicMock, patch


class TestIntentParserNewFields:
    """验证 ResourceIntentParser.parse() 不会丢弃 preferred_title/adult_identifier"""

    def test_preferred_title_candidates_survive_parse(self):
        """parse() 透传 preferred_title_candidates"""
        from media_pilot.resource_discovery.intent_parser import ResourceIntentParser

        parser = ResourceIntentParser(
            api_key="test-key",
            base_url="http://test",
            model="test",
            timeout_seconds=5,
        )
        # 模拟 LLM 返回包含新字段的 JSON
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "search_type": "movie",
            "title_candidates": ["天气之子"],
            "resource_keywords": ["天气之子 1080p"],
            "quality_hint": "1080p",
            "profile_hint": "tmdb_movie",
            "preferred_title_candidates": ["天气之子", "Weathering With You"],
            "adult_identifier_candidates": [],
            "reason": "动画电影",
        })

        with patch.object(parser._client.chat.completions, "create", return_value=mock_response):
            intent = parser.parse("天气之子")
            assert intent.preferred_title_candidates == ["天气之子", "Weathering With You"]
            assert intent.adult_identifier_candidates == []

    def test_adult_identifier_candidates_survive_parse(self):
        """parse() 透传 adult_identifier_candidates"""
        from media_pilot.resource_discovery.intent_parser import ResourceIntentParser

        parser = ResourceIntentParser(
            api_key="test-key",
            base_url="http://test",
            model="test",
            timeout_seconds=5,
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "search_type": "adult",
            "title_candidates": [],
            "resource_keywords": ["ABP-123"],
            "quality_hint": "",
            "profile_hint": "tpdb_adult_movie",
            "preferred_title_candidates": [],
            "adult_identifier_candidates": ["ABP-123"],
            "reason": "番号匹配",
        })

        with patch.object(parser._client.chat.completions, "create", return_value=mock_response):
            intent = parser.parse("ABP-123")
            assert intent.adult_identifier_candidates == ["ABP-123"]

    def test_new_fields_default_when_missing_in_llm_output(self):
        """LLM 不返回新字段时使用默认空列表"""
        from media_pilot.resource_discovery.intent_parser import ResourceIntentParser

        parser = ResourceIntentParser(
            api_key="test-key",
            base_url="http://test",
            model="test",
            timeout_seconds=5,
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "search_type": "all",
            "title_candidates": [],
            "resource_keywords": [],
            "quality_hint": "",
            "profile_hint": "unknown",
            "reason": "",
        })

        with patch.object(parser._client.chat.completions, "create", return_value=mock_response):
            intent = parser.parse("vague")
            assert intent.preferred_title_candidates == []
            assert intent.adult_identifier_candidates == []
