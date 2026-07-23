from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class CaseRecord(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", name="uq_cases_tenant_case"),
        Index("ix_cases_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    case_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(120), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    received_document_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class PolicyRecord(Base):
    __tablename__ = "policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "policy_id",
            "version",
            name="uq_policies_tenant_policy_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    required_documents: Mapped[list[dict[str, str]]] = mapped_column(
        JSON,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class InvestigationRecord(Base):
    __tablename__ = "investigations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_investigations_tenant_idempotency",
        ),
        Index("ix_investigations_tenant_case", "tenant_id", "case_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    case_id: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class AuditEventRecord(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_tenant_subject", "tenant_id", "subject_id"),
        Index("ix_audit_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(120), nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class OutboxEventRecord(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_unpublished", "published_at", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    topic: Mapped[str] = mapped_column(String(160), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SourceDocumentRecord(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "document_id",
            name="uq_source_documents_tenant_document",
        ),
        Index(
            "ix_source_documents_tenant_case",
            "tenant_id",
            "case_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    document_id: Mapped[str] = mapped_column(String(80), nullable=False)
    case_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_label: Mapped[str] = mapped_column(String(240), nullable=False)
    canonical_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class DocumentAliasRecord(Base):
    __tablename__ = "document_aliases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "normalized_label",
            "rule_version",
            name="uq_document_aliases_tenant_label_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_label: Mapped[str] = mapped_column(String(240), nullable=False)
    canonical_code: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class CaseRiskSignalRecord(Base):
    __tablename__ = "case_risk_signals"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "case_id",
            "signal_code",
            "rule_version",
            name="uq_case_risk_signals_tenant_case_signal_version",
        ),
        Index("ix_case_risk_signals_tenant_case", "tenant_id", "case_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    case_id: Mapped[str] = mapped_column(String(80), nullable=False)
    signal_code: Mapped[str] = mapped_column(String(120), nullable=False)
    signal_value: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class CollaborationRunRecord(Base):
    __tablename__ = "collaboration_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_collaboration_runs_tenant_idempotency",
        ),
        Index("ix_collaboration_runs_tenant_case", "tenant_id", "case_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    case_id: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    join_policy: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    expected_specialists: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    final_result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class DelegatedTaskRecord(Base):
    __tablename__ = "delegated_tasks"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "specialist_id",
            name="uq_delegated_tasks_run_specialist",
        ),
        Index("ix_delegated_tasks_tenant_run", "tenant_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("collaboration_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    case_id: Mapped[str] = mapped_column(String(80), nullable=False)
    specialist_id: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    allowed_evidence_kinds: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    required_scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class AgentRunRecord(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_agent_runs_tenant_idempotency",
        ),
        Index("ix_agent_runs_tenant_case", "tenant_id", "case_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    case_id: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    planner_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    step_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    final_result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class AgentCheckpointRecord(Base):
    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_agent_checkpoints_run_sequence",
        ),
        Index("ix_agent_checkpoints_tenant_run", "tenant_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class ToolExecutionRecord(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "tool_call_id",
            name="uq_tool_executions_run_call",
        ),
        Index("ix_tool_executions_tenant_run", "tenant_id", "run_id"),
        Index("ix_tool_executions_fingerprint", "run_id", "action_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(120), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(40), nullable=False)
    arguments: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class KnowledgeSourceRecord(Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_id",
            name="uq_knowledge_sources_tenant_source",
        ),
        Index("ix_knowledge_sources_tenant_active", "tenant_id", "active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    owner_team: Mapped[str] = mapped_column(String(120), nullable=False)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    refresh_sla: Mapped[str] = mapped_column(String(80), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class KnowledgeObjectRecord(Base):
    __tablename__ = "knowledge_objects"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "object_id",
            name="uq_knowledge_objects_tenant_object",
        ),
        Index("ix_knowledge_objects_tenant_subject", "tenant_id", "subject_id"),
        Index("ix_knowledge_objects_source_version", "source_record_id", "source_version"),
        Index("ix_knowledge_objects_validity", "tenant_id", "valid_from", "valid_to"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    object_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_record_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_version: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    required_scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    allowed_purposes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    supports_claims: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    facts: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    trust_level: Mapped[str] = mapped_column(String(40), nullable=False)
    contains_instructions: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class KnowledgeEntityRecord(Base):
    __tablename__ = "knowledge_entities"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "entity_key",
            name="uq_knowledge_entities_tenant_key",
        ),
        Index("ix_knowledge_entities_tenant_type", "tenant_id", "entity_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(160), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(300), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class KnowledgeRelationRecord(Base):
    __tablename__ = "knowledge_relations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "relation_id",
            name="uq_knowledge_relations_tenant_relation",
        ),
        Index("ix_knowledge_relations_from", "tenant_id", "from_entity_id"),
        Index("ix_knowledge_relations_to", "tenant_id", "to_entity_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    relation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    from_entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    to_entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    path_template: Mapped[str] = mapped_column(String(120), nullable=False)
    evidence_object_record_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_objects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class ContextRunRecord(Base):
    __tablename__ = "context_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_context_runs_tenant_idempotency",
        ),
        Index("ix_context_runs_tenant_case", "tenant_id", "case_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    case_id: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(String(80), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    retrieval_plan: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    context_pack: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    answer: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    trace: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
