"""Add delivery policy, clarification generation convergence, and durable human action requests.

Revision ID: 0009_onboarding_delivery_hitl
Revises: 0008_clarification_event_identity
Create Date: 2026-09-29 16:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_onboarding_delivery_hitl"
down_revision: Union[str, None] = "0008_clarification_event_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Extend service_route_bindings with typed delivery policy
    op.add_column(
        "service_route_bindings",
        sa.Column("delivery_policy_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # 2. Extend clarification_requests with convergence identity and provenance
    op.add_column(
        "clarification_requests",
        sa.Column("generation", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "clarification_requests",
        sa.Column("resolution_source", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "clarification_requests",
        sa.Column("resolved_snapshot_hash", sa.String(length=64), nullable=True),
    )

    # 3. Create durable human_action_requests table
    op.create_table(
        "human_action_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("workflow_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("generation", sa.Integer(), server_default="1", nullable=False),
        sa.Column("type", sa.String(length=32), server_default="single_choice", nullable=False),
        sa.Column("target_param", sa.String(length=64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "candidates_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("state", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("selected_candidate_id", sa.String(length=64), nullable=True),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workflow_plan_id"], ["workflow_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["action_plan_id"], ["action_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "action_plan_id",
            "action_id",
            "target_param",
            "generation",
            name="uq_human_action_requests_plan_param_gen",
        ),
    )
    for column in (
        "task_id",
        "workflow_plan_id",
        "action_plan_id",
        "snapshot_hash",
        "plan_hash",
        "state",
    ):
        op.create_index(
            f"ix_human_action_requests_{column}", "human_action_requests", [column]
        )
    op.create_index(
        "ix_human_action_requests_task_state",
        "human_action_requests",
        ["task_id", "state"],
    )


def downgrade() -> None:
    op.drop_table("human_action_requests")
    op.drop_column("clarification_requests", "resolved_snapshot_hash")
    op.drop_column("clarification_requests", "resolution_source")
    op.drop_column("clarification_requests", "generation")
    op.drop_column("service_route_bindings", "delivery_policy_json")
