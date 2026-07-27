from __future__ import annotations

import json
from contextvars import ContextVar, Token
from typing import Any

import uvicorn
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_rest_routes,
)
from a2a.server.tasks.database_task_store import DatabaseTaskStore
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.types import ASGIApp, Receive, Scope, Send

from caseops.agent.mcp_auth import verify_task_token
from caseops.config import Settings, get_settings
from caseops.database import build_engine, build_session_factory
from caseops.platform.runtime_envelope import (
    RuntimeEnvelopeMiddleware,
    TelemetryRuntime,
)
from caseops.service import Principal

from .contracts import DelegationTask
from .specialists import DirectSpecialistGateway

_request_claims: ContextVar[dict[str, Any] | None] = ContextVar(
    "caseops_a2a_request_claims",
    default=None,
)


class BearerTaskTokenMiddleware:
    """Authenticate every A2A operation while leaving the Agent Card discoverable."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self._app = app
        self._settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        if scope["type"] != "http" or path in {
            "/health/live",
            "/.well-known/agent-card.json",
        }:
            await self._app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        authorization = headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            await self._reject(send)
            return
        try:
            claims = verify_task_token(
                authorization.removeprefix("Bearer "),
                settings=self._settings,
                audience=self._settings.a2a_resource,
            )
        except (KeyError, TypeError, ValueError):
            await self._reject(send)
            return
        token: Token[dict[str, Any] | None] = _request_claims.set(claims)
        try:
            await self._app(scope, receive, send)
        finally:
            _request_claims.reset(token)

    @staticmethod
    async def _reject(send: Send) -> None:
        payload = json.dumps(
            {"code": "INVALID_TASK_TOKEN", "detail": "valid bearer task token required"}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": payload})


class CaseOpsSpecialistExecutor(AgentExecutor):
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
    ) -> None:
        if settings.agent_tool_transport == "mcp":
            from caseops.agent.mcp_client import MCPToolExecutor

            self._gateway = DirectSpecialistGateway(
                executor=MCPToolExecutor(settings=settings)
            )
        else:
            self._gateway = DirectSpecialistGateway(session_factory)

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            raise ValueError("A2A server did not allocate task and context identifiers")
        delegation = DelegationTask.model_validate_json(context.get_user_input())
        claims = _request_claims.get()
        if claims is None:
            raise PermissionError("task token claims are unavailable")
        if claims.get("task_id") != delegation.task_id:
            raise PermissionError("task token is not bound to delegation task")
        scopes = frozenset(str(claims.get("scope", "")).split())
        if not frozenset(delegation.required_scopes).issubset(scopes):
            raise PermissionError("task token lacks delegation scopes")

        history = [context.message] if context.message is not None else []
        await event_queue.enqueue_event(
            Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=history,
            )
        )
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context_id,
        )
        await updater.start_work()
        result = await self._gateway.execute(
            task=delegation,
            principal=Principal(
                tenant_id=str(claims["tenant_id"]),
                actor_id=str(claims["sub"]),
                scopes=scopes,
            ),
        )
        await updater.add_artifact(
            parts=[Part(text=result.model_dump_json())],
            name="caseops-specialist-result",
            last_chunk=True,
        )
        await updater.complete()

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        if not context.task_id or not context.context_id:
            return
        await TaskUpdater(
            event_queue=event_queue,
            task_id=context.task_id,
            context_id=context.context_id,
        ).cancel()


def _agent_card(settings: Settings) -> AgentCard:
    skills = [
        AgentSkill(
            id="caseops_coverage",
            name="Coverage specialist",
            description="Resolve the case-bound policy version and requirements.",
            tags=["caseops", "coverage", "read-only"],
            examples=["核对案件绑定规则与必要材料"],
            input_modes=["application/json"],
            output_modes=["application/json"],
        ),
        AgentSkill(
            id="caseops_document",
            name="Document specialist",
            description="Normalize source documents with governed alias rules.",
            tags=["caseops", "document", "read-only"],
            examples=["核对来源材料完整性"],
            input_modes=["application/json"],
            output_modes=["application/json"],
        ),
        AgentSkill(
            id="caseops_risk",
            name="Risk specialist",
            description="Evaluate structured risk signals without taking action.",
            tags=["caseops", "risk", "read-only"],
            examples=["判断是否需要人工风险复核"],
            input_modes=["application/json"],
            output_modes=["application/json"],
        ),
    ]
    return AgentCard(
        name="CaseOps specialist network",
        description=(
            "Three read-only specialists behind a typed delegation and evidence contract."
        ),
        provider=AgentProvider(
            organization="CaseOps reference implementation",
            url="https://github.com/dataPro-lgtm/production-grade-multi-agent-caseops",
        ),
        version=settings.service_version,
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=skills,
        supported_interfaces=[
            AgentInterface(
                protocol_binding="HTTP+JSON",
                protocol_version="1.0",
                url=settings.a2a_resource,
            )
        ],
    )


def _owner(_: ServerCallContext) -> str:
    claims = _request_claims.get()
    if claims is None:
        return "unauthenticated"
    return f"{claims['tenant_id']}:{claims['sub']}"


def _async_database_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    raise ValueError("durable A2A task store requires PostgreSQL")


def create_a2a_app(
    *,
    settings: Settings,
    session_factory: sessionmaker[Session],
    task_store: TaskStore | None = None,
) -> FastAPI:
    async_engine: AsyncEngine | None = None
    resolved_store = task_store
    if resolved_store is None:
        async_engine = create_async_engine(_async_database_url(settings.database_url))
        resolved_store = DatabaseTaskStore(
            async_engine,
            create_table=False,
            table_name="a2a_tasks",
            owner_resolver=_owner,
        )
    handler = DefaultRequestHandler(
        agent_executor=CaseOpsSpecialistExecutor(settings, session_factory),
        task_store=resolved_store,
        agent_card=_agent_card(settings),
    )
    app = FastAPI(
        title="CaseOps A2A specialists",
        version=settings.service_version,
    )
    telemetry = TelemetryRuntime(settings=settings, service_name="caseops-a2a")

    @app.get("/health/live")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "caseops-a2a",
            "version": settings.service_version,
        }

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card=_agent_card(settings)),
        rest_routes=create_rest_routes(
            request_handler=handler,
            path_prefix="/a2a/rest",
        ),
    )

    if async_engine is not None:
        app.router.add_event_handler("shutdown", async_engine.dispose)
    app.add_middleware(BearerTaskTokenMiddleware, settings=settings)
    app.add_middleware(
        RuntimeEnvelopeMiddleware,
        runtime=telemetry,
        default_timeout_seconds=settings.request_default_timeout_seconds,
        max_timeout_seconds=settings.request_max_timeout_seconds,
    )
    app.router.add_event_handler("shutdown", telemetry.shutdown)
    return app


def create_test_a2a_app(
    *,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> FastAPI:
    return create_a2a_app(
        settings=settings,
        session_factory=session_factory,
        task_store=InMemoryTaskStore(owner_resolver=_owner),
    )


def main() -> None:
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    uvicorn.run(
        create_a2a_app(settings=settings, session_factory=factory),
        host=settings.a2a_host,
        port=settings.a2a_port,
    )


if __name__ == "__main__":
    main()
