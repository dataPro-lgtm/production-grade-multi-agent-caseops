from __future__ import annotations

import unittest

from caseops.collaboration.contracts import (
    Claim,
    JoinPolicy,
    SpecialistId,
    SpecialistResult,
)
from caseops.collaboration.join import EvidenceJoin


def result(
    specialist: SpecialistId,
    *,
    key: str,
    value: str,
    ref: str,
) -> SpecialistResult:
    return SpecialistResult(
        task_id=f"task-{specialist.value}",
        specialist_id=specialist,
        status="succeeded",
        summary=f"{specialist.value} completed",
        claims=(
            Claim(
                key=key,
                value=value,
                confidence=1.0,
                evidence_refs=(ref,),
            ),
        ),
    )


class EvidenceJoinTest(unittest.TestCase):
    def setUp(self) -> None:
        self.expected = {
            specialist: f"task-{specialist.value}" for specialist in SpecialistId
        }
        self.join = EvidenceJoin()

    def test_all_results_preserve_risk_review_gate(self) -> None:
        joined = self.join.evaluate(
            expected_tasks=self.expected,
            policy=JoinPolicy(),
            results=[
                result(
                    SpecialistId.COVERAGE,
                    key="required_document_set",
                    value="A,B,C",
                    ref="policy://motor@2026.1",
                ),
                result(
                    SpecialistId.DOCUMENT,
                    key="document_completeness",
                    value="complete",
                    ref="evidence://C-102/doc@1",
                ),
                result(
                    SpecialistId.RISK,
                    key="risk_disposition",
                    value="manual_review_required",
                    ref="risk-signal://C-102/high-value@1",
                ),
            ],
        )

        self.assertEqual(joined.outcome, "COMPLETE_WITH_REVIEW_REQUIRED")
        self.assertEqual(joined.side_effect, "none")
        self.assertTrue(joined.join.quorum_met)

    def test_optional_failure_yields_explicit_partial_result(self) -> None:
        joined = self.join.evaluate(
            expected_tasks=self.expected,
            policy=JoinPolicy(),
            results=[
                result(
                    SpecialistId.COVERAGE,
                    key="required_document_set",
                    value="A,B,C",
                    ref="policy://motor@2026.1",
                ),
                result(
                    SpecialistId.DOCUMENT,
                    key="document_completeness",
                    value="complete",
                    ref="evidence://C-102/doc@1",
                ),
                SpecialistResult(
                    task_id="task-risk",
                    specialist_id=SpecialistId.RISK,
                    status="failed",
                    summary="risk source unavailable",
                    error_code="UPSTREAM_UNAVAILABLE",
                ),
            ],
        )

        self.assertEqual(joined.outcome, "PARTIAL_EVIDENCE")
        self.assertEqual(joined.join.failed_specialists, (SpecialistId.RISK,))

    def test_conflict_is_not_smoothed_into_natural_language(self) -> None:
        joined = self.join.evaluate(
            expected_tasks=self.expected,
            policy=JoinPolicy(),
            results=[
                result(
                    SpecialistId.COVERAGE,
                    key="document_completeness",
                    value="incomplete",
                    ref="case://C-102@7",
                ),
                result(
                    SpecialistId.DOCUMENT,
                    key="document_completeness",
                    value="complete",
                    ref="evidence://C-102/doc@1",
                ),
                result(
                    SpecialistId.RISK,
                    key="risk_disposition",
                    value="standard_review",
                    ref="risk-signal://C-102/value@1",
                ),
            ],
        )

        self.assertEqual(joined.outcome, "CONFLICT_REQUIRES_HUMAN")
        self.assertEqual(joined.join.conflicts, ("document_completeness",))
