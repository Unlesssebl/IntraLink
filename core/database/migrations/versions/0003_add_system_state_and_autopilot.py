"""Add system_state, autopilot_policies, and autopilot_corrections tables.

Revision ID: 0003_add_system_state_and_autopilot
Revises: 0002_add_fts_search_vector
Create Date: 2026-09-25 15:55:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_add_system_state_and_autopilot"
down_revision: Union[str, None] = "0002_add_fts_search_vector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. system_state
    op.create_table(
        "system_state",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_task_id", sa.Integer(), nullable=True),
        sa.Column("state_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_system_state_key", "system_state", ["key"])

    # 2. autopilot_policies
    op.create_table(
        "autopilot_policies",
        sa.Column("scenario_key", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), server_default="ASSISTED", nullable=False),
        sa.Column("min_confidence", sa.Float(), server_default="0.85", nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_circuit_broken", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("scenario_key"),
    )
    op.create_index("ix_autopilot_policies_scenario_key", "autopilot_policies", ["scenario_key"])

    # 3. autopilot_corrections
    op.create_table(
        "autopilot_corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("original_scenario", sa.String(length=64), nullable=False),
        sa.Column("corrected_scenario", sa.String(length=64), nullable=False),
        sa.Column("original_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("corrected_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("original_comment", sa.Text(), nullable=True),
        sa.Column("corrected_comment", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("factors_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("correction_tag", sa.String(length=64), server_default="general", nullable=False),
        sa.Column("operator_notes", sa.Text(), nullable=True),
        sa.Column("operator_username", sa.String(length=100), server_default="operator", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_autopilot_corrections_task_id", "autopilot_corrections", ["task_id"])
    op.create_index("ix_autopilot_corrections_original_scenario", "autopilot_corrections", ["original_scenario"])
    op.create_index("ix_autopilot_corrections_corrected_scenario", "autopilot_corrections", ["corrected_scenario"])
    op.create_index("ix_autopilot_corrections_correction_tag", "autopilot_corrections", ["correction_tag"])


def downgrade() -> None:
    op.drop_table("autopilot_corrections")
    op.drop_table("autopilot_policies")
    op.drop_table("system_state")
