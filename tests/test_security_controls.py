from __future__ import annotations

import unittest

from caseops.agent.mcp_auth import DelegationTokenIssuer
from caseops.config import Settings
from caseops.security.contracts import DataClassification
from caseops.security.privacy import OutputGuard
from caseops.security.red_team import run_dataset
from caseops.service import Principal


class SecurityControlsTest(unittest.TestCase):
    def test_red_team_dataset_passes_without_leakage_or_side_effects(self) -> None:
        report = run_dataset()

        self.assertEqual(report["total"], 11)
        self.assertEqual(report["passed"], 11)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["unauthorized_side_effects"], 0)
        self.assertEqual(report["secret_leakage_count"], 0)

    def test_output_guard_redacts_pii_without_returning_original(self) -> None:
        decision = OutputGuard().release(
            "联系 claim.owner@example.test 或 13800138000。",
            classification=DataClassification.CONFIDENTIAL,
        )

        self.assertEqual(decision.effect, "redacted")
        self.assertNotIn("claim.owner", decision.released_text or "")
        self.assertNotIn("13800138000", decision.released_text or "")
        self.assertEqual(decision.reason_codes, ("PII_MINIMIZED",))

    def test_output_guard_blocks_canary_secret(self) -> None:
        decision = OutputGuard().release(
            "CASEOPS_CANARY_A1B2C3D4E5F6",
            classification=DataClassification.CONFIDENTIAL,
        )

        self.assertEqual(decision.effect, "blocked")
        self.assertIsNone(decision.released_text)
        self.assertEqual(decision.reason_codes, ("SECRET_CANARY_DETECTED",))

    def test_delegation_token_cannot_expand_principal_scope(self) -> None:
        settings = Settings(
            environment="test",
            database_url="sqlite+pysqlite://",
            api_keys={"test-key": "tenant-demo"},
            delegation_signing_key="test-delegation-signing-key-at-least-32-bytes",
        )
        issuer = DelegationTokenIssuer(settings)
        principal = Principal(
            "tenant-demo",
            "limited-user",
            scopes=frozenset({"case:read"}),
        )

        with self.assertRaisesRegex(ValueError, "cannot expand"):
            issuer.issue(
                principal=principal,
                task_id="task-security-001",
                scopes=frozenset({"case:read", "risk:read"}),
                resource_id="C-102",
            )


if __name__ == "__main__":
    unittest.main()
