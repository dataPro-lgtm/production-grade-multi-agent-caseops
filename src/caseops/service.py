from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .application import InvestigateCase
from .domain import InvestigationRequest
from .errors import IdempotencyConflict
from .infrastructure.models import (
    AuditEventRecord,
    InvestigationRecord,
    OutboxEventRecord,
)
from .infrastructure.repositories import (
    SqlAlchemyCaseRepository,
    SqlAlchemyPolicyRepository,
)


@dataclass(frozen=True, slots=True)
class Principal:
    tenant_id: str
    actor_id: str


@dataclass(frozen=True, slots=True)
class InvestigationExecution:
    investigation_id: str
    idempotency_key: str
    created_at: datetime
    replayed: bool
    result: dict[str, object]


def _request_hash(case_id: str, action: str) -> str:
    canonical = json.dumps(
        {
            "schema_version": "caseops.investigation.request.v1",
            "case_id": case_id,
            "notification_action": action,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InvestigationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def investigate(
        self,
        *,
        principal: Principal,
        case_id: str,
        notification_action: str,
        idempotency_key: str,
        request_id: str,
    ) -> InvestigationExecution:
        request_hash = _request_hash(case_id, notification_action)
        try:
            with self._session.begin():
                existing = self._find_existing(
                    principal.tenant_id,
                    idempotency_key,
                )
                if existing is not None:
                    return self._replay(existing, request_hash)

                use_case = InvestigateCase(
                    cases=SqlAlchemyCaseRepository(self._session),
                    policies=SqlAlchemyPolicyRepository(self._session),
                )
                result = use_case.execute(
                    InvestigationRequest(
                        case_id=case_id,
                        tenant_id=principal.tenant_id,
                        notification_action=notification_action,
                    )
                ).to_dict()

                investigation = InvestigationRecord(
                    tenant_id=principal.tenant_id,
                    case_id=case_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result=result,
                )
                self._session.add(investigation)
                self._session.flush()
                self._session.add_all(
                    [
                        AuditEventRecord(
                            tenant_id=principal.tenant_id,
                            actor_id=principal.actor_id,
                            action="case.investigate",
                            subject_type="case",
                            subject_id=case_id,
                            request_id=request_id,
                            outcome=str(result["decision"]["code"]),
                            details={
                                "investigation_id": investigation.id,
                                "side_effect": "none",
                            },
                        ),
                        OutboxEventRecord(
                            tenant_id=principal.tenant_id,
                            topic="case.investigation.completed.v1",
                            aggregate_type="case",
                            aggregate_id=case_id,
                            payload={
                                "investigation_id": investigation.id,
                                "case_id": case_id,
                                "decision_code": result["decision"]["code"],
                            },
                        ),
                    ]
                )
                return InvestigationExecution(
                    investigation_id=investigation.id,
                    idempotency_key=idempotency_key,
                    created_at=investigation.created_at,
                    replayed=False,
                    result=result,
                )
        except IntegrityError:
            self._session.rollback()
            existing = self._find_existing(principal.tenant_id, idempotency_key)
            if existing is None:
                raise
            return self._replay(existing, request_hash)

    def _find_existing(
        self,
        tenant_id: str,
        idempotency_key: str,
    ) -> InvestigationRecord | None:
        return self._session.scalar(
            select(InvestigationRecord).where(
                InvestigationRecord.tenant_id == tenant_id,
                InvestigationRecord.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def _replay(
        existing: InvestigationRecord,
        request_hash: str,
    ) -> InvestigationExecution:
        if existing.request_hash != request_hash:
            raise IdempotencyConflict(existing.idempotency_key)
        return InvestigationExecution(
            investigation_id=existing.id,
            idempotency_key=existing.idempotency_key,
            created_at=existing.created_at,
            replayed=True,
            result=existing.result,
        )
