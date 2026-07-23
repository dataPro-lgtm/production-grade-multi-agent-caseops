from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InvestigationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_action: Literal["draft"] = "draft"


class DocumentRequirementResponse(BaseModel):
    code: str
    name: str


class DecisionResponse(BaseModel):
    code: str
    explanation: str


class RecommendedActionResponse(BaseModel):
    type: str
    execution_policy: str
    side_effect: str


class DraftNotificationResponse(BaseModel):
    subject: str
    body: str


class EvidenceRefResponse(BaseModel):
    kind: str
    ref: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class InvestigationResultResponse(BaseModel):
    schema_version: Literal["caseops.investigation.v1"]
    case_id: str
    tenant_id: str
    case_status: str
    decision: DecisionResponse
    missing_documents: list[DocumentRequirementResponse]
    recommended_action: RecommendedActionResponse
    draft_notification: DraftNotificationResponse | None
    evidence: list[EvidenceRefResponse]


class InvestigationResponse(BaseModel):
    investigation_id: str
    idempotency_key: str
    created_at: datetime
    replayed: bool
    result: InvestigationResultResponse


class ProblemDetails(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
