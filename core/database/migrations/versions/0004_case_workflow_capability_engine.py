"""Replace scenario routing with the ADR 0006 case/workflow/capability engine.

Revision ID: 0004_case_workflow_capability_engine
Revises: 0003_add_system_state_and_autopilot
Create Date: 2026-09-27 12:00:00.000000

This is an intentional clean break. Revisions 0004-0006 of the experimental
routing cascade were never released and are replaced by this one migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_case_workflow_capability_engine"
down_revision: Union[str, None] = "0003_add_system_state_and_autopilot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    # The old policy/correction tables encode the scenario_key model and cannot
    # coexist with the new source of truth.
    op.drop_table("autopilot_corrections")
    op.drop_table("autopilot_policies")
    op.drop_table("triage_audit")

    op.create_table(
        "case_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("frame_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("frame_version", sa.String(length=32), nullable=False),
        sa.Column("router_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("primary_case_type", sa.String(length=64), nullable=True),
        sa.Column("case_frame_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("task_id", "snapshot_hash", "frame_id", "state", "primary_case_type"):
        op.create_index(f"ix_case_decisions_{column}", "case_decisions", [column])

    op.create_table(
        "service_catalog_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("catalog_hash", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("validation_state", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version"),
        sa.UniqueConstraint("catalog_hash"),
    )
    for column in ("version", "catalog_hash", "validation_state", "is_active"):
        op.create_index(f"ix_service_catalog_versions_{column}", "service_catalog_versions", [column])
    op.create_table(
        "service_catalog_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("service_path", sa.Text(), nullable=False),
        sa.Column("parent_service_id", sa.Integer(), nullable=True),
        sa.Column("task_type_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("form_metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("field_metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("catalog_hash", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["catalog_version_id"], ["service_catalog_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("catalog_version_id", "service_id", name="uq_service_catalog_entry_version_service"),
    )
    for column in ("catalog_version_id", "service_id", "task_type_id", "is_active", "catalog_hash"):
        op.create_index(f"ix_service_catalog_entries_{column}", "service_catalog_entries", [column])
    op.create_table(
        "service_route_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("service_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("allowed_case_types_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("default_case_type", sa.String(length=64), nullable=True),
        sa.Column("allowed_workflows_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("allowed_capabilities_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_task_type_id", sa.Integer(), nullable=True),
        sa.Column("required_fields_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("redirect_strategy", sa.String(length=32), nullable=False),
        sa.Column("risk", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_validated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("catalog_hash", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", "version", "catalog_hash", name="uq_service_route_binding_key_version_catalog"),
    )
    for column in ("key", "is_active", "is_validated", "catalog_hash"):
        op.create_index(f"ix_service_route_bindings_{column}", "service_route_bindings", [column])

    op.create_table(
        "service_compatibility_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("case_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_service_id", sa.Integer(), nullable=True),
        sa.Column("binding_key", sa.String(length=64), nullable=True),
        sa.Column("binding_version", sa.String(length=32), nullable=True),
        sa.Column("catalog_hash", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("decision_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["case_decision_id"], ["case_decisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_decision_id"),
    )
    for column in (
        "task_id",
        "snapshot_hash",
        "case_decision_id",
        "source_service_id",
        "binding_key",
        "catalog_hash",
        "state",
    ):
        op.create_index(f"ix_service_compatibility_decisions_{column}", "service_compatibility_decisions", [column])

    op.create_table(
        "workflow_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("workflow_key", sa.String(length=64), nullable=False),
        sa.Column("workflow_version", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("clarification_round", sa.Integer(), server_default="0", nullable=False),
        sa.Column("plan_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["case_decision_id"], ["case_decisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("case_decision_id", "task_id", "snapshot_hash", "workflow_key", "state", "disposition"):
        op.create_index(f"ix_workflow_plans_{column}", "workflow_plans", [column])

    op.create_table(
        "action_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("workflow_key", sa.String(length=64), nullable=False),
        sa.Column("workflow_version", sa.String(length=32), nullable=False),
        sa.Column("source_service_id", sa.Integer(), nullable=True),
        sa.Column("service_binding_key", sa.String(length=64), nullable=True),
        sa.Column("service_binding_version", sa.String(length=32), nullable=True),
        sa.Column("catalog_hash", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approved_by", sa.String(length=100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workflow_plan_id"], ["workflow_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["case_decision_id"], ["case_decisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_hash"),
    )
    for column in (
        "workflow_plan_id",
        "case_decision_id",
        "task_id",
        "snapshot_hash",
        "workflow_key",
        "state",
        "disposition",
        "plan_hash",
        "source_service_id",
        "service_binding_key",
        "catalog_hash",
    ):
        op.create_index(f"ix_action_plans_{column}", "action_plans", [column])

    op.create_table(
        "redirect_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("case_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("compatibility_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_service_id", sa.Integer(), nullable=False),
        sa.Column("target_service_id", sa.Integer(), nullable=False),
        sa.Column("target_service_path", sa.Text(), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column("template_key", sa.String(length=64), nullable=False),
        sa.Column("rendered_public_comment", sa.Text(), nullable=False),
        sa.Column("catalog_hash", sa.String(length=64), nullable=False),
        sa.Column("binding_key", sa.String(length=64), nullable=False),
        sa.Column("binding_version", sa.String(length=32), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("approval_state", sa.String(length=32), nullable=False),
        sa.Column("execution_state", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("plan_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approved_by", sa.String(length=100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["case_decision_id"], ["case_decisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["compatibility_decision_id"], ["service_compatibility_decisions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_hash"),
    )
    for column in (
        "task_id",
        "snapshot_hash",
        "case_decision_id",
        "compatibility_decision_id",
        "target_service_id",
        "catalog_hash",
        "plan_hash",
        "state",
    ):
        op.create_index(f"ix_redirect_plans_{column}", "redirect_plans", [column])
    op.create_table(
        "redirect_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("redirect_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("operator_username", sa.String(length=100), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("selected_target_service_id", sa.Integer(), nullable=True),
        sa.Column("reason_tag", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["redirect_plan_id"], ["redirect_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("redirect_plan_id", "task_id", "verdict"):
        op.create_index(f"ix_redirect_feedback_{column}", "redirect_feedback", [column])

    op.create_table(
        "action_preflights",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("capability_key", sa.String(length=64), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("checks_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["action_plan_id"], ["action_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "action_plan_id",
        "task_id",
        "action_id",
        "capability_key",
        "snapshot_hash",
        "plan_hash",
        "params_hash",
        "status",
        "expires_at",
    ):
        op.create_index(f"ix_action_preflights_{column}", "action_preflights", [column])

    for name, type_ in (
        ("action_plan_id", postgresql.UUID(as_uuid=True)),
        ("action_id", sa.String(length=64)),
        ("capability_key", sa.String(length=64)),
        ("sequence_no", sa.Integer()),
        ("params_hash", sa.String(length=64)),
        ("plan_hash", sa.String(length=64)),
        ("snapshot_hash", sa.String(length=64)),
    ):
        op.add_column("commands", sa.Column(name, type_, nullable=True))
    op.create_foreign_key(
        "fk_commands_action_plan_id", "commands", "action_plans", ["action_plan_id"], ["id"], ondelete="SET NULL"
    )
    for column in ("action_plan_id", "action_id", "capability_key", "params_hash", "plan_hash", "snapshot_hash"):
        op.create_index(f"ix_commands_{column}", "commands", [column])

    op.create_table(
        "case_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("operator_username", sa.String(length=100), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("original_case_type", sa.String(length=64), nullable=True),
        sa.Column("corrected_case_type", sa.String(length=64), nullable=True),
        sa.Column("corrected_frame_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason_tag", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["case_decision_id"], ["case_decisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("case_decision_id", "task_id", "snapshot_hash", "verdict"):
        op.create_index(f"ix_case_feedback_{column}", "case_feedback", [column])

    op.create_table(
        "plan_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("operator_username", sa.String(length=100), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("original_plan_hash", sa.String(length=64), nullable=False),
        sa.Column("corrected_action_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_tag", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["action_plan_id"], ["action_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["corrected_action_plan_id"], ["action_plans.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_plan_id", name="uq_plan_feedback_terminal"),
    )
    for column in ("action_plan_id", "task_id", "verdict"):
        op.create_index(f"ix_plan_feedback_{column}", "plan_feedback", [column])

    op.create_table(
        "execution_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("capability_key", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["action_plan_id"], ["action_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["command_id"], ["commands.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id"),
    )
    for column in ("action_plan_id", "command_id", "task_id", "action_id", "capability_key", "outcome"):
        op.create_index(f"ix_execution_feedback_{column}", "execution_feedback", [column])

    op.create_table(
        "workflow_policies",
        sa.Column("workflow_key", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), server_default="ASSISTED", nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("workflow_key"),
    )
    op.create_table(
        "capability_health",
        sa.Column("capability_key", sa.String(length=64), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_circuit_broken", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("capability_key"),
    )


def downgrade() -> None:
    op.drop_table("capability_health")
    op.drop_table("workflow_policies")
    op.drop_table("execution_feedback")
    op.drop_table("plan_feedback")
    op.drop_table("case_feedback")
    op.drop_table("redirect_feedback")
    op.drop_table("redirect_plans")
    for column in ("snapshot_hash", "plan_hash", "params_hash", "capability_key", "action_id", "action_plan_id"):
        op.drop_index(f"ix_commands_{column}", table_name="commands")
    op.drop_constraint("fk_commands_action_plan_id", "commands", type_="foreignkey")
    for column in (
        "snapshot_hash",
        "plan_hash",
        "params_hash",
        "sequence_no",
        "capability_key",
        "action_id",
        "action_plan_id",
    ):
        op.drop_column("commands", column)
    op.drop_table("action_preflights")
    op.drop_table("action_plans")
    op.drop_table("workflow_plans")
    op.drop_table("service_compatibility_decisions")
    op.drop_table("service_route_bindings")
    op.drop_table("service_catalog_entries")
    op.drop_table("service_catalog_versions")
    op.drop_table("case_decisions")

    # Restore the exact 0003 schema.
    op.create_table(
        "triage_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("model_used", sa.String(length=64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("context_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("applied", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("applied_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("task_id", "action", "created_at"):
        op.create_index(f"ix_triage_audit_{column}", "triage_audit", [column])

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
    for column in ("task_id", "original_scenario", "corrected_scenario", "correction_tag"):
        op.create_index(f"ix_autopilot_corrections_{column}", "autopilot_corrections", [column])
