from __future__ import annotations

from typing import Protocol

from .domain import CaseFile, EvidenceRef, PolicyRule


class CaseRepository(Protocol):
    def get(self, tenant_id: str, case_id: str) -> tuple[CaseFile, EvidenceRef]:
        """Return an immutable case snapshot and its evidence reference."""


class PolicyRepository(Protocol):
    def get(
        self,
        tenant_id: str,
        policy_id: str,
        version: str,
    ) -> tuple[PolicyRule, EvidenceRef]:
        """Return the exact policy version and its evidence reference."""
