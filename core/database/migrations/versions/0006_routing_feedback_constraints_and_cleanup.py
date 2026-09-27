"""Add unique terminal plan constraint to routing feedback and reclassify legacy sources.

Revision ID: 0006_routing_feedback_constraints_and_cleanup
Revises: 0005_routing_feedback_consolidation
Create Date: 2026-09-26 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_routing_feedback_constraints_and_cleanup"
down_revision: Union[str, None] = "0005_routing_feedback_consolidation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Reclassify existing legacy sources into legacy_verified or legacy_unverified
    bind.execute(
        sa.text(
            """
            UPDATE routing_feedback
            SET source = CASE
                WHEN decision_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM routing_decisions rd WHERE rd.id = routing_feedback.decision_id
                ) THEN 'legacy_verified'
                ELSE 'legacy_unverified'
            END
            WHERE source = 'legacy' OR source IS NULL;
            """
        )
    )

    # 2. Add unique partial index for terminal feedback actions per prepared plan
    op.create_index(
        "uq_routing_feedback_terminal_plan",
        "routing_feedback",
        ["prepared_plan_id"],
        unique=True,
        postgresql_where=sa.text(
            "verdict IN ('approved', 'corrected', 'rejected', 'manual_takeover') "
            "AND prepared_plan_id IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "verdict IN ('approved', 'corrected', 'rejected', 'manual_takeover') "
            "AND prepared_plan_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_routing_feedback_terminal_plan",
        table_name="routing_feedback",
    )
