from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from caseops.api.app import create_app
from caseops.config import Settings
from caseops.database import build_session_factory
from caseops.infrastructure.models import (
    AuditEventRecord,
    Base,
    OperationalAssessmentRecord,
    OperationalCostEventRecord,
    SystemRunRecord,
    SystemStepRecord,
    new_id,
)
from caseops.seed import seed_reference_data


class OperationsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = build_session_factory(self.engine)
        with self.factory.begin() as session:
            seed_reference_data(session)
        self.app = create_app(
            settings=Settings(
                environment="test",
                database_url="sqlite+pysqlite://",
                api_keys={
                    "operations-test-key": "tenant-demo",
                    "other-tenant-key": "tenant-other",
                },
                log_level="WARNING",
            ),
            engine=self.engine,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.engine.dispose()

    async def test_healthy_assessment_is_durable_idempotent_and_honest_about_cost(
        self,
    ) -> None:
        system_run = await self._create_system_run("chapter-09-healthy-run")
        self.assertEqual(system_run.status_code, 201, system_run.text)
        system_run_id = system_run.json()["system_run_id"]

        first = await self._assess(system_run_id, "chapter-09-healthy-assessment")
        replay = await self._assess(system_run_id, "chapter-09-healthy-assessment")

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(replay.status_code, 200, replay.text)
        payload = first.json()
        report = payload["report"]
        self.assertFalse(payload["replayed"])
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            replay.json()["assessment_id"],
            payload["assessment_id"],
        )
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(report["severity"], "none")
        self.assertTrue(report["impact"]["goal_succeeded"])
        self.assertEqual(report["impact"]["external_side_effect_count"], 0)
        self.assertIsNone(report["first_failure"])
        self.assertGreater(report["evidence"]["context_graph_node_count"], 10)
        self.assertGreaterEqual(report["evidence"]["security_decision_count"], 0)
        self.assertEqual(report["cost"]["pricing_status"], "not_configured")
        self.assertIsNone(report["cost"]["monetary_cost_microunits"])
        self.assertEqual(report["cost"]["successful_goal_count"], 1)
        self.assertTrue(
            all(
                measure["monetary_cost_microunits"] is None
                for measure in report["cost"]["measures"]
            )
        )

        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count(OperationalAssessmentRecord.id))),
                1,
            )
            self.assertEqual(
                session.scalar(select(func.count(OperationalCostEventRecord.id))),
                4,
            )
            audit = session.scalar(
                select(AuditEventRecord).where(
                    AuditEventRecord.action == "system.operations.assess"
                )
            )
            self.assertIsNotNone(audit)
            assert audit is not None
            self.assertEqual(audit.details["side_effect"], "none")

    async def test_failed_collaboration_becomes_causal_incident_bundle(self) -> None:
        failed_run_id = self._insert_failed_collaboration_run()

        response = await self._assess(
            failed_run_id,
            "chapter-09-failed-assessment",
        )

        self.assertEqual(response.status_code, 201, response.text)
        report = response.json()["report"]
        self.assertEqual(report["status"], "incident")
        self.assertEqual(report["severity"], "SEV-2")
        self.assertFalse(report["impact"]["goal_succeeded"])
        self.assertEqual(report["impact"]["completed_step_count"], 1)
        self.assertEqual(report["impact"]["failed_step_count"], 1)
        self.assertEqual(report["impact"]["failed_delegated_task_count"], 0)
        self.assertEqual(report["first_failure"]["layer"], "collaboration")
        self.assertEqual(
            report["first_failure"]["step_key"],
            "specialist-collaboration",
        )
        self.assertEqual(report["first_failure"]["error_code"], "ConnectError")
        self.assertIsNone(report["evidence"]["context_graph_ref"])
        self.assertEqual(report["evidence"]["delegated_task_count"], 0)
        measures = {
            measure["resource_type"]: measure for measure in report["cost"]["measures"]
        }
        self.assertEqual(measures["context_run"]["attribution"], "wasted")
        self.assertEqual(measures["context_run"]["quantity"], 1)
        self.assertIsNone(measures["context_run"]["per_successful_goal"])
        self.assertEqual(
            [control["action"] for control in report["recommended_controls"]],
            [
                "wait_for_dependency_readiness",
                "route_to_human",
                "retry_from_failed_step",
            ],
        )

    async def test_assessment_is_tenant_isolated_and_requires_terminal_run(self) -> None:
        failed_run_id = self._insert_failed_collaboration_run()
        isolated = await self.client.post(
            f"/v1/system-runs/{failed_run_id}/operational-assessments",
            headers={
                "X-API-Key": "other-tenant-key",
                "Idempotency-Key": "chapter-09-isolated",
            },
        )
        self.assertEqual(isolated.status_code, 404)
        self.assertEqual(isolated.json()["code"], "SYSTEM_RUN_NOT_FOUND")

        with self.factory.begin() as session:
            running_id = new_id()
            now = datetime.now(UTC)
            session.add(
                SystemRunRecord(
                    id=running_id,
                    tenant_id="tenant-demo",
                    case_id="C-102",
                    actor_id="test-actor",
                    idempotency_key="chapter-09-running-run",
                    request_hash="a" * 64,
                    goal="验证非终态运行不能生成事故证据包。",
                    question="当前运行是否已经到达可以稳定评估的终态？",
                    as_of=now,
                    status="running",
                    version=1,
                    plan={"schema_version": "caseops.system-plan.v1", "steps": []},
                    created_at=now,
                    updated_at=now,
                )
            )
        pending = await self._assess(running_id, "chapter-09-running-assessment")
        self.assertEqual(pending.status_code, 409)
        self.assertEqual(pending.json()["code"], "SYSTEM_RUN_NOT_TERMINAL")

    async def test_idempotency_key_cannot_be_reused_for_another_run(self) -> None:
        first_run = self._insert_failed_collaboration_run()
        second_run = self._insert_failed_collaboration_run()
        first = await self._assess(first_run, "chapter-09-shared-assessment")
        conflict = await self._assess(second_run, "chapter-09-shared-assessment")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "IDEMPOTENCY_KEY_REUSED")

    async def _create_system_run(self, idempotency_key: str) -> httpx.Response:
        return await self.client.post(
            "/v1/cases/C-102/system-runs",
            headers={
                "X-API-Key": "operations-test-key",
                "Idempotency-Key": idempotency_key,
            },
            json={
                "goal": ("合并上下文调查与多专业协作结论，对 C-102 执行系统级一致性验收。"),
                "question": ("本案适用什么规则，材料是否满足要求，是否触发人工风险复核？"),
                "as_of": "2026-07-23T12:00:00+08:00",
                "evidence_token_budget": 1800,
                "max_rounds": 2,
            },
        )

    async def _assess(
        self,
        system_run_id: str,
        idempotency_key: str,
    ) -> httpx.Response:
        return await self.client.post(
            f"/v1/system-runs/{system_run_id}/operational-assessments",
            headers={
                "X-API-Key": "operations-test-key",
                "Idempotency-Key": idempotency_key,
            },
        )

    def _insert_failed_collaboration_run(self) -> str:
        now = datetime.now(UTC)
        run_id = new_id()
        context_run_id = None
        with self.factory.begin() as session:
            seed_run = session.scalar(
                select(SystemRunRecord).where(
                    SystemRunRecord.tenant_id == "tenant-demo",
                    SystemRunRecord.context_run_id.is_not(None),
                )
            )
            if seed_run is not None:
                context_run_id = seed_run.context_run_id
            if context_run_id is None:
                context_run = self._create_context_run()
                session.add(context_run)
                session.flush()
                context_run_id = context_run.id
            session.add(
                SystemRunRecord(
                    id=run_id,
                    tenant_id="tenant-demo",
                    case_id="C-102",
                    actor_id="test-actor",
                    idempotency_key=f"failed-run-{run_id}",
                    request_hash="b" * 64,
                    goal="验证 A2A 不可用时保留安全终态与诊断证据。",
                    question="A2A 不可用时系统能否定位首个失败点并保持无副作用？",
                    as_of=now,
                    status="failed",
                    version=3,
                    plan={"schema_version": "caseops.system-plan.v1", "steps": []},
                    context_run_id=context_run_id,
                    created_at=now - timedelta(seconds=3),
                    updated_at=now,
                )
            )
            session.add_all(
                [
                    self._step(
                        run_id,
                        "context-evidence",
                        "context-team",
                        "succeeded",
                        now - timedelta(seconds=2),
                        now - timedelta(seconds=1),
                    ),
                    self._step(
                        run_id,
                        "specialist-collaboration",
                        "collaboration-team",
                        "failed",
                        now - timedelta(seconds=1),
                        now,
                        error={"code": "ConnectError", "message": "redacted"},
                    ),
                    self._step(
                        run_id,
                        "system-acceptance",
                        "central-supervisor",
                        "planned",
                        None,
                        None,
                        attempt_count=0,
                    ),
                ]
            )
        return run_id

    @staticmethod
    def _create_context_run():  # type: ignore[no-untyped-def]
        from caseops.infrastructure.models import ContextRunRecord

        now = datetime.now(UTC)
        return ContextRunRecord(
            tenant_id="tenant-demo",
            case_id="C-102",
            actor_id="test-actor",
            idempotency_key=f"context-{new_id()}",
            request_hash="c" * 64,
            question="构造故障演练所需的已完成上下文证据。",
            purpose="case_investigation",
            as_of=now,
            status="complete",
            retrieval_plan={},
            context_pack={},
            answer={},
            trace=[],
            created_at=now,
            completed_at=now,
        )

    @staticmethod
    def _step(
        run_id: str,
        step_key: str,
        owner: str,
        status: str,
        started_at: datetime | None,
        completed_at: datetime | None,
        *,
        error: dict[str, object] | None = None,
        attempt_count: int = 1,
    ) -> SystemStepRecord:
        return SystemStepRecord(
            system_run_id=run_id,
            tenant_id="tenant-demo",
            step_key=step_key,
            owner=owner,
            goal=f"execute {step_key}",
            depends_on=[],
            acceptance_criteria=[],
            status=status,
            attempt_count=attempt_count,
            error=error,
            started_at=started_at,
            completed_at=completed_at,
        )
