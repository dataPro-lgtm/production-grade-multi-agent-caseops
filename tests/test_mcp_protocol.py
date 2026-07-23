from __future__ import annotations

import unittest
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from caseops.agent.mcp_auth import DelegationTokenIssuer, DelegationTokenVerifier
from caseops.agent.mcp_client import MCPToolExecutor
from caseops.agent.mcp_server import create_mcp_server
from caseops.agent.tools import GET_CASE, RESOLVE_ALIAS
from caseops.config import Settings
from caseops.database import build_session_factory
from caseops.infrastructure.models import Base
from caseops.seed import seed_reference_data
from caseops.service import Principal


class McpProtocolTest(unittest.IsolatedAsyncioTestCase):
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
            api_keys={"test-key": "tenant-demo"},
            mcp_url="http://mcp.test/mcp",
            mcp_resource="http://mcp.test/mcp",
            delegation_signing_key="test-delegation-signing-key-at-least-32-bytes",
            log_level="WARNING",
        )
        server = create_mcp_server(
            settings=self.settings,
            factory=self.factory,
        )
        self.app = server.streamable_http_app()

    async def asyncTearDown(self) -> None:
        self.engine.dispose()

    async def test_streamable_http_tools_use_token_tenant_and_task(self) -> None:
        principal = Principal("tenant-demo", "integration-test")
        token = DelegationTokenIssuer(self.settings).issue(
            principal=principal,
            task_id="run-mcp-001",
        )
        transport = httpx.ASGITransport(app=self.app)
        async with (
            self.app.router.lifespan_context(self.app),
            httpx.AsyncClient(
                transport=transport,
                base_url="http://mcp.test",
                headers={"Authorization": f"Bearer {token}"},
            ) as client,
            streamable_http_client(
                "http://mcp.test/mcp",
                http_client=client,
            ) as (read_stream, write_stream, _),
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=3),
            ) as session,
        ):
            initialized = await session.initialize()
            tools = await session.list_tools()
            case = await session.call_tool(GET_CASE, {"case_id": "C-102"})
            alias = await session.call_tool(
                RESOLVE_ALIAS,
                {"case_id": "C-102", "document_id": "DOC-C102-003"},
            )

        self.assertIsNotNone(initialized.protocolVersion)
        self.assertEqual(len(tools.tools), 4)
        self.assertFalse(case.isError)
        self.assertEqual(case.structuredContent["task_id"], "run-mcp-001")
        self.assertNotIn("tenant_id", case.structuredContent)
        self.assertEqual(
            alias.structuredContent["canonical_code"],
            "ACCIDENT_CERTIFICATE",
        )

    async def test_missing_bearer_token_is_rejected(self) -> None:
        async with (
            self.app.router.lifespan_context(self.app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://mcp.test",
            ) as client,
        ):
            response = await client.post(
                "/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                },
            )

        self.assertEqual(response.status_code, 401)

    async def test_task_token_rejects_tampering_and_wrong_audience(self) -> None:
        token = DelegationTokenIssuer(self.settings).issue(
            principal=Principal("tenant-demo", "integration-test"),
            task_id="run-token-001",
        )
        verifier = DelegationTokenVerifier(self.settings)
        wrong_audience = Settings(
            environment="test",
            database_url="sqlite+pysqlite://",
            api_keys={"test-key": "tenant-demo"},
            mcp_resource="http://different-resource.test/mcp",
            delegation_signing_key=self.settings.delegation_signing_key,
        )

        self.assertIsNone(await verifier.verify_token(f"{token[:-1]}x"))
        self.assertIsNone(await DelegationTokenVerifier(wrong_audience).verify_token(token))

    async def test_production_mcp_executor_adapter_returns_structured_output(
        self,
    ) -> None:
        executor = MCPToolExecutor(
            settings=self.settings,
            transport=httpx.ASGITransport(app=self.app),
        )
        async with self.app.router.lifespan_context(self.app):
            result = await executor.execute(
                principal=Principal("tenant-demo", "integration-test"),
                run_id="run-mcp-adapter-001",
                tool_name=GET_CASE,
                arguments={"case_id": "C-102"},
            )

        self.assertEqual(result["case_id"], "C-102")
        self.assertEqual(result["task_id"], "run-mcp-adapter-001")


if __name__ == "__main__":
    unittest.main()
