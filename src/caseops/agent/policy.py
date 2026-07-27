from __future__ import annotations

from dataclasses import dataclass

from caseops.security.contracts import SecurityContext
from caseops.security.manifests import TOOL_SECURITY_MANIFESTS
from caseops.security.tool_guard import ToolGuard
from caseops.service import Principal

from .contracts import ToolCall, ToolDefinition


class ToolAuthorizationDenied(PermissionError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """Runtime authorization. Planner output is input, never authority."""

    allowed_tools: frozenset[str]
    workload_scopes: frozenset[str] | None = None
    delegation_scopes: frozenset[str] | None = None
    purpose: str = "case_investigation"
    globally_enabled: bool = True
    policy_version: str = "caseops.tool-policy.2026-07"

    def authorize(
        self,
        *,
        principal: Principal,
        definition: ToolDefinition,
        call: ToolCall,
        case_id: str,
    ) -> None:
        decision = ToolGuard(policy_version=self.policy_version).evaluate(
            definition=definition,
            manifest=TOOL_SECURITY_MANIFESTS.get(definition.name),
            arguments=call.arguments,
            context=SecurityContext(
                tenant_id=principal.tenant_id,
                actor_id=principal.actor_id,
                user_scopes=principal.scopes,
                workload_id="caseops-agent-runtime",
                workload_scopes=self.workload_scopes or principal.scopes,
                delegation_id=call.call_id,
                delegation_scopes=self.delegation_scopes or principal.scopes,
                purpose=self.purpose,
                resource_type="case",
                resource_id=case_id,
                environment="runtime",
            ),
            runtime_allowlist=self.allowed_tools,
            globally_enabled=self.globally_enabled,
        )
        if decision.effect == "deny":
            code = decision.reason_codes[0]
            raise ToolAuthorizationDenied(
                code,
                f"Tool Guard denied {definition.name}: {', '.join(decision.reason_codes)}",
            )
