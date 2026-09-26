"""Shared fixtures for the database tests (marked `db`).

They need PostgreSQL with the migrations applied (`alembic upgrade head`) and DATABASE_URL
set, as CI's Python job has. Each test runs in a transaction that is rolled back, so tests
never see each other's rows and leave the database as they found it.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from kaagaz_shared.settings import DatabaseSettings


@pytest.fixture
async def connection() -> AsyncIterator[AsyncConnection]:
    """One connection inside a transaction that is rolled back after the test."""
    # NullPool: the connection closes for real at the end, so no pool outlives the test.
    engine = create_async_engine(
        DatabaseSettings().database_url.get_secret_value(), poolclass=NullPool
    )
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                yield conn
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
