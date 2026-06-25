"""测试 ResourceIntentParser._build_system_prompt 动态上下文注入"""

import json
from unittest.mock import MagicMock, patch

from media_pilot.resource_discovery.intent_parser import ResourceIntentParser


class TestBuildSystemPrompt:
    """验证 _build_system_prompt 注入语言偏好和已启用 profile"""

    def test_zh_language_hint_in_prompt(self):
        """zh 偏好时 prompt 包含'中文'"""
        base = "你是媒体资源搜索助手。"
        result = ResourceIntentParser._build_system_prompt(
            base, preferred_language="zh", enabled_profiles=None,
        )
        assert "中文" in result
        assert "中文片名排前" in result

    def test_en_language_hint_in_prompt(self):
        """en 偏好时 prompt 包含'English'"""
        base = "你是媒体资源搜索助手。"
        result = ResourceIntentParser._build_system_prompt(
            base, preferred_language="en", enabled_profiles=None,
        )
        assert "English" in result
        assert "英文/原名排前" in result

    def test_enabled_profiles_in_prompt(self):
        """已启用 profile 列表注入 prompt"""
        base = "你是媒体资源搜索助手。"
        result = ResourceIntentParser._build_system_prompt(
            base,
            preferred_language="zh",
            enabled_profiles=["tmdb_movie", "tpdb_adult_movie"],
        )
        assert "tmdb_movie" in result
        assert "tpdb_adult_movie" in result
        assert "profile_hint 只能从以下值中选择" in result

    def test_default_when_no_profiles(self):
        """无 profile 时默认 tmdb_movie"""
        base = "你是媒体资源搜索助手。"
        result = ResourceIntentParser._build_system_prompt(
            base, preferred_language="zh", enabled_profiles=None,
        )
        assert "tmdb_movie" in result

    def test_single_profile_still_constrains(self):
        """单个 profile 也约束范围"""
        base = "你是媒体资源搜索助手。"
        result = ResourceIntentParser._build_system_prompt(
            base,
            preferred_language="en",
            enabled_profiles=["tmdb_movie"],
        )
        assert "tmdb_movie" in result
        assert "profile_hint 只能从以下值中选择" in result


class TestParseWithContext:
    """验证 parse() 正确传递上下文到 LLM"""

    def test_language_and_profiles_in_system_message(self):
        """parse() 把语言偏好和 profile 写入 system message"""
        parser = ResourceIntentParser(
            api_key="test-key",
            base_url="http://test",
            model="test",
            timeout_seconds=5,
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "search_type": "movie",
            "title_candidates": ["Test"],
            "resource_keywords": ["test"],
            "quality_hint": "",
            "profile_hint": "tmdb_movie",
            "preferred_title_candidates": ["Test"],
            "adult_identifier_candidates": [],
            "reason": "test",
        })

        with patch.object(
            parser._client.chat.completions, "create", return_value=mock_response
        ) as mock_create:
            parser.parse(
                "test",
                preferred_language="zh",
                enabled_profiles=["tmdb_movie", "tpdb_adult_movie"],
            )
            call_args = mock_create.call_args
            system_msg = call_args[1]["messages"][0]["content"]
            assert "中文" in system_msg
            assert "tmdb_movie" in system_msg
            assert "tpdb_adult_movie" in system_msg
            assert "profile_hint 只能从以下值中选择" in system_msg

    def test_parse_defaults_when_no_context(self):
        """不传上下文时默认 zh + tmdb_movie"""
        parser = ResourceIntentParser(
            api_key="test-key",
            base_url="http://test",
            model="test",
            timeout_seconds=5,
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "search_type": "movie",
            "title_candidates": ["Test"],
            "resource_keywords": ["test"],
            "quality_hint": "",
            "profile_hint": "tmdb_movie",
            "preferred_title_candidates": ["Test"],
            "adult_identifier_candidates": [],
            "reason": "test",
        })

        with patch.object(
            parser._client.chat.completions, "create", return_value=mock_response
        ):
            intent = parser.parse("test")
            assert intent.search_type == "movie"
