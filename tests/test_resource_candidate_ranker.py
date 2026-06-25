"""ResourceCandidateRanker 单元测试"""

from __future__ import annotations

from media_pilot.resource_discovery.types import ResourceCandidate, ResourceIntent

# ── helpers ──

def _make_intent(**kwargs) -> ResourceIntent:
    defaults = {
        "query_text": "test",
        "search_type": "movie",
        "title_candidates": [],
        "resource_keywords": [],
        "quality_hint": "",
        "profile_hint": "unknown",
        "reason": "",
    }
    defaults.update(kwargs)
    return ResourceIntent(**defaults)


def _make_candidate(title: str, **kwargs) -> ResourceCandidate:
    defaults: dict = {
        "title": title,
        "indexer": "test",
        "source": "prowlarr",
        "download_url": "https://example.com/t.torrent",
        "seeders": 10,
    }
    defaults.update(kwargs)
    return ResourceCandidate(**defaults)


# ── tests ──


class TestResourceCandidateRanker:

    def test_title_exact_match_high_relevance(self):
        """片名精确命中 → high"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            title_candidates=["天气之子"],
            resource_keywords=["天气之子 1080p"],
        )
        candidates = [
            _make_candidate("[TGx] 天气之子 2019 1080p", seeders=10),
            _make_candidate("Some Other Movie 2019", seeders=100),
        ]
        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank(candidates)

        assert ranked[0].relevance_level == "high"
        assert ranked[1].relevance_level == "low"
        # 明确匹配的低 seed 候选排在高 seed 无关候选前
        assert ranked[0].title == "[TGx] 天气之子 2019 1080p"

    def test_english_title_match(self):
        """英文片名命中 → high"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            title_candidates=["Weathering With You"],
            resource_keywords=["Weathering With You 1080p"],
        )
        candidates = [
            _make_candidate("Some Other Movie", seeders=100),
            _make_candidate("Weathering With You 2019", seeders=5),
        ]
        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank(candidates)

        assert ranked[0].relevance_level == "high"
        assert ranked[0].title == "Weathering With You 2019"

    def test_keyword_partial_match_medium(self):
        """关键词部分命中 → medium"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            title_candidates=["星际穿越"],
            resource_keywords=["Interstellar 2014"],
        )
        candidates = [
            _make_candidate("Interstellar 2014 1080p x264", seeders=20),
        ]
        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank(candidates)

        assert ranked[0].relevance_level == "high"  # 英文关键词命中
        assert len(ranked[0].match_reasons) >= 1

    def test_no_match_gets_low(self):
        """无命中 → low"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            title_candidates=["不存在的电影"],
            resource_keywords=["不存在的电影"],
        )
        candidates = [
            _make_candidate("Completely Unrelated Movie", seeders=100),
        ]
        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank(candidates)

        assert ranked[0].relevance_level == "low"
        assert ranked[0].relevance_score < 0.3

    def test_year_match_bonus(self):
        """年份命中加分"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            title_candidates=["天气之子"],
            resource_keywords=["天气之子 2019"],
        )
        c1 = _make_candidate("天气之子 2019 1080p", seeders=10)
        c2 = _make_candidate("天气之子 2020 1080p", seeders=50)
        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank([c1, c2])

        assert ranked[0].title == "天气之子 2019 1080p"
        assert any("年份" in r for r in ranked[0].match_reasons)

    def test_sort_order_downloadable_first_then_relevance(self):
        """排序：可下载优先 → 相关性 → seeders"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            title_candidates=["天气之子"],
            resource_keywords=["天气之子"],
        )
        candidates = [
            _make_candidate("天气之子 1080p", seeders=1, download_url=None, magnet_url=None),
            _make_candidate("无关电影", seeders=200, download_url="https://x.com/t.torrent"),
            _make_candidate("天气之子 720p", seeders=5, download_url="https://x.com/t2.torrent"),
        ]
        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank(candidates)

        # 排序：可下载优先 → 第一个是可下载+高相关
        assert ranked[0].title == "天气之子 720p"
        # 第二个：可下载但低相关（downloadable 优先级高于 relevance）
        assert ranked[1].title == "无关电影"
        # 第三个：不可下载但高相关
        assert ranked[2].title == "天气之子 1080p"

    def test_adult_code_match_high(self):
        """成人搜索番号命中 → high"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            search_type="adult",
            title_candidates=["NACT-120"],
            resource_keywords=["NACT-120"],
            profile_hint="tpdb_adult_movie",
        )
        candidates = [
            _make_candidate("Totally Unrelated", seeders=200),
            _make_candidate("[JAV] NACT-120 1080p", seeders=5),
        ]
        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank(candidates)

        assert ranked[0].relevance_level == "high"
        assert ranked[0].title == "[JAV] NACT-120 1080p"

    def test_adult_code_fuzzy_match(self):
        """成人搜索缺横杠/大小写番号容错"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            search_type="adult",
            title_candidates=["ABCD123"],
            resource_keywords=["ABCD123"],
            profile_hint="tpdb_adult_movie",
        )
        candidates = [
            _make_candidate("[JAV] ABCD-123 1080p", seeders=10),
        ]
        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank(candidates)

        assert ranked[0].relevance_level == "high"
        assert any("番号" in r for r in ranked[0].match_reasons)

    def test_date_sort_newest_first(self):
        """同等级同分数同 seeders 时，新发布排在旧发布前"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            title_candidates=["天气之子"],
            resource_keywords=["天气之子"],
        )
        candidates = [
            _make_candidate(
                "天气之子 旧版本",
                seeders=10,
                publish_date="2020-05-01T00:00:00Z",
            ),
            _make_candidate(
                "天气之子 新版本",
                seeders=10,
                publish_date="2026-04-09T15:21:01Z",
            ),
        ]
        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank(candidates)

        # 新日期排在前面
        got = [c.title for c in ranked]
        assert ranked[0].title == "天气之子 新版本", f"expected 新版本 first, got {got}"
        assert ranked[1].title == "天气之子 旧版本"

    def test_quality_words_not_match_alone(self):
        """质量/编码词（1080p/x265 等）不应单独触发相关性匹配"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            title_candidates=["天气之子"],
            resource_keywords=["天气之子 1080p x265"],
        )
        candidates = [
            _make_candidate("Unrelated Movie 1080p x265", seeders=100),
        ]
        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank(candidates)

        # 质量词本身不应导致 medium/high
        assert ranked[0].relevance_level == "low", f"expected low, got {ranked[0].relevance_level}"
        assert ranked[0].relevance_score < 0.2


    # ── 抗干扰测试 (Phase: ranker anti-noise) ──

    def test_isolated_number_not_ranked_high(self):
        """孤立数字匹配不应进入前排：'Not My Grandpa! 8' vs 速度与激情8"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            title_candidates=["速度与激情8"],
            preferred_title_candidates=["速度与激情8", "The Fate of the Furious"],
            resource_keywords=["速度与激情8 1080p", "Fast and Furious 8"],
        )
        # 噪声候选：只匹配孤立数字 8
        noise = _make_candidate("Not My Grandpa! 8 1080p BluRay", seeders=200)
        # 正确候选：标题含有关键词
        good = _make_candidate("The Fate of the Furious 2017 1080p", seeders=100)

        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank([noise, good])

        # 正确候选应该排在噪声之前
        assert ranked[0].title == good.title, (
            f"expected good first, got {ranked[0].title}"
        )
        # 噪声候选只靠弱证据词，得分应很低
        assert ranked[1].relevance_score < 0.2, (
            f"noise score too high: {ranked[1].relevance_score}"
        )

    def test_full_phrase_match_beats_partial_words(self):
        """完整短语匹配权重高于碎片词匹配"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            title_candidates=["The Fate of the Furious"],
            preferred_title_candidates=["The Fate of the Furious"],
            resource_keywords=["The Fate of the Furious 1080p"],
        )
        # 完整短语匹配
        full_match = _make_candidate("The Fate of the Furious 2017 1080p", seeders=100)
        # 只有部分词匹配
        partial = _make_candidate("Fate Another Movie 2022 1080p", seeders=200)

        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank([partial, full_match])

        # 完整匹配排前（即使 seeders 少）
        assert ranked[0].title == full_match.title, (
            f"expected full match first, got {ranked[0].title}"
        )
        # 完整匹配得分应明显更高
        assert ranked[0].relevance_score > ranked[1].relevance_score, (
            f"full={ranked[0].relevance_score} not > partial={ranked[1].relevance_score}"
        )

    def test_generic_short_words_not_bait(self):
        """短通用词（the/a/an/and 等）不应被当作有效匹配"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            title_candidates=["The Matrix"],
            resource_keywords=["The Matrix 1080p"],
        )
        # 只命中通用词 "the"
        noise = _make_candidate("The Great Wall 1080p", seeders=300)
        # 实际命中完整片名
        good = _make_candidate("The Matrix 1999 1080p", seeders=50)

        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank([noise, good])

        # 正确结果应排前
        assert ranked[0].title == good.title, (
            f"expected 'The Matrix' first, got {ranked[0].title}"
        )

    def test_short_number_in_keyword_not_bait(self):
        """关键词中的短数字（如 8）不应单独导致高分"""
        from media_pilot.resource_discovery.ranker import ResourceCandidateRanker

        intent = _make_intent(
            title_candidates=["速度与激情8"],
            resource_keywords=["速度与激情8", "Fast Furious 8"],
        )
        # 候选只匹配孤立数字 8
        isolated = _make_candidate("Movie 8 Collection 1080p", seeders=500)
        # 匹配中文片名
        good = _make_candidate("速度与激情8 2017 1080p", seeders=10)

        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank([isolated, good])

        # 即使 seeders 相差悬殊，正确匹配仍应排前
        assert ranked[0].title == good.title, (
            f"expected good first, got {ranked[0].title}"
        )

    # ── 结构化质量偏好加权 ──

    def test_quality_preference_matching_boost(self):
        """结构化质量偏好匹配 release_tags → 加权排序"""
        from media_pilot.resource_discovery.ranker import (
            ResourceCandidateRanker,
        )

        intent = _make_intent(
            title_candidates=["天气之子"],
            preferred_resolutions=["2160p", "1080p"],
            preferred_sources=["REMUX", "BluRay"],
            preferred_hdr_tags=["HDR10"],
        )

        # 匹配偏好的候选
        good = _make_candidate(
            "天气之子 2160p REMUX HEVC HDR10",
            seeders=10,
            release_tags={
                "resolutions": ["2160p"],
                "sources": ["REMUX"],
                "codecs": ["HEVC"],
                "hdr_tags": ["HDR10"],
                "audio_tags": [],
            },
        )
        # 不匹配偏好的候选
        meh = _make_candidate(
            "天气之子 720p WEBRip x264",
            seeders=100,
            release_tags={
                "resolutions": ["720p"],
                "sources": ["WEBRip"],
                "codecs": ["AVC"],
                "hdr_tags": [],
                "audio_tags": [],
            },
        )

        ranker = ResourceCandidateRanker(intent)
        ranked = ranker.rank([meh, good])

        # 匹配偏好的候选应排前（即使 seeders 少）
        assert ranked[0].title == good.title, (
            f"expected quality match first, got {ranked[0].title}"
        )

    def test_quality_preference_no_tags_returns_zero(self):
        """无 release_tags 时质量偏好加权为 0"""
        from media_pilot.resource_discovery.ranker import _quality_preference_match

        intent = _make_intent(
            preferred_resolutions=["2160p"],
        )
        assert _quality_preference_match(intent, None) == 0.0
        assert _quality_preference_match(intent, {}) == 0.0
        assert _quality_preference_match(intent, "not-a-dict") == 0.0
