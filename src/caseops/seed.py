from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .infrastructure.models import CaseRecord, PolicyRecord


def seed_reference_data(session: Session) -> None:
    tenant_id = "tenant-demo"
    policy = session.scalar(
        select(PolicyRecord).where(
            PolicyRecord.tenant_id == tenant_id,
            PolicyRecord.policy_id == "motor-claim-standard",
            PolicyRecord.version == "2026.1",
        )
    )
    if policy is None:
        session.add(
            PolicyRecord(
                tenant_id=tenant_id,
                policy_id="motor-claim-standard",
                version="2026.1",
                effective_from=date(2026, 1, 1),
                effective_to=None,
                required_documents=[
                    {"code": "LOSS_STATEMENT", "name": "损失情况说明"},
                    {"code": "IDENTITY_DOCUMENT", "name": "身份材料"},
                    {"code": "ACCIDENT_CERTIFICATE", "name": "事故证明"},
                ],
            )
        )

    case = session.scalar(
        select(CaseRecord).where(
            CaseRecord.tenant_id == tenant_id,
            CaseRecord.case_id == "C-102",
        )
    )
    if case is None:
        session.add(
            CaseRecord(
                tenant_id=tenant_id,
                case_id="C-102",
                version=7,
                status="waiting_for_documents",
                policy_id="motor-claim-standard",
                policy_version="2026.1",
                submitted_at=datetime.fromisoformat("2026-07-18T09:30:00+08:00"),
                received_document_codes=[
                    "LOSS_STATEMENT",
                    "IDENTITY_DOCUMENT",
                ],
            )
        )
