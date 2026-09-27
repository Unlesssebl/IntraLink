import asyncio
import os
from logging.config import fileConfig

from alembic import context
from alembic.ddl.impl import DefaultImpl
from sqlalchemy import Column, MetaData, PrimaryKeyConstraint, String, Table, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import core.database.models  # noqa: F401 (load all 4 active models into metadata)
from core.database.base import Base


# Allow descriptive migration revision IDs up to 128 chars
def _custom_version_table_impl(
    self,
    *,
    version_table: str,
    version_table_schema: str | None,
    version_table_pk: bool,
    **kw,
) -> Table:
    vt = Table(
        version_table,
        MetaData(),
        Column("version_num", String(128), nullable=False),
        schema=version_table_schema,
    )
    if version_table_pk:
        vt.append_constraint(PrimaryKeyConstraint("version_num", name=f"{version_table}_pkc"))
    return vt

DefaultImpl.version_table_impl = _custom_version_table_impl

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        config.get_main_option("sqlalchemy.url", "postgresql+asyncpg://postgres:postgres@localhost:5432/intraservice"),
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        # Ensure pgvector extension and long version_num support in PostgreSQL
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector;")
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(128) NOT NULL PRIMARY KEY);"
            )
            connection.exec_driver_sql(
                "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128);"
            )
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine and associate a connection with the context."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
