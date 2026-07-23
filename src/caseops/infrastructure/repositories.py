from __future__ import annotations

import hashlib
import json
from datetime import UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from caseops.domain import (
    CaseFile,
    DocumentRequirement,
    EvidenceRef,
    PolicyRule,
)
from caseops.errors import CaseNotFound, DataContractError, PolicyNotFound

from .models import CaseRecord, PolicyRecord


def _evidence_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SqlAlchemyCaseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, tenant_id: str, case_id: str) -> tuple[CaseFile, EvidenceRef]:
        row = self._session.scalar(
            select(CaseRecord).where(
                CaseRecord.tenant_id == tenant_id,
                CaseRecord.case_id == case_id,
            )
        )
        if row is None:
            raise CaseNotFound(case_id)

        submitted_at = row.submitted_at
        if submitted_at.tzinfo is None:
            submitted_at = submitted_at.replace(tzinfo=UTC)
        case = CaseFile(
            case_id=row.case_id,
            tenant_id=row.tenant_id,
            version=row.version,
            status=row.status,
            policy_id=row.policy_id,
            policy_version=row.policy_version,
            submitted_at=submitted_at,
            received_document_codes=tuple(row.received_document_codes),
        )
        evidence_payload = {
            "case_id": case.case_id,
            "tenant_id": case.tenant_id,
            "version": case.version,
            "status": case.status,
            "policy_id": case.policy_id,
            "policy_version": case.policy_version,
            "submitted_at": case.submitted_at.isoformat(),
            "received_document_codes": case.received_document_codes,
        }
        return case, EvidenceRef(
            kind="case_snapshot",
            ref=f"case://{case.case_id}@{case.version}",
            sha256=_evidence_hash(evidence_payload),
        )


class SqlAlchemyPolicyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self,
        tenant_id: str,
        policy_id: str,
        version: str,
    ) -> tuple[PolicyRule, EvidenceRef]:
        row = self._session.scalar(
            select(PolicyRecord).where(
                PolicyRecord.tenant_id == tenant_id,
                PolicyRecord.policy_id == policy_id,
                PolicyRecord.version == version,
            )
        )
        if row is None:
            raise PolicyNotFound(policy_id, version)
        try:
            required_documents = tuple(
                DocumentRequirement(
                    code=document["code"],
                    name=document["name"],
                )
                for document in row.required_documents
            )
        except (KeyError, TypeError) as error:
            raise DataContractError(
                source=f"policy://{policy_id}@{version}",
                reason=str(error),
            ) from error

        policy = PolicyRule(
            policy_id=row.policy_id,
            version=row.version,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            required_documents=required_documents,
        )
        evidence_payload = {
            "policy_id": policy.policy_id,
            "version": policy.version,
            "effective_from": policy.effective_from.isoformat(),
            "effective_to": (
                policy.effective_to.isoformat() if policy.effective_to else None
            ),
            "required_documents": row.required_documents,
        }
        return policy, EvidenceRef(
            kind="policy_rule",
            ref=f"policy://{policy.policy_id}@{policy.version}",
            sha256=_evidence_hash(evidence_payload),
        )
