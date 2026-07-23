from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CollaborationStatus(StrEnum):
    CREATED = "created"
    DISPATCHING = "dispatching"
    JOINING = "joining"
    COMPLETED = "completed"
    PARTIAL = "partial"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class TaskStatus(StrEnum):
    PLANNED = "planned"
    SUBMITTED = "submitted"
    WORKING = "working"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"


class SpecialistId(StrEnum):
    COVERAGE = "coverage"
    DOCUMENT = "document"
    RISK = "risk"


class JoinPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: Literal["all", "quorum", "timeout_partial"] = "timeout_partial"
    minimum_successes: int = Field(default=2, ge=1, le=3)
    required_specialists: tuple[SpecialistId, ...] = (
        SpecialistId.COVERAGE,
        SpecialistId.DOCUMENT,
    )
    conflict_action: Literal["needs_human"] = "needs_human"


class DelegationTask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.delegation-task.v1"] = "caseops.delegation-task.v1"
    task_id: Annotated[str, Field(min_length=8, max_length=120)]
    parent_run_id: Annotated[str, Field(min_length=8, max_length=120)]
    case_id: Annotated[str, Field(min_length=1, max_length=80)]
    specialist_id: SpecialistId
    goal: Annotated[str, Field(min_length=10, max_length=500)]
    acceptance_criteria: tuple[Annotated[str, Field(min_length=3, max_length=240)], ...]
    allowed_evidence_kinds: tuple[Annotated[str, Field(min_length=1, max_length=80)], ...]
    required_scopes: tuple[Annotated[str, Field(min_length=1, max_length=80)], ...]
    deadline_at: datetime

    @model_validator(mode="after")
    def require_non_empty_contract(self) -> DelegationTask:
        if not self.acceptance_criteria:
            raise ValueError("delegation task requires acceptance criteria")
        if not self.allowed_evidence_kinds:
            raise ValueError("delegation task requires allowed evidence kinds")
        if not self.required_scopes:
            raise ValueError("delegation task requires scopes")
        return self


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: Annotated[str, Field(min_length=1, max_length=120)]
    value: Annotated[str, Field(min_length=1, max_length=240)]
    confidence: float = Field(ge=0, le=1)
    evidence_refs: tuple[Annotated[str, Field(min_length=3, max_length=500)], ...]

    @model_validator(mode="after")
    def require_evidence(self) -> Claim:
        if not self.evidence_refs:
            raise ValueError("claim requires evidence")
        return self


class SpecialistResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.specialist-result.v1"] = "caseops.specialist-result.v1"
    task_id: str
    specialist_id: SpecialistId
    status: Literal["succeeded", "partial", "failed"]
    summary: Annotated[str, Field(min_length=1, max_length=800)]
    claims: tuple[Claim, ...] = ()
    artifacts: tuple[Annotated[str, Field(min_length=3, max_length=500)], ...] = ()
    missing_evidence: tuple[Annotated[str, Field(min_length=1, max_length=240)], ...] = ()
    error_code: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_result_semantics(self) -> SpecialistResult:
        if self.status == "failed" and not self.error_code:
            raise ValueError("failed result requires error_code")
        if self.status == "succeeded" and not self.claims:
            raise ValueError("succeeded result requires claims")
        return self


class JoinDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted_specialists: tuple[SpecialistId, ...]
    failed_specialists: tuple[SpecialistId, ...]
    missing_required_specialists: tuple[SpecialistId, ...]
    conflicts: tuple[str, ...]
    quorum_met: bool


class CollaborationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.collaboration-result.v1"] = (
        "caseops.collaboration-result.v1"
    )
    outcome: Literal[
        "COMPLETE",
        "COMPLETE_WITH_REVIEW_REQUIRED",
        "PARTIAL_EVIDENCE",
        "INSUFFICIENT_EVIDENCE",
        "CONFLICT_REQUIRES_HUMAN",
    ]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    join: JoinDecision
    claims: tuple[Claim, ...]
    evidence_refs: tuple[str, ...]
    recommended_action: Literal[
        "continue_read_only_review",
        "request_missing_evidence",
        "route_to_human_reviewer",
    ]
    side_effect: Literal["none"] = "none"


class CloudEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    specversion: Literal["1.0"] = "1.0"
    id: str
    source: str
    type: str
    subject: str
    time: datetime
    datacontenttype: Literal["application/json"] = "application/json"
    dataschema: str
    correlationid: str
    tenantid: str
    data: dict[str, object]
