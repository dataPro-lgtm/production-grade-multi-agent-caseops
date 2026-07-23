from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from caseops.context.contracts import (
    CandidateDecision,
    ContextTraceEvent,
    EvidenceItem,
    RetrievalPlan,
    RetrievedCandidate,
)


@dataclass(frozen=True, slots=True)
class BuildOutcome:
    evidence: tuple[EvidenceItem, ...]
    omissions: tuple[str, ...]
    trace: tuple[ContextTraceEvent, ...]
    token_count: int


def estimate_tokens(content: str) -> int:
    """Provider-neutral upper estimate; online adapters should use their tokenizer."""

    return max(1, math.ceil(len(content.encode("utf-8")) / 4))


class GovernedContextBuilder:
    def build(
        self,
        *,
        candidates: list[RetrievedCandidate],
        plan: RetrievalPlan,
        principal_scopes: frozenset[str],
        round_number: int,
        sequence_start: int,
    ) -> BuildOutcome:
        selected: list[EvidenceItem] = []
        omissions: list[str] = []
        trace: list[ContextTraceEvent] = []
        seen_hashes: set[str] = set()
        token_count = 0

        for candidate in candidates:
            decision, reason = self._gate(
                candidate=candidate,
                plan=plan,
                principal_scopes=principal_scopes,
                seen_hashes=seen_hashes,
                token_count=token_count,
            )
            trace.append(
                ContextTraceEvent(
                    sequence=sequence_start + len(trace),
                    round=round_number,
                    stage="gate",
                    candidate_id=candidate.object_id,
                    channel=None,
                    decision=decision.value,
                    reason=reason,
                )
            )
            if decision is not CandidateDecision.SELECTED:
                omissions.append(f"{candidate.object_id}:{decision.value}")
                continue

            estimated_tokens = estimate_tokens(candidate.content)
            evidence = EvidenceItem(
                evidence_id=f"ev-{candidate.content_hash[:20]}",
                object_id=candidate.object_id,
                source_id=candidate.source_id,
                source_version=candidate.source_version,
                object_type=candidate.object_type,
                title=candidate.title,
                content=candidate.content,
                locator=candidate.locator,
                content_hash=candidate.content_hash,
                valid_from=candidate.valid_from,
                valid_to=candidate.valid_to,
                observed_at=candidate.observed_at,
                supports_claims=candidate.supports_claims,
                facts=candidate.facts,
                selected_by=candidate.channels,
                rrf_score=candidate.rrf_score,
                estimated_tokens=estimated_tokens,
            )
            selected.append(evidence)
            seen_hashes.add(candidate.content_hash)
            token_count += estimated_tokens

        return BuildOutcome(
            evidence=tuple(selected),
            omissions=tuple(omissions),
            trace=tuple(trace),
            token_count=token_count,
        )

    @staticmethod
    def _gate(
        *,
        candidate: RetrievedCandidate,
        plan: RetrievalPlan,
        principal_scopes: frozenset[str],
        seen_hashes: set[str],
        token_count: int,
    ) -> tuple[CandidateDecision, str]:
        required_scopes = frozenset(candidate.required_scopes)
        if not required_scopes.issubset(principal_scopes):
            return (
                CandidateDecision.REJECTED_SCOPE,
                "required scopes are not a subset of the authenticated principal",
            )
        if plan.purpose not in candidate.allowed_purposes:
            return (
                CandidateDecision.REJECTED_PURPOSE,
                "requested purpose is not allowed by the source contract",
            )
        if candidate.valid_from > plan.as_of or (
            candidate.valid_to is not None and candidate.valid_to <= plan.as_of
        ):
            return (
                CandidateDecision.REJECTED_TEMPORAL,
                "candidate is not valid at the requested as_of timestamp",
            )
        actual_hash = hashlib.sha256(candidate.content.encode("utf-8")).hexdigest()
        if actual_hash != candidate.content_hash:
            return CandidateDecision.REJECTED_INTEGRITY, "content hash mismatch"
        if candidate.trust_level == "untrusted" and candidate.contains_instructions:
            return (
                CandidateDecision.REJECTED_UNTRUSTED_INSTRUCTION,
                "untrusted content contains executable-looking instructions",
            )
        if candidate.content_hash in seen_hashes:
            return CandidateDecision.REJECTED_DUPLICATE, "duplicate content hash"
        estimated_tokens = estimate_tokens(candidate.content)
        if token_count + estimated_tokens > plan.evidence_token_budget:
            return (
                CandidateDecision.REJECTED_BUDGET,
                "candidate would exceed the evidence token budget",
            )
        return CandidateDecision.SELECTED, "all context gates passed"
