"""``decision_reply.reply_to_decision`` 集成测试.

覆盖 `select_metadata_candidate` 用户回复后, 服务层确定性 fetch +
publish (不再纯 LLM 续跑) 的新链路. 同时覆盖其它既有旁路不变.

红色: 这些测试在 `apply_user_metadata_choice` 服务和
`decision_reply.select_metadata_candidate` 分支改造**之前**必失败.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


# ── helpers (mirror test_select_metadata_candidate._make_*) ─────────


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


def _make_task(
    session, *, source_path: str = "/tmp/source", media_type: str | None = None,
    status: str = "agent_running",
):
    from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository
    task = IngestTaskRepository(session).create(IngestTaskCreate(
        source_path=source_path,
        status=status,
        current_step=status,
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


def _make_decision_option(
    *, cid: str, provider: str, provider_id: str, media_type: str,
    title: str, year: int | None, confidence: float = 0.7,
) -> dict:
    """镜像生产 `prepare_select_metadata_candidate_decision` 工具写入
    `decision.options[].payload` 的形状 — provider / provider_id /
    media_type 都在 payload 里, 决策回复服务直接消费.
    """
    return {
        "id": f"candidate_{cid}",
        "label": title,
        "description": f"{media_type} · confidence={confidence:.2f} · source={provider}",
        "payload": {
            "candidate_id": cid,
            "provider": provider,
            "provider_id": provider_id,
            "media_type": media_type,
            "title": title,
            "year": year,
            "confidence": confidence,
        },
    }


def _make_run(session, task_id: str, *, status: str = "waiting_user",
              current_step: str = "select_metadata_candidate"):
    from media_pilot.repository.repositories import AgentRunCreate, AgentRunRepository
    run = AgentRunRepository(session).create(AgentRunCreate(
        task_id=task_id, current_step=current_step,
    ))
    AgentRunRepository(session).update_status(run, status=status, current_step=current_step)
    session.commit()
    return run


def _make_decision(
    session, *, run_id: str, task_id: str, decision_type: str,
    question: str, options: list[dict],
):
    from media_pilot.repository.repositories import (
        AgentDecisionRequestCreate,
        AgentDecisionRequestRepository,
    )
    dr = AgentDecisionRequestRepository(session).create(AgentDecisionRequestCreate(
        run_id=run_id, task_id=task_id, decision_type=decision_type,
        question=question, free_text_allowed=False, options=options,
    ))
    session.commit()
    return dr


# ── 工具桩: 把 `registry.execute` / `fetch_and_save_metadata_detail`
#    替换成 deterministic stubs. 测试只关心"调用顺序 + 状态机", 不
#    关心工具内部细节. ──


@dataclass
class _FakeToolResult:
    status: str
    summary: str
    data: dict[str, Any]


class _FakeToolRegistry:
    """只暴露 ``execute`` 的 stub, 接受 ``tool_name`` → handler map."""

    def __init__(self, handlers: dict[str, Any]):
        self._handlers = handlers
        self.calls: list[dict[str, Any]] = []

    def execute(self, tool_name: str, ctx, input_data: dict):
        self.calls.append({
            "tool_name": tool_name, "input": input_data,
            "task_id": getattr(ctx, "task_id", None),
        })
        if tool_name not in self._handlers:
            return _FakeToolResult(
                status="failure", summary=f"no stub for {tool_name}",
                data={},
            )
        return self._handlers[tool_name](ctx, input_data)


# ── tests ──


class TestSelectMetadataCandidateDeterministic:
    """`select_metadata_candidate` 回复后, 服务层确定性推进
    (fetch + publish), 不再纯 LLM 续跑."""

    def test_movie_candidate_publish_success_marks_library_import_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        from media_pilot.agent import runner as runner_module

        seen_continue = {"called": False}

        def _block_continue(*args, **kwargs):
            seen_continue["called"] = True
            raise AssertionError(
                "continue_agent_run should NOT be called on publish success"
            )

        monkeypatch.setattr(runner_module, "continue_agent_run", _block_continue)

        # stub fetch_and_save_metadata_detail — 写 MetadataDetail
        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.repository.models import MetadataDetail
        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.services import select_metadata_publish as smp_module

        def _fake_fetch(*, session, config, task_id, provider_name, provider_id, media_type):
            session.add(MetadataDetail(
                task_id=task_id, provider=provider_name,
                provider_id=provider_id, media_type=media_type,
                title="Selected Movie", original_title=None, year=2026,
                payload={"overview": "..."},
            ))
            session.flush()
            return smp_module.FetchAndSaveDetailResult(
                status="success", summary="ok", provider=provider_name,
                provider_id=provider_id, title="Selected Movie", year=2026,
            )

        monkeypatch.setattr(smp_module, "fetch_and_save_metadata_detail", _fake_fetch)

        # stub registry.execute(publish_movie_to_library) → success
        publish_calls: list[dict] = []
        registry = _FakeToolRegistry({
            "publish_movie_to_library": lambda ctx, inp: (
                publish_calls.append(inp) or
                _FakeToolResult(status="success", summary="published", data={})
            ),
        })
        monkeypatch.setattr(registry_module, "get_tool_registry", lambda: registry)
        monkeypatch.setattr(registry_module, "register_builtin_tools", lambda: None)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(
                session, source_path="/tmp/movie.mkv", media_type="movie",
            )
            cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="Selected Movie", year=2026,
                external_id="movie:68735", confidence=0.7,
            )
            run = _make_run(session, task.id)
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_metadata_candidate",
                question="...",
                options=[_make_decision_option(
                    cid=cid, provider="tmdb", provider_id="movie:68735",
                    media_type="movie", title="Selected Movie", year=2026,
                )],
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

            result = reply_to_decision(
                session=session,
                config=_make_config(tmp_path),
                reply=ReplyInput(decision_id=decision_id, option_id=f"candidate_{cid}"),
            )
            session.commit()

        # LLM 不应被调
        assert not seen_continue["called"]
        # publish tool 应被调一次
        assert len(publish_calls) == 1
        assert publish_calls[0]["task_id"] == task_id
        # result 状态应是已发布 (具体字符串见服务定义, 这里只断言非 LLM 续跑)
        assert result.status not in ("continued",)

        with sf() as session:
            run_after = AgentRunRepository(session).get(run_id)
            assert run_after.status == "completed"
            task_after = IngestTaskRepository(session).get(task_id)
            assert task_after.status == "library_import_complete"
            detail = MetadataDetailRepository(session).get_for_task(task_id)
            assert detail is not None
            assert detail.title == "Selected Movie"

    def test_movie_candidate_publish_target_conflict_creates_decision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        from media_pilot.agent import runner as runner_module

        def _block_continue(*args, **kwargs):
            raise AssertionError("continue_agent_run should NOT be called on target_conflict")

        monkeypatch.setattr(runner_module, "continue_agent_run", _block_continue)

        from media_pilot.repository.models import MetadataDetail
        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.services import select_metadata_publish as smp_module

        def _fake_fetch(*, session, config, task_id, provider_name, provider_id, media_type):
            session.add(MetadataDetail(
                task_id=task_id, provider=provider_name,
                provider_id=provider_id, media_type=media_type,
                title="X", original_title=None, year=2026, payload={},
            ))
            session.flush()
            return smp_module.FetchAndSaveDetailResult(
                status="success", summary="ok", provider=provider_name,
                provider_id=provider_id, title="X", year=2026,
            )

        monkeypatch.setattr(smp_module, "fetch_and_save_metadata_detail", _fake_fetch)

        # publish 返回 target_conflict
        registry = _FakeToolRegistry({
            "publish_movie_to_library": lambda ctx, inp: _FakeToolResult(
                status="failure",
                summary="target conflict",
                data={"requires_user": True, "reason": "target_conflict"},
            ),
        })
        monkeypatch.setattr(registry_module, "get_tool_registry", lambda: registry)
        monkeypatch.setattr(registry_module, "register_builtin_tools", lambda: None)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, source_path="/tmp/movie.mkv", media_type="movie")
            cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="X", year=2026,
                external_id="movie:68735", confidence=0.7,
            )
            run = _make_run(session, task.id)
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_metadata_candidate",
                question="...",
                options=[_make_decision_option(
                    cid=cid, provider="tmdb", provider_id="movie:68735",
                    media_type="movie", title="X", year=2026,
                )],
            )
            decision_id = dr.id
            run_id = run.id
            task_id = task.id

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                AgentRunRepository,
                IngestTaskRepository,
            )
            from media_pilot.services.decision_reply import (
                ReplyInput,
                reply_to_decision,
            )
            reply_to_decision(
                session=session,
                config=_make_config(tmp_path),
                reply=ReplyInput(decision_id=decision_id, option_id=f"candidate_{cid}"),
            )
            session.commit()

        with sf() as session:
            # run.status == completed (确定性旁路收口)
            run_after = AgentRunRepository(session).get(run_id)
            assert run_after.status == "completed"
            # 创建了 target_conflict decision
            pending = AgentDecisionRequestRepository(session).list_pending_by_task(
                task_id,
            )
            target_decisions = [
                d for d in pending if d.decision_type == "target_conflict"
            ]
            assert len(target_decisions) == 1
            # task 状态: 必须 waiting_user / target_conflict. 与新路径
            # (decision_requested=True) 对齐 — service 层
            # (decision_reply.OUTCOME_TARGET_CONFLICT 分支) 防御性
            # set task.status=waiting_user + current_step=target_conflict.
            # 不得保留 agent_running — 与"等用户选 overwrite / cancel"
            # 语义冲突, UI 会误显示"Agent处理中".
            task_after = IngestTaskRepository(session).get(task_id)
            assert task_after.status == "waiting_user", (
                f"task.status was {task_after.status!r}; expected exactly "
                f"'waiting_user' (target_conflict decision pending). "
                f"agent_running would make UI show 'Agent处理中' while "
                f"actually waiting for user overwrite/cancel."
            )
            assert task_after.current_step == "target_conflict", (
                f"task.current_step was {task_after.current_step!r}; "
                f"expected exactly 'target_conflict' so the timeline "
                f"reflects the pending decision type."
            )

    def test_show_candidate_publish_success_uses_show_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        from media_pilot.agent import runner as runner_module

        def _block_continue(*args, **kwargs):
            raise AssertionError("continue_agent_run should NOT be called on show publish success")

        monkeypatch.setattr(runner_module, "continue_agent_run", _block_continue)

        from media_pilot.repository.models import MetadataDetail
        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.services import select_metadata_publish as smp_module

        def _fake_fetch(*, session, config, task_id, provider_name, provider_id, media_type):
            session.add(MetadataDetail(
                task_id=task_id, provider=provider_name,
                provider_id=provider_id, media_type=media_type,
                title="Show", original_title=None, year=2024, payload={},
            ))
            session.flush()
            return smp_module.FetchAndSaveDetailResult(
                status="success", summary="ok", provider=provider_name,
                provider_id=provider_id, title="Show", year=2024,
            )

        monkeypatch.setattr(smp_module, "fetch_and_save_metadata_detail", _fake_fetch)

        show_calls: list[dict] = []
        registry = _FakeToolRegistry({
            "publish_show_to_library": lambda ctx, inp: (
                show_calls.append(inp) or
                _FakeToolResult(status="success", summary="published show", data={})
            ),
        })
        monkeypatch.setattr(registry_module, "get_tool_registry", lambda: registry)
        monkeypatch.setattr(registry_module, "register_builtin_tools", lambda: None)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, source_path="/tmp/show", media_type="show")
            cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="show", title="Show", year=2024,
                external_id="show:1", confidence=0.6,
            )
            run = _make_run(session, task.id)
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_metadata_candidate",
                question="...",
                options=[_make_decision_option(
                    cid=cid, provider="tmdb", provider_id="show:1",
                    media_type="show", title="Show", year=2024,
                )],
            )
            decision_id = dr.id
            run_id = run.id
            task_id = task.id

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentRunRepository, IngestTaskRepository,
            )
            from media_pilot.services.decision_reply import (
                ReplyInput,
                reply_to_decision,
            )
            reply_to_decision(
                session=session,
                config=_make_config(tmp_path),
                reply=ReplyInput(decision_id=decision_id, option_id=f"candidate_{cid}"),
            )
            session.commit()

        # show tool 应被调一次, movie tool 不应被调
        assert len(show_calls) == 1
        assert show_calls[0]["task_id"] == task_id
        assert all(c["tool_name"] != "publish_movie_to_library" for c in registry.calls)

        with sf() as session:
            run_after = AgentRunRepository(session).get(run_id)
            assert run_after.status == "completed"
            task_after = IngestTaskRepository(session).get(task_id)
            assert task_after.status == "library_import_complete"

    def test_movie_candidate_fetch_fails_marks_agent_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        from media_pilot.agent import runner as runner_module

        def _block_continue(*args, **kwargs):
            raise AssertionError("continue_agent_run should NOT be called on fetch failure")

        monkeypatch.setattr(runner_module, "continue_agent_run", _block_continue)

        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.services import select_metadata_publish as smp_module

        def _fake_fetch_failure(*, session, config, task_id, provider_name, provider_id, media_type):
            return smp_module.FetchAndSaveDetailResult(
                status="failure", summary="provider down",
                provider=provider_name, provider_id=provider_id,
            )

        monkeypatch.setattr(smp_module, "fetch_and_save_metadata_detail", _fake_fetch_failure)

        # 不应被调到
        registry = _FakeToolRegistry({})
        monkeypatch.setattr(registry_module, "get_tool_registry", lambda: registry)
        monkeypatch.setattr(registry_module, "register_builtin_tools", lambda: None)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, source_path="/tmp/movie.mkv", media_type="movie")
            cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="X", year=2026,
                external_id="movie:68735", confidence=0.7,
            )
            run = _make_run(session, task.id)
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_metadata_candidate",
                question="...",
                options=[_make_decision_option(
                    cid=cid, provider="tmdb", provider_id="movie:68735",
                    media_type="movie", title="X", year=2026,
                )],
            )
            decision_id = dr.id
            run_id = run.id
            task_id = task.id

        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput,
                reply_to_decision,
            )
            reply_to_decision(
                session=session,
                config=_make_config(tmp_path),
                reply=ReplyInput(decision_id=decision_id, option_id=f"candidate_{cid}"),
            )
            session.commit()

        # publish tool 没被调
        assert all(c["tool_name"] != "publish_movie_to_library" for c in registry.calls)

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentRunRepository, IngestTaskRepository,
            )
            run_after = AgentRunRepository(session).get(run_id)
            assert run_after.status == "failed"
            task_after = IngestTaskRepository(session).get(task_id)
            assert task_after.status == "agent_failed"
            assert task_after.failure_reason == "metadata_detail_fetch_failed"

    def test_movie_candidate_publish_other_failure_falls_back_to_llm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """publish 返 `requires_user=True, reason=非 target_conflict` (e.g.
        multiple_videos) → 走 LLM 兜底, `continue_agent_run` 应被调."""
        from media_pilot.agent import runner as runner_module

        seen_continue = {"called": False, "run_id": None}

        @dataclass
        class _FakeResult:
            run_id: str
            status: str
            message_count: int = 0
            tool_call_count: int = 0

        def _fake_continue(*, session, config, run_id, mock_llm_client=None):
            seen_continue["called"] = True
            seen_continue["run_id"] = run_id
            return _FakeResult(run_id=run_id, status="continued")

        monkeypatch.setattr(runner_module, "continue_agent_run", _fake_continue)

        from media_pilot.repository.models import MetadataDetail
        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.services import select_metadata_publish as smp_module

        def _fake_fetch(*, session, config, task_id, provider_name, provider_id, media_type):
            session.add(MetadataDetail(
                task_id=task_id, provider=provider_name,
                provider_id=provider_id, media_type=media_type,
                title="X", original_title=None, year=2026, payload={},
            ))
            session.flush()
            return smp_module.FetchAndSaveDetailResult(
                status="success", summary="ok", provider=provider_name,
                provider_id=provider_id, title="X", year=2026,
            )

        monkeypatch.setattr(smp_module, "fetch_and_save_metadata_detail", _fake_fetch)

        registry = _FakeToolRegistry({
            "publish_movie_to_library": lambda ctx, inp: _FakeToolResult(
                status="failure",
                summary="multiple videos",
                data={"requires_user": True, "reason": "multiple_videos"},
            ),
        })
        monkeypatch.setattr(registry_module, "get_tool_registry", lambda: registry)
        monkeypatch.setattr(registry_module, "register_builtin_tools", lambda: None)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(session, source_path="/tmp/movie.mkv", media_type="movie")
            cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="X", year=2026,
                external_id="movie:68735", confidence=0.7,
            )
            run = _make_run(session, task.id)
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_metadata_candidate",
                question="...",
                options=[_make_decision_option(
                    cid=cid, provider="tmdb", provider_id="movie:68735",
                    media_type="movie", title="X", year=2026,
                )],
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput,
                reply_to_decision,
            )
            reply_to_decision(
                session=session,
                config=_make_config(tmp_path),
                reply=ReplyInput(decision_id=decision_id, option_id=f"candidate_{cid}"),
            )
            session.commit()

        # LLM 兜底被调
        assert seen_continue["called"]
        assert seen_continue["run_id"] is not None

    def test_user_picks_second_candidate_uses_its_provider_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """回归: 多个候选时, 用户选的 option_id 决定 fetch 走哪个候选,
        不得 fallback 到"第一个含完整 payload 的 option".

        现场: 备选项 [A 矩阵1999, B 黑客帝国1999] 都含完整 payload, 旧
        路径 fallback 到 options 列表中第一个, 实际用户选 B → 错误地
        落 A 的 metadata_detail. 修复后 apply_user_metadata_choice 必
        须严格用 reply.option_id 匹配.
        """
        from media_pilot.agent import runner as runner_module

        def _block_continue(*args, **kwargs):
            raise AssertionError(
                "continue_agent_run should NOT be called on publish success"
            )

        monkeypatch.setattr(runner_module, "continue_agent_run", _block_continue)

        from media_pilot.repository.models import MetadataDetail
        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.services import select_metadata_publish as smp_module

        captured_fetch_params: list[dict] = []

        def _fake_fetch(*, session, config, task_id, provider_name, provider_id, media_type):
            captured_fetch_params.append({
                "task_id": task_id, "provider_name": provider_name,
                "provider_id": provider_id, "media_type": media_type,
            })
            session.add(MetadataDetail(
                task_id=task_id, provider=provider_name,
                provider_id=provider_id, media_type=media_type,
                title="Fetched From Chosen Option",
                original_title=None, year=2026, payload={},
            ))
            session.flush()
            return smp_module.FetchAndSaveDetailResult(
                status="success", summary="ok", provider=provider_name,
                provider_id=provider_id, title="Fetched From Chosen Option",
                year=2026,
            )

        monkeypatch.setattr(smp_module, "fetch_and_save_metadata_detail", _fake_fetch)

        publish_calls: list[dict] = []
        registry = _FakeToolRegistry({
            "publish_movie_to_library": lambda ctx, inp: (
                publish_calls.append(inp) or
                _FakeToolResult(status="success", summary="published", data={})
            ),
        })
        monkeypatch.setattr(registry_module, "get_tool_registry", lambda: registry)
        monkeypatch.setattr(registry_module, "register_builtin_tools", lambda: None)

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            task = _make_task(
                session, source_path="/tmp/movie.mkv", media_type="movie",
            )
            # 第一个候选 A: The Matrix (1999)
            cid_a = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="The Matrix", year=1999,
                external_id="movie:603", confidence=0.7,
            )
            # 第二个候选 B: The Matrix Reloaded (2003) — 用户选这个
            cid_b = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="The Matrix Reloaded", year=2003,
                external_id="movie:604", confidence=0.65,
            )
            run = _make_run(session, task.id)
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_metadata_candidate",
                question="...",
                options=[
                    _make_decision_option(
                        cid=cid_a, provider="tmdb", provider_id="movie:603",
                        media_type="movie", title="The Matrix", year=1999,
                    ),
                    _make_decision_option(
                        cid=cid_b, provider="tmdb", provider_id="movie:604",
                        media_type="movie", title="The Matrix Reloaded",
                        year=2003, confidence=0.65,
                    ),
                ],
            )
            decision_id = dr.id

        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput, reply_to_decision,
            )
            # 用户选 B — 必须精确匹配 cid_b 的 provider_id
            reply_to_decision(
                session=session,
                config=_make_config(tmp_path),
                reply=ReplyInput(decision_id=decision_id, option_id=f"candidate_{cid_b}"),
            )
            session.commit()

        # 关键断言: fetch 必须用第二个候选的 provider_id, 不允许 fallback 到
        # 第一个 (cid_a = movie:603) 或其它非用户选中的 option.
        assert len(captured_fetch_params) == 1
        params = captured_fetch_params[0]
        assert params["provider_id"] == "movie:604", (
            f"fetch used provider_id={params['provider_id']!r}, expected "
            f"'movie:604' (the option the user actually picked). "
            f"Fallback to first option is the bug we're guarding against."
        )
        assert params["provider_name"] == "tmdb"
        assert params["media_type"] == "movie"
        # publish tool 也应被调一次
        assert len(publish_calls) == 1

    def test_publish_tool_returns_target_conflict_via_decision_requested(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """回归: publish 工具 ``status="success"`` + ``data.decision_requested=True``
        + ``data.decision_type="target_conflict"`` (工具内部已经创建了
        AgentDecisionRequest) — 上层**不得**误判为 LIBRARY_IMPORT_COMPLETE.

        旧路径 _interpret_publish_result 先看 status=="success" 就返
        LIBRARY_IMPORT_COMPLETE, 把 target_conflict 决策当作已发布,
        任务被标 library_import_complete, 用户在右侧看不到待决的
        overwrite/cancel 选项. 修复后必须:
        - 复用工具返回的 ``data.decision_id``, 不重复创建.
        - task 状态保持 agent_running / waiting_user (有 target_conflict 待决).
        - run 状态推进到 completed (决策已被系统创建).
        - pending target_conflict decision 数 == 1.
        """
        from media_pilot.agent import runner as runner_module

        def _block_continue(*args, **kwargs):
            raise AssertionError(
                "continue_agent_run should NOT be called on target_conflict"
            )

        monkeypatch.setattr(runner_module, "continue_agent_run", _block_continue)

        from media_pilot.repository.models import MetadataDetail
        from media_pilot.agent.tools import registry as registry_module
        from media_pilot.services import select_metadata_publish as smp_module

        def _fake_fetch(*, session, config, task_id, provider_name, provider_id, media_type):
            session.add(MetadataDetail(
                task_id=task_id, provider=provider_name,
                provider_id=provider_id, media_type=media_type,
                title="X", original_title=None, year=2026, payload={},
            ))
            session.flush()
            return smp_module.FetchAndSaveDetailResult(
                status="success", summary="ok", provider=provider_name,
                provider_id=provider_id, title="X", year=2026,
            )

        monkeypatch.setattr(smp_module, "fetch_and_save_metadata_detail", _fake_fetch)

        # publish_movie_to_library 真实形态: status="success" + 工具已经
        # 创建了 AgentDecisionRequest(decision_type="target_conflict"),
        # data.decision_requested=True, decision_id 透出.
        sf = _make_session_factory(tmp_path)
        with sf() as session:
            # 先把 task / run / select_metadata_candidate 决策建出来,
            # 拿到 run_id 给后续 target_conflict 决策.
            task = _make_task(session, source_path="/tmp/movie.mkv", media_type="movie")
            cid = _add_candidate(
                session, task_id=task.id, source="tmdb",
                media_type="movie", title="X", year=2026,
                external_id="movie:68735", confidence=0.7,
            )
            run = _make_run(session, task.id)
            dr = _make_decision(
                session, run_id=run.id, task_id=task.id,
                decision_type="select_metadata_candidate",
                question="...",
                options=[_make_decision_option(
                    cid=cid, provider="tmdb", provider_id="movie:68735",
                    media_type="movie", title="X", year=2026,
                )],
            )
            # 注意: 同一 run 至多一个 pending decision, 测试 publish
            # 工具桩在第一次被调时, 模拟生产
            # `_handle_publish_movie_to_library` 的副作用: 创建
            # AgentDecisionRequest(decision_type="target_conflict"), 然后
            # 把 decision_id 通过 ToolResult.data 透出. 此时
            # select_metadata_candidate 决策已被 reply_to_decision 在更
            # 早一步 save_decision, 不冲突.
            decision_id = dr.id
            run_id = run.id
            task_id = task.id
            session.commit()

        # 测试 publish 工具在第一次被调时, 模拟生产
        # _handle_publish_movie_to_library 的副作用: 创建
        # AgentDecisionRequest(decision_type="target_conflict"), 然后
        # 把 decision_id 通过 ToolResult.data 透出. 这样上层
        # _interpret_publish_result 看到的就是 "工具已经 create 了
        # 决策" 的真实形态.
        def _fake_publish_with_target_conflict(ctx, inp):
            from media_pilot.repository.repositories import (
                AgentDecisionRequestCreate,
                AgentDecisionRequestRepository,
            )
            session = ctx.session
            target_dr_created = AgentDecisionRequestRepository(session).create(
                AgentDecisionRequestCreate(
                    run_id=ctx.run_id, task_id=inp["task_id"],
                    decision_type="target_conflict",
                    question=(
                        "目标 X (2026).mkv 已被占用(target_exists)。"
                        "请选择处理方式。"
                    ),
                    free_text_allowed=False,
                    options=[
                        {"id": "overwrite_target", "label": "覆盖发布目标"},
                        {"id": "cancel_publish", "label": "取消本次发布"},
                    ],
                    payload={
                        "final_target_dir": "/library/movies",
                        "final_target_file": "X (2026).mkv",
                        "conflict": "target_exists",
                    },
                ),
            )
            session.flush()
            return _FakeToolResult(
                status="success",
                summary=(
                    "Target conflict detected: target_exists. "
                    "Awaiting user decision."
                ),
                data={
                    "decision_requested": True,
                    "decision_id": target_dr_created.id,
                    "decision_type": "target_conflict",
                    "conflict": "target_exists",
                    "final_target_dir": "/library/movies",
                    "final_target_file": "X (2026).mkv",
                },
            )

        registry = _FakeToolRegistry({
            "publish_movie_to_library": _fake_publish_with_target_conflict,
        })
        monkeypatch.setattr(registry_module, "get_tool_registry", lambda: registry)
        monkeypatch.setattr(registry_module, "register_builtin_tools", lambda: None)

        with sf() as session:
            from media_pilot.services.decision_reply import (
                ReplyInput, reply_to_decision,
            )
            reply_to_decision(
                session=session,
                config=_make_config(tmp_path),
                reply=ReplyInput(decision_id=decision_id, option_id=f"candidate_{cid}"),
            )
            session.commit()

        with sf() as session:
            from media_pilot.repository.repositories import (
                AgentDecisionRequestRepository,
                AgentRunRepository,
                IngestTaskRepository,
            )
            # run 状态: 收口为 completed (决策已被系统创建, 不再让 LLM 续跑)
            run_after = AgentRunRepository(session).get(run_id)
            assert run_after.status == "completed"
            # task 状态: 必须 waiting_user / target_conflict. 保留
            # agent_running 会让 UI 显示"Agent处理中"与实际等用户
            # 确认的状态语义冲突. 工具侧 (_handle_publish_movie_to_
            # library) 已经 set 过, service 层防御性再 set.
            task_after = IngestTaskRepository(session).get(task_id)
            assert task_after.status == "waiting_user", (
                f"task.status was {task_after.status!r}; expected exactly "
                f"'waiting_user' (target_conflict decision pending). "
                f"agent_running would make UI show 'Agent处理中' while "
                f"actually waiting for user overwrite/cancel."
            )
            assert task_after.current_step == "target_conflict", (
                f"task.current_step was {task_after.current_step!r}; "
                f"expected exactly 'target_conflict' so the timeline "
                f"reflects the pending decision type."
            )
            # pending target_conflict decision 数 == 1 (复用工具创建的,
            # 上层不重复 create)
            pending = AgentDecisionRequestRepository(session).list_pending_by_task(
                task_id,
            )
            target_decisions = [
                d for d in pending if d.decision_type == "target_conflict"
            ]
            assert len(target_decisions) == 1, (
                f"expected exactly 1 pending target_conflict decision, "
                f"got {len(target_decisions)}."
            )
