from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LayerName = Literal[
    "contract",
    "outcome",
    "path",
    "evidence",
    "security",
    "efficiency",
]


class ExpectedResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    http_status: int
    error_code: str | None = None
    run_status: str | None = None
    outcome: str | None = None
    recommended_action: str | None = None
    side_effect: Literal["none"] | None = None

    @model_validator(mode="after")
    def require_success_or_error_contract(self) -> ExpectedResult:
        if self.http_status < 400 and (
            self.run_status is None
            or self.outcome is None
            or self.recommended_action is None
            or self.side_effect is None
        ):
            raise ValueError("successful expectation requires the complete result contract")
        if self.http_status >= 400 and self.error_code is None:
            raise ValueError("error expectation requires error_code")
        return self


class GoldenCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    title: str
    risk: Literal["critical", "high", "medium"]
    mode: Literal["system_run", "idempotent_replay", "idempotency_conflict"]
    repetitions: int = Field(ge=1, le=20)
    tags: tuple[str, ...]
    request: dict[str, object]
    changed_request: dict[str, object] | None = None
    expected: ExpectedResult

    @model_validator(mode="after")
    def require_changed_request_for_conflict(self) -> GoldenCase:
        if self.mode == "idempotency_conflict" and self.changed_request is None:
            raise ValueError("idempotency_conflict requires changed_request")
        return self


class GoldenDataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.golden-dataset.v1"]
    dataset_id: str
    version: str
    fixture_version: str
    owner: str
    claim_under_test: str
    leakage_policy: str
    cases: tuple[GoldenCase, ...]

    @model_validator(mode="after")
    def require_unique_case_ids(self) -> GoldenDataset:
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("golden case ids must be unique")
        return self


class QualityThresholds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    required_layer_score: float = Field(ge=0, le=1)
    required_consistency: float = Field(ge=0, le=1)
    max_steps: int = Field(ge=1)
    max_attempts_per_step: int = Field(ge=1)
    min_claims: int = Field(ge=0)
    min_evidence_refs: int = Field(ge=0)
    min_graph_nodes: int = Field(ge=0)
    min_graph_edges: int = Field(ge=0)


class QualityContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.quality-contract.v1"]
    version: str
    release_blocking_layers: tuple[LayerName, ...]
    thresholds: QualityThresholds
    latency_policy: str
    judge_policy: str


class BaselineCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    repetitions: int
    layer_scores: dict[LayerName, float]
    consistency: float


class EvaluationBaseline(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.evaluation-baseline.v1"]
    release: str
    dataset_version: str
    execution_mode: str
    cases: tuple[BaselineCase, ...]


class LayerResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: LayerName
    score: float = Field(ge=0, le=1)
    passed: bool
    findings: tuple[str, ...] = ()


class TrialResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trial_id: str
    case_id: str
    repetition: int
    http_status: int
    elapsed_ms: float = Field(ge=0)
    semantic_fingerprint: str
    layers: tuple[LayerResult, ...]
    diagnostics: dict[str, int | float | str | bool]


class CaseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    title: str
    risk: str
    repetitions: int
    layer_scores: dict[LayerName, float]
    consistency: float = Field(ge=0, le=1)
    passed: bool
    regressions: tuple[str, ...]
    trials: tuple[TrialResult, ...]


class EvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["caseops.evaluation-report.v1"] = "caseops.evaluation-report.v1"
    eval_run_id: str
    candidate_release: str
    baseline_release: str
    dataset_id: str
    dataset_version: str
    quality_contract_version: str
    started_at: datetime
    completed_at: datetime
    execution_mode: Literal["live-http"]
    passed: bool
    summary: dict[str, int | float | str]
    cases: tuple[CaseResult, ...]
