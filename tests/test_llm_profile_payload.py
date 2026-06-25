"""LLM 档案推荐与 profile-specific payload 解析测试"""

import pytest


class TestLlmProfileRecommendation:
    """1.4: LLM 共享外壳 + profile-specific payload 解析和失败场景"""

    # ---- 共享外壳解析 ----

    def test_parse_valid_tmdb_payload(self):
        """解析完整的 TMDB 推荐响应"""
        from media_pilot.services.profile_payload import (
            LlmProfileRecommendation,
            TmdbPayload,
        )

        result = LlmProfileRecommendation.model_validate({
            "recommended_profile": "tmdb_movie",
            "keyword": "Weathering With You",
            "confidence": 0.9,
            "reason": "文件名中包含清晰的电影标题",
            "removed_tokens": ["1080p", "BluRay"],
            "profile_payload": {
                "type": "tmdb_movie",
                "candidate_title": "Weathering With You",
                "candidate_year": 2019,
                "original_title": "天気の子",
            },
        })

        assert result.recommended_profile == "tmdb_movie"
        assert result.keyword == "Weathering With You"
        assert result.confidence == 0.9
        assert isinstance(result.profile_payload, TmdbPayload)
        assert result.profile_payload.candidate_title == "Weathering With You"
        assert result.profile_payload.candidate_year == 2019
        assert result.profile_payload.original_title == "天気の子"

    def test_parse_valid_tpdb_payload(self):
        """解析完整的 TPDB 推荐响应"""
        from media_pilot.services.profile_payload import (
            LlmProfileRecommendation,
            TpdbPayload,
        )

        result = LlmProfileRecommendation.model_validate({
            "recommended_profile": "tpdb_adult_movie",
            "keyword": "ABCD-123",
            "confidence": 0.85,
            "reason": "文件名中包含番号 ABCD123，规范化为 ABCD-123",
            "removed_tokens": ["www.example.com", "1080p"],
            "profile_payload": {
                "type": "tpdb_adult_movie",
                "identifier": "ABCD-123",
                "identifier_type": "normalized_code",
                "normalized_code": "ABCD-123",
                "raw_code": "ABCD123",
            },
        })

        assert result.recommended_profile == "tpdb_adult_movie"
        assert result.keyword == "ABCD-123"
        assert result.confidence == 0.85
        assert isinstance(result.profile_payload, TpdbPayload)
        assert result.profile_payload.identifier == "ABCD-123"
        assert result.profile_payload.identifier_type == "normalized_code"
        assert result.profile_payload.normalized_code == "ABCD-123"
        assert result.profile_payload.raw_code == "ABCD123"

    # ---- 解析失败场景 ----

    def test_missing_recommended_profile_fails(self):
        """缺少 recommended_profile 字段时解析失败"""
        from pydantic import ValidationError

        from media_pilot.services.profile_payload import LlmProfileRecommendation

        with pytest.raises(ValidationError):
            LlmProfileRecommendation.model_validate({
                "keyword": "test",
                "confidence": 0.5,
                "profile_payload": {"type": "tmdb_movie"},
            })

    def test_unknown_profile_type_fails(self):
        """不支持的 profile_payload.type 解析失败"""
        from pydantic import ValidationError

        from media_pilot.services.profile_payload import LlmProfileRecommendation

        with pytest.raises(ValidationError):
            LlmProfileRecommendation.model_validate({
                "recommended_profile": "unknown_profile",
                "keyword": "test",
                "confidence": 0.5,
                "profile_payload": {"type": "unsupported_type"},
            })

    def test_tmdb_payload_missing_candidate_title_fails(self):
        """TMDB payload 缺少必填 candidate_title 时失败"""
        from pydantic import ValidationError

        from media_pilot.services.profile_payload import LlmProfileRecommendation

        with pytest.raises(ValidationError):
            LlmProfileRecommendation.model_validate({
                "recommended_profile": "tmdb_movie",
                "keyword": "test",
                "confidence": 0.5,
                "profile_payload": {
                    "type": "tmdb_movie",
                    # candidate_title 缺失
                },
            })

    def test_tpdb_payload_missing_identifier_fails(self):
        """TPDB payload 缺少必填 identifier 时失败"""
        from pydantic import ValidationError

        from media_pilot.services.profile_payload import LlmProfileRecommendation

        with pytest.raises(ValidationError):
            LlmProfileRecommendation.model_validate({
                "recommended_profile": "tpdb_adult_movie",
                "keyword": "test",
                "confidence": 0.5,
                "profile_payload": {
                    "type": "tpdb_adult_movie",
                    # identifier 缺失
                },
            })

    def test_confidence_out_of_range_fails(self):
        """置信度超出 [0,1] 区间时失败"""
        from pydantic import ValidationError

        from media_pilot.services.profile_payload import LlmProfileRecommendation

        with pytest.raises(ValidationError):
            LlmProfileRecommendation.model_validate({
                "recommended_profile": "tmdb_movie",
                "keyword": "test",
                "confidence": 1.5,
                "profile_payload": {
                    "type": "tmdb_movie",
                    "candidate_title": "Test",
                },
            })

    def test_tpdb_identifier_type_must_be_valid(self):
        """TPDB identifier_type 只接受合法值"""
        from pydantic import ValidationError

        from media_pilot.services.profile_payload import LlmProfileRecommendation

        with pytest.raises(ValidationError):
            LlmProfileRecommendation.model_validate({
                "recommended_profile": "tpdb_adult_movie",
                "keyword": "test",
                "confidence": 0.5,
                "profile_payload": {
                    "type": "tpdb_adult_movie",
                    "identifier": "ABCD-123",
                    "identifier_type": "invalid_type",
                },
            })
