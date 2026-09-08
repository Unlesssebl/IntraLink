"""identity and access control foundation

Revision ID: 20260905_0003
Revises: 20260905_0002
Create Date: 2026-09-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260905_0003"
down_revision: Union[str, None] = "20260905_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID_TYPE = sa.Uuid().with_variant(postgresql.UUID(as_uuid=True), "postgresql")
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "principals",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(160), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(24), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type", "subject", name="uq_principal_type_subject"),
    )
    op.create_index("ix_principals_type", "principals", ["type"])
    op.create_index("ix_principals_status", "principals", ["status"])
    op.create_index("ix_principals_external_id", "principals", ["external_id"])

    op.create_table(
        "roles",
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "permissions",
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_name", sa.String(64), nullable=False),
        sa.Column("permission_name", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(["role_name"], ["roles.name"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_name"], ["permissions.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_name", "permission_name"),
    )
    op.create_table(
        "principal_roles",
        sa.Column("principal_id", UUID_TYPE, nullable=False),
        sa.Column("role_name", sa.String(64), nullable=False),
        sa.Column("assigned_by", UUID_TYPE, nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_name"], ["roles.name"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_by"], ["principals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("principal_id", "role_name"),
    )
    op.create_table(
        "service_credentials",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("principal_id", UUID_TYPE, nullable=False),
        sa.Column("key_id", sa.String(80), nullable=False),
        sa.Column("secret_hash", sa.Text(), nullable=False),
        sa.Column("scopes_json", JSON_TYPE, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_service_credentials_principal_id", "service_credentials", ["principal_id"])
    op.create_index("ix_service_credentials_key_id", "service_credentials", ["key_id"], unique=True)

    op.create_table(
        "auth_sessions",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("principal_id", UUID_TYPE, nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("rotated_from_id", UUID_TYPE, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rotated_from_id"], ["auth_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auth_sessions_principal_id", "auth_sessions", ["principal_id"])
    op.create_index("ix_auth_sessions_refresh_token_hash", "auth_sessions", ["refresh_token_hash"], unique=True)
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    op.create_table(
        "telegram_links",
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("principal_id", UUID_TYPE, nullable=False),
        sa.Column("status", sa.String(24), server_default="pending_reverification", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tg_user_id"),
        sa.UniqueConstraint("principal_id"),
    )

    op.create_table(
        "telegram_link_codes",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("principal_id", UUID_TYPE, nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_telegram_link_codes_principal_id", "telegram_link_codes", ["principal_id"])
    op.create_index("ix_telegram_link_codes_code_hash", "telegram_link_codes", ["code_hash"], unique=True)
    op.create_index("ix_telegram_link_codes_expires_at", "telegram_link_codes", ["expires_at"])

    op.add_column("commands", sa.Column("initiator_principal_id", UUID_TYPE, nullable=True))
    op.create_foreign_key(
        "fk_commands_initiator_principal",
        "commands", "principals", ["initiator_principal_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_commands_initiator_principal_id", "commands", ["initiator_principal_id"])
    op.add_column("command_approvals", sa.Column("approver_principal_id", UUID_TYPE, nullable=True))
    op.create_foreign_key(
        "fk_command_approvals_principal",
        "command_approvals", "principals", ["approver_principal_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_command_approvals_approver_principal_id", "command_approvals", ["approver_principal_id"])

    op.create_table(
        "approval_challenges",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("command_id", UUID_TYPE, nullable=False),
        sa.Column("principal_id", UUID_TYPE, nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("allowed_decisions_json", JSON_TYPE, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_decision", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["command_id"], ["commands.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approval_challenges_command_id", "approval_challenges", ["command_id"])
    op.create_index("ix_approval_challenges_principal_id", "approval_challenges", ["principal_id"])
    op.create_index("ix_approval_challenges_token_hash", "approval_challenges", ["token_hash"], unique=True)
    op.create_index("ix_approval_challenges_expires_at", "approval_challenges", ["expires_at"])

    op.create_table(
        "security_events",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("principal_id", UUID_TYPE, nullable=True),
        sa.Column("auth_method", sa.String(32), nullable=True),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(160), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("details_json", JSON_TYPE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_security_events_event_type", "security_events", ["event_type"])
    op.create_index("ix_security_events_outcome", "security_events", ["outcome"])
    op.create_index("ix_security_events_principal_id", "security_events", ["principal_id"])
    op.create_index("ix_security_events_resource_id", "security_events", ["resource_id"])
    op.create_index("ix_security_events_created_at", "security_events", ["created_at"])

    roles = sa.table("roles", sa.column("name", sa.String), sa.column("description", sa.String))
    permissions = sa.table("permissions", sa.column("name", sa.String), sa.column("description", sa.String))
    role_permissions = sa.table(
        "role_permissions", sa.column("role_name", sa.String), sa.column("permission_name", sa.String)
    )
    role_rows = [
        {"name": "helpdesk_operator", "description": "Helpdesk operator"},
        {"name": "system_admin", "description": "System administrator"},
        {"name": "security_auditor", "description": "Read-only security auditor"},
    ]
    permission_names = [
        "task:read", "task:mutate", "triage:read", "triage:mutate", "ai:use",
        "command:read", "command:create", "command:approve:r1", "command:approve:r2",
        "command:cancel", "command:review", "policy:manage", "identity:manage",
        "credentials:manage", "rules:manage", "diagnostic:run", "events:read", "audit:read",
        "identity:link:self",
    ]
    op.bulk_insert(roles, role_rows)
    op.bulk_insert(permissions, [{"name": name, "description": name} for name in permission_names])
    operator_permissions = {
        "task:read", "task:mutate", "triage:read", "triage:mutate", "ai:use",
        "command:read", "command:create", "command:approve:r1", "command:approve:r2", "command:cancel",
        "diagnostic:run", "events:read",
        "identity:link:self",
    }
    auditor_permissions = {"task:read", "triage:read", "command:read", "events:read", "audit:read", "identity:link:self"}
    mappings = []
    for name in permission_names:
        mappings.append({"role_name": "system_admin", "permission_name": name})
        if name in operator_permissions:
            mappings.append({"role_name": "helpdesk_operator", "permission_name": name})
        if name in auditor_permissions:
            mappings.append({"role_name": "security_auditor", "permission_name": name})
    op.bulk_insert(role_permissions, mappings)

    # Existing Telegram records are quarantined until a corporate re-link.
    op.execute(sa.text("""
        INSERT INTO principals (id, type, subject, display_name, status)
        SELECT uuid_generate_v4(), 'human', 'legacy-telegram:' || tg_user_id::text,
               COALESCE(NULLIF(is_login, ''), tg_user_id::text), 'pending_reverification'
        FROM users
    """))
    op.execute(sa.text("""
        INSERT INTO telegram_links (tg_user_id, principal_id, status)
        SELECT u.tg_user_id, p.id, 'pending_reverification'
        FROM users u
        JOIN principals p ON p.subject = 'legacy-telegram:' || u.tg_user_id::text
    """))


def downgrade() -> None:
    op.drop_table("security_events")
    op.drop_table("approval_challenges")
    op.drop_index("ix_command_approvals_approver_principal_id", table_name="command_approvals")
    op.drop_constraint("fk_command_approvals_principal", "command_approvals", type_="foreignkey")
    op.drop_column("command_approvals", "approver_principal_id")
    op.drop_index("ix_commands_initiator_principal_id", table_name="commands")
    op.drop_constraint("fk_commands_initiator_principal", "commands", type_="foreignkey")
    op.drop_column("commands", "initiator_principal_id")
    op.drop_table("telegram_links")
    op.drop_table("telegram_link_codes")
    op.drop_table("auth_sessions")
    op.drop_table("service_credentials")
    op.drop_table("principal_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("principals")
