from __future__ import annotations

import unittest

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from caseops.api.app import create_app
from caseops.config import Settings
from caseops.database import build_session_factory
from caseops.infrastructure.models import (
    AuditEventRecord,
    Base,
    InvestigationRecord,
    OutboxEventRecord,
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


if __name__ == "__main__":
    unittest.main()
