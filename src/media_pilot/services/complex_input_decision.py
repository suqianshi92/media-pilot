"""复杂电影输入分析与决策生成服务.

把多视频、样片花絮、字幕归属不明确、疑似剧集/BDMV/ISO 等场景
转成可回复的 AgentDecisionRequest. 不引入 ffprobe/mediainfo
或本地视频时长/分辨率探测 — 决策选项完全基于文件名、扩展名、
大小、同源字幕匹配和样片标记生成.

边界:
- 只处理"看起来仍属于电影入库"的输入. 疑似剧集/合集 (SxxExx 多
  集 / show-like 目录) 不在电影入库路径上, 不创建
  ``review_complex_input``, 而是返回 ``status="show_like"``, 让
  Agent 工具把它当 ready 透传给 LLM, 提示调用
  ``prepare_show_structure`` 走剧集入库主线.
- 决策选项 payload 由后端基于任务事实生成, 不让 LLM 拼接文件路径.
- 无法形成具体可选项时返回明确失败事实, 不写泛化人工兜底状态.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from media_pilot.config import AppConfig
from media_pilot.orchestration.ingestion import MEDIA_EXTENSIONS
from media_pilot.services.task_input_analysis import (
    FileInfo,
    analyze_task_input,
    is_auxiliary_video,
)


# BDMV/ISO 等首版不支持的高风险结构.
BDMV_MARKERS = ("BDMV", "CERTIFICATE", "STREAM")
ISO_EXTENSIONS = (".iso", ".img")
SHOW_PATTERN_HINTS = ("S01E", "S01.E", "season", "episode", "complete")


@dataclass(frozen=True, kw_only=True)
class DecisionOption:
    """后端标准化生成的决策选项 — 不让 LLM 拼接文件路径."""

    id: str
    label: str
    description: str = ""
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ComplexInputDecision:
    """prepare_complex_input_decision 的结构化结果.

    - status == "ready": 单文件普通电影, 任务可以继续.
    - status == "show_like": 检测到 SxxExx 多集 / show-like 目录,
      不在电影入库路径上. 工具把它当 ready 透传, data 携带
      ``is_show=True`` 提示 LLM 走 ``prepare_show_structure``.
    - status == "decision_requested": 创建 decision_type 类型的人工决策请求.
    - status == "unsupported": 高风险结构 (BDMV/ISO) 或上一轮
      review_complex_input 已被 user_note 消费, 走 review_complex_input.
    - status == "no_videos": 任务输入节点没有任何视频文件.
    - status == "unsafe_path": 源路径不在受控输入根内.
    - status == "scan_failed": 路径解析失败.
    """

    status: str
    decision_type: str | None = None
    question: str | None = None
    options: list[DecisionOption] = field(default_factory=list)
    free_text_allowed: bool = False
    analysis: "ComplexInputAnalysis | None" = None
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class ComplexInputAnalysis:
    """复杂输入分析结果 — 供 decision reply / 任务工作台 / 时间线复用."""

    source_path: str
    is_directory: bool
    video_candidates: list[FileInfo]
    auxiliary_videos: list[FileInfo]
    subtitle_candidates: list[FileInfo]
    excluded: list[FileInfo]
    detected: list[str] = field(default_factory=list)
    # detected 标记可能值: bdmv_or_iso / show_structure / multiple_videos /
    # ambiguous_subtitles / single_video_ready


def _is_within_safe_roots(path: Path, config: AppConfig) -> bool:
    """检查源路径是否位于 downloads / watch / workspace 受控输入根内."""
    safe_roots = [
        config.downloads_dir,
        config.watch_dir,
        config.workspace_dir,
    ]
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in safe_roots:
        try:
            if root.exists() and resolved.is_relative_to(root.resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


def _detect_bdmv_or_iso(source_path: Path) -> bool:
    if source_path.is_dir():
        for marker in BDMV_MARKERS:
            if (source_path / marker).exists():
                return True
    if source_path.is_file() and source_path.suffix.lower() in ISO_EXTENSIONS:
        return True
    return False


_SXXEXX_PATTERN = re.compile(r"\bs(\d{1,2})[ex](\d{1,2})\b", re.IGNORECASE)


def _looks_like_show_structure(source_path: Path, video_files: list[FileInfo]) -> bool:
    """文件名模式检测 SxxExx / complete / season 关键词.
    命中即认为疑似剧集/合集, 不在 Agent 主线作为电影自动发布.

    识别两类输入:
    - 单文件 (e.g. ``Example.Show.S01E01.mkv``): 文件名命中 SxxExx 即视为
      剧集, 后续走 ``prepare_show_structure`` 落库 1 条 ``EpisodeMapping``.
    - 目录: 至少 2 个视频, 目录名或文件命中 season/complete/SxxExx 任一
      即视为剧集合集.
    """
    # 单文件: 仅靠文件名里的 SxxExx 决定, 不要再加多视频限制
    # (单集剧集文件天然只有 1 个视频).
    if not source_path.is_dir():
        return any(_SXXEXX_PATTERN.search(f.name) for f in video_files)
    # 目录: 多视频 + season/complete 关键词或文件名 SxxExx.
    if len(video_files) < 2:
        return False
    dir_name = source_path.name.lower()
    if any(hint in dir_name for hint in SHOW_PATTERN_HINTS):
        return True
    for f in video_files:
        if _SXXEXX_PATTERN.search(f.name):
            return True
    return False


def analyze_complex_input(
    *,
    source_path: Path,
    config: AppConfig,
) -> ComplexInputAnalysis:
    """扫描任务输入节点并返回复杂输入分析事实.

    复用 analyze_task_input 的 same-stem 字幕识别 + sample/trailer 排除
    + dominant primary video size 启发式. 不引入 ffprobe/mediainfo
    或本地视频时长/分辨率探测.

    show_structure 优先级高于 dominant_primary_video: 用
    ``analyze_task_input.pre_heuristic_videos`` (size 启发式折叠前的
    视频列表, marker 已排除) 评估 show_structure, 避免 3.7 GB +
    1.9 MB + season 关键词的剧集目录被 size 启发式误吞成单视频电影.
    """
    detected: list[str] = []
    if _detect_bdmv_or_iso(source_path):
        detected.append("bdmv_or_iso")

    analysis = analyze_task_input(source_path)
    video_candidates = [f for f in analysis.files if f.type == "video"]
    auxiliary_videos = [e for e in analysis.excluded if e.type == "video"]
    subtitle_candidates = [f for f in analysis.files if f.type == "subtitle"]

    # show_structure 必须用 pre_heuristic_videos 评估, 因为 size 启发式
    # 已经把多视频目录折叠成单视频 (典型为 1). 单文件模式
    # pre_heuristic_videos 为空, 退回 video_candidates (1 元素) —
    # _looks_like_show_structure 在单文件模式下不要求 >= 2 videos.
    show_structure_inputs = (
        analysis.pre_heuristic_videos
        if analysis.pre_heuristic_videos
        else video_candidates
    )
    if _looks_like_show_structure(source_path, show_structure_inputs):
        detected.append("show_structure")

    # dominant_primary_video: size 启发式命中 (heuristic 把多视频折
    # 叠成 1, 且 pre_heuristic_videos 比 video_candidates 元素更多).
    # show_structure / bdmv_or_iso 已命中时不再追加, 避免语义冲突 —
    # show_structure 路径下 size 启发式无效.
    heuristic_collapsed = (
        bool(analysis.pre_heuristic_videos)
        and len(video_candidates) < len(analysis.pre_heuristic_videos)
    )
    if (
        heuristic_collapsed
        and "show_structure" not in detected
        and "bdmv_or_iso" not in detected
    ):
        detected.append("dominant_primary_video")

    if len(video_candidates) > 1:
        detected.append("multiple_videos")

    return ComplexInputAnalysis(
        source_path=str(source_path),
        is_directory=source_path.is_dir(),
        video_candidates=video_candidates,
        auxiliary_videos=auxiliary_videos,
        subtitle_candidates=subtitle_candidates,
        excluded=analysis.excluded,
        detected=detected,
    )


def _build_select_primary_video_options(
    video_candidates: list[FileInfo],
    auxiliary_videos: list[FileInfo],
) -> list[DecisionOption]:
    """为多视频目录生成主视频选项. payload 由后端写入, 不让 LLM 拼路径."""
    options: list[DecisionOption] = []
    for idx, f in enumerate(video_candidates):
        options.append(DecisionOption(
            id=f"video_{idx}",
            label=f.name,
            description=f"主视频候选 ({_format_size(f.size_bytes)})",
            payload={"path": f.path, "name": f.name, "size_bytes": f.size_bytes},
        ))
    # 已知 sample/trailer 不在候选里, 但留一条提示供前端可选展示.
    return options


def _build_select_subtitles_options(
    video_candidates: list[FileInfo],
    subtitle_candidates: list[FileInfo],
) -> list[DecisionOption]:
    """为字幕归属不明确的任务生成字幕选项 + "不带入字幕" 选项."""
    options: list[DecisionOption] = []
    primary_video = video_candidates[0] if video_candidates else None
    primary_stem = (
        Path(primary_video.path).stem if primary_video is not None else None
    )
    for idx, f in enumerate(subtitle_candidates):
        sub_stem = Path(f.path).stem
        same_stem = bool(primary_stem and sub_stem == primary_stem)
        suffix_note = " (同源)" if same_stem else ""
        options.append(DecisionOption(
            id=f"subtitle_{idx}",
            label=f.name,
            description=f"{_format_size(f.size_bytes)}{suffix_note}",
            payload={"path": f.path, "name": f.name, "size_bytes": f.size_bytes},
        ))
    options.append(DecisionOption(
        id="no_subtitles",
        label="不带入字幕",
        description="不在本次发布中带入任何字幕",
        payload={"selected_subtitles": []},
    ))
    return options


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    units = ("KB", "MB", "GB", "TB")
    value = size_bytes / 1024
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    return f"{value:.1f} {units[unit_index]}"


def prepare_complex_input_decision(
    *,
    config: AppConfig,
    source_path: Path,
    user_selection: dict | None = None,
) -> ComplexInputDecision:
    """复杂电影输入分析与决策生成.

    返回结构化 ComplexInputDecision, 由 Agent 工具根据 status
    决定是否创建 AgentDecisionRequest. 不持久化任何东西, 是
    side-effect-free 的判定边界.

    user_selection (可选) — 来自 MediaSourceSelection 的最新事实, 用于
    防止"用户已选 → Agent 重跑 → 再创建同一决策"循环. 约定:
    - selected_path: 用户已选主视频路径 → 用该路径作为本轮主视频, 不再
      因 input_path 目录里其他视频触发 multiple_videos.
    - selected_subtitles: 用户已处理字幕 (含空数组) → 不再创建 select_subtitles.
    - user_note: review_complex_input 的用户说明 → 已在上一轮消费过,
      工具拒绝重复创建 review 决策, 改为返回 unsupported+stop 边界.
    """
    # ── user_note 存在 → 上一轮 review_complex_input 已消费; 本轮不再重复.
    #   显式返回 unsupported, 让 Agent 解释用户说明并停止决策.
    if user_selection and user_selection.get("user_note"):
        # 复用扫描结果但跳过所有可能创建新决策的分支.
        try:
            analysis = _safe_analyze(source_path, config)
        except Exception:
            analysis = None
        note = str(user_selection.get("user_note") or "").strip()
        return ComplexInputDecision(
            status="unsupported",
            decision_type="review_complex_input",
            question=(
                f"用户已在上轮回复复核说明: {note!r}. 不会再创建 review 决策; "
                "请基于该说明解释如何继续, 或在任务工作台采取进一步动作。"
            ),
            free_text_allowed=False,
            analysis=analysis,
            reason="review_user_note_already_consumed",
        )

    if not source_path.exists():
        return ComplexInputDecision(
            status="scan_failed",
            reason="source_path_not_found",
        )

    if not _is_within_safe_roots(source_path, config):
        return ComplexInputDecision(
            status="unsafe_path",
            reason="source_path_outside_safe_roots",
        )

    try:
        analysis = analyze_complex_input(
            source_path=source_path, config=config,
        )
    except Exception as exc:
        return ComplexInputDecision(
            status="scan_failed",
            reason=f"scan_error:{type(exc).__name__}",
        )

    # BDMV / ISO: 首版不支持, 拒绝直接转 review_complex_input.
    if "bdmv_or_iso" in analysis.detected:
        return ComplexInputDecision(
            status="unsupported",
            decision_type="review_complex_input",
            question=(
                f"源路径 {source_path} 看起来是 BDMV/ISO 目录结构, "
                "首版不支持自动入库。请说明如何处理。"
            ),
            free_text_allowed=True,
            analysis=analysis,
            reason="bdmv_or_iso",
        )

    # 疑似剧集/合集: 不在电影入库路径上, 也不再创建 review_complex_input
    # 阻塞. 改返回 show_like, 工具把它当 ready 透传, data 携带 is_show=True
    # 提示 LLM 调用 prepare_show_structure 走剧集入库主线. 这样:
    # 1) 同季连续多集 SxxExx 目录能继续推进到剧集元数据搜索 + 集映射.
    # 2) 跨季 / 稀疏 / Season 0 / 单文件多集由 prepare_show_structure
    #    的 block_reason 收口, 任务工作台显示明确的失败原因.
    if "show_structure" in analysis.detected:
        return ComplexInputDecision(
            status="show_like",
            analysis=analysis,
            reason="show_like",
        )

    if not analysis.video_candidates:
        return ComplexInputDecision(
            status="no_videos",
            analysis=analysis,
            reason="no_video_files_found",
        )

    # ── 用户已选过主视频 → 优先尊重 selected_path, 不再因目录里其他视频
    #    触发 multiple_videos 决策. selected_path 不存在或不在 input 节点
    #    → 退回让用户重新选.
    user_selected_path = (
        user_selection.get("selected_path")
        if user_selection else None
    )
    if user_selected_path:
        chosen = Path(user_selected_path)
        if chosen.exists() and chosen.is_file() and _is_within_safe_roots(chosen, config):
            # 用用户选的主视频做单视频上下文 (含同源字幕).
            from media_pilot.services.task_input_analysis import (
                _find_same_stem_subtitles,
            )
            same_stem = _find_same_stem_subtitles(chosen)
            chosen_info = FileInfo(
                path=str(chosen), name=chosen.name,
                size_bytes=chosen.stat().st_size, type="video",
            )
            return _resolve_after_primary_chosen(
                config=config,
                primary_video=chosen_info,
                non_same_stem_subs=[],
                same_stem_subs=same_stem,
                user_selection=user_selection,
                # selected_path 上下文里不携带目录扫描的辅助视频;
                # UI 可以从 task.source_path 重新扫描展示.
                auxiliary_videos=[],
                excluded=[],
            )

    # Dominant primary video size 启发式命中: video_candidates 已
    # 收敛成 1 个 dominant, 跳过 select_primary_video 决策, 走单视频
    # ready 路径. auxiliary_videos 透传伴随视频供任务工作台展示.
    if "dominant_primary_video" in analysis.detected:
        primary_video = analysis.video_candidates[0]
        from media_pilot.services.task_input_analysis import (
            _find_same_stem_subtitles,
        )
        same_stem = _find_same_stem_subtitles(Path(primary_video.path))
        non_same_stem = [
            f for f in analysis.subtitle_candidates
            if f.matched_by != "same_stem"
        ]
        return _resolve_after_primary_chosen(
            config=config,
            primary_video=primary_video,
            non_same_stem_subs=non_same_stem,
            same_stem_subs=same_stem,
            user_selection=user_selection,
            auxiliary_videos=analysis.auxiliary_videos,
            excluded=analysis.excluded,
        )

    # 多主视频: 创建 select_primary_video 决策.
    if "multiple_videos" in analysis.detected:
        options = _build_select_primary_video_options(
            analysis.video_candidates, analysis.auxiliary_videos,
        )
        return ComplexInputDecision(
            status="decision_requested",
            decision_type="select_primary_video",
            question=(
                f"源目录 {source_path} 包含 {len(analysis.video_candidates)} "
                "个候选主视频, 请选择用于本次电影入库的主视频。"
            ),
            free_text_allowed=False,
            options=options,
            analysis=analysis,
            reason="multiple_videos",
        )

    # 单视频: 字幕归属不明确才需要 select_subtitles.
    primary_video = analysis.video_candidates[0]
    non_same_stem = [
        f for f in analysis.subtitle_candidates
        if f.matched_by != "same_stem"
    ]
    return _resolve_after_primary_chosen(
        config=config,
        primary_video=primary_video,
        non_same_stem_subs=non_same_stem,
        same_stem_subs=[
            f for f in analysis.subtitle_candidates
            if f.matched_by == "same_stem"
        ],
        user_selection=user_selection,
        auxiliary_videos=analysis.auxiliary_videos,
        excluded=analysis.excluded,
    )


def _safe_analyze(source_path: Path, config: AppConfig):
    """仅扫描目录元数据, 不抛异常, 不可扫描时返回 None."""
    try:
        return analyze_complex_input(source_path=source_path, config=config)
    except Exception:
        return None


def _resolve_after_primary_chosen(
    *,
    config: AppConfig,
    primary_video: FileInfo,
    non_same_stem_subs: list[FileInfo],
    same_stem_subs: list[FileInfo],
    user_selection: dict | None,
    auxiliary_videos: list[FileInfo] | None = None,
    excluded: list[FileInfo] | None = None,
) -> ComplexInputDecision:
    """在主视频已确定 (用户已选 / 目录里只有 1 个) 的前提下, 决定下一步:
    - payload.selected_subtitles 存在 (含空数组) → 字幕已处理 → ready.
    - 非同源字幕存在 → select_subtitles 决策.
    - 否则 → ready.

    auxiliary_videos / excluded 由调用方透传: 单视频目录里被排除的
    sample / trailer 等辅助视频应保留到 analysis, 供 UI 任务工作台展示.
    """
    aux = auxiliary_videos or []
    exc = excluded or []
    # user_selection.payload.selected_subtitles 存在 → 字幕已处理
    if user_selection is not None and "selected_subtitles" in user_selection:
        ss = user_selection.get("selected_subtitles")
        # 显式空数组 或 字符串列表都视为已处理
        if isinstance(ss, list):
            return ComplexInputDecision(
                status="ready",
                reason="user_subtitles_resolved",
                analysis=ComplexInputAnalysis(
                    source_path=str(primary_video.path),
                    is_directory=False,
                    video_candidates=[primary_video],
                    auxiliary_videos=aux,
                    subtitle_candidates=[
                        *same_stem_subs,
                        *[FileInfo(
                            path=p, name=Path(p).name,
                            size_bytes=0, type="subtitle",
                            matched_by="user_selected",
                        ) for p in ss if isinstance(p, str)],
                    ],
                    excluded=exc,
                    detected=[],
                ),
            )

    if non_same_stem_subs:
        options = _build_select_subtitles_options(
            [primary_video], non_same_stem_subs,
        )
        return ComplexInputDecision(
            status="decision_requested",
            decision_type="select_subtitles",
            question=(
                f"主视频 {primary_video.name} 旁有 {len(non_same_stem_subs)} "
                "个非同源字幕, 请选择要随发布一起带入的字幕。"
            ),
            free_text_allowed=False,
            options=options,
            analysis=ComplexInputAnalysis(
                source_path=str(primary_video.path),
                is_directory=False,
                video_candidates=[primary_video],
                auxiliary_videos=aux,
                subtitle_candidates=[*same_stem_subs, *non_same_stem_subs],
                excluded=exc,
                detected=[],
            ),
            reason="ambiguous_subtitles",
        )

    return ComplexInputDecision(
        status="ready",
        reason="single_video_ready",
        analysis=ComplexInputAnalysis(
            source_path=str(primary_video.path),
            is_directory=False,
            video_candidates=[primary_video],
            auxiliary_videos=aux,
            subtitle_candidates=same_stem_subs,
            excluded=exc,
            detected=[],
        ),
    )
