"""authoritative decision envelope and verified applications

Revision ID: 20260909_0012
Revises: 20260908_0011
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260909_0012"
down_revision: Union[str, None] = "20260908_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
UUID_TYPE = sa.Uuid().with_variant(postgresql.UUID(as_uuid=True), "postgresql")


def upgrade() -> None:
    op.add_column(
        "decision_records",
        sa.Column("envelope_json", JSON_TYPE, server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    for column in ("source_json", "completeness_json", "proposal_json", "policy_json"):
        op.drop_column("decision_records", column)

    op.create_table(
        "decision_applications",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("decision_id", UUID_TYPE, nullable=False),
        sa.Column("command_id", UUID_TYPE, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("proposed_action_json", JSON_TYPE, nullable=False),
        sa.Column("applied_action_json", JSON_TYPE, nullable=False),
        sa.Column("verified_result_json", JSON_TYPE, nullable=False),
        sa.Column("operator", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["command_id"], ["commands.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id"),
        sa.CheckConstraint(
            "state IN ('applied_unmodified','applied_modified','failed','needs_review')",
            name="ck_decision_application_state",
        ),
    )
    op.create_index("ix_decision_applications_decision_id", "decision_applications", ["decision_id"])
    op.create_index("ix_decision_applications_state", "decision_applications", ["state"])
    op.create_index("ix_decision_applications_operator", "decision_applications", ["operator"])

    op.execute(
        """
        INSERT INTO autopilot_settings (key, enabled, version, updated_by)
        VALUES ('global', false, 1, 'migration:v2')
        ON CONFLICT (key) DO UPDATE SET enabled = false, updated_by = 'migration:v2'
        """
    )
    op.execute(
        """
        INSERT INTO autopilot_scenarios
            (id, service_id, scenario_key, enabled, rollout_mode, version, config_json, updated_by)
        VALUES
            (gen_random_uuid(), 53, 'create_user', true, 'shadow', 1, '{}'::jsonb, 'migration:v2'),
            (gen_random_uuid(), 183, 'install_printer', true, 'shadow', 2, '{}'::jsonb, 'migration:v2')
        ON CONFLICT (service_id, scenario_key) DO UPDATE
        SET enabled = true, rollout_mode = 'shadow', version = EXCLUDED.version,
            config_json = '{}'::jsonb, updated_by = 'migration:v2'
        """
    )


def downgrade() -> None:
    op.drop_table("decision_applications")
    for column in ("source_json", "completeness_json", "proposal_json", "policy_json"):
        op.add_column(
            "decision_records",
            sa.Column(column, JSON_TYPE, server_default=sa.text("'{}'::jsonb"), nullable=False),
        )
    op.drop_column("decision_records", "envelope_json")
