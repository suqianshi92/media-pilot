"""Adult movie library root 安全边界回归测试.

锁定:
- 4.1 target_conflict overwrite 重建 plan 时仍用 adult_movies_dir
- 4.2 API 文件资产安全根接受 adult_movies_dir 内资产
- 4.3 source_cleanup_preflight 视 adult_movies_dir 为 protected root
  (但仍是拒绝源: 任务输入节点不能等于 protected root)
- 4.3.1 source_cleanup_preflight 不会把 adult_movies_dir 加入 allowed input roots
  (受控输入根仍只 downloads/watch/workspace)
- 4.4 delete_unpublished.preview_delete_input 受控根必须包括 adult_movies_dir
  (即删除任务输入节点 = adult_movies_dir 本身 → 拒绝)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from media_pilot.config import AppConfig


def _make_config(tmp_path: Path, *, with_adult: bool = True) -> AppConfig:
    return AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "library" / "movies",
        shows_dir=tmp_path / "library" / "shows",
        database_dir=tmp_path / "db",
        adult_movies_dir=tmp_path / "library" / "adult" if with_adult else None,
    )


# ── 4.3: source_cleanup_preflight ──


class TestSourceCleanupPreflightAdultRoot:
    def test_adult_movies_dir_is_protected_root(
        self, tmp_path: Path,
    ) -> None:
        """adult_movies_dir 必须是 protected root: 任务输入节点等于它本身 → 拒绝."""
        from media_pilot.services.source_cleanup_preflight import (
            check_source_cleanup_preflight,
        )
        from media_pilot.repository.models import IngestTask

        config = _make_config(tmp_path, with_adult=True)
        # 配齐 trash, 让预检走到"受控根检查"这一步
        from dataclasses import replace
        config = replace(config, trash_dir=tmp_path / "trash")
        config.trash_dir.mkdir(parents=True, exist_ok=True)
        config.adult_movies_dir.mkdir(parents=True, exist_ok=True)

        task = IngestTask(source_path=str(config.adult_movies_dir))
        result = check_source_cleanup_preflight(
            config=config, task=task, selection=None,
        )

        assert result.allowed is False
        assert result.reason == "refuse_protected_root", (
            f"adult_movies_dir 本身绝不能被清理, got reason={result.reason!r}"
        )

    def test_adult_movies_dir_not_added_to_allowed_input_roots(
        self, tmp_path: Path,
    ) -> None:
        """受控输入根 (allowed_source_roots) 仍只包含 downloads/watch/workspace.
        即便成人影片库根已配置, 它也不是输入根 — 任务输入节点不能
        '碰巧' 落到 adult_movies_dir 下就被允许清理."""
        from media_pilot.services.source_cleanup_preflight import (
            _allowed_source_roots,
        )
        from dataclasses import replace
        config = _make_config(tmp_path, with_adult=True)
        config = replace(config, trash_dir=tmp_path / "trash")

        allowed = _allowed_source_roots(config)

        # 解析后比较路径
        resolved_allowed = {p.resolve(strict=False) for p in allowed}
        resolved_adult = config.adult_movies_dir.resolve(strict=False)
        assert resolved_adult not in resolved_allowed, (
            f"adult_movies_dir 绝不能在受控输入根里, got allowed={resolved_allowed!r}"
        )
        # 显式列三个允许根, 防止空集合误通过
        assert resolved_allowed == {
            config.downloads_dir.resolve(strict=False),
            config.watch_dir.resolve(strict=False),
            config.workspace_dir.resolve(strict=False),
        }


# ── 4.4: delete_unpublished.preview_delete_input ──


class TestDeleteUnpublishedAdultRoot:
    def test_adult_movies_dir_blocked_from_delete_input(
        self, tmp_path: Path,
    ) -> None:
        """delete_unpublished.preview_delete_input 受控根必须包含 adult_movies_dir.

        即任务输入节点 = adult_movies_dir 本身 → 拒绝删除 (跟
        movies_dir / shows_dir 同等待遇)."""
        from media_pilot.orchestration.delete_unpublished import (
            preview_delete_input,
        )
        from media_pilot.repository.database import create_session_factory, initialize_database

        config = _make_config(tmp_path, with_adult=True)
        # 受控根 = downloads/watch/workspace/movies/shows/adult_movies_dir
        # 受控输入根 = downloads/watch  (删除任务输入节点只在这些根下)
        # 任务输入节点 = adult_movies_dir → 不在 downloads/watch 内 → 拒绝
        for d in (config.downloads_dir, config.watch_dir, config.workspace_dir,
                  config.movies_dir, config.shows_dir, config.adult_movies_dir,
                  config.database_dir):
            d.mkdir(parents=True, exist_ok=True)

        initialize_database(config)
        sf = create_session_factory(config)

        # 创建一个 task, source_path = adult_movies_dir 本身
        from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path=str(config.adult_movies_dir),
                status="agent_running",
                media_type="movie",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            preview = preview_delete_input(
                session=session, task_id=task_id, config=config,
            )

        assert preview.allowed is False, (
            f"任务输入节点 = adult_movies_dir 必须拒绝删除, got: {preview!r}"
        )
        assert "受控根目录" in preview.outcome_description or "拒绝" in preview.outcome_description


# ── 4.2: API file asset safety root ──


class TestApiFileAssetSafetyRoot:
    """API 读取文件资产时, 资产路径必须在 allowed_roots 集合内.
    adult_movies_dir 必须被接受, 不应 403.
    """

    def test_adult_movies_root_assets_are_readable(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """成人影片库根内的资产可以被 API 读取, 不应被 403 拒绝."""
        # 这条测试只验证"安全根集合里包含 adult_movies_dir", 不实际启 HTTP
        # 服务, 因为完整 fixture 太重. 我们直接读 v1.py 的 allowed_roots
        # 构造逻辑.
        # 由于 v1.py 当前是 inline ``allowed_roots = (movies_dir, shows_dir,
        # workspace_dir)``, 本测试要等到 GREEN 阶段后才会通过.
        config = _make_config(tmp_path, with_adult=True)
        for d in (config.movies_dir, config.shows_dir, config.workspace_dir,
                  config.adult_movies_dir):
            d.mkdir(parents=True, exist_ok=True)

        # 模拟一个 adult_movies_dir 内的文件
        asset_path = config.adult_movies_dir / "Some Title (2026)" / "poster.jpg"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(b"\x00\x01\x02")

        # 解析该文件, 模拟 v1.py 内的 allowed_roots 检查
        from media_pilot.api.v1 import _collect_file_asset_safety_roots
        try:
            allowed = _collect_file_asset_safety_roots(config)
        except ImportError:
            pytest.fail(
                "v1.py 应导出 _collect_file_asset_safety_roots helper, "
                "这样测试可独立验证安全根集合."
            )

        resolved_asset = asset_path.resolve(strict=False)
        is_within = any(
            resolved_asset.is_relative_to(root.resolve(strict=False))
            for root in allowed
        )
        assert is_within, (
            f"成人影片库根内资产 {resolved_asset} 必须被安全根集合接受, "
            f"got allowed={allowed!r}"
        )
