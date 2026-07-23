from __future__ import annotations

import unittest
from datetime import date, datetime

from caseops.application import InvestigateCase
from caseops.domain import (
    CaseFile,
    DocumentRequirement,
    EvidenceRef,
    InvestigationRequest,
    PolicyRule,
)
from caseops.errors import ActionNotAllowed, CaseNotFound


class FakeCaseRepository:
    def get(self, tenant_id: str, case_id: str):
        if tenant_id != "tenant-demo" or case_id != "C-102":
            raise CaseNotFound(case_id)
        return (
            CaseFile(
                case_id="C-102",
                tenant_id="tenant-demo",
                version=7,
                status="waiting_for_documents",
                policy_id="motor-claim-standard",
                policy_version="2026.1",
                submitted_at=datetime.fromisoformat("2026-07-18T09:30:00+08:00"),
                received_document_codes=(
                    "LOSS_STATEMENT",
                    "IDENTITY_DOCUMENT",
                ),
            ),
            EvidenceRef("case_snapshot", "case://C-102@7", "a" * 64),
        )


class FakePolicyRepository:
    def get(self, tenant_id: str, policy_id: str, version: str):
        if tenant_id != "tenant-demo":
            raise AssertionError("tenant boundary was not propagated")
        return (
            PolicyRule(
                policy_id=policy_id,
                version=version,
                effective_from=date(2026, 1, 1),
                effective_to=None,
                required_documents=(
                    DocumentRequirement("LOSS_STATEMENT", "损失情况说明"),
                    DocumentRequirement("IDENTITY_DOCUMENT", "身份材料"),
                    DocumentRequirement("ACCIDENT_CERTIFICATE", "事故证明"),
                ),
            ),
            EvidenceRef(
                "policy_rule",
                "policy://motor-claim-standard@2026.1",
                "b" * 64,
            ),
        )


class InvestigateCaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.use_case = InvestigateCase(
            cases=FakeCaseRepository(),
            policies=FakePolicyRepository(),
        )

    def test_c102_is_blocked_by_one_missing_document(self) -> None:
        result = self.use_case.execute(
            InvestigationRequest(case_id="C-102", tenant_id="tenant-demo")
        )

        self.assertEqual(result.decision.code, "MISSING_REQUIRED_DOCUMENTS")
        self.assertEqual(
            [document.code for document in result.missing_documents],
            ["ACCIDENT_CERTIFICATE"],
        )
        self.assertEqual(result.recommended_action.side_effect, "none")
        self.assertEqual(
            result.recommended_action.execution_policy,
            "human_approval_required",
        )

    def test_tenant_boundary_is_part_of_repository_query(self) -> None:
        with self.assertRaises(CaseNotFound):
            self.use_case.execute(
                InvestigationRequest(
                    case_id="C-102",
                    tenant_id="other-tenant",
                )
            )

    def test_send_action_is_rejected_before_repository_access(self) -> None:
        with self.assertRaises(ActionNotAllowed):
            self.use_case.execute(
                InvestigationRequest(
                    case_id="C-102",
                    tenant_id="tenant-demo",
                    notification_action="send",
                )
            )

    def test_same_input_produces_same_business_result(self) -> None:
        request = InvestigationRequest(case_id="C-102", tenant_id="tenant-demo")

        self.assertEqual(
            self.use_case.execute(request).to_dict(),
            self.use_case.execute(request).to_dict(),
        )


if __name__ == "__main__":
    unittest.main()
