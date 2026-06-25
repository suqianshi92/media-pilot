"""Shared task input analysis boundary -- side-effect-free file scanning.

Used by both Agent tools and (optionally) the deterministic workflow to
inspect an ingest task's source path without mutating anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from media_pilot.orchestration.ingestion import MEDIA_EXTENSIONS

# 文件名包含这些子串的视频被认为是 sample/trailer/auxiliary，不进主入库路径。
LOW_VALUE_VIDEO_MARKERS = frozenset(
    {"sample", "trailer", "teaser", "clip", "promo"}
)

SUBTITLE_EXTENSIONS = frozenset({".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt"})

# Dominant primary video size heuristic (USBA-089 现场引入):
# 多视频目录里若主片比伴随视频大成千上万倍 (e.g. 3.7 GB 主片 + 1.9 MB
# 广告), 由后端确定性规则自动挑主, 不消耗用户确认. 判定纯基于
# ``size_bytes`` (stat 已有), 不引入视频元数据探测 (时长 / 分辨率
# / 编码 / 音轨 — 任何形式的"读视频"调用).
# - DOMINANT_MIN_PRIMARY_SIZE: dominant 候选最小门槛, 低于此不自动消歧
# - DOMINANT_MAX_COMPARE_RATIO: 伴随视频相对 dominant 的最大比例
# - DOMINANT_MAX_COMPARE_ABSOLUTE: 伴随视频绝对大小上限 (与 ratio 取 min)
DOMINANT_MIN_PRIMARY_SIZE = 200 * 1024 * 1024
DOMINANT_MAX_COMPARE_RATIO = 0.02
DOMINANT_MAX_COMPARE_ABSOLUTE = 50 * 1024 * 1024

EXCLUDED_REASON_LOW_VALUE_RATIO = "low_value_size_ratio:small_companion_video"


def classify_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in MEDIA_EXTENSIONS:
        return "video"
    if ext in SUBTITLE_EXTENSIONS:
        return "subtitle"
    return "other"


def is_auxiliary_video(name: str) -> bool:
    lower = name.lower()
    return any(marker in lower for marker in LOW_VALUE_VIDEO_MARKERS)


@dataclass(frozen=True, kw_only=True)
class FileInfo:
    path: str
    name: str
    size_bytes: int
    type: str
    matched_by: str | None = None
    excluded_reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class TaskInputAnalysis:
    source_path: str
    is_directory: bool
    files: list[FileInfo]
    excluded: list[FileInfo]
    video_count: int
    subtitle_count: int
    total_size_bytes: int
    # Dominant primary video size heuristic 痕迹: 目录模式下保留
    # 启发式折叠之前的视频列表 (marker 已排除). 供
    # ``analyze_complex_input`` 重新评估
    # ``_looks_like_show_structure`` (show_structure 优先级必须高于
    # size 启发式, 不能让 size 启发式把多视频目录折叠成单视频
    # 之后才判定剧集结构). 单文件 / 单视频目录该字段退化为
    # 1 元素或空, 与 ``files`` 里视频一致.
    pre_heuristic_videos: list[FileInfo] = field(default_factory=list)


def is_same_stem_subtitle(sub_stem: str, video_stem: str) -> bool:
    """判定字幕 stem 是否与主视频 stem 命中同源 (single-file 与
    directory 模式共用).

    命中规则: ``sub_stem == video_stem`` 或
    ``sub_stem.startswith(video_stem + ".")``. 第二条覆盖
    locale-suffix (e.g. ``Darkest Hour (2017).zh.srt``) 场景.
    ``startswith`` 必须显式带 ``"."`` 边界, 否则
    ``"Darkest Hour (2017) Bonus"`` 会被误判同源.
    """
    if sub_stem == video_stem:
        return True
    return sub_stem.startswith(video_stem + ".")


def _find_same_stem_subtitles(source_file: Path) -> list[FileInfo]:
    video_stem = source_file.stem
    parent = source_file.parent
    subtitles: list[FileInfo] = []
    for entry in sorted(parent.iterdir()):
        if not entry.is_file():
            continue
        ext = entry.suffix.lower()
        if ext not in SUBTITLE_EXTENSIONS:
            continue
        sub_stem = entry.stem
        if is_same_stem_subtitle(sub_stem, video_stem):
            subtitles.append(FileInfo(
                path=str(entry),
                name=entry.name,
                size_bytes=entry.stat().st_size,
                type="subtitle",
                matched_by="same_stem",
            ))
    return subtitles


def _apply_dominant_size_heuristic(
    files: list[FileInfo],
    excluded: list[FileInfo],
) -> bool:
    """对 ``files`` 里剩余的视频应用 dominant primary video size 启发式.

    命中条件 (全部满足才修改 files / excluded):
    - 至少 2 个视频
    - 存在唯一最大 size 视频 ``dominant``, size >= 200 MiB
    - 所有 non_dominant 视频 size <= min(50 MiB, dominant * 0.02)

    关键守卫: 只要存在任何 non_dominant > cap, 函数不得修改
    ``files`` / ``excluded``, 必须返回 False. 这避免"4 GB 主片 +
    700 MB 第二视频 + 1 MB 广告"的目录被错误消歧 — 700 MB 远超
    cap, 是有意义的歧义, 应让用户走 select_primary_video 决策,
    不能让 size 启发式吞掉 700 MB 候选. 命中时把 non_dominant
    全部就地移出 ``files``, 追加到 ``excluded`` 附
    ``low_value_size_ratio:small_companion_video`` reason.

    唯一性守卫拒绝 size 平局 (3.7 GB + 3.7 GB 是歧义, 不应被
    自动消歧). dominant 选择按 size 严格最大, 与 ``files`` 排序
    解耦 — 不依赖字典序.

    返回 True 表示规则命中, 调用方把 pre-heuristic 视频列表
    透传到 ``TaskInputAnalysis.pre_heuristic_videos`` 供
    ``analyze_complex_input`` 重新评估 show_structure.

    纯计算 + 列表变更, 无新 IO. ``files`` / ``excluded`` 原地修改.
    """
    videos = [f for f in files if f.type == "video"]
    if len(videos) < 2:
        return False

    # 唯一最大 size 守卫: 拒绝平局.
    sorted_videos = sorted(videos, key=lambda v: v.size_bytes, reverse=True)
    if sorted_videos[0].size_bytes == sorted_videos[1].size_bytes:
        return False

    dominant = sorted_videos[0]
    if dominant.size_bytes < DOMINANT_MIN_PRIMARY_SIZE:
        return False

    companion_cap = min(
        DOMINANT_MAX_COMPARE_ABSOLUTE,
        int(dominant.size_bytes * DOMINANT_MAX_COMPARE_RATIO),
    )

    non_dominant = sorted_videos[1:]

    # 关键守卫: 任何 non_dominant > cap → 全部不消歧, 保持 files
    # 原状. 不修改 files / excluded, 不返回 True.
    if any(v.size_bytes > companion_cap for v in non_dominant):
        return False

    # 全部 non_dominant 都 <= cap, 命中. dominant 留 files, 其余
    # 全部移入 excluded 附 reason.
    new_files: list[FileInfo] = []
    companions: list[FileInfo] = []
    for f in files:
        if f.type != "video" or f is dominant:
            new_files.append(f)
            continue
        # type == "video" 且不是 dominant → 必为 non_dominant 之一,
        # size 已校验 <= cap, 全部进 companions.
        companions.append(f)

    for c in companions:
        excluded.append(FileInfo(
            path=c.path, name=c.name, size_bytes=c.size_bytes,
            type=c.type, excluded_reason=EXCLUDED_REASON_LOW_VALUE_RATIO,
        ))

    files[:] = new_files
    return True


def analyze_task_input(source_path: Path) -> TaskInputAnalysis:
    """Analyse the task source path and return a classification of all relevant files.

    - Single file: only the file itself plus same-stem subtitles.
    - Directory: all supported files classified as video/subtitle/other,
      with sample/trailer/auxiliary videos separated into ``excluded``.

    目录模式额外应用 dominant primary video size 启发式: 多视频目录
    若主片比伴随视频大成千上万倍 (e.g. 3.7 GB + 1.9 MB), 自动挑
    dominant 为主, 伴随视频进 ``excluded`` 附
    ``low_value_size_ratio:small_companion_video`` reason. 触发顺序:
    marker 排除 → size 启发式, 两条路径互不冲突. 判定纯基于
    ``size_bytes``, 不引入视频元数据探测.

    ``pre_heuristic_videos`` 字段保留 size 启发式折叠之前的视频列表
    (marker 已排除), 供 ``analyze_complex_input`` 重新评估
    ``_looks_like_show_structure`` (show_structure 必须优先于
    size 启发式, 否则 3.7 GB + 1.9 MB + season 关键词的剧集目录会被
    size 启发式误吞成单视频电影).
    """
    files: list[FileInfo] = []
    excluded: list[FileInfo] = []
    pre_heuristic_videos: list[FileInfo] = []

    if source_path.is_file():
        info = FileInfo(
            path=str(source_path),
            name=source_path.name,
            size_bytes=source_path.stat().st_size,
            type=classify_file(source_path),
        )
        if is_auxiliary_video(source_path.stem):
            info = FileInfo(
                path=str(source_path),
                name=source_path.name,
                size_bytes=source_path.stat().st_size,
                type=classify_file(source_path),
                excluded_reason="sample/trailer/auxiliary",
            )
            excluded.append(info)
        else:
            files.append(info)
        subtitles = _find_same_stem_subtitles(source_path)
        files.extend(subtitles)
        scan_dir = source_path.parent
    else:
        scan_dir = source_path
        for entry in sorted(scan_dir.iterdir()):
            if not entry.is_file():
                continue
            file_type = classify_file(entry)
            info = FileInfo(
                path=str(entry),
                name=entry.name,
                size_bytes=entry.stat().st_size,
                type=file_type,
            )
            if file_type == "video" and is_auxiliary_video(entry.stem):
                info = FileInfo(
                    path=str(entry),
                    name=entry.name,
                    size_bytes=entry.stat().st_size,
                    type=file_type,
                    excluded_reason="sample/trailer/auxiliary",
                )
                excluded.append(info)
            else:
                files.append(info)
        # 目录模式: 折叠前先抓取"marker 排除后的视频列表"供
        # pre_heuristic_videos, 再应用 size 启发式. 启发式命中
        # 时 ``files`` 收缩, 但 ``pre_heuristic_videos`` 保留折叠前状态.
        pre_heuristic_videos = [f for f in files if f.type == "video"]
        _apply_dominant_size_heuristic(files, excluded)

        # 目录模式 same-stem 字幕标 matched_by (MP-Test-03 Darkest Hour
        # 现场): size 启发式 / 单视频目录折叠后, 若 ``files`` 仅剩 1
        # 个视频, 把它当作"主视频" stem, 对命中规则的字幕标
        # ``matched_by="same_stem"``. 多主视频 (heuristic 未命中) 暂
        # 不标 — ``_resolve_after_primary_chosen`` 会按 user 选中的
        # primary 路径再调 ``_find_same_stem_subtitles`` 算同源, 与
        # 单文件模式行为一致.
        post_heuristic_videos = [f for f in files if f.type == "video"]
        if len(post_heuristic_videos) == 1:
            primary_stem = Path(post_heuristic_videos[0].path).stem
            for idx, f in enumerate(files):
                if f.type != "subtitle":
                    continue
                if is_same_stem_subtitle(Path(f.path).stem, primary_stem):
                    files[idx] = FileInfo(
                        path=f.path, name=f.name, size_bytes=f.size_bytes,
                        type=f.type, matched_by="same_stem",
                        excluded_reason=f.excluded_reason,
                    )

    return TaskInputAnalysis(
        source_path=str(source_path),
        is_directory=source_path.is_dir(),
        files=files,
        excluded=excluded,
        video_count=sum(1 for f in files if f.type == "video"),
        subtitle_count=sum(1 for f in files if f.type == "subtitle"),
        total_size_bytes=sum(f.size_bytes for f in files),
        pre_heuristic_videos=pre_heuristic_videos,
    )
