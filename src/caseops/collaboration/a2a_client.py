from __future__ import annotations

from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_artifact_text
from a2a.types import Message, Part, Role, SendMessageRequest, Task

from caseops.agent.mcp_auth import DelegationTokenIssuer
from caseops.config import Settings
from caseops.service import Principal

from .contracts import DelegationTask, SpecialistResult
from .specialists import DelegationRejected


class A2ASpecialistGateway:
    """Official A2A 1.0 HTTP+JSON client adapter."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._issuer = DelegationTokenIssuer(settings)

    async def execute(
        self,
        *,
        task: DelegationTask,
        principal: Principal,
    ) -> SpecialistResult:
        token = self._issuer.issue(
            principal=principal,
            task_id=task.task_id,
            resource=self._settings.a2a_resource,
            scopes=frozenset(task.required_scopes),
        )
        timeout = httpx.Timeout(self._settings.collaboration_task_timeout_seconds)
        async with (
            httpx.AsyncClient(
                timeout=timeout,
                transport=self._transport,
            ) as discovery_client,
            httpx.AsyncClient(
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
                transport=self._transport,
            ) as authenticated_client,
        ):
            resolver = A2ACardResolver(discovery_client, self._settings.a2a_url)
            card = await resolver.get_agent_card()
            client = await create_client(
                card,
                client_config=ClientConfig(
                    streaming=False,
                    polling=False,
                    httpx_client=authenticated_client,
                    supported_protocol_bindings=["HTTP+JSON"],
                ),
            )
            final_task: Task | None = None
            try:
                request = SendMessageRequest(
                    message=Message(
                        role=Role.ROLE_USER,
                        message_id=str(uuid4()),
                        context_id=task.parent_run_id,
                        parts=[Part(text=task.model_dump_json())],
                    )
                )
                async for event in client.send_message(request):
                    if event.HasField("task"):
                        final_task = event.task
            finally:
                await client.close()
        if final_task is None or not final_task.artifacts:
            raise DelegationRejected("A2A Agent returned no terminal artifact")
        payload = get_artifact_text(final_task.artifacts[-1], delimiter="")
        try:
            result = SpecialistResult.model_validate_json(payload)
        except ValueError as error:
            raise DelegationRejected("A2A artifact violates result contract") from error
        if result.task_id != task.task_id or result.specialist_id != task.specialist_id:
            raise DelegationRejected("A2A result is not bound to delegated task")
        return result
