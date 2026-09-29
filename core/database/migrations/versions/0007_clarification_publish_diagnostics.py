"""Add clarification publication diagnostics.

Revision ID: 0007_clarification_publish_diagnostics
Revises: 0006_employee_onboarding_clarifications
Create Date: 2026-09-29 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_clarification_publish_diagnostics"
down_revision: Union[str, None] = "0006_employee_onboarding_clarifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clarification_requests",
        sa.Column("publish_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("clarification_requests", sa.Column("last_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("clarification_requests", sa.Column("last_error_code", sa.String(length=128)))
    op.add_column("clarification_requests", sa.Column("last_error_detail", sa.Text()))


def downgrade() -> None:
    op.drop_column("clarification_requests", "last_error_detail")
    op.drop_column("clarification_requests", "last_error_code")
    op.drop_column("clarification_requests", "last_attempt_at")
    op.drop_column("clarification_requests", "publish_attempts")
