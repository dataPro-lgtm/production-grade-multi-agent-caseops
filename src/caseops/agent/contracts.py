from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunStatus(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    TOOL_PROPOSED = "tool_proposed"
    TOOL_AUTHORIZED = "tool_authorized"
    TOOL_RUNNING = "tool_running"
    OBSERVING = "observing"
    COMPLETED = "completed"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"
    STOPPED = "stopped"


TERMINAL_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.NEEDS_HUMAN,
        RunStatus.FAILED,
        RunStatus.STOPPED,
    }
)


class ToolRisk(StrEnum):
    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    IRREVERSIBLE_WRITE = "irreversible_write"


class ToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    required_scope: str
    risk: ToolRisk
    timeout_seconds: float = Field(gt=0, le=60)
    max_attempts: int = Field(default=2, ge=1, le=5)

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
            "strict": True,
        }


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: Annotated[str, Field(min_length=8, max_length=120)]
    name: Annotated[str, Field(min_length=1, max_length=120)]
    arguments: dict[str, Any]

    def fingerprint(self) -> str:
        canonical = json.dumps(
            {"name": self.name, "arguments": self.arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def arguments_hash(self) -> str:
        canonical = json.dumps(
            self.arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_name: str
    fingerprint: str
    ok: bool
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempt_count: int = Field(default=1, ge=1)


class FinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["caseops.agent-result.v1"] = "caseops.agent-result.v1"
    outcome: Literal[
        "DOCUMENTS_COMPLETE",
        "DOCUMENTS_COMPLETE_AFTER_NORMALIZATION",
        "MISSING_REQUIRED_DOCUMENTS",
        "INSUFFICIENT_EVIDENCE",
    ]
    summary: Annotated[str, Field(min_length=1, max_length=800)]
    received_document_codes: list[str]
    resolved_document_codes: list[str]
    missing_document_codes: list[str]
    evidence_refs: list[str]


class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["tool_call", "final", "needs_human"]
    tool_call: ToolCall | None = None
    final_answer: FinalAnswer | None = None
    reason: str | None = Field(default=None, max_length=800)

    @model_validator(mode="after")
    def require_matching_payload(self) -> PlannerDecision:
        if self.kind == "tool_call" and self.tool_call is None:
            raise ValueError("tool_call decision requires tool_call")
        if self.kind == "final" and self.final_answer is None:
            raise ValueError("final decision requires final_answer")
        if self.kind == "needs_human" and not self.reason:
            raise ValueError("needs_human decision requires reason")
        return self


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    tenant_id: str
    case_id: str
    goal: str
    status: RunStatus = RunStatus.CREATED
    sequence: int = Field(default=0, ge=0)
    step_count: int = Field(default=0, ge=0)
    max_steps: int = Field(ge=1)
    repeat_limit: int = Field(ge=1)
    recovery_count: int = Field(default=0, ge=0)
    pending_call: ToolCall | None = None
    observations: list[ToolObservation] = Field(default_factory=list)
    fingerprint_counts: dict[str, int] = Field(default_factory=dict)
    final_answer: FinalAnswer | None = None
    stop_reason: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES
