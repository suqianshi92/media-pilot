"""剧集结构分析与集文件映射服务.

下载完成后的入库任务 Agent 主线入口: 扫描任务输入节点, 把
单集 / 同季连续多集识别为可自动发布的剧集结构, 生成稳定
``EpisodeMapping`` 任务事实. 跨季 / 稀疏集 / Season 0 specials /
单文件多集 / 无法解析 season/episode 的输入不自动发布, 返回
明确 ``block_reason`` 让 Agent 解释或进入失败类状态.

边界:
- 只基于文件名 / 扩展名 / 目录名 / 任务输入事实, 不引入外部视频
  时长 / 分辨率 / 编解码探测.
- 复用 ``search_keyword_generation.detect_show_structure`` 的
  SxxExx 解析, 不重复实现.
- 复用 ``EpisodeMapping`` ORM 持久化, 不创建新模型.
- 决策选项 payload 由后端基于任务事实生成, 不让 LLM 拼接
  season/episode 或文件路径.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from media_pilot.orchestration.search_keyword_generation import (
    EpisodeMappingEntry,
    EpisodeMappingResult,
    detect_show_structure,
)
from media_pilot.services.task_input_analysis import (
    FileInfo,
    analyze_task_input,
)


# 状态枚举 — 描述任务输入的剧集结构识别结果.
STATUS_NO_VIDEO_FILES = "no_video_files"
STATUS_NOT_SHOW_STRUCTURE = "not_show_structure"
STATUS_UNSUPPORTED_CROSS_SEASON = "unsupported_cross_season"
STATUS_UNSUPPORTED_SPARSE_EPISODES = "unsupported_sparse_episodes"
STATUS_UNSUPPORTED_SEASON_0_SPECIALS = "unsupported_season_0_specials"
STATUS_UNSUPPORTED_MULTI_EPISODE_IN_SINGLE_FILE = "unsupported_multi_episode_in_single_file"
STATUS_ABSOLUTE_EPISODE_NEEDS_METADATA = "absolute_episode_requires_metadata_detail"
STATUS_ABSOLUTE_EPISODE_OUT_OF_RANGE = "absolute_episode_out_of_provider_range"
STATUS_AUTO_PUBLISHABLE = "auto_publishable"

# block_reason 与 STATUS 一一对应, 但保持短字符串, 便于任务事实 / 时间线摘要
# 与决策 payload 复用. Agent / 前端都用同一字符串.
BLOCK_REASON_CROSS_SEASON = "cross_season_not_supported"
BLOCK_REASON_SPARSE_EPISODES = "sparse_episodes_not_supported"
BLOCK_REASON_SEASON_0_SPECIALS = "specials_season_0_not_supported"
BLOCK_REASON_MULTI_EPISODE_IN_SINGLE_FILE = "multi_episode_in_single_file_not_supported"
BLOCK_REASON_NO_VIDEO_FILES = "no_video_files_found"
BLOCK_REASON_NOT_SHOW_STRUCTURE = "no_clear_show_structure"
BLOCK_REASON_ABSOLUTE_EPISODE_NEEDS_METADATA = (
    "absolute_episode_requires_metadata_detail"
)
BLOCK_REASON_ABSOLUTE_EPISODE_OUT_OF_RANGE = (
    "absolute_episode_out_of_provider_range"
)
BLOCK_REASON_ABSOLUTE_EPISODE_SPARSE = "absolute_episode_sparse_not_supported"
BLOCK_REASON_ABSOLUTE_EPISODE_AMBIGUOUS = "absolute_episode_ambiguous_not_supported"

# 映射模式标签 — 标识映射来源, 供任务工作台展示和测试断言.
MAPPING_MODE_SXXEXX = "sxxexx"
MAPPING_MODE_ABSOLUTE = "absolute"


# 绝对集数解析: 保守支持的高置信模式.
# 拒绝 EP51 / E51 / [51-52] / 多个独立 pure 数字 等歧义模式.
# 优先级: 标准 SxxExx 永远 > 绝对集数. 文件名已含 SxxExx 时, 不走绝对集数解析.
_ABSOLUTE_BRACKET_PATTERN = re.compile(r"\[(\d{1,4})\]")
_ABSOLUTE_DASH_SUFFIX_PATTERN = re.compile(
    r"(?:^|[^A-Za-z0-9])-\s*(\d{1,4})(?:\s|$|\.)"
)
_ABSOLUTE_UNDERSCORE_PATTERN = re.compile(r"_(\d{1,4})(?:\.\w+)?$")
_ABSOLUTE_DOT_SUFFIX_PATTERN = re.compile(r"\.(\d{1,4})(?:\.\w+)?$")
_ABSOLUTE_CHINESE_HUA_PATTERN = re.compile(r"第\s*(\d{1,4})\s*话")
_ABSOLUTE_CHINESE_JI_PATTERN = re.compile(r"第\s*(\d{1,4})\s*集")


def _is_season_0_specials_block_reason(block_reason: str | None) -> bool:
    return block_reason == BLOCK_REASON_SEASON_0_SPECIALS


def _extract_absolute_episode_number(stem: str) -> int | None:
    """从文件名 stem 中保守提取单个绝对集数.

    仅接受高置信模式 (见模块顶部正则列表), 多个独立数字 / 范围
    / EP* / E* / 多行全部不解析. 标准 SxxExx 文件由调用方优先过滤.
    """
    if not stem:
        return None
    candidates: list[int] = []
    for pattern in (
        _ABSOLUTE_BRACKET_PATTERN,
        _ABSOLUTE_CHINESE_HUA_PATTERN,
        _ABSOLUTE_CHINESE_JI_PATTERN,
        _ABSOLUTE_DASH_SUFFIX_PATTERN,
        _ABSOLUTE_UNDERSCORE_PATTERN,
        _ABSOLUTE_DOT_SUFFIX_PATTERN,
    ):
        match = pattern.search(stem)
        if match:
            try:
                candidates.append(int(match.group(1)))
            except (TypeError, ValueError):
                continue
    if not candidates:
        return None
    if len(candidates) > 1:
        # 多个高置信匹配 → 歧义, 不解析.
        return None
    value = candidates[0]
    if value <= 0:
        return None
    return value


def _stem_has_sxxexx(stem: str) -> bool:
    return bool(
        re.search(r"[Ss](?:eason\s*)?\d{1,4}\s*[Ee](?:pisode\s*)?\d{1,4}", stem)
    )


def _extract_absolute_episodes(
    candidate_videos: list[FileInfo],
) -> tuple[list[tuple[FileInfo, int]], str | None]:
    """从候选视频提取绝对集数. 返回 (entries, block_reason).

    block_reason:
    - None: 成功解析所有文件, 或全部文件都没有命中任何绝对集数模式.
    - absolute_episode_ambiguous_not_supported: 部分文件命中但失败原因
      是"多个数字" / SxxExx 优先于绝对集数 → 整体不映射.
    - absolute_episode_sparse_not_supported: 命中但集合不连续.
    """
    pairs: list[tuple[FileInfo, int]] = []
    for info in candidate_videos:
        stem = Path(info.path).stem
        if _stem_has_sxxexx(stem):
            # 标准 SxxExx 永远优先, 拒绝把 S03E01 当成 E51.
            return [], BLOCK_REASON_ABSOLUTE_EPISODE_AMBIGUOUS
        number = _extract_absolute_episode_number(stem)
        if number is None:
            return [], BLOCK_REASON_ABSOLUTE_EPISODE_AMBIGUOUS
        pairs.append((info, number))
    if not pairs:
        return [], BLOCK_REASON_ABSOLUTE_EPISODE_AMBIGUOUS
    numbers = sorted(n for _, n in pairs)
    if numbers != list(range(numbers[0], numbers[-1] + 1)):
        return [], BLOCK_REASON_ABSOLUTE_EPISODE_SPARSE
    return pairs, None


def _pick_season_for_absolute_range(
    season_coverage: dict[int, int] | None,
    absolute_range: tuple[int, int],
) -> int | None:
    """根据 provider season episode count 选唯一一个能覆盖 [start, end] 的 season.

    season_coverage: {season_number: episode_count}. 没有覆盖信息 (None /
    空 dict) 时返回 None, 由调用方落到 block_reason = out_of_provider_range.
    """
    if not season_coverage:
        return None
    start, end = absolute_range
    candidates = [
        s for s, count in season_coverage.items()
        if count >= end
    ]
    if not candidates:
        return None
    # 选择 episode_count 最小的 season (避免把 [51]-[66] 错配到 season 3
    # 仅当 S3 episode_count < S1 时才选 3). 但 spec 要求"标准 SxxExy 永远
    # 优先", 因此这里采用"刚好覆盖"的语义: 选 episode_count 最小且 >= end 的 season.
    return min(candidates)


def derive_season_coverage_from_detail(
    detail_payload: dict | None,
) -> dict[int, int]:
    """从 MetadataDetail payload 推导 {season_number: episode_count}.

    TMDB show payload.raw.seasons[] 是核心来源 (list of dict with
    season_number + episode_count). 同时支持 seasons_info 自定义结构,
    便于其它 provider 适配.
    """
    if not isinstance(detail_payload, dict):
        return {}
    coverage: dict[int, int] = {}
    raw = detail_payload.get("raw") or {}
    if not raw and isinstance(detail_payload.get("payload"), dict):
        raw = detail_payload["payload"].get("raw") or {}
    seasons_iter = raw.get("seasons") if isinstance(raw, dict) else None
    if not isinstance(seasons_iter, list):
        seasons_iter = detail_payload.get("seasons")
    if isinstance(seasons_iter, list):
        for entry in seasons_iter:
            if not isinstance(entry, dict):
                continue
            try:
                s_num = int(entry.get("season_number"))
                e_count = int(entry.get("episode_count"))
            except (TypeError, ValueError):
                continue
            if s_num <= 0 or e_count < 0:
                continue
            coverage[s_num] = e_count
    return coverage


@dataclass(frozen=True, kw_only=True)
class ShowStructureResult:
    """剧集结构识别结果 — 供 Agent 工具 / 任务工作台 / 时间线复用."""

    status: str
    entries: list[EpisodeMappingEntry] = field(default_factory=list)
    detected_show_title: str | None = None
    block_reason: str | None = None
    episode_range: str | None = None
    season: int | None = None
    excluded_video_count: int = 0
    mapping_mode: str = MAPPING_MODE_SXXEXX
    candidate_video_count: int = 0


def _format_episode_range(season: int, entries: list[EpisodeMappingEntry]) -> str:
    episodes = sorted(e.episode for e in entries)
    if not episodes:
        return ""
    if len(episodes) == 1:
        return f"S{season:02d}E{episodes[0]:02d}"
    return f"S{season:02d}E{episodes[0]:02d}-E{episodes[-1]:02d}"


def _video_files_for(source_path: Path) -> tuple[list[FileInfo], list[FileInfo]]:
    """扫描任务输入节点, 返回 (候选视频, 被排除的 sample/trailer 视频).

    与 ``analyze_task_input`` 一致: 辅助视频永远不进剧集候选, 即便
    它们的文件名碰巧也匹配 SxxExx (例如 sample.S01E01.mkv 仍按 sample 排除).
    """
    analysis = analyze_task_input(source_path)
    candidate_videos = [f for f in analysis.files if f.type == "video"]
    excluded_videos = [e for e in analysis.excluded if e.type == "video"]
    return candidate_videos, excluded_videos


def _detect_per_video_first_block(
    candidate_videos: list[FileInfo],
) -> tuple[list[EpisodeMappingEntry], EpisodeMappingResult | None]:
    """对每个候选视频单独跑 ``detect_show_structure`` 并合并.

    必须 per-video 调用: ``detect_show_structure`` 看的是单个路径的
    stem 和父目录名, 它能正确处理同名同季的不同 episode (例如
    S01E01-E05 各自分布在不同文件, 而父目录里没有提示).

    per-file 合并策略: 文件名 (filename) 匹配优先, 父目录名
    (parent_dir) 匹配仅作 fallback. 这样单文件场景不会同时拿到
    (filename=S01E01, parent_dir=S01E01) 两条 S01E01 entry, 避免
    episodes 列表出现重复值而误判 sparse_episodes.

    碰到 per-file 阻塞原因 (specials / multi-episode / cross-season) 立即
    返回整段的阻塞结果, 由调用方映射到统一的 ``block_reason`` 字符串.
    """
    entries: list[EpisodeMappingEntry] = []
    for info in candidate_videos:
        result = detect_show_structure(Path(info.path))
        if result is None:
            continue
        if result.block_reason in (
            "specials_season_0_not_supported",
            "multi_episode_in_single_file_not_supported",
            "cross_season_not_supported",
        ):
            return [], result
        # 文件名匹配优先 — 稳定表达"这个文件本身的 SxxExx".
        chosen: EpisodeMappingEntry | None = None
        for entry in result.entries:
            if entry.source == "filename":
                chosen = EpisodeMappingEntry(
                    file_path=info.path,
                    season=entry.season,
                    episode=entry.episode,
                    source="filename",
                )
                break
        if chosen is None:
            # fallback: 父目录名携带的 SxxExx 提示, 用于目录已命名但文件没命名.
            for entry in result.entries:
                if entry.source == "parent_dir":
                    chosen = EpisodeMappingEntry(
                        file_path=info.path,
                        season=entry.season,
                        episode=entry.episode,
                        source="parent_dir",
                    )
                    break
        if chosen is not None:
            entries.append(chosen)
    return entries, None


def analyze_show_structure(
    source_path: Path,
    *,
    season_coverage: dict[int, int] | None = None,
) -> ShowStructureResult:
    """扫描任务输入并返回剧集结构识别结果. 纯 side-effect-free.

    输入可以是单文件或目录:
    - 单文件: 必须是能稳定解析出单集的命名 (例如 Example.Show.S01E01.mkv).
    - 目录: 多个 video, 必须在同季 episode 连续.

    返回的 ``status`` 用于 Agent 工具分支:
    - ``auto_publishable``: 单集或同季连续多集, 任务可以继续.
    - 其它: Agent 解释 / 决策 / 失败.

    ``season_coverage`` (可选): {season_number: episode_count} 用于绝对
    集数映射验证. 例如 Re:Zero TMDB season 1 共 85 集, [51]-[66] 应映
    射到 S01E51-E66. 不传或为空 → 绝对集数映射走保守路径, 拒绝
    auto_publish (走 out_of_provider_range block_reason).
    """
    if not source_path.exists():
        return ShowStructureResult(
            status=STATUS_NO_VIDEO_FILES,
            block_reason=BLOCK_REASON_NO_VIDEO_FILES,
        )

    candidate_videos, excluded_videos = _video_files_for(source_path)
    if not candidate_videos:
        return ShowStructureResult(
            status=STATUS_NO_VIDEO_FILES,
            block_reason=BLOCK_REASON_NO_VIDEO_FILES,
            excluded_video_count=len(excluded_videos),
            candidate_video_count=0,
        )

    entries, per_file_block = _detect_per_video_first_block(candidate_videos)
    if per_file_block is not None:
        # 单文件本身的命名就触发了阻塞 (specials / 单文件多集 / 单季仍跨季).
        # 复用 detect_show_structure 的 block_reason, 但映射到本服务的统一字符串.
        if per_file_block.block_reason == "specials_season_0_not_supported":
            block_reason = BLOCK_REASON_SEASON_0_SPECIALS
            status = STATUS_UNSUPPORTED_SEASON_0_SPECIALS
        elif per_file_block.block_reason == "multi_episode_in_single_file_not_supported":
            block_reason = BLOCK_REASON_MULTI_EPISODE_IN_SINGLE_FILE
            status = STATUS_UNSUPPORTED_MULTI_EPISODE_IN_SINGLE_FILE
        else:
            block_reason = BLOCK_REASON_CROSS_SEASON
            status = STATUS_UNSUPPORTED_CROSS_SEASON
        return ShowStructureResult(
            status=status,
            block_reason=block_reason,
            excluded_video_count=len(excluded_videos),
            candidate_video_count=len(candidate_videos),
        )

    if not entries:
        # 标准 SxxExx 路径无结果 → 走保守绝对集数映射 fallback.
        # 仅在多文件场景启用; 单文件 SxxExx 失败直接 not_show_structure.
        if len(candidate_videos) > 1:
            abs_result = _try_absolute_episode_mapping(
                candidate_videos, excluded_videos,
                season_coverage=season_coverage,
            )
            # 绝对集数映射成功 → 走 auto_publishable; 否则
            # 返回绝对集数自己的 block_reason (out_of_provider_range
            # / sparse / not_show_structure).
            if abs_result.status in (
                STATUS_AUTO_PUBLISHABLE,
                STATUS_ABSOLUTE_EPISODE_NEEDS_METADATA,
                STATUS_ABSOLUTE_EPISODE_OUT_OF_RANGE,
                STATUS_UNSUPPORTED_SPARSE_EPISODES,
            ):
                return abs_result
        return ShowStructureResult(
            status=STATUS_NOT_SHOW_STRUCTURE,
            block_reason=BLOCK_REASON_NOT_SHOW_STRUCTURE,
            excluded_video_count=len(excluded_videos),
            candidate_video_count=len(candidate_videos),
        )

    # 跨季检查 — 多集模式下才出现.
    seasons = {e.season for e in entries}
    if len(seasons) > 1:
        return ShowStructureResult(
            status=STATUS_UNSUPPORTED_CROSS_SEASON,
            block_reason=BLOCK_REASON_CROSS_SEASON,
            excluded_video_count=len(excluded_videos),
            candidate_video_count=len(candidate_videos),
        )

    season = next(iter(seasons))
    episodes = sorted(e.episode for e in entries)

    # 稀疏集检查 — 必须连续, 不允许 E01, E03, E05 之类跳号.
    if episodes != list(range(episodes[0], episodes[-1] + 1)):
        return ShowStructureResult(
            status=STATUS_UNSUPPORTED_SPARSE_EPISODES,
            block_reason=BLOCK_REASON_SPARSE_EPISODES,
            season=season,
            episode_range=_format_episode_range(season, entries),
            excluded_video_count=len(excluded_videos),
            candidate_video_count=len(candidate_videos),
        )

    # Season 0 双保险 — per-file 阶段已经拦截, 这里再防一次.
    if season == 0:
        return ShowStructureResult(
            status=STATUS_UNSUPPORTED_SEASON_0_SPECIALS,
            block_reason=BLOCK_REASON_SEASON_0_SPECIALS,
            season=0,
            episode_range=_format_episode_range(0, entries),
            excluded_video_count=len(excluded_videos),
            candidate_video_count=len(candidate_videos),
        )

    return ShowStructureResult(
        status=STATUS_AUTO_PUBLISHABLE,
        entries=entries,
        detected_show_title=_best_detected_title(candidate_videos),
        season=season,
        episode_range=_format_episode_range(season, entries),
        excluded_video_count=len(excluded_videos),
        candidate_video_count=len(candidate_videos),
        mapping_mode=MAPPING_MODE_SXXEXX,
    )


def _try_absolute_episode_mapping(
    candidate_videos: list[FileInfo],
    excluded_videos: list[FileInfo],
    *,
    season_coverage: dict[int, int] | None,
) -> ShowStructureResult:
    """保守绝对集数映射 fallback: 仅当 SxxExx 路径无法解析时启用.

    设计要点:
    - 拒绝 SxxExx 与绝对集数共存的混合命名 (绝对集数要求文件名无 SxxExx).
    - 拒绝多个独立数字 / EP*/E* / 范围 / 非连续.
    - 必须有 season_coverage 才能确定目标 season; 没有 coverage → out_of_provider_range.
    """
    pairs, abs_block = _extract_absolute_episodes(candidate_videos)
    if abs_block is not None:
        if abs_block == BLOCK_REASON_ABSOLUTE_EPISODE_SPARSE:
            return ShowStructureResult(
                status=STATUS_UNSUPPORTED_SPARSE_EPISODES,
                block_reason=BLOCK_REASON_ABSOLUTE_EPISODE_SPARSE,
                excluded_video_count=len(excluded_videos),
                candidate_video_count=len(candidate_videos),
                mapping_mode=MAPPING_MODE_ABSOLUTE,
            )
        # ambiguous: 留给上层按 not_show_structure 流程处理.
        return ShowStructureResult(
            status=STATUS_NOT_SHOW_STRUCTURE,
            block_reason=BLOCK_REASON_NOT_SHOW_STRUCTURE,
            excluded_video_count=len(excluded_videos),
            candidate_video_count=len(candidate_videos),
            mapping_mode=MAPPING_MODE_ABSOLUTE,
        )

    if not pairs:
        return ShowStructureResult(
            status=STATUS_NOT_SHOW_STRUCTURE,
            block_reason=BLOCK_REASON_NOT_SHOW_STRUCTURE,
            excluded_video_count=len(excluded_videos),
            candidate_video_count=len(candidate_videos),
            mapping_mode=MAPPING_MODE_ABSOLUTE,
        )

    numbers = sorted(n for _, n in pairs)
    absolute_range = (numbers[0], numbers[-1])
    target_season = _pick_season_for_absolute_range(
        season_coverage, absolute_range,
    )
    if target_season is None and not season_coverage:
        return ShowStructureResult(
            status=STATUS_ABSOLUTE_EPISODE_NEEDS_METADATA,
            block_reason=BLOCK_REASON_ABSOLUTE_EPISODE_NEEDS_METADATA,
            excluded_video_count=len(excluded_videos),
            candidate_video_count=len(candidate_videos),
            mapping_mode=MAPPING_MODE_ABSOLUTE,
            episode_range=f"{absolute_range[0]}-{absolute_range[1]}",
        )

    if target_season is None:
        return ShowStructureResult(
            status=STATUS_ABSOLUTE_EPISODE_OUT_OF_RANGE,
            block_reason=BLOCK_REASON_ABSOLUTE_EPISODE_OUT_OF_RANGE,
            excluded_video_count=len(excluded_videos),
            candidate_video_count=len(candidate_videos),
            mapping_mode=MAPPING_MODE_ABSOLUTE,
            episode_range=f"{absolute_range[0]}-{absolute_range[1]}",
        )

    entries = [
        EpisodeMappingEntry(
            file_path=info.path,
            season=target_season,
            episode=number,
            source=MAPPING_MODE_ABSOLUTE,
        )
        for info, number in sorted(pairs, key=lambda p: (p[1], p[0].path))
    ]
    return ShowStructureResult(
        status=STATUS_AUTO_PUBLISHABLE,
        entries=entries,
        detected_show_title=_best_detected_title(candidate_videos),
        season=target_season,
        episode_range=_format_episode_range(target_season, entries),
        excluded_video_count=len(excluded_videos),
        candidate_video_count=len(candidate_videos),
        mapping_mode=MAPPING_MODE_ABSOLUTE,
    )


def _best_detected_title(candidate_videos: list[FileInfo]) -> str | None:
    """从候选视频文件名里挑一个最能代表剧集标题的 stem.

    复用 detect_show_structure 的 _extract_show_title 间接行为:
    detect_show_structure 内部已经计算过, 但它返回 None 时我们仍然
    可以从原始 stem 里去掉 SxxExx 拼出标题.
    """
    from media_pilot.orchestration.search_keyword_generation import (
        SEASON_EPISODE_PATTERN,
    )
    for info in candidate_videos:
        stem = Path(info.path).stem
        cleaned = SEASON_EPISODE_PATTERN.split(stem, maxsplit=1)[0].strip(" ._-")
        if cleaned:
            return cleaned
    return None


def prepare_show_episode_mapping(
    *,
    session: Session,
    task_id: str,
    season_coverage: dict[int, int] | None = None,
) -> ShowStructureResult:
    """分析任务输入并把 ``EpisodeMapping`` 持久化为任务事实.

    仅当 ``auto_publishable`` 时落库; 其它情况返回 ``block_reason`` 让
    Agent 解释或决定后续动作, 不写入 mapping. 落库是 delete+replace 语义,
    重跑同一任务会得到一致的最新结果.

    发布阶段 (publish_show_to_library) 不再重新解析 season/episode,
    直接从 ``EpisodeMapping`` 任务事实读取.

    ``season_coverage`` (可选): 来自 MetadataDetail 派生的
    {season_number: episode_count}, 用于绝对集数映射验证. 不传 → 绝对
    集数映射走保守 out_of_provider_range block_reason 路径.
    """
    from media_pilot.repository.repositories import (
        EpisodeMappingRepository,
        IngestTaskRepository,
    )

    task_repo = IngestTaskRepository(session)
    task = task_repo.get(task_id)
    if task is None:
        return ShowStructureResult(
            status=STATUS_NO_VIDEO_FILES,
            block_reason=BLOCK_REASON_NO_VIDEO_FILES,
        )
    if not task.source_path:
        return ShowStructureResult(
            status=STATUS_NO_VIDEO_FILES,
            block_reason=BLOCK_REASON_NO_VIDEO_FILES,
        )

    source_path = Path(task.source_path)
    result = analyze_show_structure(
        source_path, season_coverage=season_coverage,
    )

    if result.status != STATUS_AUTO_PUBLISHABLE:
        # 不落库, 让 Agent 解释或决策. 清理可能存在的旧 mapping, 避免
        # 历史残留与新 block_reason 矛盾.
        EpisodeMappingRepository(session).save_mappings(task_id=task_id, entries=[])
        return result

    EpisodeMappingRepository(session).save_mappings(
        task_id=task_id,
        entries=[
            {
                "file_path": e.file_path,
                "season": e.season,
                "episode": e.episode,
                "source": e.source,
            }
            for e in result.entries
        ],
    )
    return result


def get_persisted_show_structure(
    *,
    session: Session,
    task_id: str,
) -> ShowStructureResult | None:
    """读取已落库的 ``EpisodeMapping`` 重新组装 ``ShowStructureResult``.

    发布工具 (publish_show_to_library) 用此接口消费任务事实, 不再
    重新扫描 / 解析文件. 若任务没有 mapping, 返回 None.
    """
    from media_pilot.repository.repositories import EpisodeMappingRepository

    rows = EpisodeMappingRepository(session).get_by_task(task_id)
    if not rows:
        return None
    entries = [
        EpisodeMappingEntry(
            file_path=r.file_path,
            season=r.season,
            episode=r.episode,
            source=r.source,
        )
        for r in rows
    ]
    seasons = {e.season for e in entries}
    if len(seasons) != 1:
        return None
    season = next(iter(seasons))
    mapping_mode = (
        MAPPING_MODE_ABSOLUTE
        if any(e.source == MAPPING_MODE_ABSOLUTE for e in entries)
        else MAPPING_MODE_SXXEXX
    )
    return ShowStructureResult(
        status=STATUS_AUTO_PUBLISHABLE,
        entries=entries,
        season=season,
        episode_range=_format_episode_range(season, entries),
        candidate_video_count=len(entries),
        mapping_mode=mapping_mode,
    )
