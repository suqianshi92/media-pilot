"""资源发现 LLM 意图解析器测试 — 测试 prompt 格式、JSON 解析和错误处理"""

import json

from media_pilot.resource_discovery.intent_parser import (
    IntentParseError,
)


class TestResourceIntentPrompt:
    def test_user_template_formats_correctly(self):
        from media_pilot.adapters.llm_prompts import RESOURCE_INTENT_USER_TEMPLATE

        result = RESOURCE_INTENT_USER_TEMPLATE.format(input_text="天气之子")
        assert "天气之子" in result
        assert "用户输入" in result

    def test_system_prompt_contains_json_schema(self):
        from media_pilot.adapters.llm_prompts import RESOURCE_INTENT_SYSTEM_PROMPT

        assert "search_type" in RESOURCE_INTENT_SYSTEM_PROMPT
        assert "title_candidates" in RESOURCE_INTENT_SYSTEM_PROMPT
        assert "resource_keywords" in RESOURCE_INTENT_SYSTEM_PROMPT
        assert "preferred_resolutions" in RESOURCE_INTENT_SYSTEM_PROMPT
        assert "preferred_sources" in RESOURCE_INTENT_SYSTEM_PROMPT
        assert "preferred_title_candidates" in RESOURCE_INTENT_SYSTEM_PROMPT
        assert "adult_identifier_candidates" in RESOURCE_INTENT_SYSTEM_PROMPT
        assert "resource_search_keywords" in RESOURCE_INTENT_SYSTEM_PROMPT

    def test_system_prompt_caps_output_size(self):
        """资源意图 prompt 必须约束短输出, 避免 finish_reason=length 截断 JSON."""
        from media_pilot.adapters.llm_prompts import RESOURCE_INTENT_SYSTEM_PROMPT

        assert "紧凑 JSON" in RESOURCE_INTENT_SYSTEM_PROMPT
        assert "不要 Markdown" in RESOURCE_INTENT_SYSTEM_PROMPT
        assert "title_candidates / preferred_title_candidates" in RESOURCE_INTENT_SYSTEM_PROMPT
        assert "最多 2 项" in RESOURCE_INTENT_SYSTEM_PROMPT
        assert (
            "resource_keywords / resource_search_keywords 最多 3 项"
            in RESOURCE_INTENT_SYSTEM_PROMPT
        )
        assert "reason 只写一句短理由" in RESOURCE_INTENT_SYSTEM_PROMPT
        assert "tmdb_movie|tmdb_show|tpdb_adult_movie|unknown" in RESOURCE_INTENT_SYSTEM_PROMPT


class TestParseResourceIntentResponse:
    def test_valid_movie_response(self):
        from media_pilot.adapters.llm_prompts import parse_resource_intent_response

        response = json.dumps({
            "search_type": "movie",
            "title_candidates": ["天气之子", "Weathering With You"],
            "resource_keywords": ["天气之子 1080p", "Weathering With You 1080p"],
            "quality_hint": "1080p",
            "profile_hint": "tmdb_movie",
            "reason": "用户请求动画电影",
        })
        intent = parse_resource_intent_response(response)
        assert intent.search_type == "movie"
        assert len(intent.resource_keywords) == 2
        assert intent.profile_hint == "tmdb_movie"

    def test_adult_response(self):
        from media_pilot.adapters.llm_prompts import parse_resource_intent_response

        response = json.dumps({
            "search_type": "adult",
            "title_candidates": ["ABP-123"],
            "resource_keywords": ["ABP-123"],
            "quality_hint": "",
            "profile_hint": "tpdb_adult_movie",
            "reason": "番号匹配",
        })
        intent = parse_resource_intent_response(response)
        assert intent.search_type == "adult"
        assert intent.profile_hint == "tpdb_adult_movie"

    def test_missing_fields_use_defaults(self):
        from media_pilot.adapters.llm_prompts import parse_resource_intent_response

        response = json.dumps({"search_type": "all"})
        intent = parse_resource_intent_response(response)
        assert intent.search_type == "all"
        assert intent.title_candidates == []
        assert intent.resource_keywords == []
        assert intent.quality_hint == ""
        assert intent.profile_hint == "unknown"
        assert intent.reason == ""

    def test_plot_description_generates_keywords(self):
        """剧情片段应该能推断搜索关键词"""
        from media_pilot.adapters.llm_prompts import parse_resource_intent_response

        response = json.dumps({
            "search_type": "movie",
            "title_candidates": ["天气之子"],
            "resource_keywords": ["天气之子 1080p", "Weathering With You"],
            "quality_hint": "1080p",
            "profile_hint": "tmdb_movie",
            "reason": "从剧情描述推断为天气之子",
        })
        intent = parse_resource_intent_response(response)
        assert len(intent.resource_keywords) >= 1
        assert intent.reason != ""

    def test_preferred_title_candidates_present(self):
        from media_pilot.adapters.llm_prompts import parse_resource_intent_response

        response = json.dumps({
            "search_type": "movie",
            "title_candidates": ["天气之子", "Weathering With You"],
            "resource_keywords": ["天气之子 1080p"],
            "quality_hint": "1080p",
            "profile_hint": "tmdb_movie",
            "preferred_title_candidates": ["天气之子", "Weathering With You"],
            "adult_identifier_candidates": [],
            "reason": "中文用户搜索动画电影",
        })
        intent = parse_resource_intent_response(response)
        assert intent.preferred_title_candidates == ["天气之子", "Weathering With You"]
        assert intent.adult_identifier_candidates == []

    def test_adult_identifier_candidates_present(self):
        from media_pilot.adapters.llm_prompts import parse_resource_intent_response

        response = json.dumps({
            "search_type": "adult",
            "title_candidates": ["ABP-123"],
            "resource_keywords": ["ABP-123"],
            "quality_hint": "",
            "profile_hint": "tpdb_adult_movie",
            "preferred_title_candidates": [],
            "adult_identifier_candidates": ["ABP-123"],
            "reason": "识别为完整番号",
        })
        intent = parse_resource_intent_response(response)
        assert intent.adult_identifier_candidates == ["ABP-123"]

    def test_new_fields_default_to_empty(self):
        from media_pilot.adapters.llm_prompts import parse_resource_intent_response

        response = json.dumps({
            "search_type": "all",
        })
        intent = parse_resource_intent_response(response)
        assert intent.preferred_title_candidates == []
        assert intent.adult_identifier_candidates == []


    def test_adult_identifier_only_valid_ids(self):
        """TPDB path: adult_identifier_candidates must only contain catalog numbers/prefixes"""
        from media_pilot.adapters.llm_prompts import parse_resource_intent_response

        # Even if LLM accidentally includes genre words, parser should still accept them
        # (parser doesn't validate content — prompt should enforce this)
        response = json.dumps({
            "search_type": "adult",
            "title_candidates": [],
            "resource_keywords": ["ABP-123"],
            "quality_hint": "",
            "profile_hint": "tpdb_adult_movie",
            "preferred_title_candidates": [],
            "adult_identifier_candidates": ["ABP-123", "ABP"],
            "reason": "番号搜索",
        })
        intent = parse_resource_intent_response(response)
        assert "ABP-123" in intent.adult_identifier_candidates
        assert "ABP" in intent.adult_identifier_candidates

    def test_erotic_movie_not_misrouted_to_tpdb(self):
        """情色题材普通电影不应误入 TPDB — profile_hint 保持 tmdb_movie"""
        from media_pilot.adapters.llm_prompts import parse_resource_intent_response

        response = json.dumps({
            "search_type": "movie",
            "title_candidates": ["Fifty Shades of Grey"],
            "resource_keywords": ["Fifty Shades of Grey 1080p"],
            "quality_hint": "1080p",
            "profile_hint": "tmdb_movie",
            "preferred_title_candidates": ["Fifty Shades of Grey"],
            "adult_identifier_candidates": [],
            "reason": "情色题材但属于普通电影，有明确片名",
        })
        intent = parse_resource_intent_response(response)
        assert intent.search_type == "movie"
        assert intent.profile_hint == "tmdb_movie"
        assert intent.adult_identifier_candidates == []


class TestIntentParseError:
    def test_error_message_preserved(self):
        error = IntentParseError("测试错误")
        assert error.message == "测试错误"
        assert str(error) == "测试错误"

    def test_is_exception(self):
        error = IntentParseError("test")
        assert isinstance(error, Exception)
