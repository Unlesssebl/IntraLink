"""add service_path and service_path_ids with updated search_vector for hierarchical RAG

Revision ID: 20260908_0010
Revises: 20260908_0009
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260908_0010"
down_revision: Union[str, None] = "20260908_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Добавляем колонки service_path и service_path_ids (в отдельных командах)
    op.execute(sa.text("ALTER TABLE task_knowledge_base ADD COLUMN IF NOT EXISTS service_path VARCHAR;"))
    op.execute(sa.text("ALTER TABLE task_knowledge_base ADD COLUMN IF NOT EXISTS service_path_ids INTEGER[];"))

    # 2. Создаем индексы для быстрой фильтрации по сервису и веткам
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_tkb_service_id ON task_knowledge_base (service_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_tkb_service_path_ids ON task_knowledge_base USING GIN (service_path_ids);"))

    # 3. Пересоздаем вычисляемую колонку search_vector со словарным весом 'A' для полного пути сервиса
    op.execute(sa.text("DROP INDEX IF EXISTS idx_tkb_search_vector;"))
    op.execute(sa.text("ALTER TABLE task_knowledge_base DROP COLUMN IF EXISTS search_vector;"))

    op.execute(
        sa.text(
            """
            ALTER TABLE task_knowledge_base
            ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                setweight(to_tsvector('russian', coalesce(service_path, service_name, '')), 'A') ||
                setweight(to_tsvector('russian', coalesce(original_name, '')), 'A') ||
                setweight(to_tsvector('russian', coalesce(problem, '')), 'B') ||
                setweight(to_tsvector('russian', coalesce(solution, '')), 'C')
            ) STORED;
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_tkb_search_vector
            ON task_knowledge_base USING GIN (search_vector);
            """
        )
    )


def downgrade() -> None:
    # Откат search_vector к версии без service_path
    op.execute(sa.text("DROP INDEX IF EXISTS idx_tkb_search_vector;"))
    op.execute(sa.text("ALTER TABLE task_knowledge_base DROP COLUMN IF EXISTS search_vector;"))

    op.execute(
        sa.text(
            """
            ALTER TABLE task_knowledge_base
            ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                setweight(to_tsvector('russian', coalesce(original_name, '')), 'A') ||
                setweight(to_tsvector('russian', coalesce(problem, '')), 'B') ||
                setweight(to_tsvector('russian', coalesce(solution, '')), 'C')
            ) STORED;
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_tkb_search_vector
            ON task_knowledge_base USING GIN (search_vector);
            """
        )
    )

    op.execute(sa.text("DROP INDEX IF EXISTS ix_tkb_service_path_ids;"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_tkb_service_id;"))
    op.execute(sa.text("ALTER TABLE task_knowledge_base DROP COLUMN IF EXISTS service_path_ids;"))
    op.execute(sa.text("ALTER TABLE task_knowledge_base DROP COLUMN IF EXISTS service_path;"))
