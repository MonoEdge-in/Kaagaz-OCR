"""The clients every activity needs, built once per worker process and handed to each
activity class in its constructor. Keeps activities themselves free of setup and easy to
test with a fake or fixture-built `Deps`.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from kaagaz_shared.db import create_engine, create_session_factory
from kaagaz_shared.settings import Settings
from kaagaz_shared.storage import Storage


@dataclass
class Deps:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    storage: Storage


def build_deps(settings: Settings) -> Deps:
    engine = create_engine(settings)
    return Deps(
        settings=settings,
        engine=engine,
        sessions=create_session_factory(engine),
        storage=Storage(settings),
    )
