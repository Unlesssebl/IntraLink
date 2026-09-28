"""Add durable employee onboarding clarification requests.

Revision ID: 0006_employee_onboarding_clarifications
Revises: 0005_service_routing_feedback
Create Date: 2026-09-28 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_employee_onboarding_clarifications"
down_revision: Union[str, None] = "0005_service_routing_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "clarification_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("workflow_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_key", sa.String(length=64), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("missing_facts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("baseline_event_id", sa.BigInteger(), nullable=True),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_event_id", sa.BigInteger(), nullable=True),
        sa.Column("response_facts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workflow_plan_id"], ["workflow_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_fingerprint"),
    )
    for column in ("task_id", "workflow_plan_id", "snapshot_hash", "request_fingerprint", "state"):
        op.create_index(f"ix_clarification_requests_{column}", "clarification_requests", [column])
    op.create_index(
        "ix_clarification_requests_task_state", "clarification_requests", ["task_id", "state"]
    )
    op.create_table(
        "onboarding_fact_corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("fact_key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("operator_username", sa.String(length=100), nullable=False),
        sa.Column("reason_tag", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("task_id", "snapshot_hash", "fact_key"):
        op.create_index(f"ix_onboarding_fact_corrections_{column}", "onboarding_fact_corrections", [column])


def downgrade() -> None:
    op.drop_table("onboarding_fact_corrections")
    op.drop_table("clarification_requests")
