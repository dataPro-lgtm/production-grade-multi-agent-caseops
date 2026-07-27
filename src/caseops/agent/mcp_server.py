from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from sqlalchemy.orm import Session, sessionmaker
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from caseops.config import Settings, get_settings
from caseops.database import build_engine, build_session_factory
from caseops.infrastructure.models import SecurityDecisionRecord
from caseops.platform.runtime_envelope import (
    RuntimeEnvelopeMiddleware,
    TelemetryRuntime,
)
from caseops.security.contracts import SecurityContext
from caseops.security.manifests import TOOL_SECURITY_MANIFESTS
from caseops.security.tool_guard import ToolGuard

from .mcp_auth import DelegationTokenVerifier
from .tools import (
    GET_CASE,
    GET_POLICY,
    LIST_DOCUMENTS,
    LIST_RISK_SIGNALS,
    RESOLVE_ALIAS,
    TOOL_REGISTRY,
    execute_database_tool,
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_mcp_server(
    *,
    settings: Settings,
    factory: sessionmaker[Session],
) -> FastMCP:
    resource_url = urlsplit(settings.mcp_resource)
    allowed_host = resource_url.netloc
    allowed_origin = f"{resource_url.scheme}://{resource_url.netloc}"
    server = FastMCP(
        name="CaseOps governed tools",
        instructions=(
            "Read-only case investigation tools. Tenant identity and task scope "
            "come from the verified bearer token, never from model arguments."
        ),
        host=settings.mcp_host,
        port=settings.mcp_port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        token_verifier=DelegationTokenVerifier(settings),
        auth=AuthSettings(
            issuer_url=settings.delegation_issuer,
            resource_server_url=settings.mcp_resource,
            required_scopes=[],
        ),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[allowed_host],
            allowed_origins=[allowed_origin],
        ),
    )

    @server.custom_route(  # type: ignore[untyped-decorator]
        "/health/live",
        methods=["GET"],
    )
    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "caseops-mcp",
                "version": settings.service_version,
            }
        )

    def register(
        name: str,
        title: str,
        description: str,
        function: Callable[..., dict[str, Any]],
    ) -> None:
        server.tool(
            name=name,
            title=title,
            description=description,
            annotations=READ_ONLY,
            structured_output=True,
        )(function)

    def case_snapshot(case_id: str) -> dict[str, Any]:
        return _execute(
            factory,
            settings,
            GET_CASE,
            {"case_id": case_id},
            "case:read",
        )

    def policy_requirements(case_id: str) -> dict[str, Any]:
        return _execute(
            factory,
            settings,
            GET_POLICY,
            {"case_id": case_id},
            "policy:read",
        )

    def unclassified_documents(case_id: str) -> dict[str, Any]:
        return _execute(
            factory,
            settings,
            LIST_DOCUMENTS,
            {"case_id": case_id},
            "document:read",
        )

    def resolve_alias(case_id: str, document_id: str) -> dict[str, Any]:
        return _execute(
            factory,
            settings,
            RESOLVE_ALIAS,
            {"case_id": case_id, "document_id": document_id},
            "document:resolve",
        )

    def risk_signals(case_id: str) -> dict[str, Any]:
        return _execute(
            factory,
            settings,
            LIST_RISK_SIGNALS,
            {"case_id": case_id},
            "risk:read",
        )

    register(
        GET_CASE,
        "Read case snapshot",
        "Read the immutable case snapshot inside the token's tenant boundary.",
        case_snapshot,
    )
    register(
        GET_POLICY,
        "Read policy requirements",
        "Read the exact policy version bound to a case.",
        policy_requirements,
    )
    register(
        LIST_DOCUMENTS,
        "List unclassified documents",
        "List source documents not mapped to canonical codes.",
        unclassified_documents,
    )
    register(
        RESOLVE_ALIAS,
        "Resolve document alias",
        "Resolve one document label with a governed, versioned alias rule.",
        resolve_alias,
    )
    register(
        LIST_RISK_SIGNALS,
        "List governed risk signals",
        "List structured risk signals without authorizing an operational action.",
        risk_signals,
    )
    return server


def _execute(
    factory: sessionmaker[Session],
    settings: Settings,
    tool_name: str,
    arguments: dict[str, Any],
    required_scope: str,
) -> dict[str, Any]:
    token = get_access_token()
    if token is None or token.claims is None:
        raise PermissionError("verified task token is required")
    if required_scope not in token.scopes:
        raise PermissionError(f"missing required scope: {required_scope}")
    tenant_id = token.claims.get("tenant_id")
    task_id = token.claims.get("task_id")
    if not isinstance(tenant_id, str) or not isinstance(task_id, str):
        raise PermissionError("task token lacks tenant or task binding")
    definition = TOOL_REGISTRY.get(tool_name)
    manifest = TOOL_SECURITY_MANIFESTS.get(tool_name)
    decision = ToolGuard(policy_version=settings.tool_guard_policy_version).evaluate(
        definition=definition,
        manifest=manifest,
        arguments=arguments,
        context=SecurityContext(
            tenant_id=tenant_id,
            actor_id=str(token.claims.get("sub", "unknown")),
            user_scopes=frozenset(str(token.claims.get("user_scope", "")).split()),
            workload_id=str(token.claims.get("workload_id", "unknown")),
            workload_scopes=frozenset(str(token.claims.get("workload_scope", "")).split()),
            delegation_id=task_id,
            delegation_scopes=frozenset(
                str(token.claims.get("delegation_scope", "")).split()
            ),
            purpose=str(token.claims.get("purpose", "")),
            resource_type=str(token.claims.get("resource_type", "")),
            resource_id=str(token.claims.get("resource_id", "")),
            environment=settings.environment,
        ),
        runtime_allowlist=frozenset(TOOL_REGISTRY),
        globally_enabled=settings.tool_guard_enabled,
    )
    with factory.begin() as audit_session:
        audit_session.add(
            SecurityDecisionRecord(
                id=decision.decision_id,
                tenant_id=tenant_id,
                actor_id=str(token.claims.get("sub", "unknown")),
                task_id=task_id,
                tool_name=decision.tool_id,
                tool_version=decision.tool_version,
                effect=decision.effect,
                reason_codes=list(decision.reason_codes),
                purpose=decision.purpose,
                resource_type=decision.resource_type,
                resource_id=decision.resource_id,
                policy_version=decision.policy_version,
                manifest_digest=decision.manifest_digest,
                context_digest=decision.context_digest,
                arguments_hash=decision.arguments_hash,
                data_classification=decision.data_classification.value,
            )
        )
    if decision.effect == "deny":
        raise PermissionError(
            "Tool Guard denied the request: " + ", ".join(decision.reason_codes)
        )
    with factory() as session:
        result = execute_database_tool(
            session=session,
            tenant_id=tenant_id,
            tool_name=tool_name,
            arguments=arguments,
        )
    result["task_id"] = task_id
    return result


def main() -> None:
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    server = create_mcp_server(settings=settings, factory=factory)
    telemetry = TelemetryRuntime(settings=settings, service_name="caseops-mcp")
    app = server.streamable_http_app()
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: Starlette) -> AsyncIterator[None]:
        async with original_lifespan(application):
            yield
        telemetry.shutdown()

    app.router.lifespan_context = lifespan
    app.add_middleware(
        RuntimeEnvelopeMiddleware,
        runtime=telemetry,
        default_timeout_seconds=settings.request_default_timeout_seconds,
        max_timeout_seconds=settings.request_max_timeout_seconds,
    )
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
