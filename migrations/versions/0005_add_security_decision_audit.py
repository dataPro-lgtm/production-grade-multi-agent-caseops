"""add Tool Guard security decision audit

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("actor_id", sa.String(120), nullable=False),
        sa.Column("task_id", sa.String(120), nullable=False),
        sa.Column("tool_name", sa.String(120), nullable=False),
        sa.Column("tool_version", sa.String(40), nullable=False),
        sa.Column("effect", sa.String(20), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("purpose", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(120), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("context_digest", sa.String(64), nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        sa.Column("data_classification", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_security_decisions_tenant_task",
        "security_decisions",
        ["tenant_id", "task_id"],
    )
    op.create_index(
        "ix_security_decisions_effect_created",
        "security_decisions",
        ["effect", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_security_decisions_effect_created",
        table_name="security_decisions",
    )
    op.drop_index(
        "ix_security_decisions_tenant_task",
        table_name="security_decisions",
    )
    op.drop_table("security_decisions")
