from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from caseops.collaboration.a2a_client import A2ASpecialistGateway
from caseops.collaboration.a2a_server import create_test_a2a_app
from caseops.collaboration.contracts import DelegationTask, SpecialistId
from caseops.config import Settings
from caseops.database import build_session_factory
from caseops.infrastructure.models import Base
from caseops.seed import seed_reference_data
from caseops.service import Principal


class A2AProtocolTest(unittest.IsolatedAsyncioTestCase):
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
        self.settings = Settings(
            environment="test",
            database_url="sqlite+pysqlite://",
            a2a_url="http://a2a.test",
            a2a_resource="http://a2a.test/a2a/rest",
            delegation_signing_key="test-a2a-signing-key-with-at-least-32-bytes",
        )
        self.app = create_test_a2a_app(
            settings=self.settings,
            session_factory=self.factory,
        )
        self.transport = httpx.ASGITransport(app=self.app)

    async def asyncTearDown(self) -> None:
        self.engine.dispose()

    async def test_agent_card_is_discoverable_but_operations_require_token(
        self,
    ) -> None:
        async with httpx.AsyncClient(
            transport=self.transport,
            base_url="http://a2a.test",
        ) as client:
            card = await client.get("/.well-known/agent-card.json")
            unauthorized = await client.post("/a2a/rest/message:send", json={})

        self.assertEqual(card.status_code, 200, card.text)
        self.assertEqual(card.json()["name"], "CaseOps specialist network")
        self.assertEqual(len(card.json()["skills"]), 3)
        self.assertEqual(unauthorized.status_code, 401)

    async def test_official_a2a_client_receives_typed_specialist_artifact(
        self,
    ) -> None:
        task = DelegationTask(
            task_id="delegation-coverage-001",
            parent_run_id="collaboration-run-001",
            case_id="C-102",
            specialist_id=SpecialistId.COVERAGE,
            goal="核对案件绑定规则版本并返回有证据支持的必要材料集合。",
            acceptance_criteria=("返回规则版本", "返回必要材料集合"),
            allowed_evidence_kinds=("case_snapshot", "policy_rule"),
            required_scopes=("case:read", "policy:read"),
            deadline_at=datetime.now(UTC) + timedelta(seconds=10),
        )
        result = await A2ASpecialistGateway(
            self.settings,
            transport=self.transport,
        ).execute(
            task=task,
            principal=Principal(
                tenant_id="tenant-demo",
                actor_id="test-supervisor",
                scopes=frozenset(task.required_scopes),
            ),
        )

        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(result.specialist_id, SpecialistId.COVERAGE)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.claims[0].key, "required_document_set")
