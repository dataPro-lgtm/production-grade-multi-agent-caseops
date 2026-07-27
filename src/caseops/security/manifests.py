from __future__ import annotations

import hashlib
import json

from caseops.agent.contracts import ToolDefinition
from caseops.agent.tools import (
    GET_CASE,
    GET_POLICY,
    LIST_DOCUMENTS,
    LIST_RISK_SIGNALS,
    RESOLVE_ALIAS,
    TOOL_REGISTRY,
)

from .contracts import DataClassification, ToolSecurityManifest


def definition_digest(definition: ToolDefinition) -> str:
    canonical = json.dumps(
        definition.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _manifest(
    tool_id: str,
    *,
    classification: DataClassification,
    purposes: frozenset[str] = frozenset({"case_investigation", "claim_investigation"}),
) -> ToolSecurityManifest:
    definition = TOOL_REGISTRY[tool_id]
    return ToolSecurityManifest(
        tool_id=definition.name,
        tool_version=definition.version,
        definition_digest=definition_digest(definition),
        required_scope=definition.required_scope,
        allowed_purposes=purposes,
        allowed_resource_types=frozenset({"case"}),
        risk=definition.risk.value,
        data_classification=classification,
        side_effect="none",
    )


TOOL_SECURITY_MANIFESTS = {
    GET_CASE: _manifest(GET_CASE, classification=DataClassification.CONFIDENTIAL),
    GET_POLICY: _manifest(GET_POLICY, classification=DataClassification.INTERNAL),
    LIST_DOCUMENTS: _manifest(
        LIST_DOCUMENTS,
        classification=DataClassification.CONFIDENTIAL,
    ),
    RESOLVE_ALIAS: _manifest(
        RESOLVE_ALIAS,
        classification=DataClassification.CONFIDENTIAL,
    ),
    LIST_RISK_SIGNALS: _manifest(
        LIST_RISK_SIGNALS,
        classification=DataClassification.RESTRICTED,
    ),
}
