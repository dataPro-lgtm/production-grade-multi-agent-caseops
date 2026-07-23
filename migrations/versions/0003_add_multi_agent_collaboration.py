"""add multi-Agent collaboration contracts and durable A2A tasks

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_risk_signals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("case_id", sa.String(80), nullable=False),
        sa.Column("signal_code", sa.String(120), nullable=False),
        sa.Column("signal_value", sa.JSON(), nullable=False),
        sa.Column("severity", sa.String(40), nullable=False),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("source_ref", sa.String(500), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "case_id",
            "signal_code",
            "rule_version",
            name="uq_case_risk_signals_tenant_case_signal_version",
        ),
    )
    op.create_index(
        "ix_case_risk_signals_tenant_case",
        "case_risk_signals",
        ["tenant_id", "case_id"],
    )
    op.create_table(
        "collaboration_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("case_id", sa.String(80), nullable=False),
        sa.Column("actor_id", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("join_policy", sa.JSON(), nullable=False),
        sa.Column("expected_specialists", sa.JSON(), nullable=False),
        sa.Column("final_result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_collaboration_runs_tenant_idempotency",
        ),
    )
    op.create_index(
        "ix_collaboration_runs_tenant_case",
        "collaboration_runs",
        ["tenant_id", "case_id"],
    )
    op.create_table(
        "delegated_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("collaboration_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("case_id", sa.String(80), nullable=False),
        sa.Column("specialist_id", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False),
        sa.Column("allowed_evidence_kinds", sa.JSON(), nullable=False),
        sa.Column("required_scopes", sa.JSON(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "run_id",
            "specialist_id",
            name="uq_delegated_tasks_run_specialist",
        ),
    )
    op.create_index(
        "ix_delegated_tasks_tenant_run",
        "delegated_tasks",
        ["tenant_id", "run_id"],
    )
    # Schema owned by the official A2A SDK DatabaseTaskStore. It is created
    # through Alembic so runtime startup never performs DDL.
    op.create_table(
        "a2a_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("context_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("last_updated", sa.DateTime(), nullable=True),
        sa.Column("status", sa.JSON(), nullable=False),
        sa.Column("artifacts", sa.JSON(), nullable=True),
        sa.Column("history", sa.JSON(), nullable=True),
        sa.Column("protocol_version", sa.String(16), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("a2a_tasks")
    op.drop_index("ix_delegated_tasks_tenant_run", table_name="delegated_tasks")
    op.drop_table("delegated_tasks")
    op.drop_index(
        "ix_collaboration_runs_tenant_case",
        table_name="collaboration_runs",
    )
    op.drop_table("collaboration_runs")
    op.drop_index(
        "ix_case_risk_signals_tenant_case",
        table_name="case_risk_signals",
    )
    op.drop_table("case_risk_signals")
