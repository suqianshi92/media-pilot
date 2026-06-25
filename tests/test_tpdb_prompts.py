"""TPDB 提示词与番号规范化测试 (任务 2.7)"""

from media_pilot.adapters.llm_prompts import (
    LLMKeywordParseError,
    parse_profile_router_response,
)


class TestProfileRouterParser:
    """2.7: parse_profile_router_response 边界测试"""

    def test_rejects_unenabled_profile(self):
        """推荐未启用的档案时解析失败"""
        result = parse_profile_router_response(
            '{"recommended_profile": "tpdb_adult_movie", "keyword": "ABCD-123", "confidence": 0.8}',
            enabled_profiles=["tmdb_movie"],
        )
        assert isinstance(result, LLMKeywordParseError)
        assert "不在已启用档案列表中" in result.message

    def test_accepts_enabled_profile(self):
        """推荐已启用的档案时解析成功"""
        result = parse_profile_router_response(
            '{"recommended_profile": "tpdb_adult_movie", "keyword": "ABCD-123", "confidence": 0.8}',
            enabled_profiles=["tmdb_movie", "tpdb_adult_movie"],
        )
        assert isinstance(result, dict)
        assert result["recommended_profile"] == "tpdb_adult_movie"

    def test_rejects_missing_keyword(self):
        """缺少 keyword 时解析失败"""
        result = parse_profile_router_response(
            '{"recommended_profile": "tmdb_movie", "confidence": 0.8}',
            enabled_profiles=["tmdb_movie"],
        )
        assert isinstance(result, LLMKeywordParseError)

    def test_rejects_invalid_confidence(self):
        """置信度越界时解析失败"""
        result = parse_profile_router_response(
            '{"recommended_profile": "tmdb_movie", "keyword": "test", "confidence": 1.5}',
            enabled_profiles=["tmdb_movie"],
        )
        assert isinstance(result, LLMKeywordParseError)

    def test_returns_dict_with_all_fields(self):
        """成功解析返回完整字典"""
        result = parse_profile_router_response(
            '{"recommended_profile": "tmdb_movie", "keyword": "Inception",'
            ' "confidence": 0.9, "reason": "test",'
            ' "profile_payload": {"type": "tmdb_movie", "candidate_title": "Inception"}}',
            enabled_profiles=["tmdb_movie"],
        )
        assert isinstance(result, dict)
        assert result["recommended_profile"] == "tmdb_movie"
        assert result["keyword"] == "Inception"
