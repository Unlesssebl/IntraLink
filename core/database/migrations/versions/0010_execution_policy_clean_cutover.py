"""Clean-cutover execution policy schema and seed.

Revision ID: 0010_execution_policy_clean_cutover
Revises: 0009_onboarding_delivery_hitl
Create Date: 2026-09-30 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_execution_policy_clean_cutover"
down_revision: Union[str, None] = "0009_onboarding_delivery_hitl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MUTATING_CAPABILITIES_SEED = (
    "install_printer",
    "reset_print_spooler",
    "set_default_printer",
    "create_ad_user",
    "disable_ad_user",
    "add_wlan_group_member",
)


def upgrade() -> None:
    # 1. Drop deprecated workflow_policies table
    op.execute("DROP TABLE IF EXISTS workflow_policies CASCADE")

    # 2. Create capability_execution_policies table
    op.create_table(
        "capability_execution_policies",
        sa.Column("capability_key", sa.String(length=64), nullable=False),
        sa.Column("desired_state", sa.String(length=32), server_default="disabled", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.String(length=100), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("capability_key"),
    )

    # 3. Create execution_policy_events table (append-only)
    op.create_table(
        "execution_policy_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capability_key", sa.String(length=64), nullable=False),
        sa.Column("previous_state", sa.String(length=32), nullable=True),
        sa.Column("new_state", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_policy_events_capability_key",
        "execution_policy_events",
        ["capability_key"],
    )

    # 4. Create execution_policy_decisions table (append-only)
    op.create_table(
        "execution_policy_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("capability_key", sa.String(length=64), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=True),
        sa.Column("engine_version", sa.String(length=32), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "reasons_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("action_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("phase", "capability_key", "allowed", "decision_hash", "ticket_id", "action_plan_id", "command_id"):
        op.create_index(
            f"ix_execution_policy_decisions_{col}",
            "execution_policy_decisions",
            [col],
        )

    # 5. Extend commands table with approval policy tracking
    op.add_column(
        "commands",
        sa.Column("approval_policy_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "commands",
        sa.Column("approval_policy_revision", sa.Integer(), nullable=True),
    )
    op.add_column(
        "commands",
        sa.Column("approval_policy_hash", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_commands_approval_policy_decision",
        "commands",
        "execution_policy_decisions",
        ["approval_policy_decision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_commands_approval_policy_decision_id",
        "commands",
        ["approval_policy_decision_id"],
    )

    # 6. Seed initial disabled policies for all mutating capabilities
    policies_table = sa.table(
        "capability_execution_policies",
        sa.column("capability_key", sa.String),
        sa.column("desired_state", sa.String),
        sa.column("revision", sa.Integer),
        sa.column("reason", sa.Text),
        sa.column("changed_by", sa.String),
    )
    seed_data = [
        {
            "capability_key": key,
            "desired_state": "disabled",
            "revision": 1,
            "reason": "Initial clean-cutover seed: disabled by default",
            "changed_by": "system",
        }
        for key in MUTATING_CAPABILITIES_SEED
    ]
    op.bulk_insert(policies_table, seed_data)


def downgrade() -> None:
    op.drop_constraint("fk_commands_approval_policy_decision", "commands", type_="foreignkey")
    op.drop_index("ix_commands_approval_policy_decision_id", table_name="commands")
    op.drop_column("commands", "approval_policy_hash")
    op.drop_column("commands", "approval_policy_revision")
    op.drop_column("commands", "approval_policy_decision_id")

    op.drop_table("execution_policy_decisions")
    op.drop_table("execution_policy_events")
    op.drop_table("capability_execution_policies")

    op.create_table(
        "workflow_policies",
        sa.Column("workflow_key", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), server_default="ASSISTED", nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
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
        sa.PrimaryKeyConstraint("workflow_key"),
    )
