from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from caseops.infrastructure.models import (
    CaseRiskSignalRecord,
    DocumentAliasRecord,
    SourceDocumentRecord,
)
from caseops.infrastructure.repositories import (
    SqlAlchemyCaseRepository,
    SqlAlchemyPolicyRepository,
)
from caseops.service import Principal

from .contracts import ToolDefinition, ToolRisk

GET_CASE = "caseops_get_case_snapshot"
GET_POLICY = "caseops_get_policy_requirements"
LIST_DOCUMENTS = "caseops_list_unclassified_documents"
RESOLVE_ALIAS = "caseops_resolve_document_alias"
LIST_RISK_SIGNALS = "caseops_list_risk_signals"


class CaseArgument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=80)


class ResolveAliasArgument(CaseArgument):
    document_id: str = Field(min_length=1, max_length=80)


TOOL_ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    GET_CASE: CaseArgument,
    GET_POLICY: CaseArgument,
    LIST_DOCUMENTS: CaseArgument,
    RESOLVE_ALIAS: ResolveAliasArgument,
    LIST_RISK_SIGNALS: CaseArgument,
}

TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name=GET_CASE,
        version="1.0",
        description=(
            "Read the immutable case snapshot inside the caller's tenant boundary. "
            "Use this before reasoning about required documents."
        ),
        input_schema=CaseArgument.model_json_schema(),
        required_scope="case:read",
        risk=ToolRisk.READ_ONLY,
        timeout_seconds=5,
    ),
    ToolDefinition(
        name=GET_POLICY,
        version="1.0",
        description=(
            "Read the exact policy version bound to the case and its required "
            "document codes."
        ),
        input_schema=CaseArgument.model_json_schema(),
        required_scope="policy:read",
        risk=ToolRisk.READ_ONLY,
        timeout_seconds=5,
    ),
    ToolDefinition(
        name=LIST_DOCUMENTS,
        version="1.0",
        description=(
            "List source documents for the case that have not yet been mapped to "
            "a canonical document code."
        ),
        input_schema=CaseArgument.model_json_schema(),
        required_scope="document:read",
        risk=ToolRisk.READ_ONLY,
        timeout_seconds=5,
    ),
    ToolDefinition(
        name=RESOLVE_ALIAS,
        version="1.0",
        description=(
            "Resolve one source document label with the governed alias registry. "
            "This reads a versioned rule and does not mutate the case."
        ),
        input_schema=ResolveAliasArgument.model_json_schema(),
        required_scope="document:resolve",
        risk=ToolRisk.READ_ONLY,
        timeout_seconds=5,
    ),
    ToolDefinition(
        name=LIST_RISK_SIGNALS,
        version="1.0",
        description=(
            "List governed, structured risk signals for a case. This is read-only "
            "and does not authorize an operational decision."
        ),
        input_schema=CaseArgument.model_json_schema(),
        required_scope="risk:read",
        risk=ToolRisk.READ_ONLY,
        timeout_seconds=5,
    ),
)

TOOL_REGISTRY = {definition.name: definition for definition in TOOL_DEFINITIONS}


class ToolExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        transient: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient


class ToolExecutor(Protocol):
    async def execute(
        self,
        *,
        principal: Principal,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute one already-authorized tool call."""


def _hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    model = TOOL_ARGUMENT_MODELS.get(tool_name)
    if model is None:
        raise ToolExecutionError("TOOL_UNKNOWN", f"unknown tool: {tool_name}")
    return model.model_validate(arguments).model_dump()


@dataclass(slots=True)
class DatabaseToolExecutor:
    """In-process adapter used by unit tests and local conformance runs."""

    session_factory: sessionmaker[Session]

    async def execute(
        self,
        *,
        principal: Principal,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        del run_id
        validated = validate_arguments(tool_name, arguments)
        with self.session_factory() as session:
            return execute_database_tool(
                session=session,
                tenant_id=principal.tenant_id,
                tool_name=tool_name,
                arguments=validated,
            )


def execute_database_tool(
    *,
    session: Session,
    tenant_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    case_id = str(arguments["case_id"])
    if tool_name == GET_CASE:
        case, evidence = SqlAlchemyCaseRepository(session).get(tenant_id, case_id)
        return {
            "case_id": case.case_id,
            "case_version": case.version,
            "status": case.status,
            "policy_id": case.policy_id,
            "policy_version": case.policy_version,
            "submitted_at": case.submitted_at.isoformat(),
            "received_document_codes": list(case.received_document_codes),
            "evidence_ref": evidence.ref,
            "evidence_sha256": evidence.sha256,
        }
    if tool_name == GET_POLICY:
        case, _ = SqlAlchemyCaseRepository(session).get(tenant_id, case_id)
        policy, evidence = SqlAlchemyPolicyRepository(session).get(
            tenant_id,
            case.policy_id,
            case.policy_version,
        )
        return {
            "case_id": case.case_id,
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "effective_from": policy.effective_from.isoformat(),
            "effective_to": (
                policy.effective_to.isoformat() if policy.effective_to else None
            ),
            "required_documents": [
                {"code": document.code, "name": document.name}
                for document in policy.required_documents
            ],
            "evidence_ref": evidence.ref,
            "evidence_sha256": evidence.sha256,
        }
    if tool_name == LIST_DOCUMENTS:
        SqlAlchemyCaseRepository(session).get(tenant_id, case_id)
        rows = session.scalars(
            select(SourceDocumentRecord)
            .where(
                SourceDocumentRecord.tenant_id == tenant_id,
                SourceDocumentRecord.case_id == case_id,
                SourceDocumentRecord.canonical_code.is_(None),
            )
            .order_by(SourceDocumentRecord.document_id)
        ).all()
        documents = [
            {
                "document_id": row.document_id,
                "source_label": row.source_label,
                "source_ref": row.source_ref,
                "captured_at": row.captured_at.isoformat(),
            }
            for row in rows
        ]
        return {
            "case_id": case_id,
            "documents": documents,
            "evidence_sha256": _hash({"case_id": case_id, "documents": documents}),
        }
    if tool_name == RESOLVE_ALIAS:
        document_id = str(arguments["document_id"])
        document = session.scalar(
            select(SourceDocumentRecord).where(
                SourceDocumentRecord.tenant_id == tenant_id,
                SourceDocumentRecord.case_id == case_id,
                SourceDocumentRecord.document_id == document_id,
            )
        )
        if document is None:
            raise ToolExecutionError(
                "DOCUMENT_NOT_FOUND",
                "source document is not available inside the run boundary",
            )
        alias = session.scalar(
            select(DocumentAliasRecord)
            .where(
                DocumentAliasRecord.tenant_id == tenant_id,
                DocumentAliasRecord.normalized_label == document.source_label.strip(),
                DocumentAliasRecord.active.is_(True),
            )
            .order_by(DocumentAliasRecord.rule_version.desc())
        )
        if alias is None:
            return {
                "case_id": case_id,
                "document_id": document_id,
                "source_label": document.source_label,
                "resolved": False,
                "evidence_ref": document.source_ref,
            }
        return {
            "case_id": case_id,
            "document_id": document_id,
            "source_label": document.source_label,
            "resolved": True,
            "canonical_code": alias.canonical_code,
            "rule_version": alias.rule_version,
            "confidence": alias.confidence,
            "evidence_ref": document.source_ref,
        }
    if tool_name == LIST_RISK_SIGNALS:
        SqlAlchemyCaseRepository(session).get(tenant_id, case_id)
        risk_rows = session.scalars(
            select(CaseRiskSignalRecord)
            .where(
                CaseRiskSignalRecord.tenant_id == tenant_id,
                CaseRiskSignalRecord.case_id == case_id,
            )
            .order_by(CaseRiskSignalRecord.signal_code)
        ).all()
        signals = [
            {
                "signal_code": row.signal_code,
                "signal_value": row.signal_value,
                "severity": row.severity,
                "rule_version": row.rule_version,
                "source_ref": row.source_ref,
                "captured_at": row.captured_at.isoformat(),
            }
            for row in risk_rows
        ]
        return {
            "case_id": case_id,
            "signals": signals,
            "evidence_sha256": _hash({"case_id": case_id, "signals": signals}),
        }
    raise ToolExecutionError("TOOL_UNKNOWN", f"unknown tool: {tool_name}")
