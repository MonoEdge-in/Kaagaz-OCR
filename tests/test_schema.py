"""The migrations build the schema: so far, only the vector extension.

Tests marked `db` read the migrated database (CI runs `alembic upgrade head` first).
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from kaagaz_shared.models import NAMING_CONVENTION, Base


def test_constraints_get_predictable_names() -> None:
    # Migrations drop constraints by name, and tests match on them.
    assert Base.metadata.naming_convention == NAMING_CONVENTION


@pytest.mark.db
async def test_vector_extension_is_installed(connection: AsyncConnection) -> None:
    result = await connection.execute(
        sa.text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
    )
    assert result.scalar_one() == 1


@pytest.mark.db
async def test_no_tables_yet_besides_alembics_own(connection: AsyncConnection) -> None:
    result = await connection.execute(
        sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    )
    assert set(result.scalars()) == {"alembic_version"}
