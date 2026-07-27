from __future__ import annotations

import unittest

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from caseops.api.app import create_app
from caseops.config import Settings
from caseops.database import build_session_factory
from caseops.infrastructure.models import (
    Base,
    RuntimeContextEdgeRecord,
    RuntimeContextNodeRecord,
    SystemRunRecord,
    SystemStepRecord,
)
from caseops.seed import seed_reference_data


class SystemOrchestrationTest(unittest.IsolatedAsyncioTestCase):
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
                    "system-test-key": "tenant-demo",
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

    async def test_hierarchical_run_converges_and_persists_runtime_context_graph(
        self,
    ) -> None:
        first = await self._create("chapter-07-system-run-0001")

        self.assertEqual(first.status_code, 201, first.text)
        payload = first.json()
        self.assertEqual(payload["status"], "needs_human")
        self.assertEqual(
            payload["result"]["outcome"],
            "SYSTEM_ACCEPTED_WITH_HUMAN_REVIEW",
        )
        self.assertEqual(payload["result"]["side_effect"], "none")
        self.assertEqual(
            {check["status"] for check in payload["result"]["checks"]},
            {"passed"},
        )
        self.assertEqual(
            {step["status"] for step in payload["steps"]},
            {"succeeded"},
        )
        self.assertTrue(payload["child_runs"]["context_run_id"])
        self.assertTrue(payload["child_runs"]["collaboration_run_id"])

        graph_response = await self.client.get(
            payload["context_graph_uri"],
            headers={"X-API-Key": "system-test-key"},
        )
        self.assertEqual(graph_response.status_code, 200, graph_response.text)
        graph = graph_response.json()
        node_types = {node["node_type"] for node in graph["nodes"]}
        self.assertTrue(
            {
                "goal",
                "plan",
                "step",
                "context_pack",
                "delegated_task",
                "claim",
                "evidence",
                "acceptance",
                "result",
            }.issubset(node_types)
        )
        claim_nodes = {
            node["node_key"] for node in graph["nodes"] if node["node_type"] == "claim"
        }
        supported_claims = {
            edge["from_node_key"]
            for edge in graph["edges"]
            if edge["relation_type"] == "SUPPORTED_BY"
        }
        self.assertEqual(claim_nodes, supported_claims)

        replay = await self._create("chapter-07-system-run-0001")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(replay.json()["system_run_id"], payload["system_run_id"])

        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count(SystemRunRecord.id))),
                1,
            )
            self.assertEqual(
                session.scalar(select(func.count(SystemStepRecord.id))),
                3,
            )
            self.assertGreater(
                session.scalar(select(func.count(RuntimeContextNodeRecord.id))) or 0,
                10,
            )
            self.assertGreater(
                session.scalar(select(func.count(RuntimeContextEdgeRecord.id))) or 0,
                10,
            )

    async def test_runtime_context_graph_is_tenant_isolated(self) -> None:
        created = await self._create("chapter-07-system-run-0002")
        uri = created.json()["context_graph_uri"]

        response = await self.client.get(
            uri,
            headers={"X-API-Key": "other-tenant-key"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "SYSTEM_RUN_NOT_FOUND")

    async def test_system_run_idempotency_rejects_changed_request(self) -> None:
        first = await self._create("chapter-07-system-run-0003")
        changed = await self.client.post(
            "/v1/cases/C-102/system-runs",
            headers={
                "X-API-Key": "system-test-key",
                "Idempotency-Key": "chapter-07-system-run-0003",
            },
            json={
                **self._body(),
                "question": "请仅判断材料状态，不执行原系统级验收目标。",
            },
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()["code"], "IDEMPOTENCY_KEY_REUSED")

    async def _create(self, idempotency_key: str) -> httpx.Response:
        return await self.client.post(
            "/v1/cases/C-102/system-runs",
            headers={
                "X-API-Key": "system-test-key",
                "Idempotency-Key": idempotency_key,
            },
            json=self._body(),
        )

    @staticmethod
    def _body() -> dict[str, object]:
        return {
            "goal": ("合并上下文调查与多专业协作结论，对 C-102 执行系统级一致性验收。"),
            "question": ("本案适用什么规则，材料是否满足要求，是否触发人工风险复核？"),
            "as_of": "2026-07-23T12:00:00+08:00",
            "evidence_token_budget": 1800,
            "max_rounds": 2,
        }
