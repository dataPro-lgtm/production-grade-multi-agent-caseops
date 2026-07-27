from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from caseops.collaboration.contracts import CollaborationResult, JoinPolicy
from caseops.context.contracts import ContextInvestigationResult


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


class AgentRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(
        default="判断案件材料是否满足其绑定规则，并给出可追溯结论。",
        min_length=10,
        max_length=500,
    )


class AgentResultResponse(BaseModel):
    schema_version: Literal["caseops.agent-result.v1"]
    outcome: Literal[
        "DOCUMENTS_COMPLETE",
        "DOCUMENTS_COMPLETE_AFTER_NORMALIZATION",
        "MISSING_REQUIRED_DOCUMENTS",
        "INSUFFICIENT_EVIDENCE",
    ]
    summary: str
    received_document_codes: list[str]
    resolved_document_codes: list[str]
    missing_document_codes: list[str]
    evidence_refs: list[str]


class AgentRunResponse(BaseModel):
    run_id: str
    idempotency_key: str
    status: Literal[
        "completed",
        "needs_human",
        "failed",
        "stopped",
        "created",
        "planning",
        "tool_proposed",
        "tool_authorized",
        "tool_running",
        "observing",
    ]
    replayed: bool
    resumed: bool
    step_count: int
    result: AgentResultResponse | None
    stop_reason: str | None
    created_at: datetime
    completed_at: datetime | None


class CollaborationRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(
        default=(
            "并行核对案件规则、材料完整性与风险信号，通过证据合同形成可追溯的协作结论。"
        ),
        min_length=10,
        max_length=500,
    )
    join_policy: JoinPolicy = Field(default_factory=JoinPolicy)


class DelegatedTaskResponse(BaseModel):
    task_id: str
    specialist_id: Literal["coverage", "document", "risk"]
    status: Literal[
        "planned",
        "submitted",
        "working",
        "succeeded",
        "failed",
        "timed_out",
        "rejected",
    ]
    attempt_count: int
    result: dict[str, object] | None
    error: dict[str, object] | None


class CollaborationRunResponse(BaseModel):
    run_id: str
    idempotency_key: str
    status: Literal[
        "created",
        "dispatching",
        "joining",
        "completed",
        "partial",
        "needs_human",
        "failed",
    ]
    replayed: bool
    result: CollaborationResult
    tasks: tuple[DelegatedTaskResponse, ...]
    created_at: datetime
    completed_at: datetime | None


class ContextRunResponse(BaseModel):
    run_id: str
    idempotency_key: str
    status: Literal["complete", "insufficient_evidence"]
    replayed: bool
    result: ContextInvestigationResult
    created_at: datetime
    completed_at: datetime


class ProblemDetails(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: str


class DependencyHealth(BaseModel):
    name: str
    status: Literal["ok", "degraded", "unavailable", "disabled"]
    critical: bool
    latency_ms: float = Field(ge=0)
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    service: str
    version: str
    checks: tuple[DependencyHealth, ...] = ()
