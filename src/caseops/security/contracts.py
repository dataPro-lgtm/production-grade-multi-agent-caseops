from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ToolSecurityManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.tool-security-manifest.v1"] = (
        "caseops.tool-security-manifest.v1"
    )
    tool_id: str
    tool_version: str
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_scope: str
    allowed_purposes: frozenset[str]
    allowed_resource_types: frozenset[str]
    risk: Literal["read_only", "reversible_write", "irreversible_write"]
    data_classification: DataClassification
    side_effect: Literal["none", "reversible", "irreversible"]
    enabled: bool = True

    def digest(self) -> str:
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class SecurityContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    actor_id: str
    user_scopes: frozenset[str]
    workload_id: str
    workload_scopes: frozenset[str]
    delegation_id: str
    delegation_scopes: frozenset[str]
    purpose: str
    resource_type: str
    resource_id: str
    environment: str

    def digest(self) -> str:
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.tool-policy-decision.v1"] = (
        "caseops.tool-policy-decision.v1"
    )
    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    effect: Literal["allow", "deny"]
    reason_codes: tuple[str, ...]
    policy_version: str
    tool_id: str
    tool_version: str
    purpose: str
    resource_type: str
    resource_id: str
    manifest_digest: str
    context_digest: str
    arguments_hash: str
    data_classification: DataClassification
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
