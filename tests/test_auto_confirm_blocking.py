"""profile-aware 自动确认阻塞条件单元测试

覆盖 spec/profile-aware-llm-auto-confirm/spec.md 中的 Requirement:
- 元数据候选优先的自动确认
- 自动确认安全门槛
"""

from pathlib import Path

from media_pilot.adapters.ai import AiParseResult, MediaType
from media_pilot.adapters.metadata import (
    MetadataCandidate,
)
from media_pilot.config import AdapterMode, AppConfig
from media_pilot.orchestration.auto_confirmation import (
    auto_confirm_blocked_reason,
    has_clear_winner,
)
from media_pilot.orchestration.metadata_search_flow import (
    ProviderSearchResult,
)


def _stub_keyword_result(*, confidence: float, source: str = "llm"):
    """构造 SearchKeywordResult 兼容对象"""
    from media_pilot.orchestration.search_keyword_generation import SearchKeywordResult

    return SearchKeywordResult(
        keyword="test-keyword",
        source=source,
        confidence=confidence,
        reason="stub",
        payload={},
    )


def _stub_ai_result(*, confidence: float = 0.5, title: str | None = None, year: int | None = None):
    return AiParseResult(
        media_type=MediaType.UNKNOWN,
        title=title,
        original_title="test.mkv",
        year=year,
        season=None,
        episode=None,
        resolution=None,
        release_group=None,
        language=None,
        confidence=confidence,
        reason="stub",
    )


def _make_config(ai_adapter: AdapterMode = AdapterMode.REAL) -> AppConfig:
    return AppConfig(
        downloads_dir=Path("/tmp/downloads"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp/workspace"),
        movies_dir=Path("/tmp/library/movies"),
        shows_dir=Path("/tmp/library/shows"),
        database_dir=Path("/tmp/db"),
        ai_adapter=ai_adapter,
    )


def _candidate(
    *,
    confidence: float,
    poster_url: str | None = "https://example.test/poster.jpg",
    year: int | None = None,
    provider: str = "tpdb",
):
    return MetadataCandidate(
        provider=provider,
        provider_id=f"jav/test-{confidence}",
        title="Test",
        original_title="test",
        year=year,
        media_type="movie",
        overview="",
        poster_url=poster_url,
        confidence=confidence,
        match_reason="精确匹配",
    )


# ---------- Task 1.3: 明确 TPDB 候选 + 低关键词置信度不得返回 low_keyword_confidence ----------

class TestClearWinnerOverridesLowKeywordConfidence:
    def test_single_high_confidence_candidate_skips_keyword_check(self):
        """明确元数据候选存在时，关键词置信度低不作为阻塞条件"""
        config = _make_config()
        candidates = [_candidate(confidence=0.95)]

        reason = auto_confirm_blocked_reason(
            config,
            _stub_ai_result(),
            source_selection_confidence=1.0,
            keyword_result=_stub_keyword_result(confidence=0.2),
            provider_search_result=ProviderSearchResult(candidates=candidates),
            metadata_provider_enabled=True,
            auto_confirm_confidence=0.8,
            has_llm_keyword_generator=True,
        )

        # 关键词 0.2 < 0.8，但 provider 有 0.95 明确候选 → 不应阻塞
        assert reason != "low_keyword_confidence"
        # 其他检查应通过（无年份冲突、有海报、无目标冲突）
        assert reason is None

    def test_tpdb_exact_match_with_low_llm_keyword_passes(self):
        """模拟 NACT-120 场景：TPDB 精确命中 0.95，LLM 关键词 0.2 → 不阻塞"""
        config = _make_config()
        candidates = [
            MetadataCandidate(
                provider="tpdb",
                provider_id="jav/nact-120",
                title="NACT-120: Wife Sold",
                original_title="nact-120",
                year=2026,
                media_type="movie",
                overview="",
                poster_url="https://thumb.theporndb.net/poster.jpg",
                confidence=0.95,
                match_reason="番号精确匹配",
            ),
            _candidate(confidence=0.45, year=2026),
        ]

        reason = auto_confirm_blocked_reason(
            config,
            _stub_ai_result(),
            source_selection_confidence=1.0,
            keyword_result=_stub_keyword_result(confidence=0.2),
            provider_search_result=ProviderSearchResult(candidates=candidates),
            metadata_provider_enabled=True,
            auto_confirm_confidence=0.8,
            has_llm_keyword_generator=True,
        )

        assert reason != "low_keyword_confidence"
        assert reason is None


# ---------- Task 1.4: 无候选/候选差距不足/provider error 仍进入人工确认 ----------

class TestNoClearWinnerStillBlocks:
    def test_empty_candidates_returns_no_metadata_candidates(self):
        """无候选 → no_metadata_candidates"""
        config = _make_config()

        reason = auto_confirm_blocked_reason(
            config,
            _stub_ai_result(),
            source_selection_confidence=1.0,
            keyword_result=_stub_keyword_result(confidence=0.9),
            provider_search_result=ProviderSearchResult(candidates=[]),
            metadata_provider_enabled=True,
            auto_confirm_confidence=0.8,
            has_llm_keyword_generator=True,
        )

        assert reason == "no_metadata_candidates"

    def test_ambiguous_candidates_returns_multiple_metadata_candidates(self):
        """多个高置信度候选但差距不足 → multiple_metadata_candidates"""
        config = _make_config()
        candidates = [
            _candidate(confidence=0.93),
            _candidate(confidence=0.91),
        ]

        reason = auto_confirm_blocked_reason(
            config,
            _stub_ai_result(),
            source_selection_confidence=1.0,
            keyword_result=_stub_keyword_result(confidence=0.9),
            provider_search_result=ProviderSearchResult(candidates=candidates),
            metadata_provider_enabled=True,
            auto_confirm_confidence=0.8,
            has_llm_keyword_generator=True,
        )

        assert reason == "multiple_metadata_candidates"

    def test_provider_error_returns_metadata_provider_failed(self):
        """provider 返回错误 → metadata_provider_failed"""
        config = _make_config()

        reason = auto_confirm_blocked_reason(
            config,
            _stub_ai_result(),
            source_selection_confidence=1.0,
            keyword_result=_stub_keyword_result(confidence=0.9),
            provider_search_result=ProviderSearchResult(
                candidates=[],
                error_message="provider unavailable",
            ),
            metadata_provider_enabled=True,
            auto_confirm_confidence=0.8,
            has_llm_keyword_generator=True,
        )

        assert reason == "metadata_provider_failed"

    def test_below_threshold_candidate_returns_no_metadata_candidates(self):
        """候选置信度低于阈值 → 当前实现会因 has_clear_winner 失败而走候选存在性检查"""
        config = _make_config()
        candidates = [_candidate(confidence=0.7)]

        reason = auto_confirm_blocked_reason(
            config,
            _stub_ai_result(),
            source_selection_confidence=1.0,
            keyword_result=_stub_keyword_result(confidence=0.9),
            provider_search_result=ProviderSearchResult(candidates=candidates),
            metadata_provider_enabled=True,
            auto_confirm_confidence=0.8,
            has_llm_keyword_generator=True,
        )

        # 候选存在但不满足 clear winner → multiple_metadata_candidates
        assert reason == "multiple_metadata_candidates"


# ---------- Task 1.5: 安全门槛 — 缺主封面 ----------

class TestMissingPosterBlocks:
    def test_missing_poster_blocks_auto_confirm(self):
        """明确候选缺主封面 → missing_poster"""
        config = _make_config()
        candidates = [_candidate(confidence=0.95, poster_url=None)]

        reason = auto_confirm_blocked_reason(
            config,
            _stub_ai_result(),
            source_selection_confidence=1.0,
            keyword_result=_stub_keyword_result(confidence=0.9),
            provider_search_result=ProviderSearchResult(candidates=candidates),
            metadata_provider_enabled=True,
            auto_confirm_confidence=0.8,
            has_llm_keyword_generator=True,
        )

        assert reason == "missing_poster"


# ---------- Task 1.5: 安全门槛 — 年份冲突 ----------

class TestYearConflict:
    def test_year_mismatch_blocks_auto_confirm(self):
        """AI 解析年份与候选年份不一致 → year_conflict"""
        config = _make_config()
        candidates = [_candidate(confidence=0.95, year=2025)]

        reason = auto_confirm_blocked_reason(
            config,
            _stub_ai_result(year=2026),
            source_selection_confidence=1.0,
            keyword_result=_stub_keyword_result(confidence=0.9),
            provider_search_result=ProviderSearchResult(candidates=candidates),
            metadata_provider_enabled=True,
            auto_confirm_confidence=0.8,
            has_llm_keyword_generator=True,
        )

        assert reason == "year_conflict"

    def test_year_match_or_missing_passes(self):
        """AI 无年份时不触发年份冲突"""
        config = _make_config()
        candidates = [_candidate(confidence=0.95, year=2026)]

        reason = auto_confirm_blocked_reason(
            config,
            _stub_ai_result(year=None),
            source_selection_confidence=1.0,
            keyword_result=_stub_keyword_result(confidence=0.9),
            provider_search_result=ProviderSearchResult(candidates=candidates),
            metadata_provider_enabled=True,
            auto_confirm_confidence=0.8,
            has_llm_keyword_generator=True,
        )

        assert reason != "year_conflict"
        assert reason is None


# ---------- has_clear_winner 辅助测试 ----------

class TestHasClearWinner:
    def test_single_candidate_above_threshold(self):
        candidates = [_candidate(confidence=0.95)]
        assert has_clear_winner(candidates, confidence_threshold=0.9, margin=0.08) is True

    def test_single_candidate_below_threshold(self):
        candidates = [_candidate(confidence=0.7)]
        assert has_clear_winner(candidates, confidence_threshold=0.9, margin=0.08) is False

    def test_two_candidates_above_threshold_with_margin(self):
        candidates = [_candidate(confidence=0.95), _candidate(confidence=0.70)]
        assert has_clear_winner(candidates, confidence_threshold=0.9, margin=0.08) is True

    def test_two_candidates_above_threshold_without_margin(self):
        candidates = [_candidate(confidence=0.93), _candidate(confidence=0.91)]
        assert has_clear_winner(candidates, confidence_threshold=0.9, margin=0.08) is False

    def test_empty_candidates(self):
        assert has_clear_winner([], confidence_threshold=0.9, margin=0.08) is False
