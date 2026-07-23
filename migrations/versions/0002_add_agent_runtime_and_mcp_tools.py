"""add Agent runtime ledger and MCP tool data

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("document_id", sa.String(80), nullable=False),
        sa.Column("case_id", sa.String(80), nullable=False),
        sa.Column("source_label", sa.String(240), nullable=False),
        sa.Column("canonical_code", sa.String(120), nullable=True),
        sa.Column("source_ref", sa.String(500), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            name="uq_source_documents_tenant_document",
        ),
    )
    op.create_index(
        "ix_source_documents_tenant_case",
        "source_documents",
        ["tenant_id", "case_id"],
    )
    op.create_table(
        "document_aliases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("normalized_label", sa.String(240), nullable=False),
        sa.Column("canonical_code", sa.String(120), nullable=False),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "normalized_label",
            "rule_version",
            name="uq_document_aliases_tenant_label_version",
        ),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("case_id", sa.String(80), nullable=False),
        sa.Column("actor_id", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("planner_kind", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("final_result", sa.JSON(), nullable=True),
        sa.Column("stop_reason", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_agent_runs_tenant_idempotency",
        ),
    )
    op.create_index(
        "ix_agent_runs_tenant_case",
        "agent_runs",
        ["tenant_id", "case_id"],
    )
    op.create_table(
        "agent_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_agent_checkpoints_run_sequence",
        ),
    )
    op.create_index(
        "ix_agent_checkpoints_tenant_run",
        "agent_checkpoints",
        ["tenant_id", "run_id"],
    )
    op.create_table(
        "tool_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("tool_call_id", sa.String(120), nullable=False),
        sa.Column("tool_name", sa.String(120), nullable=False),
        sa.Column("tool_version", sa.String(40), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        sa.Column("action_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "run_id",
            "tool_call_id",
            name="uq_tool_executions_run_call",
        ),
    )
    op.create_index(
        "ix_tool_executions_tenant_run",
        "tool_executions",
        ["tenant_id", "run_id"],
    )
    op.create_index(
        "ix_tool_executions_fingerprint",
        "tool_executions",
        ["run_id", "action_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_executions_fingerprint", table_name="tool_executions")
    op.drop_index("ix_tool_executions_tenant_run", table_name="tool_executions")
    op.drop_table("tool_executions")
    op.drop_index(
        "ix_agent_checkpoints_tenant_run",
        table_name="agent_checkpoints",
    )
    op.drop_table("agent_checkpoints")
    op.drop_index("ix_agent_runs_tenant_case", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_table("document_aliases")
    op.drop_index(
        "ix_source_documents_tenant_case",
        table_name="source_documents",
    )
    op.drop_table("source_documents")
