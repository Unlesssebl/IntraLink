"""Initial clean state migration: 4 active v2 tables.

Revision ID: 0001_initial
Revises: None
Create Date: 2026-09-23 17:30:00.000000

"""

from typing import Sequence, Union

import pgvector
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Ensure pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 2. task_knowledge_base
    op.create_table(
        "task_knowledge_base",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("original_name", sa.String(length=500), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("solution", sa.Text(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("service_name", sa.String(length=255), nullable=False),
        sa.Column("service_path", sa.String(length=500), nullable=True),
        sa.Column("status_name", sa.String(length=100), nullable=False),
        sa.Column("classification_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=1024), nullable=True),
        sa.Column("is_blacklisted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("quality_score", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index("idx_task_kb_task_id", "task_knowledge_base", ["task_id"])
    op.create_index("idx_task_kb_service_id", "task_knowledge_base", ["service_id"])
    op.create_index("idx_task_kb_quality", "task_knowledge_base", ["quality_score"])
    op.create_index(
        "idx_task_kb_hnsw",
        "task_knowledge_base",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"m": 16, "ef_construction": 64},
    )

    # 3. users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=True),
        sa.Column("is_user_id", sa.Integer(), nullable=True),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=True),
        sa.Column("auth_token_b64", sa.Text(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_is_user_id", "users", ["is_user_id"])
    op.create_index("ix_users_tg_user_id", "users", ["tg_user_id"])

    # 4. commands
    op.create_table(
        "commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("executor", sa.String(length=32), nullable=False),
        sa.Column("target_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("params_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="5", nullable=False),
        sa.Column("initiator", sa.String(length=100), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_commands_idempotency_key", "commands", ["idempotency_key"], unique=True)
    op.create_index("ix_commands_action", "commands", ["action"])
    op.create_index("ix_commands_executor", "commands", ["executor"])
    op.create_index("ix_commands_status", "commands", ["status"])
    op.create_index("ix_commands_initiator", "commands", ["initiator"])
    op.create_index("ix_commands_task_id", "commands", ["task_id"])

    # 5. triage_audit
    op.create_table(
        "triage_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("model_used", sa.String(length=64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("applied_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_triage_audit_task_id", "triage_audit", ["task_id"])
    op.create_index("ix_triage_audit_action", "triage_audit", ["action"])
    op.create_index("ix_triage_audit_created_at", "triage_audit", ["created_at"])


def downgrade() -> None:
    op.drop_table("triage_audit")
    op.drop_table("commands")
    op.drop_table("users")
    op.drop_table("task_knowledge_base")
