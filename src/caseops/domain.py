from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class CaseFile:
    case_id: str
    tenant_id: str
    version: int
    status: str
    policy_id: str
    policy_version: str
    submitted_at: datetime
    received_document_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DocumentRequirement:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class PolicyRule:
    policy_id: str
    version: str
    effective_from: date
    effective_to: date | None
    required_documents: tuple[DocumentRequirement, ...]

    def is_effective_on(self, day: date) -> bool:
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to


@dataclass(frozen=True, slots=True)
class InvestigationRequest:
    case_id: str
    tenant_id: str
    notification_action: str = "draft"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: str
    ref: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Decision:
    code: str
    explanation: str


@dataclass(frozen=True, slots=True)
class RecommendedAction:
    type: str
    execution_policy: str
    side_effect: str


@dataclass(frozen=True, slots=True)
class DraftNotification:
    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class InvestigationResult:
    schema_version: str
    case_id: str
    tenant_id: str
    case_status: str
    decision: Decision
    missing_documents: tuple[DocumentRequirement, ...]
    recommended_action: RecommendedAction
    draft_notification: DraftNotification | None
    evidence: tuple[EvidenceRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
