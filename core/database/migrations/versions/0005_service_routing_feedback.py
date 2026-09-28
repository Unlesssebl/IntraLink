"""Add operator feedback for catalog-first target service decisions.

Revision ID: 0005_service_routing_feedback
Revises: 0004_case_workflow_capability_engine
Create Date: 2026-09-28 08:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_service_routing_feedback"
down_revision: Union[str, None] = "0004_case_workflow_capability_engine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "service_routing_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("compatibility_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("operator_username", sa.String(length=100), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("selected_target_service_id", sa.Integer(), nullable=False),
        sa.Column("reason_tag", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["compatibility_decision_id"],
            ["service_compatibility_decisions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_service_routing_feedback_compatibility_decision_id",
        "service_routing_feedback",
        ["compatibility_decision_id"],
    )
    op.create_index("ix_service_routing_feedback_task_id", "service_routing_feedback", ["task_id"])
    op.create_index("ix_service_routing_feedback_verdict", "service_routing_feedback", ["verdict"])
    op.create_index(
        "ix_service_routing_feedback_selected_target_service_id",
        "service_routing_feedback",
        ["selected_target_service_id"],
    )


def downgrade() -> None:
    op.drop_table("service_routing_feedback")
