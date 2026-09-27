"""Add routing_decisions, routing_feedback, routing_preflights and prepared_plans tables.

Revision ID: 0004_add_routing_cascade_tables
Revises: 0003_add_system_state_and_autopilot
Create Date: 2026-09-25 20:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_add_routing_cascade_tables"
down_revision: Union[str, None] = "0003_add_system_state_and_autopilot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. routing_decisions
    op.create_table(
        "routing_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("router_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("selected_scenario", sa.String(length=64), nullable=True),
        sa.Column("selected_scenario_version", sa.String(length=32), nullable=True),
        sa.Column("snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("candidates_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verifier_result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("missing_facts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("degradation_reason", sa.Text(), nullable=True),
        sa.Column("decision_reason_codes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("degraded_components_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verifier_trace_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_routing_decisions_task_id", "routing_decisions", ["task_id"])
    op.create_index("ix_routing_decisions_snapshot_hash", "routing_decisions", ["snapshot_hash"])
    op.create_index("ix_routing_decisions_state", "routing_decisions", ["state"])
    op.create_index("ix_routing_decisions_selected_scenario", "routing_decisions", ["selected_scenario"])

    # 2. routing_feedback
    op.create_table(
        "routing_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("operator_username", sa.String(length=100), server_default="operator", nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("corrected_scenario", sa.String(length=64), nullable=True),
        sa.Column("corrected_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason_tag", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["routing_decisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_routing_feedback_decision_id", "routing_feedback", ["decision_id"])
    op.create_index("ix_routing_feedback_task_id", "routing_feedback", ["task_id"])
    op.create_index("ix_routing_feedback_verdict", "routing_feedback", ["verdict"])

    # 3. routing_preflights
    op.create_table(
        "routing_preflights",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("scenario_key", sa.String(length=64), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("checks_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["routing_decisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_routing_preflights_decision_id", "routing_preflights", ["decision_id"])
    op.create_index("ix_routing_preflights_task_id", "routing_preflights", ["task_id"])
    op.create_index("ix_routing_preflights_snapshot_hash", "routing_preflights", ["snapshot_hash"])
    op.create_index("ix_routing_preflights_scenario_key", "routing_preflights", ["scenario_key"])
    op.create_index("ix_routing_preflights_status", "routing_preflights", ["status"])
    op.create_index("ix_routing_preflights_expires_at", "routing_preflights", ["expires_at"])

    # 4. prepared_plans
    op.create_table(
        "prepared_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("scenario_key", sa.String(length=64), nullable=False),
        sa.Column("preflight_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plan_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["routing_decisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["preflight_id"], ["routing_preflights.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prepared_plans_task_id", "prepared_plans", ["task_id"])
    op.create_index("ix_prepared_plans_decision_id", "prepared_plans", ["decision_id"])
    op.create_index("ix_prepared_plans_snapshot_hash", "prepared_plans", ["snapshot_hash"])
    op.create_index("ix_prepared_plans_plan_hash", "prepared_plans", ["plan_hash"])
    op.create_index("ix_prepared_plans_scenario_key", "prepared_plans", ["scenario_key"])
    op.create_index("ix_prepared_plans_state", "prepared_plans", ["state"])

    # 5. commands table updates (links to decision, plan, preflight and plan_hash)
    op.add_column("commands", sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("commands", sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("commands", sa.Column("preflight_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("commands", sa.Column("plan_hash", sa.String(length=64), nullable=True))

    op.create_foreign_key("fk_commands_decision_id", "commands", "routing_decisions", ["decision_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_commands_plan_id", "commands", "prepared_plans", ["plan_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_commands_preflight_id", "commands", "routing_preflights", ["preflight_id"], ["id"], ondelete="SET NULL")

    op.create_index("ix_commands_decision_id", "commands", ["decision_id"])
    op.create_index("ix_commands_plan_id", "commands", ["plan_id"])
    op.create_index("ix_commands_preflight_id", "commands", ["preflight_id"])
    op.create_index("ix_commands_plan_hash", "commands", ["plan_hash"])


def downgrade() -> None:
    op.drop_constraint("fk_commands_preflight_id", "commands", type_="foreignkey")
    op.drop_constraint("fk_commands_plan_id", "commands", type_="foreignkey")
    op.drop_constraint("fk_commands_decision_id", "commands", type_="foreignkey")

    op.drop_index("ix_commands_plan_hash", table_name="commands")
    op.drop_index("ix_commands_preflight_id", table_name="commands")
    op.drop_index("ix_commands_plan_id", table_name="commands")
    op.drop_index("ix_commands_decision_id", table_name="commands")

    op.drop_column("commands", "plan_hash")
    op.drop_column("commands", "preflight_id")
    op.drop_column("commands", "plan_id")
    op.drop_column("commands", "decision_id")

    op.drop_table("prepared_plans")
    op.drop_table("routing_preflights")
    op.drop_table("routing_feedback")
    op.drop_table("routing_decisions")
