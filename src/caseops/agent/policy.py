from __future__ import annotations

from dataclasses import dataclass

from caseops.service import Principal

from .contracts import ToolCall, ToolDefinition, ToolRisk


class ToolAuthorizationDenied(PermissionError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """Runtime authorization. Planner output is input, never authority."""

    allowed_tools: frozenset[str]

    def authorize(
        self,
        *,
        principal: Principal,
        definition: ToolDefinition,
        call: ToolCall,
        case_id: str,
    ) -> None:
        if definition.name not in self.allowed_tools:
            raise ToolAuthorizationDenied(
                "TOOL_NOT_ALLOWLISTED",
                f"tool {definition.name} is not in the runtime allowlist",
            )
        if definition.required_scope not in principal.scopes:
            raise ToolAuthorizationDenied(
                "TOOL_SCOPE_MISSING",
                f"missing required scope: {definition.required_scope}",
            )
        if definition.risk is not ToolRisk.READ_ONLY:
            raise ToolAuthorizationDenied(
                "TOOL_RISK_NOT_ALLOWED",
                "Slice 1 permits read-only tools only",
            )
        requested_case_id = call.arguments.get("case_id")
        if requested_case_id != case_id:
            raise ToolAuthorizationDenied(
                "TOOL_RESOURCE_MISMATCH",
                "tool proposal attempted to leave the run's case boundary",
            )
