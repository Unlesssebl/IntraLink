"""Add stable clarification lifetime event identities.

Revision ID: 0008_clarification_event_identity
Revises: 0007_clarification_publish_diagnostics
Create Date: 2026-09-29 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_clarification_event_identity"
down_revision: Union[str, None] = "0007_clarification_publish_diagnostics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("clarification_requests", sa.Column("baseline_event_key", sa.String(length=64)))
    op.add_column("clarification_requests", sa.Column("response_event_key", sa.String(length=64)))


def downgrade() -> None:
    op.drop_column("clarification_requests", "response_event_key")
    op.drop_column("clarification_requests", "baseline_event_key")
