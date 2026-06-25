"""library_root_resolver 共享服务测试.

锁定路由规则: 由最终元数据 provider / media_type 决定库根.
- tmdb + movie → movies_dir
- tpdb + movie → adult_movies_dir
- 任何 + show → shows_dir
- 未知 provider + movie → movies_dir (兜底)

红: 当前没有该服务, 测试必失败.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from media_pilot.config import AppConfig


def _make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        adult_movies_dir=tmp_path / "library" / "adult",
    )


class TestResolveLibraryRoot:
    """2.2: 库根解析服务覆盖核心 provider / media_type 组合."""

    def test_tmdb_movie_resolves_to_movies_dir(self, tmp_path: Path) -> None:
        from media_pilot.services.library_root_resolver import resolve_library_root

        config = _make_config(tmp_path)
        result = resolve_library_root(
            config, media_type="movie", provider="tmdb",
        )
        assert result == config.movies_dir

    def test_tpdb_movie_resolves_to_adult_movies_dir(self, tmp_path: Path) -> None:
        from media_pilot.services.library_root_resolver import resolve_library_root

        config = _make_config(tmp_path)
        result = resolve_library_root(
            config, media_type="movie", provider="tpdb",
        )
        assert result == config.adult_movies_dir, (
            f"tpdb + movie 必须解析到 adult_movies_dir, got {result}"
        )

    def test_show_resolves_to_shows_dir_regardless_of_provider(
        self, tmp_path: Path,
    ) -> None:
        from media_pilot.services.library_root_resolver import resolve_library_root

        config = _make_config(tmp_path)
        # show 跟 provider 无关, 任何 provider 都解析到 shows_dir
        for provider in (None, "tmdb", "tpdb", "unknown"):
            result = resolve_library_root(
                config, media_type="show", provider=provider,
            )
            assert result == config.shows_dir, (
                f"show + provider={provider!r} 必须解析到 shows_dir, got {result}"
            )

    def test_unknown_provider_with_movie_falls_back_to_movies_dir(
        self, tmp_path: Path,
    ) -> None:
        """未知 provider + movie → movies_dir (保守兜底, 不假设是成人影片)."""
        from media_pilot.services.library_root_resolver import resolve_library_root

        config = _make_config(tmp_path)
        result = resolve_library_root(
            config, media_type="movie", provider="future_provider",
        )
        assert result == config.movies_dir

    def test_provider_none_with_movie_falls_back_to_movies_dir(
        self, tmp_path: Path,
    ) -> None:
        """provider 缺失 + movie → movies_dir (没有元数据档案时不假设分流)."""
        from media_pilot.services.library_root_resolver import resolve_library_root

        config = _make_config(tmp_path)
        result = resolve_library_root(
            config, media_type="movie", provider=None,
        )
        assert result == config.movies_dir

    def test_adult_movies_dir_none_raises_value_error(
        self, tmp_path: Path,
    ) -> None:
        """tpdb + movie 但 adult_movies_dir 未配置 → 抛 ValueError.

        不静默 fallback 到 movies_dir — 这是 spec 明确禁止的语义.
        """
        from media_pilot.services.library_root_resolver import resolve_library_root

        config = AppConfig(
            downloads_dir=tmp_path / "downloads",
            watch_dir=tmp_path / "watch",
            workspace_dir=tmp_path / "workspace",
            movies_dir=tmp_path / "library" / "movies",
            shows_dir=tmp_path / "library" / "shows",
            database_dir=tmp_path / "db",
            # adult_movies_dir=None
        )
        with pytest.raises(ValueError, match="adult_movies_dir"):
            resolve_library_root(
                config, media_type="movie", provider="tpdb",
            )
