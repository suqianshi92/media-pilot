"""资源发布标签解析 — 从规范化标题中抽取分辨率、片源、编码、HDR、音频标签"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── 标签组定义 ──

_RESOLUTION_PATTERNS: list[tuple[str, str]] = [
    (r"\b2160p\b", "2160p"),
    (r"\b1080p\b", "1080p"),
    (r"\b1080i\b", "1080i"),
    (r"\b720p\b", "720p"),
    (r"\b480p\b", "480p"),
    (r"\b4K\b", "4K"),
    (r"\b8K\b", "8K"),
]

_SOURCE_PATTERNS: list[tuple[str, str]] = [
    (r"\bREMUX\b", "REMUX"),
    (r"\bBluRay\b", "BluRay"),
    (r"\bBlu-ray\b", "BluRay"),
    (r"\bWEB-DL\b", "WEB-DL"),
    (r"\bWEBRip\b", "WEBRip"),
    (r"\bHDTV\b", "HDTV"),
    (r"\bBDRip\b", "BDRip"),
    (r"\bBRRip\b", "BRRip"),
    (r"\bDVD\b", "DVD"),
    (r"\bDVDRip\b", "DVDRip"),
]

_CODEC_PATTERNS: list[tuple[str, str]] = [
    (r"\bHEVC\b", "HEVC"),
    (r"\bx265\b", "HEVC"),
    (r"\bx\.?265\b", "HEVC"),
    (r"\bAVC\b", "AVC"),
    (r"\bx264\b", "AVC"),
    (r"\bx\.?264\b", "AVC"),
    (r"\bAV1\b", "AV1"),
    (r"\bH\.?265\b", "HEVC"),
    (r"\bH\.?264\b", "AVC"),
]

_HDR_PATTERNS: list[tuple[str, str]] = [
    (r"\bHDR10\+\b", "HDR10+"),
    (r"\bHDR10\b", "HDR10"),
    (r"\bDolby\s*Vision\b", "Dolby Vision"),
    (r"\bDV\b", "Dolby Vision"),
    (r"\bHLG\b", "HLG"),
    (r"\bHDR\b", "HDR"),
]

_AUDIO_PATTERNS: list[tuple[str, str]] = [
    (r"\bAtmos\b", "Atmos"),
    (r"\bTrueHD\b", "TrueHD"),
    (r"\bDTS-HD\b", "DTS-HD"),
    (r"\bDTS\b", "DTS"),
    (r"\bDDP5\.1\b", "DDP5.1"),
    (r"\bDD\+?\b", "DD+"),
    (r"\bFLAC\b", "FLAC"),
    (r"\bAAC\b", "AAC"),
    (r"\bAC3\b", "AC3"),
    (r"\bDDP\b", "DDP"),
    (r"\bEAC3\b", "E-AC3"),
    (r"\bLPCM\b", "LPCM"),
    (r"\bOpus\b", "Opus"),
]

# ── 标签优先级排序（用于卡片摘要前 5 标签） ──

_TAG_DISPLAY_ORDER: dict[str, int] = {
    # 分辨率
    "2160p": 10, "4K": 11, "8K": 9, "1080p": 20, "1080i": 21,
    "720p": 30, "480p": 40,
    # 片源
    "REMUX": 50, "BluRay": 51, "WEB-DL": 52, "WEBRip": 53,
    "HDTV": 54, "BDRip": 55, "BRRip": 56, "DVD": 57, "DVDRip": 58,
    # 编码
    "HEVC": 60, "AV1": 61, "AVC": 62,
    # HDR
    "Dolby Vision": 70, "HDR10+": 71, "HDR10": 72, "HDR": 73, "HLG": 74,
    # 音频
    "Atmos": 80, "TrueHD": 81, "DTS-HD": 82, "DTS": 83,
    "DDP5.1": 84, "DD+": 85, "DDP": 86, "FLAC": 87,
    "E-AC3": 88, "AAC": 89, "AC3": 90, "LPCM": 91, "Opus": 92,
}


@dataclass(frozen=True, kw_only=True)
class ReleaseTags:
    """从标题解析出的结构化资源发布标签"""

    resolutions: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    codecs: list[str] = field(default_factory=list)
    hdr_tags: list[str] = field(default_factory=list)
    audio_tags: list[str] = field(default_factory=list)

    def display_tags(self, max_tags: int = 5) -> list[str]:
        """返回用于卡片摘要的标签列表（最多 max_tags 个，按优先级排序）"""
        all_tags: list[str] = []
        for tag_list in (
            self.resolutions,
            self.sources,
            self.codecs,
            self.hdr_tags,
            self.audio_tags,
        ):
            all_tags.extend(tag_list)

        # 去重并按显示优先级排序
        seen: set[str] = set()
        ordered: list[str] = []
        for tag in sorted(all_tags, key=lambda t: _TAG_DISPLAY_ORDER.get(t, 999)):
            if tag not in seen:
                seen.add(tag)
                ordered.append(tag)

        return ordered[:max_tags]


def parse_release_tags(title: str) -> ReleaseTags:
    """从资源标题解析资源发布标签"""
    upper = title.upper()

    def _match(patterns: list[tuple[str, str]]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for pattern, label in patterns:
            if re.search(pattern, upper, re.IGNORECASE):
                if label not in seen:
                    seen.add(label)
                    result.append(label)
        return result

    return ReleaseTags(
        resolutions=_match(_RESOLUTION_PATTERNS),
        sources=_match(_SOURCE_PATTERNS),
        codecs=_match(_CODEC_PATTERNS),
        hdr_tags=_match(_HDR_PATTERNS),
        audio_tags=_match(_AUDIO_PATTERNS),
    )
