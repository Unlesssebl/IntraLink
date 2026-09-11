"""stage 4 decision response variants

Revision ID: 20260910_0014
Revises: 20260910_0013
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260910_0014"
down_revision: Union[str, None] = "20260910_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")
    uuid_type = sa.Uuid(as_uuid=True).with_variant(postgresql.UUID(as_uuid=True), "postgresql")

    op.create_table(
        "decision_response_variants",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "decision_id",
            uuid_type,
            sa.ForeignKey("decision_records.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False, index=True),
        sa.Column("tone", sa.String(length=32), nullable=False, index=True),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="template"),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="valid"),
        sa.Column("violations_json", json_type, nullable=False, server_default="[]"),
        sa.Column("provenance_json", json_type, nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(length=100), nullable=False, server_default="system"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
    )

    op.create_index(
        "ix_decision_response_variants_decision_version",
        "decision_response_variants",
        ["decision_id", "decision_version"],
    )
    op.create_index(
        "ix_decision_response_variants_task_active",
        "decision_response_variants",
        ["task_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_decision_response_variants_task_active",
        table_name="decision_response_variants",
    )
    op.drop_index(
        "ix_decision_response_variants_decision_version",
        table_name="decision_response_variants",
    )
    op.drop_table("decision_response_variants")
