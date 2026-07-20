from media_pilot.orchestration.state_machine import IngestTaskStatus
from media_pilot.repository.repositories import (
    AgentDecisionRequestCreate,
    AgentDecisionRequestRepository,
    AgentRunCreate,
    AgentRunRepository,
)


class TestAgentDecisionRequestRepository:
    def _seed_run_and_task(self, session):
        from media_pilot.repository.repositories import IngestTaskCreate, IngestTaskRepository

        task_repo = IngestTaskRepository(session)
        task = task_repo.create(IngestTaskCreate(
            source_path="/data/downloads/test.mkv",
            status="agent_running",
            current_step="agent_start",
        ))
        run_repo = AgentRunRepository(session)
        run = run_repo.create(AgentRunCreate(task_id=task.id))
        session.flush()
        return run, task

    def test_create_decision_request(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        run_id = task_id = None
        with sf() as session:
            run, task = self._seed_run_and_task(session)
            session.commit()
            run_id, task_id = run.id, task.id

        with sf() as session:
            repo = AgentDecisionRequestRepository(session)
            req = repo.create(AgentDecisionRequestCreate(
                run_id=run_id,
                task_id=task_id,
                decision_type="publish_confirmation",
                question="是否将影片发布到媒体库？",
                free_text_allowed=True,
                options={"title": "Test Movie", "target_dir": "/data/library/movies/Test Movie (2024)"},
            ))
            session.commit()

            assert req.id is not None
            assert req.run_id == run_id
            assert req.task_id == task_id
            assert req.decision_type == "publish_confirmation"
            assert req.status == "pending"
            assert req.question == "是否将影片发布到媒体库？"
            assert req.free_text_allowed is True
            assert req.options == {"title": "Test Movie", "target_dir": "/data/library/movies/Test Movie (2024)"}

    def test_create_sets_task_status_to_waiting_user(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        run_id = task_id = None
        with sf() as session:
            run, task = self._seed_run_and_task(session)
            session.commit()
            run_id, task_id = run.id, task.id

        with sf() as session:
            repo = AgentDecisionRequestRepository(session)
            repo.create(AgentDecisionRequestCreate(
                run_id=run_id,
                task_id=task_id,
                decision_type="publish_confirmation",
                question="确认发布？",
            ))
            session.commit()

        # 验证任务状态被联动更新
        with sf() as session:
            from media_pilot.repository.repositories import IngestTaskRepository
            task_repo = IngestTaskRepository(session)
            task = task_repo.get(task_id)
            assert task.status == IngestTaskStatus.WAITING_USER

    def test_save_decision(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        run_id = task_id = None
        with sf() as session:
            run, task = self._seed_run_and_task(session)
            session.commit()
            run_id, task_id = run.id, task.id

        with sf() as session:
            repo = AgentDecisionRequestRepository(session)
            req = repo.create(AgentDecisionRequestCreate(
                run_id=run_id,
                task_id=task_id,
                decision_type="publish_confirmation",
            ))
            session.commit()
            req_id = req.id

        with sf() as session:
            repo = AgentDecisionRequestRepository(session)
            saved = repo.save_decision(
                req_id,
                decision={"action": "publish"},
                decided_by="user",
            )
            session.commit()

            assert saved.status == "decided"
            assert saved.decision == {"action": "publish"}
            assert saved.decided_by == "user"
            assert saved.decided_at is not None

    def test_save_decision_only_claims_pending_request_once(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            run, task = self._seed_run_and_task(session)
            req = AgentDecisionRequestRepository(session).create(
                AgentDecisionRequestCreate(
                    run_id=run.id,
                    task_id=task.id,
                    decision_type="publish_confirmation",
                ),
            )
            session.commit()
            req_id = req.id

        with sf() as session:
            repo = AgentDecisionRequestRepository(session)
            first = repo.save_decision(
                req_id,
                decision={"action": "publish"},
                decided_by="user",
            )
            session.commit()
            assert first is not None

        with sf() as session:
            repo = AgentDecisionRequestRepository(session)
            second = repo.save_decision(
                req_id,
                decision={"action": "cancel"},
                decided_by="user",
            )
            session.commit()
            assert second is None

        with sf() as session:
            req = AgentDecisionRequestRepository(session).get(req_id)
            assert req is not None
            assert req.decision == {"action": "publish"}

    def test_list_pending_by_task(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        run_id = task_id = None
        with sf() as session:
            run, task = self._seed_run_and_task(session)
            session.commit()
            run_id, task_id = run.id, task.id

        with sf() as session:
            repo = AgentDecisionRequestRepository(session)
            # 创建第一个 pending decision 并立即决定
            req1 = repo.create(AgentDecisionRequestCreate(
                run_id=run_id, task_id=task_id, decision_type="publish_confirmation",
            ))
            session.commit()
            repo.save_decision(req1.id, decision={"action": "publish"}, decided_by="user")
            session.commit()

            # 第一个已决定，可以创建第二个 pending decision（同一 run 不再有 pending）
            req2 = repo.create(AgentDecisionRequestCreate(
                run_id=run_id, task_id=task_id, decision_type="metadata_correction",
            ))
            session.commit()

            pending = repo.list_pending_by_task(task_id)
            assert len(pending) == 1
            assert pending[0].id == req2.id

    def test_list_pending(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        run_id = task_id = None
        with sf() as session:
            run, task = self._seed_run_and_task(session)
            session.commit()
            run_id, task_id = run.id, task.id

        with sf() as session:
            repo = AgentDecisionRequestRepository(session)
            repo.create(AgentDecisionRequestCreate(
                run_id=run_id, task_id=task_id, decision_type="publish_confirmation",
            ))
            session.commit()

            pending = repo.list_pending()
            assert len(pending) >= 1
            assert all(r.status == "pending" for r in pending)

    def test_save_decision_nonexistent(self, tmp_path):
        from tests.test_api_v1 import _make_session_factory

        sf = _make_session_factory(tmp_path)
        with sf() as session:
            repo = AgentDecisionRequestRepository(session)
            result = repo.save_decision("nonexistent", decision={}, decided_by="user")
            assert result is None
