"""decision audit, scenario registry and command correlation

Revision ID: 20260906_0005
Revises: 20260906_0004
Create Date: 2026-09-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260906_0005"
down_revision: Union[str, None] = "20260906_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID_TYPE = sa.Uuid().with_variant(postgresql.UUID(as_uuid=True), "postgresql")
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "autopilot_scenarios",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("scenario_key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("config_json", JSON_TYPE, nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_id", "scenario_key", name="uq_autopilot_scenario_service"),
    )
    op.create_index("ix_autopilot_scenarios_service_id", "autopilot_scenarios", ["service_id"])
    op.create_index("ix_autopilot_scenarios_scenario_key", "autopilot_scenarios", ["scenario_key"])

    op.create_table(
        "decision_records",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("ticket_run_id", UUID_TYPE, nullable=True),
        sa.Column("previous_decision_id", UUID_TYPE, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("analysis_kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("context_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_json", JSON_TYPE, nullable=False),
        sa.Column("context_json", JSON_TYPE, nullable=False),
        sa.Column("completeness_json", JSON_TYPE, nullable=False),
        sa.Column("proposal_json", JSON_TYPE, nullable=False),
        sa.Column("policy_json", JSON_TYPE, nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("build_version", sa.String(80), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ticket_run_id"], ["ticket_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["previous_decision_id"], ["decision_records.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "version", name="uq_decision_task_version"),
        sa.UniqueConstraint("task_id", "context_fingerprint", "analysis_kind", name="uq_decision_context"),
    )
    for column in ("task_id", "ticket_run_id", "analysis_kind", "status", "outcome", "context_fingerprint", "created_at"):
        op.create_index(f"ix_decision_records_{column}", "decision_records", [column])

    op.create_table(
        "decision_steps",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("decision_id", UUID_TYPE, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("component", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("input_json", JSON_TYPE, nullable=False),
        sa.Column("output_json", JSON_TYPE, nullable=False),
        sa.Column("metadata_json", JSON_TYPE, nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", "sequence", name="uq_decision_step_sequence"),
    )
    op.create_index("ix_decision_steps_decision_id", "decision_steps", ["decision_id"])
    op.create_index("ix_decision_steps_component", "decision_steps", ["component"])
    op.create_index("ix_decision_steps_status", "decision_steps", ["status"])

    op.create_table(
        "decision_feedback",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("decision_id", UUID_TYPE, nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("final_action_json", JSON_TYPE, nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("decision_id", "verdict", "actor", "created_at"):
        op.create_index(f"ix_decision_feedback_{column}", "decision_feedback", [column])

    op.add_column("commands", sa.Column("decision_id", UUID_TYPE, nullable=True))
    op.create_foreign_key(
        "fk_commands_decision",
        "commands",
        "decision_records",
        ["decision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_commands_decision_id", "commands", ["decision_id"])

    # The previous audit table contained placeholder metrics and is deliberately
    # not migrated. The product explicitly starts the decision journal clean.
    op.drop_table("triage_audit_log")


def downgrade() -> None:
    op.create_table(
        "triage_audit_log",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("generated_comment", sa.Text(), nullable=True),
        sa.Column("final_comment", sa.Text(), nullable=False),
        sa.Column("confidence_score", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("diff_ratio", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("operator_id", sa.String(100), nullable=True),
        sa.Column("status_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_triage_audit_log_task_id", "triage_audit_log", ["task_id"])
    op.create_index("ix_triage_audit_log_operator_id", "triage_audit_log", ["operator_id"])
    op.create_index("ix_triage_audit_log_created_at", "triage_audit_log", ["created_at"])
    op.drop_index("ix_commands_decision_id", table_name="commands")
    op.drop_constraint("fk_commands_decision", "commands", type_="foreignkey")
    op.drop_column("commands", "decision_id")
    op.drop_table("decision_feedback")
    op.drop_table("decision_steps")
    op.drop_table("decision_records")
    op.drop_table("autopilot_scenarios")
