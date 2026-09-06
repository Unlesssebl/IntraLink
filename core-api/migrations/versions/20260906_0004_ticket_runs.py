"""durable ticket runs and autopilot gate

Revision ID: 20260906_0004
Revises: 20260905_0003
Create Date: 2026-09-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260906_0004"
down_revision: Union[str, None] = "20260905_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID_TYPE = sa.Uuid().with_variant(postgresql.UUID(as_uuid=True), "postgresql")
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "autopilot_settings",
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "autopilot_setting_events",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("setting_key", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["setting_key"], ["autopilot_settings.key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_autopilot_setting_events_setting_key",
        "autopilot_setting_events",
        ["setting_key"],
    )
    op.create_index(
        "ix_autopilot_setting_events_created_at",
        "autopilot_setting_events",
        ["created_at"],
    )

    op.create_table(
        "ticket_runs",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("trigger_kind", sa.String(32), nullable=False),
        sa.Column("trigger_key", sa.String(160), nullable=False),
        sa.Column("trigger_snapshot_json", JSON_TYPE, nullable=False),
        sa.Column("current_step", sa.String(64), nullable=True),
        sa.Column("waiting_reason", sa.String(64), nullable=True),
        sa.Column("waiting_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clarification_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pause_reason", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "trigger_key", name="uq_ticket_run_trigger"),
    )
    op.create_index("ix_ticket_runs_task_id", "ticket_runs", ["task_id"])
    op.create_index("ix_ticket_runs_mode", "ticket_runs", ["mode"])
    op.create_index("ix_ticket_runs_state", "ticket_runs", ["state"])
    op.create_index("ix_ticket_runs_outcome", "ticket_runs", ["outcome"])
    op.create_index("ix_ticket_runs_created_at", "ticket_runs", ["created_at"])
    op.create_index(
        "uq_ticket_run_active_task",
        "ticket_runs",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("completed_at IS NULL"),
        sqlite_where=sa.text("completed_at IS NULL"),
    )

    op.create_table(
        "ticket_run_events",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("ticket_run_id", UUID_TYPE, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("details_json", JSON_TYPE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_run_id"], ["ticket_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticket_run_id", "sequence", name="uq_ticket_run_event_sequence"),
    )
    op.create_index("ix_ticket_run_events_ticket_run_id", "ticket_run_events", ["ticket_run_id"])
    op.create_index("ix_ticket_run_events_event_type", "ticket_run_events", ["event_type"])
    op.create_index("ix_ticket_run_events_created_at", "ticket_run_events", ["created_at"])

    op.add_column("commands", sa.Column("ticket_run_id", UUID_TYPE, nullable=True))
    op.create_foreign_key(
        "fk_commands_ticket_run",
        "commands",
        "ticket_runs",
        ["ticket_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_commands_ticket_run_id", "commands", ["ticket_run_id"])

    permissions = sa.table(
        "permissions", sa.column("name", sa.String), sa.column("description", sa.String)
    )
    role_permissions = sa.table(
        "role_permissions", sa.column("role_name", sa.String), sa.column("permission_name", sa.String)
    )
    permission_names = ["autopilot:read", "autopilot:control", "autopilot:manage"]
    op.bulk_insert(
        permissions,
        [{"name": name, "description": name} for name in permission_names],
    )
    op.bulk_insert(
        role_permissions,
        [
            {"role_name": "helpdesk_operator", "permission_name": "autopilot:read"},
            {"role_name": "helpdesk_operator", "permission_name": "autopilot:control"},
            {"role_name": "system_admin", "permission_name": "autopilot:read"},
            {"role_name": "system_admin", "permission_name": "autopilot:control"},
            {"role_name": "system_admin", "permission_name": "autopilot:manage"},
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_name IN "
            "('autopilot:read', 'autopilot:control', 'autopilot:manage')"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM permissions WHERE name IN "
            "('autopilot:read', 'autopilot:control', 'autopilot:manage')"
        )
    )
    op.drop_index("ix_commands_ticket_run_id", table_name="commands")
    op.drop_constraint("fk_commands_ticket_run", "commands", type_="foreignkey")
    op.drop_column("commands", "ticket_run_id")
    op.drop_table("ticket_run_events")
    op.drop_index("uq_ticket_run_active_task", table_name="ticket_runs")
    op.drop_table("ticket_runs")
    op.drop_table("autopilot_setting_events")
    op.drop_table("autopilot_settings")
