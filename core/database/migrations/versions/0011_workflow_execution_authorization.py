"""Workflow execution mode policy, per-action authorization and command schema.

Revision ID: 0011_workflow_execution_authorization
Revises: 0010_execution_policy_clean_cutover
Create Date: 2026-09-30 16:30:00.000000
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_workflow_execution_authorization"
down_revision: Union[str, None] = "0010_execution_policy_clean_cutover"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REGISTERED_WORKFLOWS_SEED = (
    "printer_connection_workflow",
    "printing_incident_workflow",
    "wireless_access_workflow",
    "employee_onboarding_workflow",
    "access_revocation_workflow",
    "workstation_unavailable_workflow",
    "workstation_hardware_diagnostic_workflow",
    "knowledge_consultation_workflow",
    "manual_review_workflow",
)


def upgrade() -> None:
    # 1. Create workflow_execution_modes table
    op.create_table(
        "workflow_execution_modes",
        sa.Column("workflow_key", sa.String(length=64), nullable=False),
        sa.Column("desired_mode", sa.String(length=32), server_default="ASSISTED", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.String(length=100), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("workflow_key"),
    )

    # 2. Create workflow_execution_mode_events table (append-only)
    op.create_table(
        "workflow_execution_mode_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_key", sa.String(length=64), nullable=False),
        sa.Column("previous_mode", sa.String(length=32), nullable=True),
        sa.Column("new_mode", sa.String(length=32), nullable=False),
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
        "ix_workflow_execution_mode_events_workflow_key",
        "workflow_execution_mode_events",
        ["workflow_key"],
    )

    # 3. Seed initial ASSISTED mode for all registered workflows (never FULL_AUTO in seed)
    workflow_modes_table = sa.table(
        "workflow_execution_modes",
        sa.column("workflow_key", sa.String),
        sa.column("desired_mode", sa.String),
        sa.column("revision", sa.Integer),
        sa.column("reason", sa.Text),
        sa.column("changed_by", sa.String),
    )
    seed_modes = [
        {
            "workflow_key": key,
            "desired_mode": "ASSISTED",
            "revision": 1,
            "reason": "Initial clean seed: ASSISTED mode default",
            "changed_by": "system",
        }
        for key in REGISTERED_WORKFLOWS_SEED
    ]
    op.bulk_insert(workflow_modes_table, seed_modes)

    # Also log seed events into append-only event table
    mode_events_table = sa.table(
        "workflow_execution_mode_events",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("workflow_key", sa.String),
        sa.column("previous_mode", sa.String),
        sa.column("new_mode", sa.String),
        sa.column("revision", sa.Integer),
        sa.column("reason", sa.Text),
        sa.column("changed_by", sa.String),
    )
    seed_events = [
        {
            "id": uuid.uuid4(),
            "workflow_key": key,
            "previous_mode": None,
            "new_mode": "ASSISTED",
            "revision": 1,
            "reason": "Initial clean seed: ASSISTED mode default",
            "changed_by": "system",
        }
        for key in REGISTERED_WORKFLOWS_SEED
    ]
    op.bulk_insert(mode_events_table, seed_events)

    # 4. Extend execution_policy_decisions table
    op.add_column("execution_policy_decisions", sa.Column("action_id", sa.String(length=64), nullable=True))
    op.add_column("execution_policy_decisions", sa.Column("authorization_kind", sa.String(length=32), nullable=True))
    op.add_column("execution_policy_decisions", sa.Column("workflow_policy_revision", sa.Integer(), nullable=True))
    op.add_column(
        "execution_policy_decisions",
        sa.Column("context_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_execution_policy_decisions_action_id", "execution_policy_decisions", ["action_id"])

    # Transform historical 'approval' decisions to 'authorization' phase with kind 'operator'
    op.execute(
        "UPDATE execution_policy_decisions "
        "SET phase = 'authorization', authorization_kind = 'operator' "
        "WHERE phase = 'approval'"
    )

    # 5. Clean-break rename of command authorization tracking columns
    # Drop existing foreign key and index
    op.drop_constraint("fk_commands_approval_policy_decision", "commands", type_="foreignkey")
    op.drop_index("ix_commands_approval_policy_decision_id", table_name="commands")

    op.alter_column("commands", "approval_policy_decision_id", new_column_name="authorization_decision_id")
    op.alter_column("commands", "approval_policy_revision", new_column_name="authorization_capability_policy_revision")
    op.alter_column("commands", "approval_policy_hash", new_column_name="authorization_decision_hash")
    op.add_column("commands", sa.Column("authorization_workflow_policy_revision", sa.Integer(), nullable=True))

    op.create_foreign_key(
        "fk_commands_authorization_decision",
        "commands",
        "execution_policy_decisions",
        ["authorization_decision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_commands_authorization_decision_id",
        "commands",
        ["authorization_decision_id"],
    )

    # 6. Legacy action plans: transition any old approved plans without action-specific authorization to needs_review
    op.execute(
        "UPDATE action_plans "
        "SET state = 'needs_review' "
        "WHERE state = 'approved'"
    )


def downgrade() -> None:
    # 1. Revert commands columns
    op.drop_constraint("fk_commands_authorization_decision", "commands", type_="foreignkey")
    op.drop_index("ix_commands_authorization_decision_id", table_name="commands")

    op.drop_column("commands", "authorization_workflow_policy_revision")
    op.alter_column("commands", "authorization_decision_hash", new_column_name="approval_policy_hash")
    op.alter_column("commands", "authorization_capability_policy_revision", new_column_name="approval_policy_revision")
    op.alter_column("commands", "authorization_decision_id", new_column_name="approval_policy_decision_id")

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

    # 2. Revert execution_policy_decisions
    op.execute(
        "UPDATE execution_policy_decisions "
        "SET phase = 'approval' "
        "WHERE phase = 'authorization' AND authorization_kind = 'operator'"
    )
    op.drop_index("ix_execution_policy_decisions_action_id", table_name="execution_policy_decisions")
    op.drop_column("execution_policy_decisions", "context_json")
    op.drop_column("execution_policy_decisions", "workflow_policy_revision")
    op.drop_column("execution_policy_decisions", "authorization_kind")
    op.drop_column("execution_policy_decisions", "action_id")

    # 3. Drop workflow execution modes
    op.drop_table("workflow_execution_mode_events")
    op.drop_table("workflow_execution_modes")
