"""Consolidate routing feedback, migrate legacy corrections and remove autopilot_corrections table.

Revision ID: 0005_routing_feedback_consolidation
Revises: 0004_add_routing_cascade_tables
Create Date: 2026-09-26 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_routing_feedback_consolidation"
down_revision: Union[str, None] = "0004_add_routing_cascade_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update routing_feedback table schema
    # Make decision_id nullable to allow legacy records without proven decision provenance
    op.alter_column("routing_feedback", "decision_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)

    # Add new provenance and tracking columns
    op.add_column("routing_feedback", sa.Column("prepared_plan_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("routing_feedback", sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("routing_feedback", sa.Column("snapshot_hash", sa.String(length=64), nullable=True))
    op.add_column("routing_feedback", sa.Column("original_scenario", sa.String(length=64), nullable=True))
    op.add_column(
        "routing_feedback",
        sa.Column("original_params", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
    )
    op.add_column("routing_feedback", sa.Column("operator_notes", sa.Text(), nullable=True))
    op.add_column("routing_feedback", sa.Column("router_version", sa.String(length=64), nullable=True))
    op.add_column("routing_feedback", sa.Column("prompt_version", sa.String(length=64), nullable=True))
    op.add_column(
        "routing_feedback",
        sa.Column("verifier_used", sa.Boolean(), server_default="false", nullable=True),
    )
    op.add_column(
        "routing_feedback",
        sa.Column("source", sa.String(length=32), server_default="runtime", nullable=False),
    )

    # Foreign key constraints and indices
    op.create_foreign_key(
        "fk_routing_feedback_prepared_plan_id",
        "routing_feedback",
        "prepared_plans",
        ["prepared_plan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_routing_feedback_command_id",
        "routing_feedback",
        "commands",
        ["command_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index("ix_routing_feedback_prepared_plan_id", "routing_feedback", ["prepared_plan_id"])
    op.create_index("ix_routing_feedback_command_id", "routing_feedback", ["command_id"])
    op.create_index("ix_routing_feedback_snapshot_hash", "routing_feedback", ["snapshot_hash"])
    op.create_index("ix_routing_feedback_original_scenario", "routing_feedback", ["original_scenario"])
    op.create_index("ix_routing_feedback_source", "routing_feedback", ["source"])

    # 2. Migrate legacy data from autopilot_corrections into routing_feedback
    # Provenance join: check if decision_id actually exists in routing_decisions.
    # Discovered entries get source='legacy_verified', otherwise source='legacy_unverified' (excluded from calibration export).
    op.execute(
        """
        INSERT INTO routing_feedback (
            id,
            decision_id,
            prepared_plan_id,
            command_id,
            task_id,
            snapshot_hash,
            operator_username,
            verdict,
            original_scenario,
            corrected_scenario,
            original_params,
            corrected_params,
            reason_tag,
            operator_notes,
            notes,
            router_version,
            prompt_version,
            verifier_used,
            source,
            created_at,
            updated_at
        )
        SELECT
            ac.id,
            rd.id AS decision_id,
            NULL AS prepared_plan_id,
            NULL AS command_id,
            ac.task_id,
            NULL AS snapshot_hash,
            COALESCE(ac.operator_username, 'operator'),
            CASE
                WHEN ac.correction_tag = 'approved' THEN 'approved'
                ELSE 'corrected'
            END AS verdict,
            ac.original_scenario,
            ac.corrected_scenario,
            COALESCE(ac.original_params, '{}'::jsonb),
            COALESCE(ac.corrected_params, '{}'::jsonb),
            ac.correction_tag,
            ac.operator_notes,
            ac.operator_notes,
            rd.router_version,
            rd.prompt_version,
            false AS verifier_used,
            CASE
                WHEN rd.id IS NOT NULL THEN 'legacy_verified'
                ELSE 'legacy_unverified'
            END AS source,
            ac.created_at,
            ac.updated_at
        FROM autopilot_corrections ac
        LEFT JOIN routing_decisions rd ON (
            ac.factors_snapshot IS NOT NULL
            AND ac.factors_snapshot->>'decision_id' ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
            AND rd.id = (ac.factors_snapshot->>'decision_id')::uuid
        )
        WHERE NOT EXISTS (
            SELECT 1 FROM routing_feedback rf
            WHERE rf.id = ac.id
               OR (
                   rf.task_id = ac.task_id
                   AND rf.original_scenario = ac.original_scenario
                   AND rf.corrected_scenario = ac.corrected_scenario
                   AND COALESCE(rf.reason_tag, '') = COALESCE(ac.correction_tag, '')
               )
        );
        """
    )

    # 3. Drop legacy autopilot_corrections table
    op.drop_table("autopilot_corrections")

    # 4. Remove min_confidence from autopilot_policies if present
    try:
        op.drop_column("autopilot_policies", "min_confidence")
    except Exception:
        pass


def downgrade() -> None:
    # 1. Recreate autopilot_policies min_confidence column
    try:
        op.add_column("autopilot_policies", sa.Column("min_confidence", sa.Float(), server_default="0.85", nullable=False))
    except Exception:
        pass

    # 2. Recreate autopilot_corrections table
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

    # 3. Copy records back from routing_feedback to autopilot_corrections
    op.execute(
        """
        INSERT INTO autopilot_corrections (
            id,
            task_id,
            original_scenario,
            corrected_scenario,
            original_params,
            corrected_params,
            confidence,
            factors_snapshot,
            correction_tag,
            operator_notes,
            operator_username,
            created_at,
            updated_at
        )
        SELECT
            rf.id,
            rf.task_id,
            COALESCE(rf.original_scenario, rf.corrected_scenario, 'unknown'),
            COALESCE(rf.corrected_scenario, rf.original_scenario, 'unknown'),
            COALESCE(rf.original_params, '{}'::jsonb),
            COALESCE(rf.corrected_params, '{}'::jsonb),
            0.0,
            jsonb_build_object('decision_id', rf.decision_id, 'source', rf.source),
            COALESCE(rf.reason_tag, 'general'),
            COALESCE(rf.operator_notes, rf.notes),
            rf.operator_username,
            rf.created_at,
            rf.updated_at
        FROM routing_feedback rf
        WHERE rf.verdict IN ('approved', 'corrected');
        """
    )

    # 4. Remove columns from routing_feedback
    op.drop_constraint("fk_routing_feedback_command_id", "routing_feedback", type_="foreignkey")
    op.drop_constraint("fk_routing_feedback_prepared_plan_id", "routing_feedback", type_="foreignkey")

    op.drop_index("ix_routing_feedback_source", table_name="routing_feedback")
    op.drop_index("ix_routing_feedback_original_scenario", table_name="routing_feedback")
    op.drop_index("ix_routing_feedback_snapshot_hash", table_name="routing_feedback")
    op.drop_index("ix_routing_feedback_command_id", table_name="routing_feedback")
    op.drop_index("ix_routing_feedback_prepared_plan_id", table_name="routing_feedback")

    op.drop_column("routing_feedback", "source")
    op.drop_column("routing_feedback", "verifier_used")
    op.drop_column("routing_feedback", "prompt_version")
    op.drop_column("routing_feedback", "router_version")
    op.drop_column("routing_feedback", "operator_notes")
    op.drop_column("routing_feedback", "original_params")
    op.drop_column("routing_feedback", "original_scenario")
    op.drop_column("routing_feedback", "snapshot_hash")
    op.drop_column("routing_feedback", "command_id")
    op.drop_column("routing_feedback", "prepared_plan_id")

    # Clean up null decision_id before re-enabling not null
    op.execute("DELETE FROM routing_feedback WHERE decision_id IS NULL;")
    op.alter_column("routing_feedback", "decision_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
