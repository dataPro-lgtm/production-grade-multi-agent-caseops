from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request
from starlette.responses import JSONResponse

from caseops.config import Settings, get_settings
from caseops.database import build_engine, build_session_factory

from .mcp_auth import DelegationTokenVerifier
from .tools import (
    GET_CASE,
    GET_POLICY,
    LIST_DOCUMENTS,
    LIST_RISK_SIGNALS,
    RESOLVE_ALIAS,
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
        return _execute(factory, GET_CASE, {"case_id": case_id}, "case:read")

    def policy_requirements(case_id: str) -> dict[str, Any]:
        return _execute(factory, GET_POLICY, {"case_id": case_id}, "policy:read")

    def unclassified_documents(case_id: str) -> dict[str, Any]:
        return _execute(
            factory,
            LIST_DOCUMENTS,
            {"case_id": case_id},
            "document:read",
        )

    def resolve_alias(case_id: str, document_id: str) -> dict[str, Any]:
        return _execute(
            factory,
            RESOLVE_ALIAS,
            {"case_id": case_id, "document_id": document_id},
            "document:resolve",
        )

    def risk_signals(case_id: str) -> dict[str, Any]:
        return _execute(
            factory,
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
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
