"""Create the clean IntraLink schema.

Revision ID: 20260905_0001
Revises:
"""

from alembic import op

revision = "20260905_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
        op.execute("DROP EXTENSION IF EXISTS vector")
