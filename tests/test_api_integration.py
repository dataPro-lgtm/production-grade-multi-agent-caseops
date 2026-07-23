from __future__ import annotations

import unittest

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from caseops.agent.contracts import AgentState, RunStatus, ToolCall
from caseops.agent.service import _request_hash
from caseops.agent.tools import GET_CASE
from caseops.api.app import create_app
from caseops.config import Settings
from caseops.database import build_session_factory
from caseops.infrastructure.models import (
    AgentCheckpointRecord,
    AgentRunRecord,
    AuditEventRecord,
    Base,
    CollaborationRunRecord,
    DelegatedTaskRecord,
    InvestigationRecord,
    OutboxEventRecord,
    ToolExecutionRecord,
)
from caseops.seed import seed_reference_data


class ApiIntegrationTest(unittest.IsolatedAsyncioTestCase):
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

        settings = Settings(
            environment="test",
            database_url="sqlite+pysqlite://",
            api_keys={
                "integration-test-key": "tenant-demo",
                "other-tenant-key": "tenant-other",
            },
            log_level="WARNING",
        )
        self.app = create_app(settings=settings, engine=self.engine)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.engine.dispose()

    async def test_health_and_metrics_are_exposed(self) -> None:
        live = await self.client.get("/health/live")
        ready = await self.client.get("/health/ready")
        metrics = await self.client.get("/metrics")

        self.assertEqual(live.status_code, 200)
        self.assertEqual(ready.status_code, 200)
        self.assertIn("caseops_http_requests_total", metrics.text)

    async def test_investigation_is_persisted_with_audit_and_outbox(self) -> None:
        response = await self._investigate("book-ch01-c102-0001")

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertFalse(payload["replayed"])
        self.assertEqual(
            payload["result"]["decision"]["code"],
            "MISSING_REQUIRED_DOCUMENTS",
        )
        self.assertEqual(
            payload["result"]["missing_documents"][0]["code"],
            "ACCIDENT_CERTIFICATE",
        )
        self.assertEqual(
            payload["result"]["recommended_action"]["side_effect"],
            "none",
        )

        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count(InvestigationRecord.id))),
                1,
            )
            self.assertEqual(
                session.scalar(select(func.count(AuditEventRecord.id))),
                1,
            )
            self.assertEqual(
                session.scalar(select(func.count(OutboxEventRecord.id))),
                1,
            )

    async def test_same_idempotency_key_replays_without_duplicate_writes(self) -> None:
        first = await self._investigate("book-ch01-c102-0002")
        second = await self._investigate("book-ch01-c102-0002")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(
            first.json()["investigation_id"],
            second.json()["investigation_id"],
        )
        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count(InvestigationRecord.id))),
                1,
            )

    async def test_unknown_or_cross_tenant_case_has_same_not_found_contract(
        self,
    ) -> None:
        unknown = await self._investigate(
            "book-ch01-c404-0001",
            case_id="C-404",
        )
        unauthorized = await self.client.post(
            "/v1/cases/C-102/investigations",
            headers={
                "X-API-Key": "other-tenant-key",
                "Idempotency-Key": "book-ch01-cross-tenant-0001",
            },
            json={"notification_action": "draft"},
        )

        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unauthorized.status_code, 404)
        self.assertEqual(
            unknown.headers["content-type"],
            "application/problem+json",
        )

    async def test_missing_api_key_is_rejected(self) -> None:
        response = await self.client.post(
            "/v1/cases/C-102/investigations",
            headers={"Idempotency-Key": "book-ch01-no-auth-0001"},
            json={"notification_action": "draft"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "AUTHENTICATION_FAILED")

    async def test_request_contract_rejects_unknown_fields(self) -> None:
        response = await self.client.post(
            "/v1/cases/C-102/investigations",
            headers={
                "X-API-Key": "integration-test-key",
                "Idempotency-Key": "book-ch01-invalid-body-0001",
            },
            json={"notification_action": "draft", "send_immediately": True},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["code"],
            "REQUEST_VALIDATION_FAILED",
        )

    async def test_controlled_agent_resolves_unstructured_evidence(self) -> None:
        response = await self._run_agent("book-ch02-c102-0001")

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        self.assertFalse(payload["resumed"])
        self.assertEqual(payload["step_count"], 4)
        self.assertEqual(
            payload["result"]["outcome"],
            "DOCUMENTS_COMPLETE_AFTER_NORMALIZATION",
        )
        self.assertEqual(
            payload["result"]["resolved_document_codes"],
            ["ACCIDENT_CERTIFICATE"],
        )
        self.assertEqual(payload["result"]["missing_document_codes"], [])

        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count(AgentRunRecord.id))),
                1,
            )
            self.assertEqual(
                session.scalar(select(func.count(ToolExecutionRecord.id))),
                4,
            )
            checkpoint_count = session.scalar(select(func.count(AgentCheckpointRecord.id)))
            self.assertIsNotNone(checkpoint_count)
            self.assertGreaterEqual(checkpoint_count, 18)
            statuses = session.scalars(
                select(ToolExecutionRecord.status).order_by(ToolExecutionRecord.started_at)
            ).all()
            self.assertEqual(statuses, ["succeeded"] * 4)

    async def test_agent_idempotency_replays_without_tool_reexecution(self) -> None:
        first = await self._run_agent("book-ch02-c102-0002")
        second = await self._run_agent("book-ch02-c102-0002")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(first.json()["run_id"], second.json()["run_id"])
        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count(AgentRunRecord.id))),
                1,
            )
            self.assertEqual(
                session.scalar(select(func.count(ToolExecutionRecord.id))),
                4,
            )

    async def test_agent_cannot_cross_tenant_boundary(self) -> None:
        response = await self.client.post(
            "/v1/cases/C-102/agent-runs",
            headers={
                "X-API-Key": "other-tenant-key",
                "Idempotency-Key": "book-ch02-cross-tenant-0001",
            },
            json={"goal": "判断案件材料是否满足其绑定规则，并给出可追溯结论。"},
        )

        self.assertEqual(response.status_code, 404)
        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count(AgentRunRecord.id))),
                0,
            )

    async def test_agent_resumes_read_only_crash_window_from_checkpoint(
        self,
    ) -> None:
        idempotency_key = "book-ch02-recovery-0001"
        goal = "判断案件材料是否满足其绑定规则，并给出可追溯结论。"
        state = AgentState(
            run_id="run-recovery-integration",
            tenant_id="tenant-demo",
            case_id="C-102",
            goal=goal,
            status=RunStatus.TOOL_RUNNING,
            sequence=3,
            step_count=1,
            max_steps=8,
            repeat_limit=2,
            pending_call=ToolCall(
                call_id="call_interrupted_read",
                name=GET_CASE,
                arguments={"case_id": "C-102"},
            ),
        )
        with self.factory.begin() as session:
            session.add(
                AgentRunRecord(
                    id=state.run_id,
                    tenant_id=state.tenant_id,
                    case_id=state.case_id,
                    actor_id="api-key:crash-test",
                    idempotency_key=idempotency_key,
                    request_hash=_request_hash(state.case_id, goal),
                    goal=goal,
                    planner_kind="conformance",
                    model=None,
                    status=state.status.value,
                    step_count=state.step_count,
                    max_steps=state.max_steps,
                    state=state.model_dump(mode="json"),
                )
            )

        response = await self._run_agent(idempotency_key)

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["resumed"])
        self.assertFalse(payload["replayed"])
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["step_count"], 5)

    async def test_multi_agent_collaboration_persists_tasks_join_and_events(
        self,
    ) -> None:
        response = await self._run_collaboration("book-ch03-c102-0001")

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        self.assertFalse(payload["replayed"])
        self.assertEqual(
            payload["result"]["outcome"],
            "COMPLETE_WITH_REVIEW_REQUIRED",
        )
        self.assertEqual(payload["result"]["side_effect"], "none")
        self.assertEqual(
            payload["result"]["join"]["accepted_specialists"],
            ["coverage", "document", "risk"],
        )
        self.assertEqual(len(payload["tasks"]), 3)
        self.assertEqual(
            {task["status"] for task in payload["tasks"]},
            {"succeeded"},
        )

        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count(CollaborationRunRecord.id))),
                1,
            )
            self.assertEqual(
                session.scalar(select(func.count(DelegatedTaskRecord.id))),
                3,
            )
            self.assertEqual(
                session.scalar(select(func.count(OutboxEventRecord.id))),
                5,
            )

    async def test_collaboration_idempotency_does_not_redispatch_agents(
        self,
    ) -> None:
        first = await self._run_collaboration("book-ch03-c102-0002")
        second = await self._run_collaboration("book-ch03-c102-0002")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(first.json()["run_id"], second.json()["run_id"])
        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count(DelegatedTaskRecord.id))),
                3,
            )

    async def _investigate(
        self,
        idempotency_key: str,
        *,
        case_id: str = "C-102",
    ) -> httpx.Response:
        return await self.client.post(
            f"/v1/cases/{case_id}/investigations",
            headers={
                "X-API-Key": "integration-test-key",
                "Idempotency-Key": idempotency_key,
                "X-Request-ID": f"request-{idempotency_key}",
            },
            json={"notification_action": "draft"},
        )

    async def _run_agent(self, idempotency_key: str) -> httpx.Response:
        return await self.client.post(
            "/v1/cases/C-102/agent-runs",
            headers={
                "X-API-Key": "integration-test-key",
                "Idempotency-Key": idempotency_key,
                "X-Request-ID": f"request-{idempotency_key}",
            },
            json={"goal": "判断案件材料是否满足其绑定规则，并给出可追溯结论。"},
        )

    async def _run_collaboration(self, idempotency_key: str) -> httpx.Response:
        return await self.client.post(
            "/v1/cases/C-102/collaboration-runs",
            headers={
                "X-API-Key": "integration-test-key",
                "Idempotency-Key": idempotency_key,
                "X-Request-ID": f"request-{idempotency_key}",
            },
            json={
                "goal": (
                    "并行核对案件规则、材料完整性与风险信号，"
                    "通过证据合同形成可追溯的协作结论。"
                )
            },
        )


if __name__ == "__main__":
    unittest.main()
