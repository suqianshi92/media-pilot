"""共享库根解析服务 — 由最终元数据 provider / media_type 决定发布根.

设计要点 (route-adult-movie-library-root):
- 唯一入口: 所有发布链路 (agent/tools/write.py::publish_movie_to_library,
  publish_plan_draft, manual_selection._quick_publish, target_conflict_handler
  重建 plan) 必须通过 ``resolve_library_root(config, media_type, provider)``
  拿到库根, 不得继续硬编码 ``config.movies_dir`` / ``config.shows_dir``.
- 解析规则 (与 MetadataProfile.library_dir_attr + provider 绑定):
  - media_type="show" → 任何 provider → shows_dir
  - media_type="movie" + provider="tpdb" → adult_movies_dir
  - media_type="movie" + provider in (None, "tmdb", 其它未知) → movies_dir
- 强制约束: tpdb 成人影片能力启用时, adult_movies_dir 必须存在. 缺失时
  抛 ValueError, 不静默 fallback 到 movies_dir. 这与设计目标"避免混
  库"直接相关.

不在本模块做的事: 校验目录存在性 (由 validate_startup_config 负责);
构造实际发布计划 (由 build_movie_write_plan 负责). 本模块只回答"给定
元数据档案, 库根是哪个 Path".
"""

from __future__ import annotations

from pathlib import Path

from media_pilot.config import AppConfig


# Provider → 库根字段名. 不在此处定义 show — show 不区分 provider.
_MOVIE_PROVIDER_TO_DIR_ATTR: dict[str, str] = {
    "tpdb": "adult_movies_dir",
}


def resolve_library_root(
    config: AppConfig,
    *,
    media_type: str,
    provider: str | None = None,
) -> Path:
    """根据元数据档案解析库根.

    Args:
        config: AppConfig
        media_type: 媒体类型 "movie" / "show" (其它值按 movie 兜底).
        provider: 元数据 provider 标识, 例如 "tmdb" / "tpdb" /
            None (provider 缺失). provider=None 视为未知 provider,
            不假设是成人影片.

    Returns:
        解析后的库根 Path.

    Raises:
        ValueError: 当 provider="tpdb" + media_type="movie" 但
            ``config.adult_movies_dir`` 未配置时. 这是 spec 明确禁止的
            隐式 fallback 语义, 应当被显式捕获.
    """
    if media_type == "show":
        return config.shows_dir

    # media_type == "movie" (或兜底, 包括未识别的 media_type)
    dir_attr = _MOVIE_PROVIDER_TO_DIR_ATTR.get(provider or "", "movies_dir")
    root = getattr(config, dir_attr, None)
    if root is None:
        # tpdb 命中 adult_movies_dir 但未配置 → 显式报错, 不静默 fallback
        if dir_attr == "adult_movies_dir":
            raise ValueError(
                "adult_movies_dir is not configured; cannot resolve library "
                "root for tpdb adult movie. Set MEDIA_PILOT_ADULT_MOVIES_DIR "
                "explicitly (it may equal MEDIA_PILOT_MOVIES_DIR if you "
                "intentionally want the same root)."
            )
        # 兜底: movies_dir 必填字段, 缺失理论上不会发生 (validate_startup_config
        # 守住). 抛 KeyError 比 NoneType 错误更可读.
        return config.movies_dir
    return root


__all__ = ["resolve_library_root"]
