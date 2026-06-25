"""Profile Registry 测试 — 注册、查找和单任务单档案约束"""

import pytest


class TestProfileRegistry:
    """1.3: TMDB movie 和 TPDB adult movie 都能注册，单任务只选一个"""

    def test_register_tmdb_movie_profile(self):
        """注册 TMDB 电影档案"""
        from media_pilot.services.profile_registry import (
            MetadataProfile,
            ProfileRegistry,
        )

        registry = ProfileRegistry()
        profile = MetadataProfile(
            name="tmdb_movie",
            label="TMDB 电影",
            provider_name="tmdb",
            prompt_profile="tmdb_movie",
        )
        registry.register(profile)

        assert registry.get("tmdb_movie") is profile
        assert "tmdb_movie" in registry.list_names()

    def test_register_tpdb_adult_movie_profile(self):
        """注册 TPDB 成人影片档案"""
        from media_pilot.services.profile_registry import (
            MetadataProfile,
            ProfileRegistry,
        )

        registry = ProfileRegistry()
        profile = MetadataProfile(
            name="tpdb_adult_movie",
            label="TPDB 成人影片",
            provider_name="tpdb",
            prompt_profile="tpdb_adult_movie",
        )
        registry.register(profile)

        assert registry.get("tpdb_adult_movie") is profile
        assert "tpdb_adult_movie" in registry.list_names()

    def test_register_both_profiles(self):
        """两个档案共存，各自独立"""
        from media_pilot.services.profile_registry import (
            MetadataProfile,
            ProfileRegistry,
        )

        registry = ProfileRegistry()
        tmdb = MetadataProfile(
            name="tmdb_movie",
            label="TMDB 电影",
            provider_name="tmdb",
            prompt_profile="tmdb_movie",
        )
        tpdb = MetadataProfile(
            name="tpdb_adult_movie",
            label="TPDB 成人影片",
            provider_name="tpdb",
            prompt_profile="tpdb_adult_movie",
        )
        registry.register(tmdb)
        registry.register(tpdb)

        names = registry.list_names()
        assert "tmdb_movie" in names
        assert "tpdb_adult_movie" in names
        assert len(names) == 2

    def test_single_task_selects_one_profile(self):
        """单任务只能选一个档案——通过 selected_profile 字段表示"""
        from media_pilot.services.profile_registry import (
            MetadataProfile,
            ProfileRegistry,
        )

        registry = ProfileRegistry()
        registry.register(MetadataProfile(
            name="tmdb_movie", label="TMDB 电影",
            provider_name="tmdb", prompt_profile="tmdb_movie",
        ))
        registry.register(MetadataProfile(
            name="tpdb_adult_movie", label="TPDB 成人影片",
            provider_name="tpdb", prompt_profile="tpdb_adult_movie",
        ))

        # 模拟任务选择档案：按启用顺序优先
        enabled = ["tmdb_movie", "tpdb_adult_movie"]
        selected = enabled[0]  # 按顺序
        profile = registry.get(selected)
        assert profile is not None
        assert profile.name == "tmdb_movie"

        # 切换选择
        selected = enabled[1]
        profile = registry.get(selected)
        assert profile.name == "tpdb_adult_movie"

    def test_get_nonexistent_profile_raises(self):
        """获取不存在的档案应抛出异常"""
        from media_pilot.services.profile_registry import ProfileRegistry

        registry = ProfileRegistry()
        with pytest.raises(KeyError):
            registry.get("nonexistent")

    def test_duplicate_register_raises(self):
        """重复注册同名档案应报错"""
        from media_pilot.services.profile_registry import (
            MetadataProfile,
            ProfileRegistry,
        )

        registry = ProfileRegistry()
        registry.register(MetadataProfile(
            name="tmdb_movie", label="TMDB 电影",
            provider_name="tmdb", prompt_profile="tmdb_movie",
        ))
        with pytest.raises(ValueError, match="already registered"):
            registry.register(MetadataProfile(
                name="tmdb_movie", label="TMDB 电影",
                provider_name="tmdb", prompt_profile="tmdb_movie",
            ))


class TestBuiltinProfilesLibraryDirAttr:
    """route-adult-movie-library-root 2.1: 内置档案的 library_dir_attr
    必须显式指向正确的库根字段.

    - tmdb_movie / tmdb_show: movies_dir / shows_dir
    - tpdb_adult_movie: adult_movies_dir (新分流)
    """

    def test_builtin_tpdb_adult_movie_uses_adult_movies_dir_attr(self):
        from media_pilot.services.profile_registry import get_profile_registry
        registry = get_profile_registry()
        # registry 进程内单例, 但内置档案已 register_builtin_profiles 注册过.
        # 防重复注册, 直接查询.
        try:
            profile = registry.get("tpdb_adult_movie")
        except KeyError:
            from media_pilot.services.profile_registry import register_builtin_profiles
            register_builtin_profiles()
            profile = registry.get("tpdb_adult_movie")

        assert profile.library_dir_attr == "adult_movies_dir", (
            f"tpdb_adult_movie 必须绑定 adult_movies_dir, got {profile.library_dir_attr!r}. "
            "漏改会让成人影片发布到 movies_dir, 跟设计目标冲突."
        )

    def test_builtin_tmdb_movie_uses_movies_dir_attr(self):
        from media_pilot.services.profile_registry import get_profile_registry
        registry = get_profile_registry()
        try:
            profile = registry.get("tmdb_movie")
        except KeyError:
            from media_pilot.services.profile_registry import register_builtin_profiles
            register_builtin_profiles()
            profile = registry.get("tmdb_movie")
        assert profile.library_dir_attr == "movies_dir"

    def test_builtin_tmdb_show_uses_shows_dir_attr(self):
        from media_pilot.services.profile_registry import get_profile_registry
        registry = get_profile_registry()
        try:
            profile = registry.get("tmdb_show")
        except KeyError:
            from media_pilot.services.profile_registry import register_builtin_profiles
            register_builtin_profiles()
            profile = registry.get("tmdb_show")
        assert profile.library_dir_attr == "shows_dir"
