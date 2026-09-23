"""autopilot internal comments settings

Revision ID: 20260923_0014
Revises: 20260910_0013
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260923_0014"
down_revision: Union[str, None] = "20260910_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("autopilot_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "internal_comments_enabled",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "internal_comments_depth",
                sa.String(length=16),
                server_default="applied",
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_autopilot_settings_comments_depth",
            "internal_comments_depth IN ('applied', 'technical')",
        )


def downgrade() -> None:
    with op.batch_alter_table("autopilot_settings") as batch_op:
        batch_op.drop_constraint("ck_autopilot_settings_comments_depth", type_="check")
        batch_op.drop_column("internal_comments_depth")
        batch_op.drop_column("internal_comments_enabled")
