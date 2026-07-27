"""add operational assessments and cost attribution events

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column(
            "system_run_id",
            sa.String(36),
            sa.ForeignKey("system_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("report_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_operational_assessments_tenant_idempotency",
        ),
    )
    op.create_index(
        "ix_operational_assessments_tenant_run",
        "operational_assessments",
        ["tenant_id", "system_run_id"],
    )
    op.create_table(
        "operational_cost_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column(
            "system_run_id",
            sa.String(36),
            sa.ForeignKey("system_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assessment_id",
            sa.String(36),
            sa.ForeignKey("operational_assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dimension", sa.String(40), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("attribution", sa.String(40), nullable=False),
        sa.Column("monetary_cost_microunits", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "assessment_id",
            "dimension",
            "resource_type",
            name="uq_operational_cost_events_assessment_dimension_resource",
        ),
    )
    op.create_index(
        "ix_operational_cost_events_tenant_run",
        "operational_cost_events",
        ["tenant_id", "system_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operational_cost_events_tenant_run",
        table_name="operational_cost_events",
    )
    op.drop_table("operational_cost_events")
    op.drop_index(
        "ix_operational_assessments_tenant_run",
        table_name="operational_assessments",
    )
    op.drop_table("operational_assessments")
