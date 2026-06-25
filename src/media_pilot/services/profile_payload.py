"""LLM 档案推荐结果 DTO — 共享外壳 + profile-specific payload"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TmdbPayload(BaseModel):
    """TMDB 通用 payload（电影与剧集共用）"""
    type: Literal["tmdb_movie", "tmdb_show"] = "tmdb_movie"
    candidate_title: str
    candidate_year: int | None = None
    original_title: str | None = None


class TpdbPayload(BaseModel):
    """TPDB 成人影片专属 payload"""
    type: Literal["tpdb_adult_movie"] = "tpdb_adult_movie"
    identifier: str
    identifier_type: Literal["normalized_code", "raw_code", "title"]
    normalized_code: str | None = None
    raw_code: str | None = None


class LlmProfileRecommendation(BaseModel):
    """LLM 档案推荐与关键词清洗结果"""
    recommended_profile: str
    keyword: str
    confidence: float = Field(ge=0, le=1)
    reason: str = ""
    removed_tokens: list[str] = Field(default_factory=list)
    profile_payload: TmdbPayload | TpdbPayload = Field(discriminator="type")
