from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .infrastructure.models import (
    CaseRecord,
    CaseRiskSignalRecord,
    DocumentAliasRecord,
    PolicyRecord,
    SourceDocumentRecord,
)


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

    source_document = session.scalar(
        select(SourceDocumentRecord).where(
            SourceDocumentRecord.tenant_id == tenant_id,
            SourceDocumentRecord.document_id == "DOC-C102-003",
        )
    )
    if source_document is None:
        session.add(
            SourceDocumentRecord(
                tenant_id=tenant_id,
                document_id="DOC-C102-003",
                case_id="C-102",
                source_label="道路交通事故认定书",
                canonical_code=None,
                source_ref="evidence://C-102/DOC-C102-003@1",
                captured_at=datetime.fromisoformat("2026-07-18T09:24:00+08:00"),
            )
        )

    alias = session.scalar(
        select(DocumentAliasRecord).where(
            DocumentAliasRecord.tenant_id == tenant_id,
            DocumentAliasRecord.normalized_label == "道路交通事故认定书",
            DocumentAliasRecord.rule_version == "2026.1",
        )
    )
    if alias is None:
        session.add(
            DocumentAliasRecord(
                tenant_id=tenant_id,
                normalized_label="道路交通事故认定书",
                canonical_code="ACCIDENT_CERTIFICATE",
                rule_version="2026.1",
                confidence=1.0,
                active=True,
            )
        )

    risk_signals = (
        (
            "CLAIM_AMOUNT",
            {"amount": 128000, "currency": "CNY"},
            "medium",
            "risk-signal://C-102/claim-amount@1",
        ),
        (
            "POLICY_TENURE_DAYS",
            {"days": 12},
            "medium",
            "risk-signal://C-102/policy-tenure@1",
        ),
    )
    for signal_code, signal_value, severity, source_ref in risk_signals:
        existing = session.scalar(
            select(CaseRiskSignalRecord).where(
                CaseRiskSignalRecord.tenant_id == tenant_id,
                CaseRiskSignalRecord.case_id == "C-102",
                CaseRiskSignalRecord.signal_code == signal_code,
                CaseRiskSignalRecord.rule_version == "2026.1",
            )
        )
        if existing is None:
            session.add(
                CaseRiskSignalRecord(
                    tenant_id=tenant_id,
                    case_id="C-102",
                    signal_code=signal_code,
                    signal_value=signal_value,
                    severity=severity,
                    rule_version="2026.1",
                    source_ref=source_ref,
                    captured_at=datetime.fromisoformat("2026-07-18T09:26:00+08:00"),
                )
            )
