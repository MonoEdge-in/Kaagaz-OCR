"""Every table, as SQLAlchemy 2 models. They describe the schema after the latest migration.

The migrations in `migrations/versions/` create the schema, and `alembic check` fails in CI
if these models and the migrations ever disagree. Change both together, with
`docs/data-dictionary.md`.

Conventions (docs/prd/day-1-prd.html section 4, and the approved schema plan):
- UUID keys from `gen_random_uuid()`; `created_at` and `updated_at` set by the database.
- Status and type fields are text with a named CHECK, easier to extend than enums.
- Every foreign key has an index.

The tables arrive in the migrations after 0001; this file starts with only the base class.
"""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, Text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeEngine

# Predictable constraint names, so a migration can drop one by name and a test can tell
# which rule an insert broke.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Python types to column types, so most columns need no explicit type.
    type_annotation_map: ClassVar[dict[Any, TypeEngine[Any]]] = {
        str: Text(),
        datetime: DateTime(timezone=True),
    }
