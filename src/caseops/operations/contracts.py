from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OperationalImpact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    terminal_status: Literal["completed", "needs_human", "failed"]
    goal_succeeded: bool
    affected_tenant_count: Literal[1] = 1
    external_side_effect_count: Literal[0] = 0
    completed_step_count: int = Field(ge=0)
    failed_step_count: int = Field(ge=0)
    failed_delegated_task_count: int = Field(ge=0)


class OperationalFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: Literal[
        "context",
        "collaboration",
        "system_acceptance",
        "orchestration",
    ]
    component: str
    step_key: str
    error_code: str
    occurred_at: datetime
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class OperationalTimelineEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    occurred_at: datetime
    phase: Literal["created", "started", "completed", "failed", "terminal"]
    fact: str
    evidence_ref: str


class OperationalEvidenceInventory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    system_run_ref: str
    step_refs: tuple[str, ...]
    context_run_ref: str | None
    collaboration_run_ref: str | None
    context_graph_ref: str | None
    context_graph_node_count: int = Field(ge=0)
    context_graph_edge_count: int = Field(ge=0)
    delegated_task_count: int = Field(ge=0)
    security_decision_count: int = Field(ge=0)


class OperationalUnitMeasure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_type: Literal[
        "system_step_attempt",
        "context_run",
        "delegated_task_attempt",
        "security_decision",
    ]
    quantity: int = Field(ge=0)
    unit: Literal["attempt", "run", "decision"]
    attribution: Literal["productive", "wasted", "protective"]
    per_successful_goal: float | None = Field(default=None, ge=0)
    monetary_cost_microunits: None = None


class OperationalCostSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attribution_basis: Literal["durable-resource-counts"] = "durable-resource-counts"
    pricing_status: Literal["not_configured"] = "not_configured"
    currency: None = None
    monetary_cost_microunits: None = None
    successful_goal_count: Literal[0, 1]
    measures: tuple[OperationalUnitMeasure, ...]
    limitation: str


class OperationalControl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal[
        "none",
        "wait_for_dependency_readiness",
        "route_to_human",
        "retry_from_failed_step",
        "repair_failed_step",
    ]
    trigger: str
    safe_state: str


class OperationalVersions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    release: str
    database_revision: str
    tool_guard_policy: str
    report_schema: Literal["caseops.operational-assessment.v1"] = (
        "caseops.operational-assessment.v1"
    )


class OperationalAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.operational-assessment.v1"] = (
        "caseops.operational-assessment.v1"
    )
    assessment_id: str
    system_run_id: str
    status: Literal["healthy", "incident"]
    severity: Literal["none", "SEV-2"]
    impact: OperationalImpact
    first_failure: OperationalFailure | None
    timeline: tuple[OperationalTimelineEvent, ...] = Field(min_length=2)
    evidence: OperationalEvidenceInventory
    cost: OperationalCostSummary
    recommended_controls: tuple[OperationalControl, ...]
    versions: OperationalVersions
