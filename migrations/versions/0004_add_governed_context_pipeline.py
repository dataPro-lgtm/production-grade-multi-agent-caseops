"""add governed context pipeline and evidence graph

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("source_id", sa.String(120), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("owner_team", sa.String(120), nullable=False),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("refresh_sla", sa.String(80), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            name="uq_knowledge_sources_tenant_source",
        ),
    )
    op.create_index(
        "ix_knowledge_sources_tenant_active",
        "knowledge_sources",
        ["tenant_id", "active"],
    )
    op.create_table(
        "knowledge_objects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("object_id", sa.String(160), nullable=False),
        sa.Column(
            "source_record_id",
            sa.String(36),
            sa.ForeignKey("knowledge_sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_version", sa.String(80), nullable=False),
        sa.Column("object_type", sa.String(40), nullable=False),
        sa.Column("subject_id", sa.String(120), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("locator", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("required_scopes", sa.JSON(), nullable=False),
        sa.Column("allowed_purposes", sa.JSON(), nullable=False),
        sa.Column("supports_claims", sa.JSON(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("trust_level", sa.String(40), nullable=False),
        sa.Column("contains_instructions", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "object_id",
            name="uq_knowledge_objects_tenant_object",
        ),
    )
    op.create_index(
        "ix_knowledge_objects_tenant_subject",
        "knowledge_objects",
        ["tenant_id", "subject_id"],
    )
    op.create_index(
        "ix_knowledge_objects_source_version",
        "knowledge_objects",
        ["source_record_id", "source_version"],
    )
    op.create_index(
        "ix_knowledge_objects_validity",
        "knowledge_objects",
        ["tenant_id", "valid_from", "valid_to"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE INDEX ix_knowledge_objects_search
            ON knowledge_objects
            USING gin (
              to_tsvector(
                'simple',
                coalesce(title, '') || ' ' || coalesce(content, '')
              )
            )
            """
        )
    op.create_table(
        "knowledge_entities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("entity_key", sa.String(160), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("canonical_name", sa.String(300), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "entity_key",
            name="uq_knowledge_entities_tenant_key",
        ),
    )
    op.create_index(
        "ix_knowledge_entities_tenant_type",
        "knowledge_entities",
        ["tenant_id", "entity_type"],
    )
    op.create_table(
        "knowledge_relations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("relation_id", sa.String(160), nullable=False),
        sa.Column(
            "from_entity_id",
            sa.String(36),
            sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(80), nullable=False),
        sa.Column(
            "to_entity_id",
            sa.String(36),
            sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path_template", sa.String(120), nullable=False),
        sa.Column(
            "evidence_object_record_id",
            sa.String(36),
            sa.ForeignKey("knowledge_objects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "relation_id",
            name="uq_knowledge_relations_tenant_relation",
        ),
    )
    op.create_index(
        "ix_knowledge_relations_from",
        "knowledge_relations",
        ["tenant_id", "from_entity_id"],
    )
    op.create_index(
        "ix_knowledge_relations_to",
        "knowledge_relations",
        ["tenant_id", "to_entity_id"],
    )
    op.create_table(
        "context_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("case_id", sa.String(80), nullable=False),
        sa.Column("actor_id", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("purpose", sa.String(80), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("retrieval_plan", sa.JSON(), nullable=False),
        sa.Column("context_pack", sa.JSON(), nullable=False),
        sa.Column("answer", sa.JSON(), nullable=False),
        sa.Column("trace", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_context_runs_tenant_idempotency",
        ),
    )
    op.create_index(
        "ix_context_runs_tenant_case",
        "context_runs",
        ["tenant_id", "case_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_context_runs_tenant_case", table_name="context_runs")
    op.drop_table("context_runs")
    op.drop_index("ix_knowledge_relations_to", table_name="knowledge_relations")
    op.drop_index("ix_knowledge_relations_from", table_name="knowledge_relations")
    op.drop_table("knowledge_relations")
    op.drop_index(
        "ix_knowledge_entities_tenant_type",
        table_name="knowledge_entities",
    )
    op.drop_table("knowledge_entities")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("ix_knowledge_objects_search", table_name="knowledge_objects")
    op.drop_index("ix_knowledge_objects_validity", table_name="knowledge_objects")
    op.drop_index(
        "ix_knowledge_objects_source_version",
        table_name="knowledge_objects",
    )
    op.drop_index(
        "ix_knowledge_objects_tenant_subject",
        table_name="knowledge_objects",
    )
    op.drop_table("knowledge_objects")
    op.drop_index(
        "ix_knowledge_sources_tenant_active",
        table_name="knowledge_sources",
    )
    op.drop_table("knowledge_sources")
