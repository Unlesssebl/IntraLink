"""Add FTS search_vector column and GIN index to task_knowledge_base.

Revision ID: 0002_add_fts_search_vector
Revises: 0001_initial
Create Date: 2026-09-24 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002_add_fts_search_vector"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add generated tsvector column for Russian morphology full-text search
    op.execute(
        """
        ALTER TABLE task_knowledge_base
        ADD COLUMN IF NOT EXISTS search_vector tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('russian', coalesce(original_name, '')), 'A') ||
            setweight(to_tsvector('russian', coalesce(problem, '')), 'B') ||
            setweight(to_tsvector('russian', coalesce(solution, '')), 'C')
        ) STORED;
        """
    )

    # 2. Create GIN index for high-speed lexical search
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_task_kb_search_vector
        ON task_knowledge_base
        USING gin (search_vector);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_task_kb_search_vector;")
    op.execute("ALTER TABLE task_knowledge_base DROP COLUMN IF EXISTS search_vector;")
