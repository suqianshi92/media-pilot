"""元数据配置档案注册表 — 绑定 provider、prompt、关键词构造和 writer 映射"""

from __future__ import annotations

from dataclasses import dataclass

from media_pilot.services.app_settings import AppSettingsService


@dataclass
class MetadataProfile:
    """单个元数据档案定义

    每个档案绑定：
    - provider_name: 对应的 metadata provider 标识
    - prompt_profile: LLM prompt 档案名
    - label: 中文展示名
    - library_dir_attr: AppConfig 中对应媒体库目录的属性名
    - writer_profile: 写入器档案标识（影响 NFO 字段和命名规则）
    """
    name: str
    label: str
    provider_name: str
    prompt_profile: str
    library_dir_attr: str = "movies_dir"
    writer_profile: str = "movie"


class ProfileRegistry:
    """档案注册表 — 支持按名称查找，单任务选择一个档案"""

    def __init__(self) -> None:
        self._profiles: dict[str, MetadataProfile] = {}

    def register(self, profile: MetadataProfile) -> None:
        if profile.name in self._profiles:
            raise ValueError(f"profile already registered: {profile.name}")
        self._profiles[profile.name] = profile

    def get(self, name: str) -> MetadataProfile:
        if name not in self._profiles:
            raise KeyError(name)
        return self._profiles[name]

    def list_names(self) -> list[str]:
        return list(self._profiles.keys())

    def enabled_profiles(self, settings_service: AppSettingsService) -> list[MetadataProfile]:
        """返回已启用且已注册的档案列表（按应用配置顺序）"""
        settings = settings_service.read()
        result: list[MetadataProfile] = []
        for name in settings.enabled_metadata_profiles:
            try:
                result.append(self.get(name))
            except KeyError:
                continue
        return result


# 全局单例
_profile_registry = ProfileRegistry()


def get_profile_registry() -> ProfileRegistry:
    return _profile_registry


def register_builtin_profiles() -> None:
    """注册内置档案"""
    if _profile_registry.list_names():
        return  # 已注册，幂等
    _profile_registry.register(MetadataProfile(
        name="tmdb_movie",
        label="TMDB 电影",
        provider_name="tmdb",
        prompt_profile="tmdb_movie",
        library_dir_attr="movies_dir",
        writer_profile="movie",
    ))
    _profile_registry.register(MetadataProfile(
        name="tpdb_adult_movie",
        label="TPDB 成人影片",
        provider_name="tpdb",
        prompt_profile="tpdb_adult_movie",
        # route-adult-movie-library-root: 成人影片库根独立于常规电影库根,
        # 解析逻辑见 services.library_root_resolver.resolve_library_root.
        library_dir_attr="adult_movies_dir",
        writer_profile="movie",
    ))
    _profile_registry.register(MetadataProfile(
        name="tmdb_show",
        label="TMDB 剧集",
        provider_name="tmdb",
        prompt_profile="tmdb_show",
        library_dir_attr="shows_dir",
        writer_profile="show",
    ))
