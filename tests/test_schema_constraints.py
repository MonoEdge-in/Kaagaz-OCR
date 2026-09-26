"""Each table accepts a valid row, and each rule refuses a row that breaks it.

A refused insert is matched by the name of the constraint it broke, so a test can't pass
because some other rule happened to fire. Every test needs the migrated database (`db`).
More tables arrive in later PRs, with their own constraint tests.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
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


@dataclass(frozen=True)
class Tree:
    """A document with one page and one run: what a block hangs from."""

    document_id: UUID
    page_id: UUID
    run_id: UUID


async def make_tree(connection: AsyncConnection) -> Tree:
    document_id = await insert(connection, "documents", **document_values())
    page_id = await insert(
        connection, "pages", document_id=document_id, page_no=1, route="text_layer", status="done"
    )
    run_id = await insert(
        connection,
        "extraction_runs",
        document_id=document_id,
        pipeline_version="0.1.0",
        status="running",
    )
    return Tree(document_id, page_id, run_id)


def block_values(
    document_id: UUID, page_id: UUID, run_id: UUID, **overrides: Any
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "document_id": document_id,
        "page_id": page_id,
        "extraction_run_id": run_id,
        "block_no": 1,
        "block_type": "paragraph",
        "text": "Total 1,200.00",
        "bbox": [10.0, 20.0, 110.0, 40.0],
        "confidence": 0.9,
        "source": "pdfplumber",
    }
    return values | overrides


@pytest.fixture
async def tree(connection: AsyncConnection) -> Tree:
    return await make_tree(connection)


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


async def test_a_document_can_carry_more_than_one_tag(connection: AsyncConnection) -> None:
    document_id = await insert(connection, "documents", **document_values())
    tag_id = await insert(connection, "tags", name="urgent")
    await connection.execute(
        sa.insert(TABLES["document_tags"]).values(document_id=document_id, tag_id=tag_id)
    )


async def test_a_tag_name_is_unique(connection: AsyncConnection) -> None:
    await insert(connection, "tags", name="urgent")
    await refused(connection, constraint("uq_tags_name"), "tags", name="urgent")


async def test_a_document_and_its_tag_cannot_repeat(connection: AsyncConnection) -> None:
    document_id = await insert(connection, "documents", **document_values())
    tag_id = await insert(connection, "tags", name="urgent")
    await connection.execute(
        sa.insert(TABLES["document_tags"]).values(document_id=document_id, tag_id=tag_id)
    )
    with pytest.raises(IntegrityError, match=constraint("pk_document_tags")):
        async with connection.begin_nested():
            await connection.execute(
                sa.insert(TABLES["document_tags"]).values(document_id=document_id, tag_id=tag_id)
            )


async def test_removing_a_document_drops_its_tags(connection: AsyncConnection) -> None:
    document_id = await insert(connection, "documents", **document_values())
    tag_id = await insert(connection, "tags", name="urgent")
    await connection.execute(
        sa.insert(TABLES["document_tags"]).values(document_id=document_id, tag_id=tag_id)
    )
    await connection.execute(
        sa.delete(TABLES["documents"]).where(TABLES["documents"].c.id == document_id)
    )
    remaining = await connection.execute(
        sa.select(sa.func.count()).select_from(TABLES["document_tags"])
    )
    assert remaining.scalar_one() == 0


async def test_a_version_links_two_documents(connection: AsyncConnection) -> None:
    old_id = await insert(connection, "documents", **document_values())
    new_id = await insert(connection, "documents", **document_values())
    version_id = await insert(
        connection,
        "document_versions",
        document_id=new_id,
        supersedes_document_id=old_id,
        created_by="Tushar",
    )
    assert version_id


async def test_a_link_names_two_documents(connection: AsyncConnection) -> None:
    invoice_id = await insert(connection, "documents", **document_values())
    po_id = await insert(connection, "documents", **document_values())
    link_id = await insert(
        connection,
        "document_links",
        from_document_id=invoice_id,
        to_document_id=po_id,
        link_type="purchase_order",
    )
    assert link_id


async def test_a_document_gets_pages_a_run_and_blocks(connection: AsyncConnection) -> None:
    tree = await make_tree(connection)
    block_id = await insert(
        connection, "blocks", **block_values(tree.document_id, tree.page_id, tree.run_id)
    )
    assert block_id


async def test_page_route_and_status_lists_are_enforced(
    connection: AsyncConnection, tree: Tree
) -> None:
    await refused(
        connection,
        constraint("ck_pages_page_no_positive"),
        "pages",
        document_id=tree.document_id,
        page_no=0,
        status="done",
    )
    await refused(
        connection,
        constraint("ck_pages_route"),
        "pages",
        document_id=tree.document_id,
        page_no=2,
        route="scanned",
        status="done",
    )
    await refused(
        connection,
        constraint("ck_pages_status"),
        "pages",
        document_id=tree.document_id,
        page_no=2,
        status="waiting",
    )


async def test_a_page_number_is_unique_per_document(
    connection: AsyncConnection, tree: Tree
) -> None:
    await refused(
        connection,
        constraint("uq_pages_document_id_page_no"),
        "pages",
        document_id=tree.document_id,
        page_no=1,
        status="done",
    )


async def test_extraction_run_status_list_is_enforced(
    connection: AsyncConnection, tree: Tree
) -> None:
    await refused(
        connection,
        constraint("ck_extraction_runs_status"),
        "extraction_runs",
        document_id=tree.document_id,
        pipeline_version="0.1.0",
        status="waiting",
    )


async def test_a_document_has_at_most_one_current_run(
    connection: AsyncConnection, tree: Tree
) -> None:
    runs = TABLES["extraction_runs"]
    await connection.execute(
        sa.update(runs).where(runs.c.id == tree.run_id).values(status="succeeded")
    )
    # Any number of older runs may be superseded or failed.
    await insert(
        connection,
        "extraction_runs",
        document_id=tree.document_id,
        pipeline_version="0.1.0",
        status="superseded",
    )
    await refused(
        connection,
        constraint("uq_extraction_runs_current_run"),
        "extraction_runs",
        document_id=tree.document_id,
        pipeline_version="0.1.0",
        status="succeeded",
    )


async def test_block_type_and_confidence_are_enforced(
    connection: AsyncConnection, tree: Tree
) -> None:
    await refused(
        connection,
        constraint("ck_blocks_block_type"),
        "blocks",
        **block_values(tree.document_id, tree.page_id, tree.run_id, block_type="footnote"),
    )
    await refused(
        connection,
        constraint("ck_blocks_confidence_range"),
        "blocks",
        **block_values(tree.document_id, tree.page_id, tree.run_id, confidence=1.5),
    )


def real_array(literal: str) -> sa.Cast[Any]:
    """An array Postgres builds from its own text form, for shapes a Python list can't make."""
    return sa.cast(sa.literal(literal), ARRAY(sa.REAL()))


BAD_BBOXES = [
    pytest.param([1.0, 2.0, 3.0], id="three numbers"),
    pytest.param([1.0, 2.0, 3.0, 4.0, 5.0], id="five numbers"),
    pytest.param([1.0, None, 3.0, 4.0], id="a missing number"),
    pytest.param([-1.0, 2.0, 3.0, 4.0], id="negative x0"),
    pytest.param([1.0, -2.0, 3.0, 4.0], id="negative y0"),
    pytest.param([5.0, 2.0, 3.0, 4.0], id="x0 past x1"),
    pytest.param([1.0, 6.0, 3.0, 4.0], id="y0 past y1"),
    pytest.param([], id="empty"),
    pytest.param([[1.0, 2.0], [3.0, 4.0]], id="two dimensions"),
    pytest.param("[0:3]={1,2,3,4}", id="starts at index 0"),
]


@pytest.mark.parametrize("bbox", BAD_BBOXES)
async def test_a_block_bbox_must_be_four_ordered_numbers(
    connection: AsyncConnection, tree: Tree, bbox: Any
) -> None:
    value = real_array(bbox) if isinstance(bbox, str) else bbox
    await refused(
        connection,
        constraint("ck_blocks_bbox_shape"),
        "blocks",
        **block_values(tree.document_id, tree.page_id, tree.run_id, block_no=2, bbox=value),
    )


async def test_a_single_spreadsheet_cell_is_a_valid_bbox(
    connection: AsyncConnection, tree: Tree
) -> None:
    # B7 in cell-grid units: [col0, row0, col1, row1], 0-based, end excluded.
    await insert(
        connection,
        "blocks",
        **block_values(tree.document_id, tree.page_id, tree.run_id, bbox=[1.0, 6.0, 2.0, 7.0]),
    )


async def test_a_block_cannot_sit_on_another_documents_page(
    connection: AsyncConnection, tree: Tree
) -> None:
    other = await make_tree(connection)
    await refused(
        connection,
        constraint("fk_blocks_page_id_document_id_pages"),
        "blocks",
        **block_values(other.document_id, tree.page_id, other.run_id, block_no=2),
    )


async def test_deleting_a_document_removes_its_pages_runs_and_blocks(
    connection: AsyncConnection,
) -> None:
    tree = await make_tree(connection)
    await insert(connection, "blocks", **block_values(tree.document_id, tree.page_id, tree.run_id))
    await connection.execute(
        sa.delete(TABLES["documents"]).where(TABLES["documents"].c.id == tree.document_id)
    )
    for table in ("pages", "extraction_runs", "blocks"):
        remaining = await connection.execute(
            sa.select(sa.func.count())
            .select_from(TABLES[table])
            .where(TABLES[table].c.document_id == tree.document_id)
        )
        assert remaining.scalar_one() == 0
