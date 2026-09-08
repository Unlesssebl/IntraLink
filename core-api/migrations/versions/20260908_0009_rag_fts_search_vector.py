"""add search_vector tsvector column with setweight and GIN index for hybrid RAG

Revision ID: 20260908_0009
Revises: 20260907_0008
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260908_0009"
down_revision: Union[str, None] = "20260907_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Добавляем вычисляемую колонку search_vector (TSVECTOR) с весами setweight
    # A - original_name (тема заявки), B - problem (описание проблемы), C - solution (резолюция)
    # Конфигурация 'russian' уже двуязычная: ASCII-токены идут в english_stem, технические токены - в simple
    op.execute(
        sa.text(
            """
            ALTER TABLE task_knowledge_base
            ADD COLUMN IF NOT EXISTS search_vector tsvector
            GENERATED ALWAYS AS (
                setweight(to_tsvector('russian', coalesce(original_name, '')), 'A') ||
                setweight(to_tsvector('russian', coalesce(problem, '')), 'B') ||
                setweight(to_tsvector('russian', coalesce(solution, '')), 'C')
            ) STORED;
            """
        )
    )

    # 2. Создаем GIN-индекс для быстрого полнотекстового поиска @@
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_tkb_search_vector
            ON task_knowledge_base USING GIN (search_vector);
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_tkb_search_vector;"))
    op.execute(sa.text("ALTER TABLE task_knowledge_base DROP COLUMN IF EXISTS search_vector;"))
