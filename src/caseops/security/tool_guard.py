from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from caseops.agent.contracts import ToolDefinition

from .contracts import (
    DataClassification,
    PolicyDecision,
    SecurityContext,
    ToolSecurityManifest,
)
from .manifests import definition_digest


def _arguments_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolGuard:
    """Policy Enforcement Point between model intent and tool execution."""

    policy_version: str = "caseops.tool-policy.2026-07"

    def evaluate(
        self,
        *,
        definition: ToolDefinition | None,
        manifest: ToolSecurityManifest | None,
        arguments: dict[str, Any],
        context: SecurityContext,
        runtime_allowlist: frozenset[str],
        globally_enabled: bool = True,
    ) -> PolicyDecision:
        reasons: list[str] = []
        tool_id = definition.name if definition is not None else "unknown"
        tool_version = definition.version if definition is not None else "unknown"
        classification = (
            manifest.data_classification
            if manifest is not None
            else DataClassification.RESTRICTED
        )
        manifest_digest = manifest.digest() if manifest is not None else "missing"

        if definition is None or manifest is None:
            reasons.append("TOOL_MANIFEST_MISSING")
        else:
            tool_id = manifest.tool_id
            tool_version = manifest.tool_version
            if definition.name != manifest.tool_id:
                reasons.append("TOOL_ID_MISMATCH")
            if definition.version != manifest.tool_version:
                reasons.append("TOOL_VERSION_MISMATCH")
            if definition_digest(definition) != manifest.definition_digest:
                reasons.append("TOOL_DEFINITION_DRIFT")
            if not manifest.enabled or not globally_enabled:
                reasons.append("TOOL_DISABLED")
            if manifest.tool_id not in runtime_allowlist:
                reasons.append("TOOL_NOT_ALLOWLISTED")
            if context.purpose not in manifest.allowed_purposes:
                reasons.append("TOOL_PURPOSE_DENIED")
            if context.resource_type not in manifest.allowed_resource_types:
                reasons.append("TOOL_RESOURCE_TYPE_DENIED")
            requested_resource = arguments.get("case_id")
            if requested_resource != context.resource_id:
                reasons.append("TOOL_RESOURCE_MISMATCH")
            effective_scopes = (
                context.user_scopes & context.workload_scopes & context.delegation_scopes
            )
            if manifest.required_scope not in effective_scopes:
                reasons.append("TOOL_SCOPE_INTERSECTION_MISSING")
            if definition.required_scope != manifest.required_scope:
                reasons.append("TOOL_SCOPE_MANIFEST_DRIFT")
            if definition.risk.value != manifest.risk:
                reasons.append("TOOL_RISK_MANIFEST_DRIFT")
            if manifest.side_effect != "none" or manifest.risk != "read_only":
                reasons.append("TOOL_SIDE_EFFECT_NOT_APPROVED")

        return PolicyDecision(
            effect="deny" if reasons else "allow",
            reason_codes=tuple(reasons or ["POLICY_ALLOW"]),
            policy_version=self.policy_version,
            tool_id=tool_id,
            tool_version=tool_version,
            purpose=context.purpose,
            resource_type=context.resource_type,
            resource_id=context.resource_id,
            manifest_digest=manifest_digest,
            context_digest=context.digest(),
            arguments_hash=_arguments_hash(arguments),
            data_classification=classification,
        )
