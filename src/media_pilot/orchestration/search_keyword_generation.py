import re
from dataclasses import dataclass, field
from pathlib import Path

CANONICAL_QUALITY_TOKENS = {
    "480p": "480p",
    "720p": "720p",
    "1080p": "1080p",
    "2160p": "2160p",
    "4k": "4K",
    "web-dl": "WEB-DL",
    "webrip": "WEBRip",
    "bluray": "BluRay",
    "bdrip": "BDRip",
    "remux": "REMUX",
    "dts": "DTS",
}
NOISE_TOKENS = {
    "sample",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "group",
    "aac",
    "ac3",
}
# SxxExx / Season xx Episode xx 模式（不区分大小写）
SEASON_EPISODE_PATTERN = re.compile(
    r"[Ss](?:eason\s*)?(\d{1,4})\s*[Ee](?:pisode\s*)?(\d{1,4})",
)
# 单文件多集 S01E01E02 模式
MULTI_EPISODE_PATTERN = re.compile(
    r"[Ss](?:eason\s*)?(\d{1,4})\s*[Ee](?:pisode\s*)?(\d{1,4})[Ee](\d{1,4})",
)

YEAR_PATTERN = re.compile(r"^(?:19|20)\d{2}$")
BRACKET_PATTERN = re.compile(r"\[([^\]]+)\]")
# URL/域名/特殊分隔符等噪声模式
URL_NOISE_PATTERN = re.compile(
    r"www\d*\."              # www. / www2. 等
    r"|\.(com|org|net|cc|io|tv|me|co|xyz|info|biz|top|live|club)\b"  # 域名后缀
    r"|https?://"             # 协议
    r"|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"  # IP 地址
    r"|@\w+\."               # @domain. 模式（排除邮箱用户名）
    r"|^[a-fA-F0-9]{6,}$"   # 纯十六进制 hash
    r"|^\d+$"                # 纯数字
    r"|^\w$"                 # 单字符
    ,
    re.IGNORECASE,
)
GENERIC_PARENT_NAMES = {
    "workspace",
    "downloads",
    "download",
    "library",
    "movies",
    "shows",
}


@dataclass(frozen=True)
class SearchKeywordResult:
    keyword: str
    source: str
    confidence: float
    reason: str
    payload: dict = field(default_factory=dict)


def generate_search_keyword(input_path: Path) -> SearchKeywordResult:
    candidates = [_build_candidate(input_path.stem)]
    parent_name = input_path.parent.name.strip()
    if (
        parent_name
        and parent_name not in {".", input_path.stem}
        and parent_name.lower() not in GENERIC_PARENT_NAMES
    ):
        candidates.append(_build_candidate(parent_name))

    best = max(
        candidates,
        key=lambda candidate: (
            candidate["has_title_signals"],
            len(candidate["keyword_tokens"]),
            len(candidate["quality_tokens"]),
        ),
    )
    keyword = " ".join(best["keyword_tokens"]).strip()
    url_noise_hit = _detect_url_noise(input_path.stem)

    if not best["has_title_signals"] or url_noise_hit:
        fallback_tokens = best["quality_tokens"] or best["keyword_tokens"]
        return SearchKeywordResult(
            keyword=" ".join(fallback_tokens).strip(),
            source="rule",
            confidence=0.3,
            reason=(
                "insufficient_title_signals"
                if not best["has_title_signals"]
                else "url_noise_detected"
            ),
            payload={
                "quality_tokens": best["quality_tokens"],
                "tokens_removed": best["removed_tokens"],
            },
        )

    return SearchKeywordResult(
        keyword=keyword,
        source="rule",
        confidence=0.95,
        reason="filename_rule_cleanup",
        payload={
            "quality_tokens": best["quality_tokens"],
            "tokens_removed": best["removed_tokens"],
        },
    )


def _build_candidate(value: str) -> dict:
    keyword_tokens: list[str] = []
    quality_tokens: list[str] = []
    removed_tokens: list[str] = []

    for token in _tokenize(value):
        canonical_quality = _canonical_quality_token(token)
        if canonical_quality is not None:
            quality_tokens.append(canonical_quality)
            continue
        if _is_noise_token(token):
            removed_tokens.append(_normalize_removed_token(token))
            continue
        keyword_tokens.append(token)

    return {
        "keyword_tokens": keyword_tokens,
        "quality_tokens": quality_tokens,
        "removed_tokens": removed_tokens,
        "has_title_signals": _has_title_signals(keyword_tokens),
    }


def _tokenize(value: str) -> list[str]:
    removed_ads = [f"[{match.strip()}]" for match in BRACKET_PATTERN.findall(value)]
    normalized = BRACKET_PATTERN.sub(" ", value)
    segments = [segment for segment in re.split(r"[.\s_]+", normalized) if segment]
    tokens: list[str] = removed_ads
    for segment in segments:
        tokens.extend(_split_hyphenated_segment(segment))
    return tokens


def _split_hyphenated_segment(segment: str) -> list[str]:
    if "-" not in segment:
        return [segment]

    pieces = [piece for piece in segment.split("-") if piece]
    tokens: list[str] = []
    index = 0
    while index < len(pieces):
        matched = False
        for size in (3, 2):
            if index + size > len(pieces):
                continue
            joined = "-".join(pieces[index : index + size])
            if _canonical_quality_token(joined) is not None:
                tokens.append(joined)
                index += size
                matched = True
                break
        if matched:
            continue
        tokens.append(pieces[index])
        index += 1
    return tokens


def _canonical_quality_token(token: str) -> str | None:
    normalized = token.strip().lower()
    return CANONICAL_QUALITY_TOKENS.get(normalized)


def _is_noise_token(token: str) -> bool:
    normalized = token.strip().lower()
    return bool(BRACKET_PATTERN.fullmatch(token)) or normalized in NOISE_TOKENS


def _normalize_removed_token(token: str) -> str:
    return token.strip().strip("[]")


def _detect_url_noise(stem: str) -> bool:
    """检测原始文件名是否含 URL/IP/hash/纯数字等噪声模式"""
    return bool(URL_NOISE_PATTERN.search(stem))


def _has_title_signals(tokens: list[str]) -> bool:
    non_year_tokens = [token for token in tokens if not YEAR_PATTERN.match(token)]
    if len(non_year_tokens) >= 2:
        return True
    if len(non_year_tokens) != 1:
        return False

    token = non_year_tokens[0]
    if len(token) >= 4:
        return True
    return any("\u4e00" <= character <= "\u9fff" for character in token)


# \u2500\u2500 \u5267\u96c6\u7ed3\u6784\u68c0\u6d4b\u4e0e\u96c6\u6587\u4ef6\u6620\u5c04 \u2500\u2500


@dataclass(frozen=True)
class EpisodeMappingEntry:
    """\u5355\u4e2a\u6587\u4ef6\u7684 season/episode \u6620\u5c04"""
    file_path: str
    season: int
    episode: int
    source: str  # "filename" | "parent_dir"


@dataclass
class EpisodeMappingResult:
    """\u96c6\u6587\u4ef6\u6620\u5c04\u7ed3\u679c"""
    entries: list[EpisodeMappingEntry] = field(default_factory=list)
    is_auto_publishable: bool = False
    block_reason: str | None = None
    detected_show_title: str | None = None


def detect_show_structure(path: Path) -> EpisodeMappingResult | None:
    """\u68c0\u6d4b\u8def\u5f84\u4e2d\u7684\u5267\u96c6\u7ed3\u6784\u8bc1\u636e\u3002

    \u4ece\u6587\u4ef6\u540d\u548c\u7236\u76ee\u5f55\u540d\u4e2d\u641c\u7d22 SxxExx \u6a21\u5f0f\u3002
    \u8fd4\u56de None \u8868\u793a\u6ca1\u6709\u53d1\u73b0\u4efb\u4f55\u5267\u96c6\u7ed3\u6784\u8bc1\u636e\u3002
    """
    texts = [path.stem]
    parent = path.parent.name.strip()
    if parent and parent.lower() not in GENERIC_PARENT_NAMES:
        texts.append(parent)

    entries: list[EpisodeMappingEntry] = []
    seen_sources: set[str] = set()

    for text in texts:
        source = "filename" if text == path.stem else "parent_dir"
        if source in seen_sources:
            continue
        seen_sources.add(source)

        for match in SEASON_EPISODE_PATTERN.finditer(text):
            season = int(match.group(1))
            episode = int(match.group(2))
            entries.append(EpisodeMappingEntry(
                file_path=str(path),
                season=season,
                episode=episode,
                source=source,
            ))

    if not entries:
        return None

    # \u68c0\u67e5\u662f\u5426\u4e3a\u6392\u9664\u6a21\u5f0f
    result = EpisodeMappingResult(entries=entries)

    # Season 0 = specials
    if any(e.season == 0 for e in entries):
        result.block_reason = "specials_season_0_not_supported"
        return result

    # \u5355\u6587\u4ef6\u591a\u96c6\uff08\u5982 S01E01E02\uff09
    for text in texts:
        if MULTI_EPISODE_PATTERN.search(text):
            result.block_reason = "multi_episode_in_single_file_not_supported"
            return result

    # \u5355\u6587\u4ef6\u5355\u96c6 \u2192 \u53ef\u76f4\u63a5\u81ea\u52a8\u53d1\u5e03
    if len(entries) == 1:
        result.is_auto_publishable = True
        result.detected_show_title = _extract_show_title(path.stem)
        return result

    # \u591a\u6761\u76ee \u2192 \u68c0\u67e5\u662f\u5426\u4e3a\u540c\u5b63\u8fde\u7eed\u591a\u96c6
    seasons = {e.season for e in entries}
    if len(seasons) != 1:
        result.block_reason = "cross_season_not_supported"
        return result

    episodes = sorted(e.episode for e in entries)
    if episodes != list(range(episodes[0], episodes[-1] + 1)):
        result.block_reason = "sparse_episodes_not_supported"
        return result

    result.is_auto_publishable = True
    result.detected_show_title = _extract_show_title(path.stem)
    return result


def _extract_show_title(stem: str) -> str | None:
    """\u4ece\u6587\u4ef6\u540d\u4e2d\u63d0\u53d6\u53ef\u80fd\u7684\u5267\u96c6\u6807\u9898\uff08\u53bb\u9664 SxxExx \u53ca\u540e\u7eed\u5185\u5bb9\uff09"""
    cleaned = SEASON_EPISODE_PATTERN.split(stem, maxsplit=1)[0].strip()
    cleaned = re.split(r"[.\s_]+", cleaned)
    # \u53bb\u9664\u5e74\u4efd token
    cleaned = [t for t in cleaned if not YEAR_PATTERN.match(t)]
    if not cleaned:
        return None
    return " ".join(cleaned)
