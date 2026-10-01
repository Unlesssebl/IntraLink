"""Durable assignment-triggered autonomy runs and execution permits.

Revision ID: 0012_autonomy_runs
Revises: 0011_workflow_execution_authorization
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_autonomy_runs"
down_revision: Union[str, None] = "0011_workflow_execution_authorization"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSON = postgresql.JSONB(astext_type=sa.Text())


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "autonomy_ticket_state",
        sa.Column("task_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("bot_user_id", sa.Integer(), nullable=False),
        sa.Column("bot_assigned", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("assignment_epoch", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_assignment_event_key", sa.String(64), nullable=True),
        sa.Column("last_lifetime_event_key", sa.String(64), nullable=True),
        sa.Column("status_id", sa.Integer(), nullable=False),
        sa.Column("is_final", sa.Boolean(), server_default="false", nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_autonomy_ticket_state_bot_user_id", "autonomy_ticket_state", ["bot_user_id"])

    op.create_table(
        "autonomy_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("bot_user_id", sa.Integer(), nullable=False),
        sa.Column("assignment_epoch", sa.Integer(), nullable=False),
        sa.Column("assignment_event_key", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_input_event_key", sa.String(64), nullable=True),
        sa.Column("blocker_code", sa.String(128), nullable=True),
        sa.Column("snapshot_hash", sa.String(64), nullable=True),
        sa.Column(
            "action_plan_id",
            UUID,
            sa.ForeignKey("action_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("task_id", "bot_user_id", "assignment_epoch", name="uq_autonomy_run_assignment"),
    )
    for column in ("task_id", "bot_user_id", "assignment_event_key", "state", "blocker_code", "snapshot_hash", "action_plan_id", "next_attempt_at"):
        op.create_index(f"ix_autonomy_runs_{column}", "autonomy_runs", [column])

    op.create_table(
        "autonomy_run_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("autonomy_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("payload_json", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_autonomy_run_events_run_id", "autonomy_run_events", ["run_id"])
    op.create_index("ix_autonomy_run_events_kind", "autonomy_run_events", ["kind"])
    op.create_index("ix_autonomy_run_events_state", "autonomy_run_events", ["state"])

    op.create_table(
        "autonomy_comment_outbox",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("autonomy_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("dedup_key", sa.String(160), nullable=False, unique=True),
        sa.Column("phase", sa.String(64), nullable=False),
        sa.Column("blocker_code", sa.String(128), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    for column in ("run_id", "task_id", "dedup_key", "state", "next_attempt_at"):
        op.create_index(f"ix_autonomy_comment_outbox_{column}", "autonomy_comment_outbox", [column])

    op.create_table(
        "execution_permits",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("autonomy_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("action_plan_id", UUID, sa.ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("capability_key", sa.String(64), nullable=False),
        sa.Column("assignment_event_key", sa.String(128), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("params_hash", sa.String(64), nullable=False),
        sa.Column("preflight_id", UUID, sa.ForeignKey("action_preflights.id", ondelete="CASCADE"), nullable=False),
        sa.Column("checks_json", JSON, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("action_plan_id", "action_id", name="uq_execution_permit_action"),
    )
    for column in ("run_id", "task_id", "action_plan_id", "action_id", "capability_key", "snapshot_hash", "plan_hash", "params_hash", "preflight_id", "expires_at"):
        op.create_index(f"ix_execution_permits_{column}", "execution_permits", [column])

    op.add_column("action_plans", sa.Column("autonomy_run_id", UUID, nullable=True))
    op.create_foreign_key("fk_action_plans_autonomy_run", "action_plans", "autonomy_runs", ["autonomy_run_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_action_plans_autonomy_run_id", "action_plans", ["autonomy_run_id"])
    op.add_column("commands", sa.Column("autonomy_run_id", UUID, nullable=True))
    op.add_column("commands", sa.Column("execution_permit_id", UUID, nullable=True))
    op.create_foreign_key("fk_commands_autonomy_run", "commands", "autonomy_runs", ["autonomy_run_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_commands_execution_permit", "commands", "execution_permits", ["execution_permit_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_commands_autonomy_run_id", "commands", ["autonomy_run_id"])
    op.create_index("ix_commands_execution_permit_id", "commands", ["execution_permit_id"])


def downgrade() -> None:
    op.drop_index("ix_commands_execution_permit_id", table_name="commands")
    op.drop_index("ix_commands_autonomy_run_id", table_name="commands")
    op.drop_constraint("fk_commands_execution_permit", "commands", type_="foreignkey")
    op.drop_constraint("fk_commands_autonomy_run", "commands", type_="foreignkey")
    op.drop_column("commands", "execution_permit_id")
    op.drop_column("commands", "autonomy_run_id")
    op.drop_index("ix_action_plans_autonomy_run_id", table_name="action_plans")
    op.drop_constraint("fk_action_plans_autonomy_run", "action_plans", type_="foreignkey")
    op.drop_column("action_plans", "autonomy_run_id")
    op.drop_table("execution_permits")
    op.drop_table("autonomy_comment_outbox")
    op.drop_table("autonomy_run_events")
    op.drop_table("autonomy_runs")
    op.drop_table("autonomy_ticket_state")
