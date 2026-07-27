from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from caseops.collaboration.contracts import JoinPolicy


class SystemRunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    CONSOLIDATING = "consolidating"
    COMPLETED = "completed"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class SystemStepStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SystemRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(
        default=(
            "合并受治理的上下文调查与多专业协作结论，"
            "在不执行外部动作的前提下形成系统级可验收结论。"
        ),
        min_length=10,
        max_length=500,
    )
    question: str = Field(
        default=("本案适用什么规则，材料是否满足要求，是否触发人工风险复核？"),
        min_length=10,
        max_length=1000,
    )
    as_of: datetime
    evidence_token_budget: int = Field(default=1800, ge=400, le=8000)
    max_rounds: int = Field(default=2, ge=1, le=3)
    join_policy: JoinPolicy = Field(default_factory=JoinPolicy)

    @field_validator("as_of")
    @classmethod
    def as_of_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of 必须包含时区")
        return value


class SystemPlanStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: Literal[
        "context-evidence",
        "specialist-collaboration",
        "system-acceptance",
    ]
    owner: Literal["context-team", "collaboration-team", "central-supervisor"]
    goal: str
    depends_on: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...]


class SystemPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.system-plan.v1"] = "caseops.system-plan.v1"
    steps: tuple[SystemPlanStep, ...]
    capability_snapshot: dict[str, object]

    @model_validator(mode="after")
    def validate_dag(self) -> SystemPlan:
        keys = [step.key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("system plan step keys must be unique")
        known = set(keys)
        if any(dep not in known for step in self.steps for dep in step.depends_on):
            raise ValueError("system plan dependency references unknown step")
        visiting: set[str] = set()
        visited: set[str] = set()
        by_key: dict[str, SystemPlanStep] = {step.key: step for step in self.steps}

        def visit(key: str) -> None:
            if key in visiting:
                raise ValueError("system plan must be acyclic")
            if key in visited:
                return
            visiting.add(key)
            for dependency in by_key[key].depends_on:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in keys:
            visit(key)
        return self


class SystemAcceptanceCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    check_id: str
    status: Literal["passed", "failed"]
    detail: str
    evidence_refs: tuple[str, ...] = ()


class SystemClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str
    statement: str
    value: str
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    source: Literal["context-team", "collaboration-team", "system-acceptance"]


class SystemRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.system-run-result.v1"] = "caseops.system-run-result.v1"
    outcome: Literal[
        "SYSTEM_ACCEPTED",
        "SYSTEM_ACCEPTED_WITH_HUMAN_REVIEW",
        "SYSTEM_REJECTED",
    ]
    summary: str
    checks: tuple[SystemAcceptanceCheck, ...]
    claims: tuple[SystemClaim, ...]
    evidence_refs: tuple[str, ...]
    recommended_action: Literal[
        "continue_read_only_review",
        "route_to_human_reviewer",
        "repair_failed_system_checks",
    ]
    side_effect: Literal["none"] = "none"


class ContextGraphNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_key: str
    node_type: Literal[
        "goal",
        "plan",
        "step",
        "context_pack",
        "delegated_task",
        "artifact",
        "claim",
        "evidence",
        "acceptance",
        "result",
    ]
    label: str
    owner: str
    status: str
    ref: str | None
    payload_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    classification: Literal["metadata", "internal", "confidential"]


class ContextGraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    edge_key: str
    from_node_key: str
    to_node_key: str
    relation_type: Literal[
        "DECOMPOSED_INTO",
        "DEPENDS_ON",
        "PRODUCED",
        "USED",
        "SUPPORTED_BY",
        "VALIDATED_BY",
        "DERIVED_FROM",
    ]


class RuntimeContextGraph(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.runtime-context-graph.v1"] = (
        "caseops.runtime-context-graph.v1"
    )
    system_run_id: str
    nodes: tuple[ContextGraphNode, ...]
    edges: tuple[ContextGraphEdge, ...]

    @model_validator(mode="after")
    def validate_graph(self) -> RuntimeContextGraph:
        node_keys = {node.node_key for node in self.nodes}
        if len(node_keys) != len(self.nodes):
            raise ValueError("runtime context graph node keys must be unique")
        edge_keys = {edge.edge_key for edge in self.edges}
        if len(edge_keys) != len(self.edges):
            raise ValueError("runtime context graph edge keys must be unique")
        if any(
            edge.from_node_key not in node_keys or edge.to_node_key not in node_keys
            for edge in self.edges
        ):
            raise ValueError("runtime context graph edge references unknown node")
        return self
