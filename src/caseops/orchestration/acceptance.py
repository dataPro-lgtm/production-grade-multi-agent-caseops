from __future__ import annotations

from caseops.collaboration.contracts import CollaborationResult
from caseops.context.contracts import ContextInvestigationResult

from .contracts import SystemAcceptanceCheck, SystemClaim, SystemRunResult


class SystemAcceptance:
    """Deterministic cross-team reducer; no model may smooth over disagreement."""

    def evaluate(
        self,
        *,
        context: ContextInvestigationResult,
        collaboration: CollaborationResult,
    ) -> SystemRunResult:
        context_claims = {claim.claim_id: claim for claim in context.answer.claims}
        collaboration_claims = {claim.key: claim for claim in collaboration.claims}
        evidence_by_id = {
            item.evidence_id: item.locator for item in context.context_pack.evidence
        }
        all_context_refs = tuple(
            dict.fromkeys(
                evidence_by_id[evidence_id]
                for claim in context.answer.claims
                for evidence_id in claim.evidence_ids
            )
        )
        all_refs = tuple(dict.fromkeys((*all_context_refs, *collaboration.evidence_refs)))

        policy = context_claims.get("claim-policy-version")
        documents = context_claims.get("claim-document-status")
        risk = context_claims.get("claim-manual-review")
        coverage = collaboration_claims.get("required_document_set")
        document_result = collaboration_claims.get("document_completeness")
        risk_result = collaboration_claims.get("risk_disposition")

        checks = (
            self._check(
                "context-complete",
                context.answer.verdict.value == "complete",
                f"context verdict={context.answer.verdict.value}",
                all_context_refs,
            ),
            self._check(
                "collaboration-complete",
                collaboration.outcome in {"COMPLETE", "COMPLETE_WITH_REVIEW_REQUIRED"},
                f"collaboration outcome={collaboration.outcome}",
                collaboration.evidence_refs,
            ),
            self._check(
                "policy-version-consistent",
                policy is not None
                and coverage is not None
                and any(
                    ref.endswith(f"@{policy.value}")
                    for ref in coverage.evidence_refs
                    if ref.startswith("policy://")
                ),
                "context policy version must match the case-bound specialist evidence",
                () if policy is None or coverage is None else coverage.evidence_refs,
            ),
            self._check(
                "document-status-consistent",
                documents is not None
                and document_result is not None
                and documents.value.startswith("satisfied:")
                and document_result.value == "complete",
                "context and document specialist must agree on material completeness",
                (
                    ()
                    if documents is None or document_result is None
                    else tuple(
                        dict.fromkeys(
                            (
                                *self._context_refs(documents.evidence_ids, evidence_by_id),
                                *document_result.evidence_refs,
                            )
                        )
                    )
                ),
            ),
            self._check(
                "risk-gate-consistent",
                risk is not None
                and risk_result is not None
                and (
                    ("required=true" in risk.value)
                    == (risk_result.value == "manual_review_required")
                ),
                "context rule evaluation and risk specialist must agree",
                (
                    ()
                    if risk is None or risk_result is None
                    else tuple(
                        dict.fromkeys(
                            (
                                *self._context_refs(risk.evidence_ids, evidence_by_id),
                                *risk_result.evidence_refs,
                            )
                        )
                    )
                ),
            ),
            self._check(
                "claims-evidence-bound",
                bool(context.answer.claims)
                and bool(collaboration.claims)
                and all(claim.evidence_ids for claim in context.answer.claims)
                and all(claim.evidence_refs for claim in collaboration.claims),
                "every accepted claim must retain at least one evidence reference",
                all_refs,
            ),
            self._check(
                "side-effect-free",
                context.answer.side_effect == "none"
                and collaboration.side_effect == "none",
                "both child teams must remain read-only",
                (),
            ),
        )
        accepted = all(check.status == "passed" for check in checks)
        human_review = (
            accepted
            and risk_result is not None
            and risk_result.value == "manual_review_required"
        )
        claims = self._claims(
            context=context,
            collaboration=collaboration,
            evidence_by_id=evidence_by_id,
        )
        if not accepted:
            return SystemRunResult(
                outcome="SYSTEM_REJECTED",
                summary=(
                    "跨团队验收发现未满足项；系统保留证据和异议，"
                    "不生成可继续执行的自动结论。"
                ),
                checks=checks,
                claims=claims,
                evidence_refs=all_refs,
                recommended_action="repair_failed_system_checks",
            )
        return SystemRunResult(
            outcome=(
                "SYSTEM_ACCEPTED_WITH_HUMAN_REVIEW" if human_review else "SYSTEM_ACCEPTED"
            ),
            summary=(
                "上下文证据、专业协作结论与关键业务门禁已通过系统级一致性验收；"
                + (
                    "风险条件要求人工复核，系统未执行任何外部动作。"
                    if human_review
                    else "系统可继续只读审查，未执行任何外部动作。"
                )
            ),
            checks=checks,
            claims=claims,
            evidence_refs=all_refs,
            recommended_action=(
                "route_to_human_reviewer" if human_review else "continue_read_only_review"
            ),
        )

    @staticmethod
    def _check(
        check_id: str,
        passed: bool,
        detail: str,
        evidence_refs: tuple[str, ...],
    ) -> SystemAcceptanceCheck:
        return SystemAcceptanceCheck(
            check_id=check_id,
            status="passed" if passed else "failed",
            detail=detail,
            evidence_refs=evidence_refs,
        )

    @staticmethod
    def _context_refs(
        evidence_ids: tuple[str, ...],
        evidence_by_id: dict[str, str],
    ) -> tuple[str, ...]:
        return tuple(evidence_by_id[evidence_id] for evidence_id in evidence_ids)

    def _claims(
        self,
        *,
        context: ContextInvestigationResult,
        collaboration: CollaborationResult,
        evidence_by_id: dict[str, str],
    ) -> tuple[SystemClaim, ...]:
        context_claims = tuple(
            SystemClaim(
                claim_id=claim.claim_id,
                statement=claim.statement,
                value=claim.value,
                evidence_refs=self._context_refs(claim.evidence_ids, evidence_by_id),
                source="context-team",
            )
            for claim in context.answer.claims
        )
        collaboration_claims = tuple(
            SystemClaim(
                claim_id=f"specialist-{claim.key}",
                statement=f"专业团队结论：{claim.key}",
                value=claim.value,
                evidence_refs=claim.evidence_refs,
                source="collaboration-team",
            )
            for claim in collaboration.claims
        )
        return (*context_claims, *collaboration_claims)
