"""Each table accepts a valid row, and each rule refuses a row that breaks it.

A refused insert is matched by the name of the constraint it broke, so a test can't pass
because some other rule happened to fire. Every test needs the migrated database (`db`).
More tables arrive in later PRs, with their own constraint tests.
"""

from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from kaagaz_shared.models import Base

pytestmark = pytest.mark.db

TABLES = Base.metadata.tables


async def insert(connection: AsyncConnection, table: str, **values: Any) -> UUID:
    """Insert one row and return its id."""
    statement = sa.insert(TABLES[table]).values(**values).returning(TABLES[table].c.id)
    row_id: UUID = (await connection.execute(statement)).scalar_one()
    return row_id


async def refused(connection: AsyncConnection, pattern: str, table: str, **values: Any) -> None:
    """The insert fails with a message matching the pattern; earlier rows stay usable."""
    with pytest.raises(IntegrityError, match=pattern):
        # A savepoint, so the failed insert doesn't abort the test's transaction.
        async with connection.begin_nested():
            await connection.execute(sa.insert(TABLES[table]).values(**values))


def constraint(name: str) -> str:
    return f'constraint "{name}"'


def document_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "original_filename": "invoice.pdf",
        "content_type": "application/pdf",
        "size_bytes": 10,
        "storage_key": "originals/invoice.pdf",
        "uploaded_by": "Tushar",
    }
    return values | overrides


def upload_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "original_filename": "invoice.pdf",
        "content_type": "application/pdf",
        "size_bytes": 10,
        "incoming_key": "incoming/upload/invoice.pdf",
        "uploaded_by": "Tushar",
    }
    return values | overrides


async def test_every_table_accepts_a_valid_row(connection: AsyncConnection) -> None:
    folder_id = await insert(connection, "folders", name="Invoices", created_by="Tushar")
    document_id = await insert(connection, "documents", **document_values(folder_id=folder_id))
    upload_id = await insert(connection, "uploads", **upload_values(document_id=document_id))
    audit_id = await insert(
        connection,
        "audit_log",
        actor="Tushar",
        action="upload.created",
        entity_type="upload",
        entity_id=upload_id,
    )
    assert folder_id and document_id and upload_id and audit_id


async def test_database_defaults_fill_ids_times_and_statuses(connection: AsyncConnection) -> None:
    document_id = await insert(connection, "documents", **document_values())
    row = (
        await connection.execute(
            sa.select(TABLES["documents"]).where(TABLES["documents"].c.id == document_id)
        )
    ).one()
    assert row.status == "processing"
    assert row.created_at is not None
    assert row.created_at == row.updated_at


async def test_status_and_type_lists_are_enforced(connection: AsyncConnection) -> None:
    await refused(
        connection,
        constraint("ck_documents_status"),
        "documents",
        **document_values(status="deleted"),
    )
    await refused(
        connection,
        constraint("ck_uploads_status"),
        "uploads",
        **upload_values(status="uploading"),
    )


async def test_shape_checks_are_enforced(connection: AsyncConnection) -> None:
    await refused(
        connection,
        constraint("ck_documents_sha256_hex"),
        "documents",
        **document_values(sha256="not-hex"),
    )
    await refused(
        connection,
        constraint("ck_documents_page_count_not_negative"),
        "documents",
        **document_values(page_count=-1),
    )
    await refused(
        connection, constraint("ck_uploads_size_positive"), "uploads", **upload_values(size_bytes=0)
    )


async def test_sha256_is_unique_when_set(connection: AsyncConnection) -> None:
    sha = "b" * 64
    await insert(connection, "documents", **document_values(sha256=sha))
    await refused(
        connection, constraint("uq_documents_sha256"), "documents", **document_values(sha256=sha)
    )
    # Two documents with no sha256 yet don't collide: NULL is never equal to NULL.
    await insert(connection, "documents", **document_values())
    await insert(connection, "documents", **document_values())


async def test_a_folder_can_sit_inside_another(connection: AsyncConnection) -> None:
    parent_id = await insert(connection, "folders", name="2026", created_by="Tushar")
    child_id = await insert(
        connection, "folders", name="September", created_by="Tushar", parent_id=parent_id
    )
    assert child_id != parent_id


async def test_deleting_a_folder_with_documents_fails(connection: AsyncConnection) -> None:
    folder_id = await insert(connection, "folders", name="Invoices", created_by="Tushar")
    await insert(connection, "documents", **document_values(folder_id=folder_id))
    with pytest.raises(IntegrityError, match=constraint("fk_documents_folder_id_folders")):
        async with connection.begin_nested():
            await connection.execute(
                sa.delete(TABLES["folders"]).where(TABLES["folders"].c.id == folder_id)
            )


async def test_deleting_a_document_an_upload_points_at_fails(connection: AsyncConnection) -> None:
    document_id = await insert(connection, "documents", **document_values())
    await insert(connection, "uploads", **upload_values(document_id=document_id))
    with pytest.raises(IntegrityError, match=constraint("fk_uploads_document_id_documents")):
        async with connection.begin_nested():
            await connection.execute(
                sa.delete(TABLES["documents"]).where(TABLES["documents"].c.id == document_id)
            )
