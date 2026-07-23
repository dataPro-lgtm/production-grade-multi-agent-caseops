from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from caseops.config import Settings
from caseops.service import Principal

from .mcp_auth import DelegationTokenIssuer
from .tools import ToolExecutionError, validate_arguments


class MCPToolExecutor:
    def __init__(
        self,
        *,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._issuer = DelegationTokenIssuer(settings)
        self._transport = transport

    async def execute(
        self,
        *,
        principal: Principal,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        validated = validate_arguments(tool_name, arguments)
        token = self._issuer.issue(principal=principal, task_id=run_id)
        headers = {"Authorization": f"Bearer {token}"}
        timeout = httpx.Timeout(self._settings.agent_tool_timeout_seconds)
        try:
            async with (
                httpx.AsyncClient(
                    headers=headers,
                    timeout=timeout,
                    transport=self._transport,
                ) as client,
                streamable_http_client(
                    self._settings.mcp_url,
                    http_client=client,
                ) as (read_stream, write_stream, _),
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(
                        seconds=self._settings.agent_tool_timeout_seconds
                    ),
                ) as session,
            ):
                await session.initialize()
                result = await session.call_tool(tool_name, validated)
        except (httpx.HTTPError, TimeoutError) as error:
            raise ToolExecutionError(
                "MCP_TRANSPORT_ERROR",
                str(error),
                transient=True,
            ) from error
        except Exception as error:
            raise ToolExecutionError(
                "MCP_PROTOCOL_ERROR",
                str(error),
                transient=False,
            ) from error
        if result.isError:
            message = "MCP tool execution failed"
            if result.content:
                message = str(getattr(result.content[0], "text", message))
            raise ToolExecutionError(
                "MCP_TOOL_ERROR",
                message,
                transient=False,
            )
        if result.structuredContent is None:
            raise ToolExecutionError(
                "MCP_OUTPUT_CONTRACT_ERROR",
                "tool did not return structuredContent",
            )
        return result.structuredContent
