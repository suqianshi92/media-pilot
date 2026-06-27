import shutil
from pathlib import Path

import httpx
from sqlalchemy import select

from media_pilot.adapters.metadata import (
    MetadataCredits,
    MetadataDetail,
    MetadataExternalIds,
    MetadataImages,
)
from media_pilot.config import AppConfig
from media_pilot.orchestration.jellyfin_movie_writer import (
    build_movie_write_plan,
    detect_movie_write_conflict,
    execute_movie_write,
    render_movie_nfo,
)
from media_pilot.repository.database import create_session_factory, initialize_database
from media_pilot.repository.models import (
    OperationRecord,
    WriteResult,
)
from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository


def test_build_movie_write_plan_uses_jellyfin_movie_directory_name(tmp_path: Path) -> None:
    detail = make_detail()
    source_path = tmp_path / "Example.Movie.2026.1080p.BluRay.mkv"

    plan = build_movie_write_plan(
        movies_dir=tmp_path / "movies",
        source_path=source_path,
        detail=detail,
        task_id="task-1",
    )

    assert plan.target_dir == (
        tmp_path / "movies" / ".media-pilot-staging" / "task-1" / "Example Movie (2026)"
    )
    assert plan.target_file == plan.target_dir / "Example Movie (2026) - 1080p BluRay.mkv"
    assert plan.nfo_path == plan.target_dir / "Example Movie (2026).nfo"


def test_build_movie_write_plan_for_bdmv_uses_index_nfo_layout(tmp_path: Path) -> None:
    detail = make_detail()
    source_path = make_bdmv_source(tmp_path / "downloads" / "Example Movie Disc")

    plan = build_movie_write_plan(
        movies_dir=tmp_path / "movies",
        source_path=source_path,
        detail=detail,
        task_id="task-bdmv",
    )

    assert plan.source_kind == "bdmv"
    assert plan.target_dir == (
        tmp_path / "movies" / ".media-pilot-staging" / "task-bdmv" / "Example Movie (2026)"
    )
    assert plan.target_file == plan.target_dir / "BDMV" / "index.bdmv"
    assert plan.final_target_file == (
        tmp_path / "movies" / "Example Movie (2026)" / "BDMV" / "index.bdmv"
    )
    assert plan.nfo_path == plan.target_dir / "BDMV" / "index.nfo"
    assert plan.poster_path == plan.target_dir / "Example Movie (2026)-poster.jpg"


def test_build_movie_write_plan_matches_expected_chinese_movie_layout(tmp_path: Path) -> None:
    detail = MetadataDetail(
        **{
            **make_detail().__dict__,
            "title": "飞驰人生2",
            "original_title": "Pegasus 2",
            "year": 2024,
        }
    )
    source_path = tmp_path / "Fei.Chi.Ren.Sheng.2.2024.2160p.WEB-DL.mkv"

    plan = build_movie_write_plan(
        movies_dir=tmp_path / "movies",
        source_path=source_path,
        detail=detail,
        task_id="task-1",
    )

    assert plan.target_dir == (
        tmp_path / "movies" / ".media-pilot-staging" / "task-1" / "飞驰人生2 (2024)"
    )
    assert plan.target_file == plan.target_dir / "飞驰人生2 (2024) - 2160p WEB-DL.mkv"
    assert plan.nfo_path == plan.target_dir / "飞驰人生2 (2024).nfo"
    assert plan.poster_path == plan.target_dir / "飞驰人生2 (2024)-poster.jpg"
    assert plan.fanart_path == plan.target_dir / "飞驰人生2 (2024)-fanart.jpg"
    assert plan.clearlogo_path == plan.target_dir / "飞驰人生2 (2024)-clearlogo.png"


def test_build_movie_write_plan_uses_identifier_for_long_adult_title(tmp_path: Path) -> None:
    detail = MetadataDetail(
        **{
            **make_detail().__dict__,
            "title": (
                "MDTM-547: I Didn't Cum! That's What She's Saying, but You're "
                "Listening in Binaural Audio Fidelity, So You Know That She's "
                "Feeling Good!! She's Trying to Breathe Deep to Prevent Herself "
                "From Cumming and She's Panting and Moaning to Orgasmic Ecstasy in"
            ),
            "original_title": "MDTM-547",
            "year": 2019,
            "payload": {"external_id": "MDTM-547"},
        }
    )
    source_path = tmp_path / "MDTM-547-C.mp4"

    plan = build_movie_write_plan(
        movies_dir=tmp_path / "adult",
        source_path=source_path,
        detail=detail,
        task_id="task-long-title",
        provider="tpdb",
    )

    assert plan.final_target_dir == tmp_path / "adult" / "MDTM-547 (2019)"
    assert plan.final_target_file.name == "MDTM-547 (2019).mp4"
    for component in (plan.final_target_dir.name, plan.final_target_file.name):
        assert len(component.encode("utf-8")) <= 180
    assert detect_movie_write_conflict(plan) is None


def test_build_movie_write_plan_truncates_long_regular_title(tmp_path: Path) -> None:
    long_title = "A Very Long Movie Title " * 20
    detail = MetadataDetail(
        **{
            **make_detail().__dict__,
            "title": long_title,
            "year": 2026,
        }
    )
    source_path = tmp_path / "Long.Movie.2026.2160p.WEB-DL.mkv"

    plan = build_movie_write_plan(
        movies_dir=tmp_path / "movies",
        source_path=source_path,
        detail=detail,
        task_id="task-long-regular",
    )

    assert plan.final_target_dir.name.endswith("(2026)")
    assert "..." in plan.final_target_dir.name
    assert len(plan.final_target_dir.name.encode("utf-8")) <= 180
    assert len(plan.final_target_file.name.encode("utf-8")) <= 180
    assert plan.final_target_file.name.endswith(".mkv")


def test_build_movie_write_plan_uses_hidden_staging_directory(tmp_path: Path) -> None:
    detail = make_detail()
    source_path = tmp_path / "downloads" / "Example.Movie.2026.1080p.BluRay.mkv"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"movie")

    plan = build_movie_write_plan(
        movies_dir=tmp_path / "movies",
        source_path=source_path,
        detail=detail,
        task_id="task-123",
    )

    expected_base = (
        tmp_path
        / "movies"
        / ".media-pilot-staging"
        / "task-123"
        / "Example Movie (2026)"
    )
    assert plan.target_dir == expected_base
    assert plan.target_file == expected_base / "Example Movie (2026) - 1080p BluRay.mkv"
    assert plan.nfo_path == expected_base / "Example Movie (2026).nfo"


def test_render_movie_nfo_includes_media_pilot_signature_and_basic_fields() -> None:
    detail = make_detail()

    xml = render_movie_nfo(detail)

    assert "<movie>" in xml
    assert "Generated by Media Pilot" in xml
    assert "<title>Example Movie</title>" in xml
    assert "<originaltitle>Example Original</originaltitle>" in xml
    assert "<year>2026</year>" in xml
    assert "<plot>Fake plot</plot>" in xml
    assert "<outline>Fake plot</outline>" in xml
    assert "<runtime>120</runtime>" in xml
    assert "<premiered>2026-01-01</premiered>" in xml
    assert "<rating>7.5</rating>" in xml
    assert "<source>Media Pilot</source>" in xml


def test_render_movie_nfo_includes_ids_taxonomy_and_people() -> None:
    detail = make_detail()

    xml = render_movie_nfo(detail)

    assert "<genre>Drama</genre>" in xml
    assert "<country>CN</country>" in xml
    assert "<studio>Media Pilot Studio</studio>" in xml
    assert "<tmdbid>12345</tmdbid>" in xml
    assert "<imdbid>tt2026000</imdbid>" in xml
    assert '<uniqueid type="tmdb" default="true">12345</uniqueid>' in xml
    assert '<uniqueid type="imdb" default="false">tt2026000</uniqueid>' in xml
    assert "<director>Director One</director>" in xml
    assert "<credits>Director One</credits>" in xml
    assert "<actor>" in xml
    assert "<name>Actor One</name>" in xml
    assert "<role>Lead</role>" in xml
    assert "<thumb>https://example.invalid/actor-one.jpg</thumb>" in xml
    assert "<profile>https://example.invalid/actor-one</profile>" in xml
    assert "<tmdbid>500</tmdbid>" in xml


def test_detect_movie_write_conflict_when_target_directory_exists(tmp_path: Path) -> None:
    detail = make_detail()
    source_path = tmp_path / "Example.Movie.2026.1080p.BluRay.mkv"
    plan = build_movie_write_plan(
        movies_dir=tmp_path / "movies",
        source_path=source_path,
        detail=detail,
        task_id="task-1",
    )
    plan.target_dir.mkdir(parents=True)

    conflict = detect_movie_write_conflict(plan)

    assert conflict == "target_dir_exists"


def test_detect_movie_write_conflict_when_final_publish_directory_exists(tmp_path: Path) -> None:
    detail = make_detail()
    source_path = tmp_path / "Example.Movie.2026.1080p.BluRay.mkv"
    plan = build_movie_write_plan(
        movies_dir=tmp_path / "movies",
        source_path=source_path,
        detail=detail,
        task_id="task-1",
    )
    final_dir = tmp_path / "movies" / "Example Movie (2026)"
    final_dir.mkdir(parents=True)

    conflict = detect_movie_write_conflict(plan)

    assert conflict == "final_target_dir_exists"


def test_detect_movie_write_conflict_when_target_file_exists(tmp_path: Path) -> None:
    detail = make_detail()
    source_path = tmp_path / "Example.Movie.2026.1080p.BluRay.mkv"
    plan = build_movie_write_plan(
        movies_dir=tmp_path / "movies",
        source_path=source_path,
        detail=detail,
        task_id="task-1",
    )
    plan.target_dir.mkdir(parents=True)
    plan.target_file.write_bytes(b"existing")

    conflict = detect_movie_write_conflict(plan)

    assert conflict == "target_file_exists"






def test_execute_movie_write_keeps_staging_when_publish_to_library_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = make_config(tmp_path)
    session_factory = create_session_factory(config)
    source_path = config.downloads_dir / "Example.Movie.2026.1080p.BluRay.mkv"
    source_path.write_bytes(b"movie-bytes")
    detail = make_detail()
    detail = MetadataDetail(
        **{
            **detail.__dict__,
            "images": MetadataImages(
                poster_url="https://img.test/poster.jpg",
                backdrop_url=None,
                logo_url=None,
            ),
        }
    )
    client = make_http_client(
        {"https://img.test/poster.jpg": httpx.Response(200, content=b"poster")}
    )

    original_move = shutil.move

    def failing_move(src: str, dst: str):
        if ".media-pilot-staging" in src:
            raise OSError("publish_failed")
        return original_move(src, dst)

    monkeypatch.setattr("media_pilot.orchestration.jellyfin_movie_writer.shutil.move", failing_move)

    with session_factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path=str(source_path),
                status="confirmed",
                current_step="confirmed",
                media_type="movie",
            )
        )
        plan = build_movie_write_plan(
            movies_dir=config.movies_dir,
            source_path=source_path,
            detail=detail,
            task_id=task.id,
        )

        result = execute_movie_write(
            session,
            task_id=task.id,
            source_path=source_path,
            detail=detail,
            plan=plan,
            client=client,
        )
        session.commit()

        write_result = session.scalars(select(WriteResult)).one()
        operations = session.scalars(
            select(OperationRecord).order_by(OperationRecord.created_at)
        ).all()

    assert result.status == "failed"
    assert source_path.read_bytes() == b"movie-bytes"
    assert plan.target_dir.exists()
    assert plan.target_file.exists()
    assert not plan.final_target_dir.exists()
    assert write_result.status == "failed"
    assert write_result.payload["failure_reason"] == "publish_to_library_failed"
    assert operations[-1].operation_type == "publish_to_library"
    assert operations[-1].status == "failed"
    assert operations[-1].details["error_message"] == "publish_failed"


def test_execute_movie_write_reports_progress_steps_in_order(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    session_factory = create_session_factory(config)
    source_path = config.downloads_dir / "Example.Movie.2026.1080p.BluRay.mkv"
    source_path.write_bytes(b"movie-bytes")
    detail = make_detail()
    detail = MetadataDetail(
        **{
            **detail.__dict__,
            "images": MetadataImages(
                poster_url="https://img.test/poster.jpg",
                backdrop_url=None,
                logo_url=None,
            ),
        }
    )
    client = make_http_client(
        {"https://img.test/poster.jpg": httpx.Response(200, content=b"poster")}
    )
    progress_steps: list[str] = []

    with session_factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path=str(source_path),
                status="confirmed",
                current_step="confirmed",
                media_type="movie",
            )
        )
        plan = build_movie_write_plan(
            movies_dir=config.movies_dir,
            source_path=source_path,
            detail=detail,
            task_id=task.id,
        )

        execute_movie_write(
            session,
            task_id=task.id,
            source_path=source_path,
            detail=detail,
            plan=plan,
            client=client,
            progress_callback=progress_steps.append,
        )

    assert progress_steps == [
        "write_metadata_assets",
        "copy_to_staging",
        "publish_to_library",
    ]


def test_execute_movie_write_force_overwrite_replaces_existing_final_target_dir(
    tmp_path: Path,
) -> None:
    """force_overwrite=True 走真实 execute_movie_write 路径, 覆盖旧 final_target_dir.

    回归保护: 之前 execute_movie_write() 内部字幕循环里有局部
    ``import shutil``, 触发 Python 把 ``shutil`` 判定为函数局部变量,
    上一段 force_overwrite 分支调 ``shutil.rmtree(plan.final_target_dir)``
    时抛 UnboundLocalError. 修复后顶部 import 统一覆盖, 该路径必须
    不再抛错, 旧 final_target_dir 被覆盖, 新视频 / NFO 正常落盘.

    注意: 不 stub execute_movie_write 本身, 走真实路径才能复现
    local-vs-module-scope 冲突.
    """
    config = make_config(tmp_path)
    session_factory = create_session_factory(config)
    source_path = config.downloads_dir / "Example.Movie.2026.1080p.BluRay.mkv"
    source_path.write_bytes(b"new-movie-bytes")
    detail = MetadataDetail(
        **{
            **make_detail().__dict__,
            "images": MetadataImages(
                poster_url="https://img.test/poster.jpg",
                backdrop_url=None,
                logo_url=None,
            ),
        }
    )
    client = make_http_client(
        {"https://img.test/poster.jpg": httpx.Response(200, content=b"poster")}
    )

    with session_factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path=str(source_path),
                status="confirmed",
                current_step="confirmed",
                media_type="movie",
            )
        )
        plan = build_movie_write_plan(
            movies_dir=config.movies_dir,
            source_path=source_path,
            detail=detail,
            task_id=task.id,
        )

        # 预先创建 final_target_dir 制造 target conflict (旧内容).
        plan.final_target_dir.mkdir(parents=True)
        stale_marker = plan.final_target_dir / "stale-marker.txt"
        stale_marker.write_bytes(b"stale-bytes")

        # 真实调用: 不 stub execute_movie_write, 走完整的
        # force_overwrite 分支 → shutil.rmtree → 字幕循环 → publish.
        result = execute_movie_write(
            session,
            task_id=task.id,
            source_path=source_path,
            detail=detail,
            plan=plan,
            client=client,
            force_overwrite=True,
        )
        session.commit()

    # 1. 不抛 UnboundLocalError, 走完正常发布流程 (warning / succeeded 都算
    # 成功完成, 因为 detail 把 backdrop / logo 设为 None, 会触发 fanart /
    # clearlogo 下载失败警告 — 那是正常的, 不是 overwrite 分支的回归).
    assert result.status in {"succeeded", "warning"}
    # 2. 旧 final_target_dir 已被覆盖 (stale marker 消失).
    assert not stale_marker.exists()
    # 3. 新 final_target_dir 存在, 视频 / NFO 落盘到 final 位置.
    assert plan.final_target_dir.exists()
    assert plan.final_target_file.exists()
    assert plan.final_target_file.read_bytes() == b"new-movie-bytes"
    # 4. NFO 落到 final_target_dir 下 (publish 后 repoint 修过路径).
    nfo_in_final = plan.final_target_dir / plan.nfo_path.name
    assert nfo_in_final.exists()
    assert b"<movie>" in nfo_in_final.read_bytes()


def test_execute_movie_write_publishes_bdmv_directory_layout(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    session_factory = create_session_factory(config)
    source_path = make_bdmv_source(config.downloads_dir / "Example Movie Disc")
    # 源里已有旧 index.nfo，发布后应被生成的 NFO 覆盖。
    (source_path / "BDMV" / "index.nfo").write_text("stale nfo", encoding="utf-8")
    (source_path / "download-site.txt").write_text("do not copy", encoding="utf-8")
    detail = MetadataDetail(
        **{
            **make_detail().__dict__,
            "images": MetadataImages(
                poster_url="https://img.test/poster.jpg",
                backdrop_url=None,
                logo_url=None,
            ),
        }
    )
    client = make_http_client(
        {"https://img.test/poster.jpg": httpx.Response(200, content=b"poster")}
    )

    with session_factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path=str(source_path),
                status="confirmed",
                current_step="confirmed",
                media_type="movie",
            )
        )
        plan = build_movie_write_plan(
            movies_dir=config.movies_dir,
            source_path=source_path,
            detail=detail,
            task_id=task.id,
        )
        result = execute_movie_write(
            session,
            task_id=task.id,
            source_path=source_path,
            detail=detail,
            plan=plan,
            client=client,
        )
        session.commit()

    assert result.status in {"succeeded", "warning"}
    assert (plan.final_target_dir / "BDMV" / "index.bdmv").exists()
    assert (plan.final_target_dir / "BDMV" / "MovieObject.bdmv").exists()
    assert (plan.final_target_dir / "BDMV" / "STREAM" / "00001.m2ts").read_bytes() == b"main"
    assert (plan.final_target_dir / "CERTIFICATE" / "id.bdmv").exists()
    assert not (plan.final_target_dir / "download-site.txt").exists()
    nfo = plan.final_target_dir / "BDMV" / "index.nfo"
    assert nfo.exists()
    assert "<title>Example Movie</title>" in nfo.read_text(encoding="utf-8")
    assert "stale nfo" not in nfo.read_text(encoding="utf-8")
    assert (plan.final_target_dir / "Example Movie (2026)-poster.jpg").read_bytes() == b"poster"


def test_execute_movie_write_force_overwrite_replaces_bdmv_target_dir(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    session_factory = create_session_factory(config)
    source_path = make_bdmv_source(config.downloads_dir / "Example Movie Disc")
    detail = MetadataDetail(
        **{
            **make_detail().__dict__,
            "images": MetadataImages(
                poster_url="https://img.test/poster.jpg",
                backdrop_url=None,
                logo_url=None,
            ),
        }
    )
    client = make_http_client(
        {"https://img.test/poster.jpg": httpx.Response(200, content=b"poster")}
    )

    with session_factory() as session:
        task = IngestTaskRepository(session).create(
            IngestTaskCreate(
                source_path=str(source_path),
                status="confirmed",
                current_step="confirmed",
                media_type="movie",
            )
        )
        plan = build_movie_write_plan(
            movies_dir=config.movies_dir,
            source_path=source_path,
            detail=detail,
            task_id=task.id,
        )
        plan.final_target_dir.mkdir(parents=True)
        stale = plan.final_target_dir / "stale.txt"
        stale.write_text("stale", encoding="utf-8")

        result = execute_movie_write(
            session,
            task_id=task.id,
            source_path=source_path,
            detail=detail,
            plan=plan,
            client=client,
            force_overwrite=True,
        )
        session.commit()

    assert result.status in {"succeeded", "warning"}
    assert not stale.exists()
    assert (plan.final_target_dir / "BDMV" / "STREAM" / "00001.m2ts").exists()


def make_bdmv_source(root: Path) -> Path:
    (root / "BDMV" / "STREAM").mkdir(parents=True)
    (root / "BDMV" / "PLAYLIST").mkdir()
    (root / "BDMV" / "CLIPINF").mkdir()
    (root / "BDMV" / "index.bdmv").write_bytes(b"index")
    (root / "BDMV" / "MovieObject.bdmv").write_bytes(b"movie-object")
    (root / "BDMV" / "STREAM" / "00001.m2ts").write_bytes(b"main")
    (root / "CERTIFICATE").mkdir()
    (root / "CERTIFICATE" / "id.bdmv").write_bytes(b"cert")
    return root


def make_detail() -> MetadataDetail:
    return MetadataDetail(
        provider="tmdb",
        provider_id="12345",
        media_type="movie",
        title="Example Movie",
        original_title="Example Original",
        year=2026,
        plot="Fake plot",
        runtime_minutes=120,
        premiered="2026-01-01",
        rating=7.5,
        genres=["Drama"],
        countries=["CN"],
        studios=["Media Pilot Studio"],
        credits=MetadataCredits(
            directors=[
                make_person(
                    provider_id="100",
                    name="Director One",
                    role="Director",
                    profile_url="https://example.invalid/director-one",
                    image_url="https://example.invalid/director-one.jpg",
                )
            ],
            actors=[
                make_person(
                    provider_id="500",
                    name="Actor One",
                    role="Lead",
                    profile_url="https://example.invalid/actor-one",
                    image_url="https://example.invalid/actor-one.jpg",
                )
            ],
        ),
        external_ids=MetadataExternalIds(imdb_id="tt2026000"),
        images=MetadataImages(
            poster_url="https://example.invalid/poster.jpg",
            backdrop_url="https://example.invalid/backdrop.jpg",
            logo_url="https://example.invalid/logo.png",
        ),
    )


def make_config(root: Path) -> AppConfig:
    config = AppConfig(
downloads_dir=root / "downloads",
        watch_dir=root / "watch",
        workspace_dir=root / "workspace-root",
        movies_dir=root / "movies",
        shows_dir=root / "shows",
        database_dir=root / "db",
    )
    for directory in (
        config.downloads_dir,
            config.watch_dir,
        config.workspace_dir,
        config.movies_dir,
        config.shows_dir,
        config.database_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    initialize_database(config)
    return config


def make_http_client(routes: dict[str, httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handler))


def make_person(
    *,
    provider_id: str,
    name: str,
    role: str,
    profile_url: str,
    image_url: str,
):
    from media_pilot.adapters.metadata import MetadataPerson

    return MetadataPerson(
        provider="tmdb",
        provider_id=provider_id,
        name=name,
        role=role,
        profile_url=profile_url,
        image_url=image_url,
    )
