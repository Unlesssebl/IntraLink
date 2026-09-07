"""plan_hash, preflight evidence and durable workflow

Revision ID: 20260907_0006
Revises: 20260906_0005
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260907_0006"
down_revision: Union[str, None] = "20260906_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 1. Поля plan_hash, preflight_evidence, plan_expires_at в таблице commands
    op.add_column("commands", sa.Column("plan_hash", sa.String(64), nullable=True))
    op.add_column("commands", sa.Column("preflight_evidence_json", JSON_TYPE, nullable=True))
    op.add_column("commands", sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_commands_plan_hash", "commands", ["plan_hash"])

    # 2. Поля command_version, plan_hash, expires_at в таблице command_approvals
    op.add_column("command_approvals", sa.Column("command_version", sa.Integer(), server_default="1", nullable=False))
    op.add_column("command_approvals", sa.Column("plan_hash", sa.String(64), nullable=True))
    op.add_column("command_approvals", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("command_approvals", "expires_at")
    op.drop_column("command_approvals", "plan_hash")
    op.drop_column("command_approvals", "command_version")

    op.drop_index("ix_commands_plan_hash", table_name="commands")
    op.drop_column("commands", "plan_expires_at")
    op.drop_column("commands", "preflight_evidence_json")
    op.drop_column("commands", "plan_hash")
