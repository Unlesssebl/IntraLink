"""stage 5 audit and feedback

Revision ID: 20260912_0015
Revises: 20260910_0014
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260912_0015"
down_revision: Union[str, None] = "20260910_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")
    uuid_type = sa.Uuid(as_uuid=True).with_variant(postgresql.UUID(as_uuid=True), "postgresql")

    # 1. decision_application_requests
    op.create_table(
        "decision_application_requests",
        sa.Column("request_id", uuid_type, primary_key=True),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_decision_application_requests_actor",
        "decision_application_requests",
        ["actor"],
    )
    op.create_index(
        "ix_decision_application_requests_request_hash",
        "decision_application_requests",
        ["request_hash"],
    )
    op.create_index(
        "ix_decision_application_requests_created_at",
        "decision_application_requests",
        ["created_at"],
    )

    # 2. decision_application_attempts
    op.create_table(
        "decision_application_attempts",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "request_id",
            uuid_type,
            sa.ForeignKey("decision_application_requests.request_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column(
            "decision_id",
            uuid_type,
            sa.ForeignKey("decision_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column(
            "response_variant_id",
            uuid_type,
            sa.ForeignKey("decision_response_variants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposed_action_json", json_type, nullable=False, server_default="{}"),
        sa.Column("requested_action_json", json_type, nullable=False, server_default="{}"),
        sa.Column("suboperations_json", json_type, nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("request_id", "task_id", name="uq_decision_application_attempts_request_task"),
    )
    op.create_index("ix_decision_application_attempts_request_id", "decision_application_attempts", ["request_id"])
    op.create_index("ix_decision_application_attempts_task_id", "decision_application_attempts", ["task_id"])
    op.create_index("ix_decision_application_attempts_decision_id", "decision_application_attempts", ["decision_id"])
    op.create_index("ix_decision_application_attempts_response_variant_id", "decision_application_attempts", ["response_variant_id"])
    op.create_index("ix_decision_application_attempts_actor", "decision_application_attempts", ["actor"])
    op.create_index("ix_decision_application_attempts_source", "decision_application_attempts", ["source"])
    op.create_index("ix_decision_application_attempts_state", "decision_application_attempts", ["state"])
    op.create_index("ix_decision_application_attempts_created_at", "decision_application_attempts", ["created_at"])
    op.create_index(
        "ix_decision_application_attempts_state_lease",
        "decision_application_attempts",
        ["state", "lease_expires_at"],
    )

    # 3. Alter decision_applications
    op.alter_column("decision_applications", "command_id", nullable=True)
    op.add_column(
        "decision_applications",
        sa.Column(
            "attempt_id",
            uuid_type,
            sa.ForeignKey("decision_application_attempts.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
        ),
    )
    op.add_column(
        "decision_applications",
        sa.Column(
            "application_type",
            sa.String(length=32),
            nullable=False,
            server_default="command_execution",
        ),
    )
    op.add_column(
        "decision_applications",
        sa.Column("task_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_decision_applications_application_type", "decision_applications", ["application_type"])
    op.create_index("ix_decision_applications_task_id", "decision_applications", ["task_id"])
    op.create_check_constraint(
        "ck_decision_applications_source_xor",
        "decision_applications",
        "(command_id IS NOT NULL AND attempt_id IS NULL AND application_type = 'command_execution') OR "
        "(command_id IS NULL AND attempt_id IS NOT NULL AND application_type = 'triage_apply')",
    )

    # Backfill task_id for existing command_executions from commands table
    op.execute(
        """
        UPDATE decision_applications da
        SET task_id = c.task_id
        FROM commands c
        WHERE da.command_id = c.id AND da.task_id IS NULL
        """
    )

    # 4. Alter decision_feedback
    op.add_column(
        "decision_feedback",
        sa.Column(
            "attempt_id",
            uuid_type,
            sa.ForeignKey("decision_application_attempts.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
        ),
    )
    op.add_column(
        "decision_feedback",
        sa.Column("event_id", uuid_type, nullable=True, unique=True),
    )
    op.add_column(
        "decision_feedback",
        sa.Column("source", sa.String(length=32), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "decision_feedback",
        sa.Column("decision_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "decision_feedback",
        sa.Column("task_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "decision_feedback",
        sa.Column(
            "response_variant_id",
            uuid_type,
            sa.ForeignKey("decision_response_variants.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "decision_feedback",
        sa.Column("request_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "decision_feedback",
        sa.Column("operator_reason_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "decision_feedback",
        sa.Column("reason_source", sa.String(length=32), nullable=True),
    )

    op.create_index("ix_decision_feedback_source", "decision_feedback", ["source"])
    op.create_index("ix_decision_feedback_task_id", "decision_feedback", ["task_id"])
    op.create_index("ix_decision_feedback_response_variant_id", "decision_feedback", ["response_variant_id"])
    op.create_check_constraint(
        "ck_decision_feedback_source_identity",
        "decision_feedback",
        "(source = 'legacy') OR "
        "(source = 'explicit_feedback' AND event_id IS NOT NULL) OR "
        "(source IN ('recommendation_apply', 'manual_apply') AND attempt_id IS NOT NULL)",
    )

    # Backfill decision_version and task_id for existing feedback from decision_records
    op.execute(
        """
        UPDATE decision_feedback df
        SET decision_version = dr.version, task_id = dr.task_id
        FROM decision_records dr
        WHERE df.decision_id = dr.id AND (df.decision_version IS NULL OR df.task_id IS NULL)
        """
    )
    op.execute(
        """
        UPDATE decision_feedback
        SET reason_source = 'legacy'
        WHERE reason_source IS NULL
        """
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Safety check: do not lose audit data if attempts or requests have been created
    has_attempts = False
    has_requests = False
    try:
        res_attempts = conn.execute(sa.text("SELECT count(*) FROM decision_application_attempts")).scalar()
        has_attempts = bool(res_attempts and res_attempts > 0)
        res_requests = conn.execute(sa.text("SELECT count(*) FROM decision_application_requests")).scalar()
        has_requests = bool(res_requests and res_requests > 0)
    except Exception:
        pass

    if has_attempts or has_requests:
        raise RuntimeError(
            "Cannot downgrade migration 20260912_0015: existing decision application requests or attempts would be lost."
        )

    # 1. Revert decision_feedback
    op.drop_constraint("ck_decision_feedback_source_identity", "decision_feedback", type_="check")
    op.drop_index("ix_decision_feedback_response_variant_id", table_name="decision_feedback")
    op.drop_index("ix_decision_feedback_task_id", table_name="decision_feedback")
    op.drop_index("ix_decision_feedback_source", table_name="decision_feedback")
    op.drop_column("decision_feedback", "reason_source")
    op.drop_column("decision_feedback", "operator_reason_code")
    op.drop_column("decision_feedback", "request_hash")
    op.drop_column("decision_feedback", "response_variant_id")
    op.drop_column("decision_feedback", "task_id")
    op.drop_column("decision_feedback", "decision_version")
    op.drop_column("decision_feedback", "source")
    op.drop_column("decision_feedback", "event_id")
    op.drop_column("decision_feedback", "attempt_id")

    # 2. Revert decision_applications
    op.drop_constraint("ck_decision_applications_source_xor", "decision_applications", type_="check")
    op.drop_index("ix_decision_applications_task_id", table_name="decision_applications")
    op.drop_index("ix_decision_applications_application_type", table_name="decision_applications")
    op.drop_column("decision_applications", "task_id")
    op.drop_column("decision_applications", "application_type")
    op.drop_column("decision_applications", "attempt_id")
    op.alter_column("decision_applications", "command_id", nullable=False)

    # 3. Drop decision_application_attempts
    op.drop_table("decision_application_attempts")

    # 4. Drop decision_application_requests
    op.drop_table("decision_application_requests")
