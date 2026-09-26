"""The database connection the API and the worker share: one engine, one session factory each.

Both take `DatabaseSettings`, so anything that has the full `Settings` can pass it too.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from kaagaz_shared.settings import DatabaseSettings


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    """One connection pool per process, over asyncpg.

    `pool_pre_ping` checks a pooled connection before handing it out, so a database
    restart costs one reconnect instead of one failed request.
    """
    return create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions whose rows stay readable after commit.

    Without `expire_on_commit=False`, reading a column after commit would query again,
    which async code can't do implicitly.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
