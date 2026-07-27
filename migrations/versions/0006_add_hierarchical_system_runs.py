"""add hierarchical system runs and runtime context graph

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("case_id", sa.String(80), nullable=False),
        sa.Column("actor_id", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column(
            "context_run_id",
            sa.String(36),
            sa.ForeignKey("context_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "collaboration_run_id",
            sa.String(36),
            sa.ForeignKey("collaboration_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("final_result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_system_runs_tenant_idempotency",
        ),
    )
    op.create_index(
        "ix_system_runs_tenant_case",
        "system_runs",
        ["tenant_id", "case_id"],
    )
    op.create_table(
        "system_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "system_run_id",
            sa.String(36),
            sa.ForeignKey("system_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("step_key", sa.String(80), nullable=False),
        sa.Column("owner", sa.String(120), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("depends_on", sa.JSON(), nullable=False),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("result_ref", sa.String(500), nullable=True),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "system_run_id",
            "step_key",
            name="uq_system_steps_run_key",
        ),
    )
    op.create_index(
        "ix_system_steps_tenant_run",
        "system_steps",
        ["tenant_id", "system_run_id"],
    )
    op.create_table(
        "runtime_context_nodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "system_run_id",
            sa.String(36),
            sa.ForeignKey("system_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("node_key", sa.String(180), nullable=False),
        sa.Column("node_type", sa.String(40), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("owner", sa.String(120), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("ref", sa.String(500), nullable=True),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "system_run_id",
            "node_key",
            name="uq_runtime_context_nodes_run_key",
        ),
    )
    op.create_index(
        "ix_runtime_context_nodes_tenant_run",
        "runtime_context_nodes",
        ["tenant_id", "system_run_id"],
    )
    op.create_table(
        "runtime_context_edges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "system_run_id",
            sa.String(36),
            sa.ForeignKey("system_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("edge_key", sa.String(240), nullable=False),
        sa.Column("from_node_key", sa.String(180), nullable=False),
        sa.Column("to_node_key", sa.String(180), nullable=False),
        sa.Column("relation_type", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "system_run_id",
            "edge_key",
            name="uq_runtime_context_edges_run_key",
        ),
    )
    op.create_index(
        "ix_runtime_context_edges_tenant_run",
        "runtime_context_edges",
        ["tenant_id", "system_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_context_edges_tenant_run",
        table_name="runtime_context_edges",
    )
    op.drop_table("runtime_context_edges")
    op.drop_index(
        "ix_runtime_context_nodes_tenant_run",
        table_name="runtime_context_nodes",
    )
    op.drop_table("runtime_context_nodes")
    op.drop_index("ix_system_steps_tenant_run", table_name="system_steps")
    op.drop_table("system_steps")
    op.drop_index("ix_system_runs_tenant_case", table_name="system_runs")
    op.drop_table("system_runs")
