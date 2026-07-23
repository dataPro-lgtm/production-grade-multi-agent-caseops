from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RetrievalChannel(StrEnum):
    STRUCTURED = "structured"
    FULL_TEXT = "full_text"
    GRAPH = "graph"
    VECTOR = "vector"


class CandidateDecision(StrEnum):
    SELECTED = "selected"
    REJECTED_SCOPE = "rejected_scope"
    REJECTED_PURPOSE = "rejected_purpose"
    REJECTED_TEMPORAL = "rejected_temporal"
    REJECTED_INTEGRITY = "rejected_integrity"
    REJECTED_UNTRUSTED_INSTRUCTION = "rejected_untrusted_instruction"
    REJECTED_DUPLICATE = "rejected_duplicate"
    REJECTED_BUDGET = "rejected_budget"


class InvestigationVerdict(StrEnum):
    COMPLETE = "complete"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ContextInvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=10, max_length=1000)
    purpose: Literal["claim_investigation"] = "claim_investigation"
    as_of: datetime
    evidence_token_budget: int = Field(default=1800, ge=400, le=8000)
    max_rounds: int = Field(default=2, ge=1, le=3)

    @field_validator("as_of")
    @classmethod
    def as_of_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of 必须包含时区")
        return value


class RetrievalPlan(BaseModel):
    schema_version: Literal["caseops.retrieval-plan.v1"] = "caseops.retrieval-plan.v1"
    question: str
    case_id: str
    purpose: str
    as_of: datetime
    channels: tuple[RetrievalChannel, ...]
    query_terms: tuple[str, ...]
    seed_entities: tuple[str, ...]
    graph_path_templates: tuple[str, ...]
    max_hops: int = Field(ge=1, le=3)
    candidate_limit: int = Field(ge=5, le=100)
    evidence_token_budget: int
    max_rounds: int


class RetrievedCandidate(BaseModel):
    object_id: str
    tenant_id: str
    source_id: str
    source_version: str
    object_type: str
    subject_id: str
    title: str
    content: str
    locator: str
    content_hash: str
    valid_from: datetime
    valid_to: datetime | None
    observed_at: datetime
    required_scopes: tuple[str, ...]
    allowed_purposes: tuple[str, ...]
    supports_claims: tuple[str, ...]
    facts: dict[str, object]
    trust_level: Literal["authoritative", "operational", "untrusted"]
    contains_instructions: bool
    channels: tuple[RetrievalChannel, ...]
    channel_ranks: dict[str, int]
    rrf_score: float


class EvidenceItem(BaseModel):
    schema_version: Literal["caseops.evidence-item.v1"] = "caseops.evidence-item.v1"
    evidence_id: str
    object_id: str
    source_id: str
    source_version: str
    object_type: str
    title: str
    content: str
    locator: str
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    valid_from: datetime
    valid_to: datetime | None
    observed_at: datetime
    supports_claims: tuple[str, ...]
    facts: dict[str, object]
    selected_by: tuple[RetrievalChannel, ...]
    rrf_score: float
    estimated_tokens: int


class ContextTraceEvent(BaseModel):
    sequence: int = Field(ge=1)
    round: int = Field(ge=1)
    stage: Literal["retrieve", "gate", "sufficiency", "answer"]
    candidate_id: str | None
    channel: RetrievalChannel | None
    decision: str
    reason: str


class ContextPack(BaseModel):
    schema_version: Literal["caseops.context-pack.v1"] = "caseops.context-pack.v1"
    pack_id: str
    run_id: str
    purpose: str
    as_of: datetime
    builder_version: Literal["context-builder-0.4.0"] = "context-builder-0.4.0"
    retrieval_plan: RetrievalPlan
    evidence: tuple[EvidenceItem, ...]
    omissions: tuple[str, ...]
    evidence_token_count: int
    evidence_token_budget: int
    retrieval_rounds: int
    stop_reason: Literal[
        "evidence_sufficient",
        "max_rounds_reached",
        "no_progress",
    ]


class AnswerClaim(BaseModel):
    claim_id: str
    statement: str
    value: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class ContextAnswer(BaseModel):
    schema_version: Literal["caseops.context-answer.v1"] = "caseops.context-answer.v1"
    verdict: InvestigationVerdict
    summary: str
    claims: tuple[AnswerClaim, ...]
    unresolved_questions: tuple[str, ...]
    recommended_action: Literal[
        "route_to_human_reviewer",
        "continue_read_only_review",
        "request_more_evidence",
    ]
    side_effect: Literal["none"] = "none"


class ContextInvestigationResult(BaseModel):
    schema_version: Literal["caseops.context-investigation-result.v1"] = (
        "caseops.context-investigation-result.v1"
    )
    case_id: str
    context_pack: ContextPack
    answer: ContextAnswer
    trace: tuple[ContextTraceEvent, ...]
