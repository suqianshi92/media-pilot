"""Task 2: 通用 select_metadata_candidate 决策服务测试.

覆盖场景:
- 后端生成 DecisionOption: payload 携带稳定 candidate_id, 不暴露路径.
- movie / show 共用: media_type 仅由后端从 MediaCandidate 写, 不让 LLM 拼接.
- prepare_select_metadata_candidate_decision: clear_winner → auto_confirm.
- prepare_select_metadata_candidate_decision: 无 winner → decision_requested.
- prepare_select_metadata_candidate_decision: 无候选 → no_candidates.
- handle_select_metadata_candidate: 有效 option_id 写入 user_decision candidate.
- handle_select_metadata_candidate: 非法 option_id / 不存在的 candidate 拒绝.
- handle_select_metadata_candidate: 同步更新 task.media_type / title / year / confidence.
- decision_reply: select_metadata_candidate 决策类型可路由, run 状态由
  waiting_user 切回 active 并续跑 Agent (mocked LLM).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

# ── helpers ──────────────────────────────────────────────────────


def _make_config(tmp_path: Path):
    from media_pilot.config.settings import AppConfig

    return AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "ws",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path,
        llm_api_key="test-key",
        llm_base_url="https://test.example.com/v1",
        llm_model="test-model",
        tmdb_api_key="test-tmdb-key",
    )


def _make_session_factory(tmp_path: Path):
    from media_pilot.repository.database import create_session_factory, initialize_database
    config = _make_config(tmp_path)
    initialize_database(config)
    return create_session_factory(config)


def _make_task(session, *, source_path: str = "/tmp/source", media_type: str | None = None):
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

    task = IngestTaskRepository(session).create(IngestTaskCreate(
        source_path=source_path,
        status="discovered",
        current_step="agent_start",
        media_type=media_type,
    ))
    session.commit()
    return task


def _add_candidate(
    session, *, task_id: str, source: str, media_type: str,
    title: str, year: int | None, external_id: str,
    confidence: float, overview: str = "",
) -> str:
    from media_pilot.repository.repositories import MediaCandidateRepository
    c = MediaCandidateRepository(session).add_candidate(
        task_id=task_id,
        source=source,
        media_type=media_type,
        title=title,
        original_title=None,
        year=year,
        external_id=external_id,
        confidence=confidence,
        reason=f"provider {source} matched",
        payload={"overview": overview} if overview else {},
    )
    session.commit()
    return c.id


@dataclass
class _DecisionShim:
    """与 AgentDecisionRequest 兼容的最小化 shim, 用于 reply handler 直接调用."""

    id: str
    task_id: str
    run_id: str
    decision_type: str
    options: list[dict] = field(default_factory=list)
    decision: dict | None = None


# ── build_candidate_options 后端生成选项 ────────────────────────


class TestBuildCandidateOptions:
    def test_options_carry_stable_candidate_id(self, tmp_path: Path):
        """payload.candidate_id 必须是 MediaCandidate.id, 不暴露路径或拼字符串."""
        sf = _make_session_factory(tmp_path)

        with sf() as session:
            task = _make_task(session)
            cid1 = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Example Movie", year=2026,
                external_id="tmdb:12345", confidence=0.95,
                overview="Movie overview",
            )
            cid2 = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Example Movie 2", year=2026,
                external_id="tmdb:67890", confidence=0.5,
            )

        with sf() as session:
            from media_pilot.repository.repositories import MediaCandidateRepository
            from media_pilot.services.select_metadata_candidate import (
                build_candidate_options,
            )
            candidates = MediaCandidateRepository(session).list_for_task(task.id)
            options = build_candidate_options(candidates)

            assert len(options) == 2
            by_id = {opt.payload["candidate_id"]: opt for opt in options}
            assert cid1 in by_id
            assert cid2 in by_id
            # option id 必须用 candidate_<id> 形式
            assert by_id[cid1].id == f"candidate_{cid1}"
            assert by_id[cid2].id == f"candidate_{cid2}"
            # payload 必须暴露 provider / provider_id / media_type / title
            # (供前端展示, 不暴露路径)
            opt1_payload = by_id[cid1].payload
            assert opt1_payload["provider"] == "tmdb"
            assert opt1_payload["provider_id"] == "tmdb:12345"
            assert opt1_payload["media_type"] == "movie"
            assert opt1_payload["title"] == "Example Movie"
            assert opt1_payload["year"] == 2026
            assert opt1_payload["confidence"] == 0.95
            assert opt1_payload["overview"] == "Movie overview"
            # 不含路径
            assert "path" not in opt1_payload
            assert "file_path" not in opt1_payload

    def test_label_includes_year_when_present(self, tmp_path: Path):
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="The Example", year=2026,
                external_id="tmdb:1", confidence=0.9,
            )

        with sf() as session:
            from media_pilot.repository.repositories import MediaCandidateRepository
            from media_pilot.services.select_metadata_candidate import (
                build_candidate_options,
            )
            options = build_candidate_options(
                MediaCandidateRepository(session).list_for_task(task.id),
            )
            assert options[0].label == "The Example (2026)"

    def test_show_candidate_includes_season_episode_in_description(
        self, tmp_path: Path,
    ):
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="show", title="Example Show", year=2024,
                external_id="tmdb:show-1", confidence=0.8,
            )

        with sf() as session:
            from media_pilot.repository.repositories import MediaCandidateRepository
            from media_pilot.services.select_metadata_candidate import (
                build_candidate_options,
            )
            options = build_candidate_options(
                MediaCandidateRepository(session).list_for_task(task.id),
            )
            # description 至少包含 media_type 标识
            assert "show" in options[0].description


# ── prepare_select_metadata_candidate_decision ─────────────────────


class TestPrepareSelectMetadataCandidateDecision:
    def test_auto_confirm_when_clear_winner(self, tmp_path: Path):
        """当 top1 confidence 显著高于 runner-up → auto_confirm, 不创建 decision."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Clear Winner", year=2026,
                external_id="tmdb:winner", confidence=0.99,
            )
            _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Loser", year=2025,
                external_id="tmdb:loser", confidence=0.5,
            )

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_AUTO_CONFIRM,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task.id,
            )
            assert result.status == STATUS_AUTO_CONFIRM
            assert result.best_candidate is not None
            assert result.best_candidate["title"] == "Clear Winner"
            assert result.best_candidate["provider_id"] == "tmdb:winner"

    def test_decision_requested_when_no_clear_winner(self, tmp_path: Path):
        """两个候选置信度接近 → 决策请求, 选项全部后端生成."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            cid_a = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Candidate A", year=2026,
                external_id="tmdb:a", confidence=0.7,
            )
            cid_b = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Candidate B", year=2026,
                external_id="tmdb:b", confidence=0.68,
            )

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_DECISION_REQUESTED,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task.id,
            )
            assert result.status == STATUS_DECISION_REQUESTED
            assert result.decision_type == "select_metadata_candidate"
            assert len(result.options) == 2
            option_cids = {opt.payload["candidate_id"] for opt in result.options}
            assert option_cids == {cid_a, cid_b}

    def test_no_candidates_returns_no_candidates(self, tmp_path: Path):
        """任务没有持久化候选 → STATUS_NO_CANDIDATES, 不创建空决策."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            from media_pilot.services.select_metadata_candidate import (
                STATUS_NO_CANDIDATES,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task.id,
            )
            assert result.status == STATUS_NO_CANDIDATES
            assert result.options == []

    def test_missing_task_returns_failed(self, tmp_path: Path):
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_EMPTY_CANDIDATES,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path),
                task_id="missing-task",
            )
            assert result.status == STATUS_EMPTY_CANDIDATES

    def test_show_candidates_also_use_universal_path(self, tmp_path: Path):
        """剧集候选走相同决策 — 决策类型不依赖 media_type."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="show", title="Show A", year=2024,
                external_id="tmdb:show-a", confidence=0.7,
            )
            _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="show", title="Show B", year=2024,
                external_id="tmdb:show-b", confidence=0.69,
            )

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_DECISION_REQUESTED,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task.id,
            )
            assert result.status == STATUS_DECISION_REQUESTED
            # 全部选项的 media_type 都是 show — 决策类型不区分媒体类型
            assert all(
                opt.payload["media_type"] == "show" for opt in result.options
            )


class TestSearchHintPersistence:
    """低置信搜索后续: 工具接受 keyword/provider/media_type,
    服务层在没有持久化候选时重新搜索并落库, 然后走决策路径."""

    def test_movie_no_candidates_researches_with_hints(self, tmp_path, monkeypatch):
        """电影 + 搜索提示 → 服务层调 search_metadata → 落库 → 决策请求."""
        from media_pilot.adapters.metadata import MetadataCandidate

        captured_kwargs: dict = {}

        def fake_search(*, config, provider_name, keyword, language_priority, media_type):
            captured_kwargs.update(
                provider_name=provider_name,
                keyword=keyword,
                media_type=media_type,
            )
            return _FakeSearchResult(candidates=[
                MetadataCandidate(
                    provider=provider_name, provider_id="tmdb:m1",
                    title="Match A", original_title=None, year=2026,
                    media_type="movie", overview="A", poster_url=None,
                    confidence=0.71, match_reason="title match",
                ),
                MetadataCandidate(
                    provider=provider_name, provider_id="tmdb:m2",
                    title="Match B", original_title=None, year=2026,
                    media_type="movie", overview="B", poster_url=None,
                    confidence=0.69, match_reason="title match",
                ),
            ])

        # search_metadata 在 _search_and_persist_candidates 内是局部
        # import, 必须 patch 源模块.
        import media_pilot.services.metadata_search as ms
        monkeypatch.setattr(ms, "search_metadata", fake_search)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            task_id = task.id

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_DECISION_REQUESTED,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task_id,
                keyword="Example Movie 2026",
                provider_name="tmdb", media_type="movie",
            )
            assert result.status == STATUS_DECISION_REQUESTED
            assert len(result.options) == 2
            # 搜索参数被透传给 search_metadata
            assert captured_kwargs["keyword"] == "Example Movie 2026"
            assert captured_kwargs["media_type"] == "movie"
            assert captured_kwargs["provider_name"] == "tmdb"
            session.commit()

        # 候选已经持久化, decision 选项里 payload.candidate_id 全部存在
        with sf() as session:
            from media_pilot.repository.repositories import (
                MediaCandidateRepository,
            )
            cands = MediaCandidateRepository(session).list_for_task(task_id)
            assert len(cands) == 2
            assert {c.external_id for c in cands} == {"tmdb:m1", "tmdb:m2"}
            assert {c.media_type for c in cands} == {"movie"}

    def test_show_no_candidates_researches_with_hints(self, tmp_path, monkeypatch):
        """剧集 + 搜索提示 → 服务层调 search_metadata → 落库 → 决策请求.
        验证 show 路径也能用同一工具 / 同一决策类型.
        """
        from media_pilot.adapters.metadata import MetadataCandidate

        def fake_search(*, config, provider_name, keyword, language_priority, media_type):
            return _FakeSearchResult(candidates=[
                MetadataCandidate(
                    provider=provider_name, provider_id="tmdb:s1",
                    title="Show A", original_title=None, year=2024,
                    media_type="show", overview="A", poster_url=None,
                    confidence=0.6, match_reason="title match",
                ),
                MetadataCandidate(
                    provider=provider_name, provider_id="tmdb:s2",
                    title="Show B", original_title=None, year=2024,
                    media_type="show", overview="B", poster_url=None,
                    confidence=0.59, match_reason="title match",
                ),
            ])

        import media_pilot.services.metadata_search as ms
        monkeypatch.setattr(ms, "search_metadata", fake_search)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, media_type="show")
            task_id = task.id

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_DECISION_REQUESTED,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task_id,
                keyword="Example Show",
                provider_name="tmdb", media_type="show",
            )
            assert result.status == STATUS_DECISION_REQUESTED
            assert len(result.options) == 2
            # 全部选项的 media_type 是 show
            assert all(
                opt.payload["media_type"] == "show" for opt in result.options
            )

    def test_research_returns_no_candidates_returns_no_candidates(
        self, tmp_path, monkeypatch,
    ):
        """搜索提示给到但 search_metadata 也没结果 → STATUS_NO_CANDIDATES."""
        def fake_search(*, config, provider_name, keyword, language_priority, media_type):
            return _FakeSearchResult(candidates=[], errors=[])

        import media_pilot.services.metadata_search as ms
        monkeypatch.setattr(ms, "search_metadata", fake_search)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            task_id = task.id

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_NO_CANDIDATES,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task_id,
                keyword="Unknown",
                provider_name="tmdb", media_type="movie",
            )
            assert result.status == STATUS_NO_CANDIDATES
            assert result.reason == "search_returned_no_candidates"

    def test_existing_candidates_ignore_research_hints(self, tmp_path, monkeypatch):
        """任务已有持久化候选 → 搜索提示被忽略, 直接用现成候选判定."""
        def fake_search(*, config, provider_name, keyword, language_priority, media_type):
            raise AssertionError(
                "search_metadata must not be called when persisted candidates exist"
            )

        import media_pilot.services.metadata_search as ms
        monkeypatch.setattr(ms, "search_metadata", fake_search)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Existing A", year=2026,
                external_id="tmdb:existing-a", confidence=0.71,
            )
            _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Existing B", year=2026,
                external_id="tmdb:existing-b", confidence=0.69,
            )

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_DECISION_REQUESTED,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task.id,
                keyword="ignored",
                provider_name="tmdb", media_type="movie",
            )
            assert result.status == STATUS_DECISION_REQUESTED
            # 仅基于现有 2 个候选, 不调用 search_metadata.
            assert len(result.options) == 2

    def test_partial_hints_no_research(self, tmp_path, monkeypatch):
        """只提供 keyword 但没给 provider/media_type → 不重搜, 返回 no_candidates."""
        def fake_search(*, config, provider_name, keyword, language_priority, media_type):
            raise AssertionError("search_metadata must not be called without full hints")

        import media_pilot.services.metadata_search as ms
        monkeypatch.setattr(ms, "search_metadata", fake_search)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            task_id = task.id

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_NO_CANDIDATES,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task_id,
                keyword="Example", provider_name=None, media_type=None,
            )
            assert result.status == STATUS_NO_CANDIDATES
            assert result.reason == "no_persisted_candidates"


# ── MP-Test-02 (Titanic) 现场: search_metadata 历史自动恢复 ──────


class TestRecoverFromSearchHistory:
    """``prepare_select_metadata_candidate_decision`` 在 LLM 没传搜索
    三件套时, 从同 run 最近一次成功 ``search_metadata`` 的
    ``AgentToolCall.output`` 恢复候选, 重新落库并走决策.

    Titanic 现场: LLM 调 ``search_metadata`` 拿到 20 候选, 工具没持久化
    (READ_ONLY), 接着调 ``prepare_select_metadata_candidate_decision``
    没传 keyword/provider/media_type → ``no_persisted_candidates`` → 累计
    tool failures → ``agent_failed``. 修复后 LLM 不必重传三件套."""

    def _inject_search_metadata_history(
        self, session, *, run_id: str, output: dict,
    ) -> None:
        """注入一条成功的 ``search_metadata`` ``AgentToolCall`` 记录,
        模拟 LLM 在同一 run 内已调过 search_metadata 工具."""
        from media_pilot.repository.repositories import (
            AgentToolCallCreate,
            AgentToolCallRepository,
        )

        tc = AgentToolCallRepository(session).create(AgentToolCallCreate(
            run_id=run_id,
            tool_name="search_metadata",
            input={"keyword": "Titanic", "provider": "tmdb", "media_type": "movie"},
        ))
        AgentToolCallRepository(session).update_status(
            tc, status="succeeded", output=output,
        )

    def test_recovers_from_same_run_search_metadata_when_args_omitted(
        self, tmp_path: Path,
    ):
        """MP-Test-02 现场: 不传 keyword/provider/media_type, 任务无
        持久化候选, 同 run 有成功 ``search_metadata`` 历史 → 服务层
        接受 ``recovered_search_results`` 直接落库, 返回 decision_requested."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            task_id = task.id

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                prepare_select_metadata_candidate_decision,
            )
            # 工具层会把 AgentToolCall.output 解析成候选 dict 列表
            # 传给 service. 这里直接调 service, 传 ``recovered_search_results``:
            recovered = [
                {
                    "provider": "tmdb", "provider_id": "movie:597",
                    "title": "Titanic", "original_title": "Titanic",
                    "year": 1997, "media_type": "movie",
                    "overview": "...", "confidence": 0.7,
                    "match_reason": "title exact",
                },
                {
                    "provider": "tmdb", "provider_id": "movie:11036",
                    "title": "Titanic (TV)", "original_title": "Titanic",
                    "year": 2012, "media_type": "movie",
                    "overview": "...", "confidence": 0.69,
                    "match_reason": "title close",
                },
            ]
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task_id,
                recovered_search_results=recovered,
            )
            # 落库成功, 走 decision_requested (2 候选, 无 clear winner)
            assert result.status == "decision_requested"
            assert len(result.options) == 2
            assert result.reason == "no_clear_winner"
            session.commit()

        # 候选已经落库
        with sf() as session:
            from media_pilot.repository.repositories import (
                MediaCandidateRepository,
            )
            cands = MediaCandidateRepository(session).list_for_task(task_id)
            assert len(cands) == 2
            assert {c.external_id for c in cands} == {"movie:597", "movie:11036"}

    def test_no_recovery_input_returns_no_persisted_candidates(
        self, tmp_path: Path,
    ):
        """无 keyword/provider/media_type 提示, 也无 recovered_search_results
        → 仍返 no_persisted_candidates, 不静默回退到空 success."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            task_id = task.id

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_NO_CANDIDATES,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task_id,
            )
            assert result.status == STATUS_NO_CANDIDATES
            assert result.reason == "no_persisted_candidates"

    def test_recovered_output_missing_candidate_fields_raises(
        self, tmp_path: Path,
    ):
        """``recovered_search_results`` 缺必要字段 → 工具返失败, 不写
        半成品 candidates. 这是 fallback safety: 恢复路径不应静默吞错."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            task_id = task.id

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_RECOVERED_OUTPUT_INVALID,
                prepare_select_metadata_candidate_decision,
            )
            # 缺 provider_id / title
            recovered = [{
                "provider": "tmdb", "media_type": "movie", "year": 2020,
            }]
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task_id,
                recovered_search_results=recovered,
            )
            assert result.status == STATUS_RECOVERED_OUTPUT_INVALID
            assert "missing" in result.reason.lower() or "recovered" in result.reason.lower()

        # 没有半成品 candidate
        with sf() as session:
            from media_pilot.repository.repositories import (
                MediaCandidateRepository,
            )
            cands = MediaCandidateRepository(session).list_for_task(task_id)
            assert cands == []

    def test_recovery_does_not_cross_run_id_boundary(
        self, tmp_path: Path,
    ):
        """同 run 无 prior search_metadata → 仍 no_persisted_candidates.

        工具层 ``_handle_prepare_select_metadata_candidate_decision`` 必须按
        ``context.run_id`` 过滤 ``AgentToolCallRepository.list_by_run``, 不
        跨 run 串数据. 本测试通过 service 路径验证: 工具层不传
        ``recovered_search_results``, 服务层无法独立恢复, 必须由调用方
        (工具层) 负责 run_id scoping. 这里验证: 旧 run 的 search_metadata
        不被误传到当前 run.
        """
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            task_id = task.id
            # 旧 run + search_metadata 历史
            old_run = _make_run(session, task_id, status="completed")
            self._inject_search_metadata_history(
                session, run_id=old_run.id, output={
                    "candidates": [{
                        "provider": "tmdb", "provider_id": "movie:597",
                        "title": "Titanic", "original_title": "Titanic",
                        "year": 1997, "media_type": "movie",
                        "overview": "", "confidence": 0.95,
                        "match_reason": "x",
                    }],
                    "keyword": "Titanic", "provider": "tmdb",
                    "has_clear_winner": True,
                },
            )
            # 当前 active run — 没有 search_metadata
            current_run = _make_run(session, task_id, status="active")
            session.commit()
            current_run_id = current_run.id

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_NO_CANDIDATES,
                prepare_select_metadata_candidate_decision,
            )
            # 工具层用 current_run_id 调 list_by_run — 拿不到旧 run 的历史
            # (list_by_run 按 run_id 过滤), 所以不会传 recovered_search_results
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task_id,
            )
            # 旧 run 的 search_metadata 不应被当前 run 借用
            assert result.status == STATUS_NO_CANDIDATES
            assert result.reason == "no_persisted_candidates"

    def test_existing_search_and_persist_path_unchanged(
        self, tmp_path, monkeypatch,
    ):
        """既有 ``_search_and_persist_candidates`` 路径 (keyword +
        provider_name + media_type 三件套全传) 不被影响."""
        from media_pilot.adapters.metadata import MetadataCandidate

        def fake_search(*, config, provider_name, keyword, language_priority, media_type):
            return _FakeSearchResult(candidates=[
                MetadataCandidate(
                    provider=provider_name, provider_id="tmdb:existing",
                    title="Existing", original_title=None, year=2026,
                    media_type="movie", overview="", poster_url=None,
                    confidence=0.95, match_reason="x",  # clear winner
                ),
            ])

        import media_pilot.services.metadata_search as ms
        monkeypatch.setattr(ms, "search_metadata", fake_search)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            task_id = task.id

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_AUTO_CONFIRM,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path), task_id=task_id,
                keyword="Example Movie 2026",
                provider_name="tmdb", media_type="movie",
            )
            # 单候选, clear winner → auto_confirm
            assert result.status == STATUS_AUTO_CONFIRM


# ── 工具层 (_handle_prepare_select_metadata_candidate_decision) 端到端 ─


class TestToolLayerSearchHistoryRecovery:
    """工具层走 ``_handle_prepare_select_metadata_candidate_decision``:
    当 LLM 漏传 keyword/provider/media_type 且任务无持久化候选时, 反查
    同 run 最近 ``search_metadata`` ``AgentToolCall.output``, 恢复并落库.
    """

    def _inject_search_metadata_history(
        self, session, *, run_id: str, output: dict,
    ) -> None:
        from media_pilot.repository.repositories import (
            AgentToolCallCreate,
            AgentToolCallRepository,
        )
        tc = AgentToolCallRepository(session).create(AgentToolCallCreate(
            run_id=run_id,
            tool_name="search_metadata",
            input={"keyword": "Titanic", "provider": "tmdb", "media_type": "movie"},
        ))
        AgentToolCallRepository(session).update_status(
            tc, status="succeeded", output=output,
        )

    def test_tool_recovers_search_history_when_args_omitted(
        self, tmp_path: Path,
    ):
        """Titanic 现场: 工具层 + 漏传三件套 + 同 run search_metadata 历史
        → 工具自动恢复并落库, 返回 decision_requested."""
        from media_pilot.agent.tools.base import ToolContext
        from media_pilot.agent.tools.decision import (
            _handle_prepare_select_metadata_candidate_decision,
        )
        from media_pilot.repository.repositories import (
            AgentRunCreate,
            AgentRunRepository,
            IngestTaskCreate,
            IngestTaskRepository,
            MediaCandidateRepository,
        )
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source", status="agent_running",
                current_step="agent_start", media_type="movie",
            ))
            run = AgentRunRepository(session).create(AgentRunCreate(
                task_id=task.id, current_step="agent_start",
            ))
            # 注入 search_metadata 历史
            self._inject_search_metadata_history(session, run_id=run.id, output={
                "candidates": [
                    {
                        "provider": "tmdb", "provider_id": "movie:597",
                        "title": "Titanic", "original_title": "Titanic",
                        "year": 1997, "media_type": "movie",
                        "overview": "...", "confidence": 0.7,
                        "match_reason": "title exact",
                    },
                    {
                        "provider": "tmdb", "provider_id": "movie:11036",
                        "title": "Titanic (TV)", "original_title": "Titanic",
                        "year": 2012, "media_type": "movie",
                        "overview": "...", "confidence": 0.69,
                        "match_reason": "title close",
                    },
                ],
                "keyword": "Titanic", "provider": "tmdb",
                "media_type": "movie", "has_clear_winner": False,
            })
            session.commit()
            task_id, run_id = task.id, run.id

            ctx = ToolContext(
                session=session, config=_make_config(tmp_path),
                task_id=task_id, run_id=run_id,
            )
            # LLM 漏传 keyword/provider/media_type — 全部 None
            result = _handle_prepare_select_metadata_candidate_decision(
                context=ctx, input_data={"task_id": task_id},
            )
            session.commit()
            assert result.status == "success"
            assert result.data.get("decision_requested") is True
            # 候选已落库
            cands = MediaCandidateRepository(session).list_for_task(task_id)
            assert len(cands) == 2

    def test_tool_no_history_returns_failure(self, tmp_path: Path):
        """无 search_metadata 历史 → 工具返 no_persisted_candidates 失败."""
        from media_pilot.agent.tools.base import ToolContext
        from media_pilot.agent.tools.decision import (
            _handle_prepare_select_metadata_candidate_decision,
        )
        from media_pilot.repository.repositories import (
            AgentRunCreate,
            AgentRunRepository,
            IngestTaskCreate,
            IngestTaskRepository,
        )
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source", status="agent_running",
                current_step="agent_start", media_type="movie",
            ))
            run = AgentRunRepository(session).create(AgentRunCreate(
                task_id=task.id, current_step="agent_start",
            ))
            session.commit()
            task_id, run_id = task.id, run.id

            ctx = ToolContext(
                session=session, config=_make_config(tmp_path),
                task_id=task_id, run_id=run_id,
            )
            result = _handle_prepare_select_metadata_candidate_decision(
                context=ctx, input_data={"task_id": task_id},
            )
            assert result.status == "failure"
            assert result.data.get("reason") == "no_persisted_candidates"


# ── 工具层: 真实 AgentRunner 写入 shape (status="completed" + data envelope) ──


class TestToolLayerSearchHistoryRecoveryRealRunnerShape:
    """``runner.py:614-624`` 写入 ``AgentToolCall`` 的真实 shape:

    - ``status="completed"`` (ToolResult success)
    - ``output={"status": "success", "summary": "...", "data": <tool data>}``

    ``search_metadata`` 工具的 ``data`` 字面 dict 含 ``candidates`` /
    ``keyword`` / ``provider`` / ``has_clear_winner`` /
    ``best_candidate`` / ``runner_up`` (见 ``read_only.py:297-306``).
    ``media_type`` 不在 ``data`` echo, 走 ``tc.input`` (LLM 调用时传入).

    任何 ``data.candidates`` 缺失/格式不对/为空的记录继续向前找; 找到
    search_metadata 但所有记录字段都坏 → 返
    ``recovered_output_invalid`` (不静默回退到
    ``no_persisted_candidates``)."""

    def _inject_completed_search_metadata(
        self, session, *, run_id: str,
        input_data: dict, data: dict,
    ) -> None:
        """Real runner shape: status="completed" + output 含
        status/summary/data envelope. ``data`` 是 search_metadata 工具
        ``ToolResult.data`` 字面 dict."""
        from media_pilot.repository.repositories import (
            AgentToolCallCreate,
            AgentToolCallRepository,
        )
        tc = AgentToolCallRepository(session).create(AgentToolCallCreate(
            run_id=run_id, tool_name="search_metadata",
            input=input_data,
        ))
        AgentToolCallRepository(session).update_status(
            tc, status="completed",
            output={
                "status": "success",
                "summary": (
                    f"Found {len(data.get('candidates', []))} candidates"
                ),
                "data": data,
            },
        )

    def test_recovers_using_completed_status_with_data_envelope(
        self, tmp_path: Path,
    ):
        """MP-Test-02 (Titanic) 现场真实 shape: status="completed"
        + output.data envelope. 漏传 keyword/provider/media_type →
        工具读 ``tc.output.data`` 落库, 返 decision_requested.

        这是修复后必须走通的主契约路径."""
        from media_pilot.agent.tools.base import ToolContext
        from media_pilot.agent.tools.decision import (
            _handle_prepare_select_metadata_candidate_decision,
        )
        from media_pilot.repository.repositories import (
            AgentRunCreate, AgentRunRepository,
            IngestTaskCreate, IngestTaskRepository,
            MediaCandidateRepository,
        )
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source", status="agent_running",
                current_step="agent_start", media_type="movie",
            ))
            run = AgentRunRepository(session).create(AgentRunCreate(
                task_id=task.id, current_step="agent_start",
            ))
            self._inject_completed_search_metadata(
                session, run_id=run.id,
                input_data={
                    "keyword": "Titanic", "provider": "tmdb",
                    "media_type": "movie",
                },
                data={
                    "candidates": [
                        {
                            "provider": "tmdb",
                            "provider_id": "movie:597",
                            "title": "Titanic",
                            "original_title": "Titanic",
                            "year": 1997, "media_type": "movie",
                            "overview": "Ship sinks",
                            "confidence": 0.7,
                            "match_reason": "title exact",
                        },
                        {
                            "provider": "tmdb",
                            "provider_id": "movie:11036",
                            "title": "Titanic (TV)",
                            "original_title": "Titanic",
                            "year": 2012, "media_type": "movie",
                            "overview": "TV movie",
                            "confidence": 0.69,
                            "match_reason": "title close",
                        },
                    ],
                    "keyword": "Titanic",
                    "provider": "tmdb",
                    "has_clear_winner": False,
                    "best_candidate": {
                        "provider": "tmdb", "provider_id": "movie:597",
                        "title": "Titanic", "year": 1997,
                        "confidence": 0.7,
                    },
                    "runner_up": {
                        "provider": "tmdb", "provider_id": "movie:11036",
                        "title": "Titanic (TV)", "year": 2012,
                        "confidence": 0.69,
                    },
                },
            )
            session.commit()
            task_id, run_id = task.id, run.id

            ctx = ToolContext(
                session=session, config=_make_config(tmp_path),
                task_id=task_id, run_id=run_id,
            )
            result = _handle_prepare_select_metadata_candidate_decision(
                context=ctx, input_data={"task_id": task_id},
            )
            session.commit()
            assert result.status == "success"
            assert result.data.get("decision_requested") is True
            cands = MediaCandidateRepository(session).list_for_task(task_id)
            assert len(cands) == 2
            assert {c.external_id for c in cands} == {"movie:597", "movie:11036"}
            assert {c.media_type for c in cands} == {"movie"}

    def test_returns_recovered_output_invalid_when_all_records_have_empty_data_candidates(
        self, tmp_path: Path,
    ):
        """同 run 有 search_metadata 调用但所有记录 ``data.candidates``
        都是空 list → 工具返 ``recovered_output_invalid``, 不静默回
        退到 ``no_persisted_candidates``. 避免 LLM 误以为没拉过
        候选而浪费时间重搜."""
        from media_pilot.agent.tools.base import ToolContext
        from media_pilot.agent.tools.decision import (
            _handle_prepare_select_metadata_candidate_decision,
        )
        from media_pilot.repository.repositories import (
            AgentRunCreate, AgentRunRepository,
            AgentToolCallCreate, AgentToolCallRepository,
            IngestTaskCreate, IngestTaskRepository,
            MediaCandidateRepository,
        )
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source", status="agent_running",
                current_step="agent_start", media_type="movie",
            ))
            run = AgentRunRepository(session).create(AgentRunCreate(
                task_id=task.id, current_step="agent_start",
            ))
            # 2 条 search_metadata 记录, 都 success 但 candidates 为空.
            # 倒序遍历时后者先被检查, 跳过; 前者也跳过; 两条都
            # broken → 返 recovered_output_invalid.
            for kw in ("Titanic 1997", "Titanic"):
                tc = AgentToolCallRepository(session).create(AgentToolCallCreate(
                    run_id=run.id, tool_name="search_metadata",
                    input={"keyword": kw, "provider": "tmdb",
                           "media_type": "movie"},
                ))
                AgentToolCallRepository(session).update_status(
                    tc, status="completed",
                    output={
                        "status": "success",
                        "summary": "Found 0 candidates",
                        "data": {
                            "candidates": [],
                            "keyword": kw,
                            "provider": "tmdb",
                            "has_clear_winner": False,
                        },
                    },
                )
            session.commit()
            task_id, run_id = task.id, run.id

            ctx = ToolContext(
                session=session, config=_make_config(tmp_path),
                task_id=task_id, run_id=run_id,
            )
            result = _handle_prepare_select_metadata_candidate_decision(
                context=ctx, input_data={"task_id": task_id},
            )
            assert result.status == "failure"
            assert result.data.get("reason") == "recovered_output_invalid"
            # 没有半成品 candidate
            cands = MediaCandidateRepository(session).list_for_task(task_id)
            assert cands == []


@dataclass
class _FakeSearchResult:
    """Compatible with services.metadata_search.MetadataSearchResult."""
    candidates: list = field(default_factory=list)
    errors: list = field(default_factory=list)


# ── handle_select_metadata_candidate ────────────────────────────


class TestHandleSelectMetadataCandidate:
    def test_records_user_decision_candidate(self, tmp_path: Path):
        """有效 option_id → 写入 user_decision MediaCandidate, 不动原 candidate."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, media_type=None)
            cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Example Movie", year=2026,
                external_id="tmdb:12345", confidence=0.7,
                overview="A movie.",
            )
            options = [{"id": f"candidate_{cid}", "label": "Example Movie (2026)",
                        "description": "movie · confidence=0.70",
                        "payload": {"candidate_id": cid}}]
            decision = _DecisionShim(
                id="dec-1", task_id=task.id, run_id="run-1",
                decision_type="select_metadata_candidate",
                options=options,
            )

        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskRepository,
                MediaCandidateRepository,
            )
            from media_pilot.services.select_metadata_candidate import (
                handle_select_metadata_candidate,
            )
            result = handle_select_metadata_candidate(
                session=session,
                config=_make_config(tmp_path),
                decision=decision,
                option_id=f"candidate_{cid}",
            )
            session.commit()
            assert result.status == "recorded"
            assert result.media_type == "movie"

            # 新 candidate 以 user_decision 落库
            all_candidates = MediaCandidateRepository(session).list_for_task(task.id)
            user_decision_candidates = [
                c for c in all_candidates if c.source == "user_decision"
            ]
            assert len(user_decision_candidates) == 1
            new_c = user_decision_candidates[0]
            assert new_c.title == "Example Movie"
            assert new_c.year == 2026
            assert new_c.external_id == "tmdb:12345"
            assert new_c.media_type == "movie"
            # payload 携带 decision_id 与源 candidate 引用
            assert new_c.payload["decision_id"] == "dec-1"
            assert new_c.payload["source_candidate_id"] == cid
            assert new_c.payload["overview"] == "A movie."

            # task.media_type / title / year 被同步更新
            task_after = IngestTaskRepository(session).get(task.id)
            assert task_after.media_type == "movie"
            assert task_after.title == "Example Movie"
            assert task_after.year == 2026
            assert task_after.confidence == 0.7

    def test_existing_user_decision_is_auto_confirmed_not_shown_again(
        self, tmp_path: Path,
    ):
        """Retry path: previous user selection is a strong fact, not a new option."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, media_type="show")
            original_id = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="show", title="Re：从零开始的异世界生活",
                year=2016, external_id="show:65942", confidence=0.54,
                overview="Show overview",
            )
            user_id = _add_candidate(
                session, task_id=task.id, source="user_decision",
                media_type="show", title="Re：从零开始的异世界生活",
                year=2016, external_id="show:65942", confidence=0.54,
                overview="Show overview",
            )
            from media_pilot.repository.repositories import MediaCandidateRepository
            user_candidate = next(
                c for c in MediaCandidateRepository(session).list_for_task(task.id)
                if c.id == user_id
            )
            user_candidate.payload = {
                "decision_id": "previous-decision",
                "source_candidate_id": original_id,
                "overview": "Show overview",
            }
            session.commit()
            task_id = task.id

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_AUTO_CONFIRM,
                prepare_select_metadata_candidate_decision,
            )

            result = prepare_select_metadata_candidate_decision(
                session=session,
                config=_make_config(tmp_path),
                task_id=task_id,
            )

            assert result.status == STATUS_AUTO_CONFIRM
            assert result.reason == "existing_user_decision"
            assert result.best_candidate == {
                "candidate_id": user_id,
                "provider": "tmdb",
                "provider_id": "show:65942",
                "media_type": "show",
                "title": "Re：从零开始的异世界生活",
                "year": 2016,
                "confidence": 0.54,
                "candidate_source": "user_decision",
            }
            assert result.options == []

    def test_invalid_option_id_returns_failed(self, tmp_path: Path):
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            decision = _DecisionShim(
                id="dec-2", task_id=task.id, run_id="run-1",
                decision_type="select_metadata_candidate",
                options=[],
            )

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                REPLY_STATUS_FAILED,
                handle_select_metadata_candidate,
            )
            result = handle_select_metadata_candidate(
                session=session, config=_make_config(tmp_path),
                decision=decision, option_id="candidate_evil",
            )
            assert result.status == REPLY_STATUS_FAILED
            assert "invalid" in result.reason or "not_found" in result.reason

    def test_unknown_candidate_id_returns_failed(self, tmp_path: Path):
        """candidate_<不存在id> → failed, 不写入任何 user_decision candidate."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            real_cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Real Movie", year=2026,
                external_id="tmdb:real", confidence=0.7,
            )
            decision = _DecisionShim(
                id="dec-3", task_id=task.id, run_id="run-1",
                decision_type="select_metadata_candidate",
                options=[{
                    "id": f"candidate_{real_cid}", "label": "...",
                    "payload": {"candidate_id": real_cid},
                }],
            )

        with sf() as session:
            from media_pilot.repository.repositories import MediaCandidateRepository
            from media_pilot.services.select_metadata_candidate import (
                handle_select_metadata_candidate,
            )
            result = handle_select_metadata_candidate(
                session=session, config=_make_config(tmp_path),
                decision=decision, option_id="candidate_does-not-exist",
            )
            assert result.status == "failed"
            # 没有新 candidate 被写入
            cands = MediaCandidateRepository(session).list_for_task(task.id)
            user_decision = [c for c in cands if c.source == "user_decision"]
            assert user_decision == []

    def test_updates_task_fields_for_show(self, tmp_path: Path):
        """剧集选择 → task.media_type = 'show', title / year 也更新."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, media_type=None)
            cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="show", title="Example Show", year=2024,
                external_id="tmdb:show-1", confidence=0.6,
            )
            decision = _DecisionShim(
                id="dec-4", task_id=task.id, run_id="run-1",
                decision_type="select_metadata_candidate",
                options=[{
                    "id": f"candidate_{cid}", "label": "...",
                    "payload": {"candidate_id": cid},
                }],
            )

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            from media_pilot.services.select_metadata_candidate import (
                handle_select_metadata_candidate,
            )
            result = handle_select_metadata_candidate(
                session=session, config=_make_config(tmp_path),
                decision=decision, option_id=f"candidate_{cid}",
            )
            session.commit()
            assert result.status == "recorded"
            assert result.media_type == "show"
            task_after = IngestTaskRepository(session).get(task.id)
            assert task_after.media_type == "show"
            assert task_after.title == "Example Show"
            assert task_after.year == 2024


# ── decision_reply 路由 ────────────────────────────────────────


class TestDecisionReplyRouting:
    def test_select_metadata_candidate_reply_continues_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """select_metadata_candidate 决策回复后走确定性 fetch + publish 路径
        (LLM 仅在 publish 失败时介入). 旧测试断言 LLM 续跑; 新行为下
        确定性 publish 工具的 fetch 失败 → task 进入 agent_failed /
        run 进入 failed (因为 fixture 不带真实 provider, 工具不工作).

        这里把确定性 fetch + publish 全部 stub 成 success, 验证:
        1. `continue_agent_run` **不应被调** (确定性 publish 已落库).
        2. run.status == "completed".
        3. task.status == "library_import_complete".
        """
        from media_pilot.agent import runner as runner_module

        def _block_continue(*args, **kwargs):
            raise AssertionError(
                "continue_agent_run should NOT be called when publish succeeds"
            )

        monkeypatch.setattr(runner_module, "continue_agent_run", _block_continue)

        # stub fetch_and_save_metadata_detail
        from media_pilot.repository.models import MetadataDetail
        from media_pilot.services import select_metadata_publish as smp_module

        def _fake_fetch(*, session, config, task_id, provider_name, provider_id, media_type):
            session.add(MetadataDetail(
                task_id=task_id, provider=provider_name,
                provider_id=provider_id, media_type=media_type,
                title="Selected Movie", original_title=None, year=2026,
                payload={},
            ))
            session.flush()
            return smp_module.FetchAndSaveDetailResult(
                status="success", summary="ok",
                provider=provider_name, provider_id=provider_id,
                title="Selected Movie", year=2026,
            )

        monkeypatch.setattr(smp_module, "fetch_and_save_metadata_detail", _fake_fetch)

        # stub registry.execute(publish_movie_to_library) → success
        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.agent.tools.base import ToolResult

        publish_calls: list[dict] = []

        def _fake_publish(ctx, inp):
            publish_calls.append(inp)
            task_repo = ctx.session
            from media_pilot.repository.repositories import IngestTaskRepository
            t = IngestTaskRepository(task_repo).get(inp["task_id"])
            if t is not None:
                t.status = "library_import_complete"
                t.current_step = "library_import_complete"
                task_repo.flush()
            return ToolResult(status="success", summary="published")

        registry_obj = type("R", (), {})()
        registry_obj._tools = {"publish_movie_to_library": None}  # placeholder
        # Use a simple stub that mimics ToolRegistry.execute signature
        class _StubRegistry:
            def __init__(self):
                self._tools = {}
            def execute(self, tool_name, context, input_data):
                if tool_name == "publish_movie_to_library":
                    return _fake_publish(context, input_data)
                return ToolResult(
                    status="failure",
                    summary=f"no stub for {tool_name}",
                )
        monkeypatch.setattr(registry_module, "get_tool_registry", lambda: _StubRegistry())
        monkeypatch.setattr(registry_module, "register_builtin_tools", lambda: None)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, media_type=None)
            cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Selected Movie", year=2026,
                external_id="tmdb:selected", confidence=0.65,
            )
            run = _make_run(session, task.id, status="waiting_user",
                            current_step="select_metadata_candidate")
            dr = _make_decision(
                session,
                run_id=run.id, task_id=task.id,
                decision_type="select_metadata_candidate",
                question="请选择元数据",
                options=[{
                    "id": f"candidate_{cid}",
                    "label": "Selected Movie (2026)",
                    "description": "movie",
                    "payload": {
                        "candidate_id": cid,
                        "provider": "tmdb",
                        "provider_id": "tmdb:selected",
                        "media_type": "movie",
                        "title": "Selected Movie",
                        "year": 2026,
                        "confidence": 0.65,
                    },
                }],
            )
            decision_id = dr.id
            run_id = run.id
            task_id = task.id

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                AgentRunRepository,
                IngestTaskRepository,
                MetadataDetailRepository,
            )
            from media_pilot.services.decision_reply import (
                ReplyInput,
                reply_to_decision,
            )

            decision = AgentDecisionRequestRepository(session).get(decision_id)
            result = reply_to_decision(
                session=session,
                config=_make_config(tmp_path),
                reply=ReplyInput(decision_id=decision_id, option_id=f"candidate_{cid}"),
            )
            session.commit()

            # run 必须切到 completed
            run_after = AgentRunRepository(session).get(decision.run_id)
            assert run_after.status == "completed"
            # task 必须切到 library_import_complete (publish 工具内部)
            task_after = IngestTaskRepository(session).get(decision.task_id)
            assert task_after.status == "library_import_complete"
            # MetadataDetail 已落库
            detail = MetadataDetailRepository(session).get_for_task(decision.task_id)
            assert detail is not None
            assert detail.title == "Selected Movie"
            # task.media_type 由候选决定
            assert task_after.media_type == "movie"
            assert task_after.title == "Selected Movie"

        # publish tool 被调一次
        assert len(publish_calls) == 1
        assert publish_calls[0]["task_id"] == task_id
        # result 状态
        assert result.status == "metadata_published"

    def test_invalid_candidate_id_raises_400(self, tmp_path: Path, monkeypatch):
        """伪造 option_id (candidate_evil) → reply handler 拒绝, 不续跑 Agent."""
        from media_pilot.agent import runner as runner_module

        seen_continue = {"called": False}

        def _block_continue(*args, **kwargs):
            seen_continue["called"] = True
            raise AssertionError("continue_agent_run should not be called")

        monkeypatch.setattr(runner_module, "continue_agent_run", _block_continue)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="X", year=2026,
                external_id="tmdb:x", confidence=0.5,
            )
            run = _make_run(session, task.id, status="waiting_user")
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_metadata_candidate",
                question="...",
                options=[{
                    "id": f"candidate_{cid}", "label": "...",
                    "payload": {"candidate_id": cid},
                }],
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput,
                reply_to_decision,
            )
            with pytest.raises(ValueError) as exc:
                reply_to_decision(
                    session=session,
                    config=_make_config(tmp_path),
                    reply=ReplyInput(
                        decision_id=decision_id,
                        option_id="candidate_evil-id-does-not-exist",
                    ),
                )
            assert exc.value.args[0]["status_code"] == 400
            assert not seen_continue["called"]

    def test_active_run_returns_409(self, tmp_path: Path, monkeypatch):
        """pending select_metadata_candidate 但 run.status=active → 409.

        不再作为确定性旁路: select_metadata_candidate 与 complex input
        决策同样要求 run.status == "waiting_user". 工具创建决策时
        已把 run 切到 waiting_user, 但若有人手工改回 active /
        调度器没同步, 拒绝回复以避免循环创建.
        """
        from media_pilot.agent import runner as runner_module

        def _block_continue(*args, **kwargs):
            raise AssertionError("continue_agent_run should not be called")

        monkeypatch.setattr(runner_module, "continue_agent_run", _block_continue)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Y", year=2026,
                external_id="tmdb:y", confidence=0.6,
            )
            # 显式保留 run 在 active 状态 (例如 reply 时机错了)
            run = _make_run(session, task.id, status="active")
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_metadata_candidate",
                question="...",
                options=[{
                    "id": f"candidate_{cid}", "label": "...",
                    "payload": {"candidate_id": cid},
                }],
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput,
                reply_to_decision,
            )
            with pytest.raises(ValueError) as exc:
                reply_to_decision(
                    session=session,
                    config=_make_config(tmp_path),
                    reply=ReplyInput(
                        decision_id=decision_id,
                        option_id=f"candidate_{cid}",
                    ),
                )
            assert exc.value.args[0]["status_code"] == 409
            assert "not waiting" in exc.value.args[0]["detail"]

    def test_completed_run_returns_409(self, tmp_path: Path, monkeypatch):
        """run.status=completed → 409 (决策已 decided, 重复回复拒绝)."""
        from media_pilot.agent import runner as runner_module

        monkeypatch.setattr(
            runner_module, "continue_agent_run",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("continue_agent_run should not be called")
            ),
        )

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session)
            cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Z", year=2026,
                external_id="tmdb:z", confidence=0.6,
            )
            run = _make_run(session, task.id, status="completed")
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_metadata_candidate",
                question="...",
                options=[{
                    "id": f"candidate_{cid}", "label": "...",
                    "payload": {"candidate_id": cid},
                }],
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput,
                reply_to_decision,
            )
            with pytest.raises(ValueError) as exc:
                reply_to_decision(
                    session=session,
                    config=_make_config(tmp_path),
                    reply=ReplyInput(
                        decision_id=decision_id,
                        option_id=f"candidate_{cid}",
                    ),
                )
            assert exc.value.args[0]["status_code"] == 409


class TestPrepareToolTransitionsRunToWaitingUser:
    """prepare_select_metadata_candidate_decision 工具创建决策时
    必须把 AgentRun 切到 waiting_user, 与 complex input 决策保持
    一致. 决策的 reply guard 依赖此状态."""

    def test_tool_transitions_run_to_waiting_user(self, tmp_path: Path):
        from media_pilot.agent.tools.base import ToolContext
        from media_pilot.agent.tools.decision import (
            _handle_prepare_select_metadata_candidate_decision,
        )
        from media_pilot.repository.repositories import (
            AgentDecisionRequestRepository,
            AgentRunCreate,
            AgentRunRepository,
            IngestTaskCreate,
            IngestTaskRepository,
        )
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source", status="agent_running",
                current_step="agent_start", media_type="movie",
            ))
            run = AgentRunRepository(session).create(AgentRunCreate(
                task_id=task.id, current_step="agent_start",
            ))
            # 注入一个低置信候选, 让工具走 decision_requested 路径.
            from media_pilot.repository.repositories import MediaCandidateRepository
            MediaCandidateRepository(session).add_candidate(
                task_id=task.id, source="tmdb", media_type="movie",
                title="Low", original_title=None, year=2026,
                external_id="tmdb:low", confidence=0.6,
                reason="low conf", payload={},
            )
            MediaCandidateRepository(session).add_candidate(
                task_id=task.id, source="tmdb", media_type="movie",
                title="Closer", original_title=None, year=2026,
                external_id="tmdb:close", confidence=0.59,
                reason="close conf", payload={},
            )
            session.commit()
            task_id, run_id = task.id, run.id

            ctx = ToolContext(
                session=session, config=_make_config(tmp_path),
                task_id=task_id, run_id=run_id,
            )
            result = _handle_prepare_select_metadata_candidate_decision(
                context=ctx, input_data={"task_id": task_id},
            )
            session.commit()

            # 工具创建了 decision
            assert result.data.get("decision_requested") is True
            # run 已经被切到 waiting_user
            run_after = AgentRunRepository(session).get(run_id)
            assert run_after.status == "waiting_user"
            # task 同步切到 waiting_user
            task_after = IngestTaskRepository(session).get(task_id)
            assert task_after.status == "waiting_user"
            # 决策存在, decision_id 来自 result.data
            decision_id = result.data["decision_id"]
            dr = AgentDecisionRequestRepository(session).get(decision_id)
            assert dr is not None
            assert dr.status == "pending"
            assert dr.decision_type == "select_metadata_candidate"


# ── helpers used by routing tests ────────────────────────────────


def _make_run(session, task_id: str, *, status: str = "waiting_user",
              current_step: str | None = None):
    from media_pilot.repository.repositories import (
        AgentRunCreate,
        AgentRunRepository,
    )

    run = AgentRunRepository(session).create(
        AgentRunCreate(task_id=task_id, current_step=current_step),
    )
    if status != "active":
        AgentRunRepository(session).update_status(
            run, status=status, current_step=current_step,
        )
    session.commit()
    return run


def _make_decision(session, *, run_id: str, task_id: str, decision_type: str,
                   question: str, options: list[dict], free_text_allowed: bool = False):
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
    )

    dr = AgentDecisionRequestRepository(session).create(
        AgentDecisionRequestCreate(
            run_id=run_id, task_id=task_id,
            decision_type=decision_type, question=question,
            options=options, free_text_allowed=free_text_allowed,
        ),
    )
    session.commit()
    return dr


@dataclass
class _FakeResult:
    run_id: str
    status: str
    message_count: int = 0
    tool_call_count: int = 0


# ── A. 预选元数据优先级 (DownloadTask.preselected_metadata_*) ─────


class TestPreselectedMetadataPriority:
    """DownloadTask 上挂的 preselected 元数据三字段都存在时, 决策层
    把 preselected 当强事实: 不向用户确认同一个元数据, 直接
    auto_confirm 一条 source='preselected' / confidence=1.0 的候选.

    背景: Issue 1 — ``task 5043c11e-...`` 在 DownloadTask 上
    已经有 preselected_metadata_provider=tmdb / external_id=movie:68735,
    Agent 重试却要求用户选择元数据, 重复劳动且增加误选风险.
    """

    def test_preselected_short_circuits_to_auto_confirm(self, tmp_path: Path):
        """movie 任务 + preselected tmdb movie:68735 → STATUS_AUTO_CONFIRM,
        best_candidate.candidate_id 是一条新建的 source='preselected' 候选,
        confidence=1.0. 不得触发 decision_requested, 即不得创建
        select_metadata_candidate 决策."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source", status="agent_running",
                current_step="agent_start", media_type="movie",
                preselected_metadata_provider="tmdb",
                preselected_metadata_external_id="movie:68735",
                preselected_metadata_profile="tmdb_movie",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            from media_pilot.repository.repositories import (
                MediaCandidateRepository,
            )
            from media_pilot.services.select_metadata_candidate import (
                STATUS_AUTO_CONFIRM,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path),
                task_id=task_id,
            )
            assert result.status == STATUS_AUTO_CONFIRM
            assert result.reason == "preselected_from_download_task"
            assert result.best_candidate is not None
            assert result.best_candidate["provider"] == "tmdb"
            assert result.best_candidate["provider_id"] == "movie:68735"
            assert result.best_candidate["media_type"] == "movie"
            assert result.best_candidate["confidence"] == 1.0

            # 决策路径没被触发, 不得创建 AgentDecisionRequest
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
            )
            drs = AgentDecisionRequestRepository(session).list_pending_by_task(task_id)
            assert drs == [], (
                "preselected 强事实旁路不得创建 select_metadata_candidate 决策; "
                f"actual={drs}"
            )

            # best_candidate.candidate_id 是一条新建的 preselected 候选
            preselected_cid = result.best_candidate["candidate_id"]
            cands = MediaCandidateRepository(session).list_for_task(task_id)
            preselected = [c for c in cands if c.id == preselected_cid]
            assert len(preselected) == 1
            assert preselected[0].source == "preselected"
            assert preselected[0].external_id == "movie:68735"
            assert preselected[0].media_type == "movie"
            assert preselected[0].confidence == 1.0

    def test_preselected_upgrades_existing_candidate_confidence(
        self, tmp_path: Path,
    ):
        """已有同 external_id 的 tmdb 候选 (来自 search_metadata) →
        复用它, confidence 升到 1.0, 不创建重复候选. 复用既有
        candidate_id, 后续 fetch / publish 工具走同一条事实."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
                MediaCandidateRepository,
            )
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source", status="agent_running",
                current_step="agent_start", media_type="movie",
                preselected_metadata_provider="tmdb",
                preselected_metadata_external_id="movie:68735",
                preselected_metadata_profile="tmdb_movie",
            ))
            existing = MediaCandidateRepository(session).add_candidate(
                task_id=task.id, source="tmdb", media_type="movie",
                title="Some Title", original_title=None, year=2024,
                external_id="movie:68735", confidence=0.7,
                reason="search match", payload={},
            )
            session.commit()
            existing_id = existing.id
            task_id = task.id

        with sf() as session:
            from media_pilot.repository.repositories import (
                MediaCandidateRepository,
            )
            from media_pilot.services.select_metadata_candidate import (
                STATUS_AUTO_CONFIRM,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path),
                task_id=task_id,
            )
            assert result.status == STATUS_AUTO_CONFIRM
            assert result.best_candidate["candidate_id"] == existing_id
            # confidence 已升级到 1.0
            assert result.best_candidate["confidence"] == 1.0

            cands = MediaCandidateRepository(session).list_for_task(task_id)
            assert len(cands) == 1
            assert cands[0].id == existing_id
            assert cands[0].confidence == 1.0

    def test_preselected_missing_provider_does_not_short_circuit(
        self, tmp_path: Path,
    ):
        """三字段没全 (例如没传 provider) → 走常规路径, 不视为 preselected."""
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source", status="agent_running",
                current_step="agent_start", media_type="movie",
                preselected_metadata_provider=None,
                preselected_metadata_external_id="movie:68735",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_NO_CANDIDATES,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path),
                task_id=task_id,
            )
            # 走常规路径, 没候选 → no_candidates
            assert result.status == STATUS_NO_CANDIDATES

    def test_preselected_summary_does_not_contain_none(
        self, tmp_path: Path, monkeypatch,
    ):
        """regression: preselected 强事实旁路 + 持久化候选空 + fetch_and_
        save_metadata_detail 失败 → 必须回退到 task.title / task.year,
        best_candidate 的 title / year 字段不得是字面量 None, summary
        / 工具返回不得出现 "Auto-confirm candidate: None" 这样的字
        符串 (该字符串会让 LLM 误以为元数据缺失).

        历史现场: task 1514489f (Titanic, tmdb movie:597) 在
        preselected 命中后 best_candidate.title=None, year=None,
        让 LLM 收到 "Auto-confirm candidate: None" 摘要, 误判元
        数据不全又调 search_metadata 浪费 step."""
        sf = _make_session_factory(tmp_path)
        config = _make_config(tmp_path)

        # 模拟 fetch_and_save_metadata_detail 失败 (e.g. provider
        # network error). 实际生产现场这种情况会导致 best_candidate
        # title/year 是 None, 修复前会出 "Auto-confirm candidate: None".
        from media_pilot.services import auto_ingest as auto_ingest_module
        from media_pilot.services.auto_ingest import FetchAndSaveDetailResult

        def _fake_fetch_fail(*, session, config, task_id, provider_name,
                             provider_id, media_type):
            return FetchAndSaveDetailResult(
                status="failure",
                summary="Provider error: network timeout",
                provider=provider_name,
                provider_id=provider_id,
            )

        monkeypatch.setattr(
            auto_ingest_module, "fetch_and_save_metadata_detail",
            _fake_fetch_fail,
        )

        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
            )
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source", status="agent_running",
                current_step="agent_start", media_type="movie",
                preselected_metadata_provider="tmdb",
                preselected_metadata_external_id="movie:597",
                preselected_metadata_profile="tmdb_movie",
            ))
            # 关键: task.title / task.year 必须有值, 否则 fallback 也不
            # 解决 None 问题. 真实现场 task 总是带 title/year (来自
            # DownloadTask 的 filename 解析).
            task.title = "Titanic"
            task.year = 1997
            session.commit()
            task_id = task.id

        with sf() as session:
            from media_pilot.services.select_metadata_candidate import (
                STATUS_AUTO_CONFIRM,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=config, task_id=task_id,
            )

        # 关键断言: best_candidate.title / year 是 task 兜底值, 不是 None
        assert result.status == STATUS_AUTO_CONFIRM
        assert result.best_candidate is not None
        assert result.best_candidate["title"] == "Titanic", (
            f"preselected fetch 失败 → best_candidate.title 必须回退到 "
            f"task.title, 不得是 None. actual={result.best_candidate['title']!r}"
        )
        assert result.best_candidate["year"] == 1997, (
            f"preselected fetch 失败 → best_candidate.year 必须回退到 "
            f"task.year, 不得是 None. actual={result.best_candidate['year']!r}"
        )
        # reason / best_candidate repr 都不得出现字面量 "None"
        import json
        serialized = json.dumps({
            "reason": result.reason,
            "best_candidate": result.best_candidate,
        }, default=str)
        assert ": None" not in serialized, (
            "preselected 摘要 / best_candidate 不得含字面量 'None' 字段; "
            f"actual={serialized}"
        )

    def test_preselected_matched_candidate_syncs_task_fields(
        self, tmp_path: Path,
    ):
        """USBA-089 修复: preselected 命中 (matched existing candidate 路径) →
        task.media_type / title / year / confidence 同步回写, 后续
        draft_publish_plan 不再因 "Task has no media_type" 失败.

        复用既有同 external_id 候选, 走 _resolve_preselected_winner 的
        matched 分支. winner.title / year 来自 existing candidate.
        task 初始字段全空, 必须全部被同步.
        """
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate, IngestTaskRepository,
                MediaCandidateRepository,
            )
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source", status="agent_running",
                current_step="agent_start", media_type=None,
                preselected_metadata_provider="tmdb",
                preselected_metadata_external_id="movie:68735",
                preselected_metadata_profile="tmdb_movie",
            ))
            existing = MediaCandidateRepository(session).add_candidate(
                task_id=task.id, source="tmdb", media_type="movie",
                title="Wreck-It Ralph", original_title=None, year=2012,
                external_id="movie:68735", confidence=0.7,
                reason="search match", payload={},
            )
            session.commit()
            task_id = task.id
            existing_id = existing.id

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            from media_pilot.services.select_metadata_candidate import (
                STATUS_AUTO_CONFIRM,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path),
                task_id=task_id,
            )
            assert result.status == STATUS_AUTO_CONFIRM
            assert result.best_candidate["candidate_id"] == existing_id

            # 关键回归: task 主字段必须被同步.
            task_after = IngestTaskRepository(session).get(task_id)
            assert task_after.media_type == "movie", (
                f"preselected 命中后 task.media_type 必须同步, "
                f"actual={task_after.media_type!r}"
            )
            assert task_after.title == "Wreck-It Ralph", (
                f"task.title 必须同步, actual={task_after.title!r}"
            )
            assert task_after.year == 2012, (
                f"task.year 必须同步, actual={task_after.year!r}"
            )
            assert task_after.confidence == 1.0, (
                f"task.confidence 必须升到 1.0, actual={task_after.confidence!r}"
            )

    def test_preselected_does_not_overwrite_existing_task_fields(
        self, tmp_path: Path,
    ):
        """task 已有 media_type / title 时, preselected 不得覆盖.

        与 persist_metadata_selection / handle_select_metadata_candidate
        行为一致: 已存在的 task 字段优先, 不被后续 preselected 解析结果
        覆盖 (避免破坏用户已显式选过的元数据).
        """
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate, IngestTaskRepository,
            )
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source", status="agent_running",
                current_step="agent_start", media_type="movie",
                confidence=0.5,
                preselected_metadata_provider="tmdb",
                preselected_metadata_external_id="movie:99999",
                preselected_metadata_profile="tmdb_movie",
            ))
            task.title = "User Selected Title"
            task.year = 2020
            session.commit()
            task_id = task.id

        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            from media_pilot.services.select_metadata_candidate import (
                STATUS_AUTO_CONFIRM,
                prepare_select_metadata_candidate_decision,
            )
            result = prepare_select_metadata_candidate_decision(
                session=session, config=_make_config(tmp_path),
                task_id=task_id,
            )
            assert result.status == STATUS_AUTO_CONFIRM

            task_after = IngestTaskRepository(session).get(task_id)
            # 已有字段不被覆盖
            assert task_after.media_type == "movie"
            assert task_after.title == "User Selected Title"
            # confidence 仍走 max — 1.0 > 0.5, 应升到 1.0
            assert task_after.confidence == 1.0, (
                "preselected confidence=1.0 应与 task.confidence=0.5 取 max"
            )

    def test_preselected_readonly_path_does_not_write_task_fields(
        self, tmp_path: Path,
    ):
        """read-only 路径 (``check_eligibility``) MUST NOT 写 task 字段.

        ``_build_preselected_fact`` 是 read-only 工具的纯计算入口, 任何
        副作用都破坏 ``check_eligibility`` 的 side-effect-free 契约.
        本测试构造 preselected 三字段都存在的 task, 调 ``check_eligibility``
        (read-only 路径), 断言 task 字段不被改.
        """
        sf = _make_session_factory(tmp_path)
        # 真实目录扫描需要 downloads_dir 在 safe_roots 内, 准备一个含
        # preselected 三字段的任务 + 一个空目录, 让 check_eligibility
        # 走 preselected 旁路但不在 sample/multiple/unsafe 等门禁上阻断.
        from media_pilot.repository.repositories import (
            IngestTaskCreate, IngestTaskRepository,
        )
        config = _make_config(tmp_path)
        config.downloads_dir.mkdir(parents=True, exist_ok=True)
        source_dir = config.downloads_dir / "preselected_source"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "main.mp4").write_bytes(b"\x00" * (300 * 1024 * 1024))

        with sf() as session:
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path=str(source_dir), status="agent_running",
                current_step="agent_start", media_type=None,
                confidence=None,
                preselected_metadata_provider="tmdb",
                preselected_metadata_external_id="movie:68735",
                preselected_metadata_profile="tmdb_movie",
            ))
            # title / year 保持 None, 验证 read-only 路径不动它们.
            session.commit()
            task_id = task.id

        with sf() as session:
            from media_pilot.services.auto_ingest import check_eligibility
            result = check_eligibility(
                session=session, config=config, task_id=task_id,
            )
            # preselected 旁路被触发, 候选事实可用
            assert result.best_candidate is not None
            assert result.best_candidate["provider"] == "tmdb"

        # 关键回归: read-only 路径不写 task 字段
        with sf() as session:
            task_after = IngestTaskRepository(session).get(task_id)
            assert task_after.media_type is None, (
                f"check_eligibility 不得写 task.media_type, "
                f"actual={task_after.media_type!r}"
            )
            assert task_after.title is None
            assert task_after.year is None
            assert task_after.confidence is None


# ── B. MediaCandidate 去重 (task, source, external_id, media_type) ──


class TestMediaCandidateDedup:
    """``MediaCandidateRepository.add_candidate`` 按
    (task_id, source, external_id, media_type) 去重 — 同一 external_id
    不论 title 怎么变都收敛成一条, 升级 confidence / 补 title / 合并
    payload. 修复 Issue 2 (TMDB 同一 movie:68735 中英文两条目
    在 search_metadata 命中后写入两条候选 → has_clear_winner 失败)."""

    def test_same_external_id_collapses_to_one_row(self, tmp_path: Path):
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            from media_pilot.repository.repositories import (
                IngestTaskCreate,
                IngestTaskRepository,
                MediaCandidateRepository,
            )
            task = IngestTaskRepository(session).create(IngestTaskCreate(
                source_path="/tmp/source", status="discovered",
                current_step="agent_start", media_type="movie",
            ))
            session.commit()
            task_id = task.id

        with sf() as session:
            from media_pilot.repository.repositories import (
                MediaCandidateRepository,
            )
            repo = MediaCandidateRepository(session)
            c1 = repo.add_candidate(
                task_id=task_id, source="tmdb", media_type="movie",
                title="Movie 68735 ZH", original_title="电影", year=2024,
                external_id="movie:68735", confidence=0.5,
                reason="first pass", payload={"lang": "zh"},
            )
            c2 = repo.add_candidate(
                task_id=task_id, source="tmdb", media_type="movie",
                title="Movie 68735 EN", original_title="Movie", year=2024,
                external_id="movie:68735", confidence=0.99,
                reason="second pass", payload={"lang": "en"},
            )
            session.commit()
            # 两条 add_candidate 返回同一条 row id
            assert c1.id == c2.id
            # 数据库里也只剩一条
            cands = MediaCandidateRepository(session).list_for_task(task_id)
            assert len(cands) == 1
            # 升级 confidence 到较高值
            assert cands[0].confidence == 0.99
            # 标题按 "fill missing" 语义保留首次写入的非空值
            # (新 title 不覆盖已有非空 title, 避免 dedup 把正常数据
            # 抹掉; reason / year / payload 走同语义 fill-missing)
            assert cands[0].title == "Movie 68735 ZH"
            assert cands[0].reason == "first pass"
            # payload 用 setdefault 合并 — 原 lang=zh 保留, 新 lang=en
            # 不覆盖既有 key
            assert cands[0].payload.get("lang") == "zh"

    def test_dedup_persists_across_search_hint_replay(self, tmp_path: Path):
        """search_metadata 落库后, _search_and_persist_candidates 再次落
        同一 external_id 不应再新增 row. 这是 prepare_select_metadata_
        candidate_decision 在 LLM 提供搜索提示时实际触发的场景."""
        import media_pilot.services.metadata_search as ms
        from media_pilot.adapters.metadata import MetadataCandidate

        def fake_search(*, config, provider_name, keyword, language_priority, media_type):
            return _FakeSearchResult(candidates=[
                MetadataCandidate(
                    provider=provider_name, provider_id="movie:68735",
                    title="Movie 68735 ZH", original_title=None, year=2024,
                    media_type="movie", overview="A", poster_url=None,
                    confidence=0.5, match_reason="title",
                ),
            ])

        import unittest.mock as mock
        original_add = None
        with mock.patch.object(ms, "search_metadata", fake_search):
            sf = _make_session_factory(tmp_path)
            with sf() as session:
                from media_pilot.repository.repositories import (
                    IngestTaskCreate,
                    IngestTaskRepository,
                    MediaCandidateRepository,
                )
                task = IngestTaskRepository(session).create(IngestTaskCreate(
                    source_path="/tmp/source", status="discovered",
                    current_step="agent_start", media_type="movie",
                ))
                # 先手工落库一条
                MediaCandidateRepository(session).add_candidate(
                    task_id=task.id, source="tmdb", media_type="movie",
                    title="Movie 68735 EN", original_title=None, year=2024,
                    external_id="movie:68735", confidence=0.99,
                    reason="first", payload={},
                )
                session.commit()
                task_id = task.id

            with sf() as session:
                from media_pilot.services.select_metadata_candidate import (
                    prepare_select_metadata_candidate_decision,
                )
                # 走 search 提示路径, _search_and_persist_candidates 会
                # 再次调 add_candidate 落同一 external_id.
                prepare_select_metadata_candidate_decision(
                    session=session, config=_make_config(tmp_path),
                    task_id=task_id,
                    keyword="Movie 68735", provider_name="tmdb",
                    media_type="movie",
                )
                session.commit()

            with sf() as session:
                from media_pilot.repository.repositories import (
                    MediaCandidateRepository,
                )
                cands = MediaCandidateRepository(session).list_for_task(task_id)
                # 即使 _search_and_persist_candidates 再次 add, 仍只一条
                assert len(cands) == 1
                assert cands[0].external_id == "movie:68735"
