"""Alembic's entry point: runs the migrations in versions/ against DATABASE_URL.

The address comes from `DatabaseSettings`, so Alembic needs DATABASE_URL and nothing else.
`alembic check` compares the migrated database with `kaagaz_shared.models`, server defaults
included, and fails CI if they differ.

`alembic upgrade head --sql` prints the SQL without connecting ("offline" mode).
"""

import asyncio
from logging.config import fileConfig

# Registers the "vector" type, so `alembic check` can read chunks.embedding back.
import pgvector.sqlalchemy  # noqa: F401 - imported for that side effect only
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from kaagaz_shared.models import Base
from kaagaz_shared.settings import DatabaseSettings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    return DatabaseSettings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Print the SQL instead of running it. No database is needed."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_server_default=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # NullPool: a migration opens one connection and closes it, so there's nothing to pool.
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
