"""资源候选相关性评分与排序"""

from __future__ import annotations

import re
from datetime import datetime

from media_pilot.resource_discovery.types import ResourceCandidate, ResourceIntent

# ── 质量/编码词忽略集：这些词不参与关键词匹配 ──

_IGNORE_KEYWORDS: set[str] = {
    # 分辨率
    "1080p", "2160p", "720p", "480p", "4k", "8k",
    # 编码
    "x264", "x265", "h264", "h265", "hevc", "av1",
    # 片源
    "bluray", "blu-ray", "web-dl", "webrip", "web", "remux",
    "bdrip", "brrip", "dvdrip", "hdtv",
    # 音频
    "aac", "ac3", "dts", "flac", "truehd", "atmos", "ddp", "dd+",
    # HDR
    "hdr", "hdr10", "hdr10+", "dolby", "vision", "sdr",
    # 位深
    "10bit", "8bit",
}

# ── 弱证据词模式：单独数字或非常短的通用英文词 ──

_ISOLATED_NUMBER_RE = re.compile(r"^\d{1,2}$")  # 1-2 位孤立数字
_SHORT_GENERIC_RE = re.compile(
    r"^(the|a|an|and|or|of|in|on|to|for|is|it|at|by|no|go)$"
)


def _is_weak_evidence_word(word: str) -> bool:
    """判断单个词是否属于弱证据（不应单独作为匹配依据）。"""
    w = word.lower()
    return bool(
        _ISOLATED_NUMBER_RE.match(w)
        or _SHORT_GENERIC_RE.match(w)
    )


class ResourceCandidateRanker:
    """根据 LLM 意图对 Prowlarr 候选评分并排序。

    评分维度：标题命中、关键词命中、年份匹配、番号匹配。
    排序：可下载优先 → relevance_level → relevance_score → seeders → publish_date。
    """

    def __init__(self, intent: ResourceIntent) -> None:
        self._intent = intent

    def rank(self, candidates: list[ResourceCandidate]) -> list[ResourceCandidate]:
        scored = [self._score(c) for c in candidates]
        return sorted(scored, key=self._sort_key)

    # ── 评分 ──

    @staticmethod
    def _filter_keywords(keywords: list[str]) -> list[str]:
        """过滤掉质量/编码词，返回可用于匹配的关键词列表。"""
        result: list[str] = []
        for kw in keywords:
            words = kw.split()
            meaningful = [w for w in words if w.lower() not in _IGNORE_KEYWORDS]
            if meaningful:
                result.append(" ".join(meaningful))
        return result

    def _score(self, c: ResourceCandidate) -> ResourceCandidate:
        title_lower = c.title.lower()
        reasons: list[str] = []
        score = 0.0

        # 1) 片名命中（title_candidates）：完整短语匹配，权重最高
        for tc in self._intent.title_candidates:
            tc_lower = tc.lower()
            if tc_lower in title_lower:
                score += 0.5
                reasons.append(f"匹配片名「{tc}」")
                break  # 只加一次
        else:
            # 1b) preferred_title_candidates 完整短语匹配
            for ptc in self._intent.preferred_title_candidates:
                ptc_lower = ptc.lower()
                if ptc_lower in title_lower:
                    score += 0.5
                    reasons.append(f"匹配优先片名「{ptc}」")
                    break

        # 2) 关键词命中（过滤质量词后）
        #    注意：需要区分「短语级命中」和「词级命中」，
        #    词级命中只接受有意义的词（排除孤立数字/短通用词）
        if not reasons:
            filtered_kw = self._filter_keywords(self._intent.resource_keywords)
            for kw in filtered_kw:
                kw_lower = kw.lower()
                # 2a) 完整短语命中 → 高权重
                if kw_lower in title_lower:
                    score += 0.4
                    reasons.append(f"匹配关键词「{kw}」")
                    break
                # 2b) 词级命中 → 低权重，过滤弱证据词
                words = kw_lower.split()
                strong_words = [w for w in words if not _is_weak_evidence_word(w)]
                if strong_words and any(w in title_lower for w in strong_words):
                    score += 0.25
                    matched = [w for w in strong_words if w in title_lower]
                    reasons.append(
                        f"匹配关键词片段「{', '.join(matched[:2])}」"
                    )
                    break
                # 2c) 如果只有弱证据词命中 → 极小权重（避免噪声前排）
                weak_matches = [
                    w for w in words
                    if _is_weak_evidence_word(w) and w in title_lower
                ]
                if weak_matches:
                    score += 0.08
                    reasons.append("弱证据词命中")

        # 3) 英文/原名命中（从关键词中提取纯英文词）
        if not reasons:  # 还没命中任何东西
            for kw in self._intent.resource_keywords:
                en_words = [w for w in kw.split() if re.match(r"^[a-zA-Z0-9]+$", w)]
                for ew in en_words:
                    ew_lower = ew.lower()
                    # 跳过质量词和弱证据词
                    if ew_lower in _IGNORE_KEYWORDS or _is_weak_evidence_word(ew):
                        continue
                    if len(ew) >= 3 and ew_lower in title_lower:
                        score += 0.3
                        reasons.append(f"匹配英文名「{ew}」")
                        break
                if reasons:
                    break

        # 4) 年份命中
        year_pattern = re.compile(r"\b(?:19|20)\d{2}\b")
        intent_years = set()
        for kw in self._intent.resource_keywords:
            intent_years.update(year_pattern.findall(kw))
        candidate_years = set(year_pattern.findall(c.title))
        if intent_years and candidate_years:
            if intent_years & candidate_years:
                score += 0.15
                reasons.append(f"年份匹配 {', '.join(intent_years)}")
            else:
                # 不同年份 → 负向信号
                score -= 0.1

        # 5) 成人番号匹配
        if self._intent.search_type == "adult":
            code_score, code_reason = self._match_adult_code(title_lower)
            if code_score > 0:
                score += code_score
                reasons.append(code_reason)

        # 6) 负向信号：完全无关
        if not reasons:
            score = max(score, 0.0)

        # 7) 结构化质量偏好加权（基于 release_tags 匹配 preferred_* 字段）
        quality_bonus = _quality_preference_match(self._intent, c.release_tags)
        if quality_bonus > 0:
            score += quality_bonus

        # 8) 确定 relevance_level
        if score >= 0.5:
            level = "high"
        elif score >= 0.2:
            level = "medium"
        else:
            level = "low"

        return ResourceCandidate(
            title=c.title,
            indexer=c.indexer,
            source=c.source,
            download_url=c.download_url,
            magnet_url=c.magnet_url,
            size_bytes=c.size_bytes,
            seeders=c.seeders,
            leechers=c.leechers,
            publish_date=c.publish_date,
            download_count=c.download_count,
            category=c.category,
            relevance_score=round(score, 3),
            relevance_level=level,
            match_reasons=reasons,
            release_tags=c.release_tags,
        )

    # ── 番号匹配 ──

    _ADULT_CODE_RE = re.compile(
        r"\b([a-zA-Z]{2,6})[-\s]?(\d{2,5})\b", re.IGNORECASE
    )

    def _match_adult_code(self, title_lower: str) -> tuple[float, str]:
        """匹配成人番号，支持缺横杠/大小写差异。"""
        # 从意图中提取番号
        for kw in self._intent.resource_keywords:
            m = self._ADULT_CODE_RE.search(kw)
            if m:
                prefix, num = m.group(1).upper(), m.group(2)
                # 候选标题中查找同一番号（容错缺横杠）
                pattern = re.compile(
                    re.escape(prefix) + r"[-\s]?" + re.escape(num),
                    re.IGNORECASE,
                )
                if pattern.search(title_lower):
                    return 0.6, f"番号匹配 {prefix}-{num}"
        return 0.0, ""

    # ── 排序 ──

    @staticmethod
    def _sort_key(c: ResourceCandidate) -> tuple:
        # 1) downloadable 优先
        dl = 0 if (c.download_url or c.magnet_url) else 1
        # 2) relevance_level 序: high(0) < medium(1) < low(2)
        level_rank = {"high": 0, "medium": 1, "low": 2}
        level = level_rank.get(c.relevance_level, 2)
        # 3) relevance_score 降序
        neg_score = -c.relevance_score
        # 4) seeders 降序
        neg_seed = -c.seeders
        # 5) publish_date 新到旧（None 排最后）
        if c.publish_date:
            try:
                dt = datetime.fromisoformat(
                    c.publish_date.replace("Z", "+00:00")
                )
                neg_ts = -dt.timestamp()
            except (ValueError, TypeError):
                neg_ts = float("inf")
        else:
            neg_ts = float("inf")
        return (dl, level, neg_score, neg_seed, neg_ts)


# ── 质量偏好匹配 ──

def _quality_preference_match(intent: ResourceIntent, release_tags: dict | None) -> float:
    """根据结构化质量偏好对 release_tags 加权。

    每组偏好匹配得分上限 0.05，总分上限 0.15（轻度加权）。
    """
    if not release_tags or not isinstance(release_tags, dict):
        return 0.0

    bonus = 0.0

    # 分辨率: 完全匹配
    preferred = intent.preferred_resolutions
    if preferred:
        candidate_tags = release_tags.get("resolutions", [])
        if candidate_tags and any(r in preferred for r in candidate_tags):
            bonus += 0.05

    # 片源
    preferred = intent.preferred_sources
    if preferred:
        candidate_tags = release_tags.get("sources", [])
        if candidate_tags and any(s in preferred for s in candidate_tags):
            bonus += 0.05

    # 编码
    preferred = intent.preferred_video_codecs
    if preferred:
        candidate_tags = release_tags.get("codecs", [])
        if candidate_tags and any(c in preferred for c in candidate_tags):
            bonus += 0.05

    # HDR
    preferred = intent.preferred_hdr_tags
    if preferred:
        candidate_tags = release_tags.get("hdr_tags", [])
        if candidate_tags and any(h in preferred for h in candidate_tags):
            bonus += 0.05

    # 音频
    preferred = intent.preferred_audio_tags
    if preferred:
        candidate_tags = release_tags.get("audio_tags", [])
        if candidate_tags and any(a in preferred for a in candidate_tags):
            bonus += 0.05

    # 单维度加分上限约束，总分 ≤ 0.15
    return min(bonus, 0.15)
