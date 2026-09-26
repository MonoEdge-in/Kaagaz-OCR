"""The migrations build the schema: the vector extension, then every table in the approved
schema plan (PRs 1 to 6).

Tests marked `db` read the migrated database (CI runs `alembic upgrade head` first).
"""

from typing import Any

import pytest
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.ext.asyncio import AsyncConnection

from kaagaz_shared.models import EMBEDDING_DIM, NAMING_CONVENTION, Base

TABLES_SO_FAR = {
    "folders",
    "documents",
    "uploads",
    "audit_log",
    "tags",
    "document_tags",
    "document_versions",
    "document_links",
    "pages",
    "extraction_runs",
    "blocks",
    "invoices",
    "invoice_lines",
    "extracted_fields",
    "field_sources",
    "review_tasks",
    "chunks",
}


TABLES = Base.metadata.tables


def test_constraints_get_predictable_names() -> None:
    # Migrations drop constraints by name, and tests match on them.
    assert Base.metadata.naming_convention == NAMING_CONVENTION


def test_models_describe_every_table_in_the_approved_plan() -> None:
    assert set(TABLES) == TABLES_SO_FAR


def covered(fk_columns: list[str], index_columns: list[list[str]]) -> bool:
    """An index serves a foreign key when its leading columns are the key's columns."""
    size = len(fk_columns)
    return any(set(columns[:size]) == set(fk_columns) for columns in index_columns)


def test_every_foreign_key_in_the_models_has_an_index() -> None:
    missing: list[str] = []
    for table in Base.metadata.tables.values():
        leading = [
            [column.name for column in index.columns]
            for index in table.indexes
            if index.dialect_options["postgresql"]["where"] is None
        ]
        leading += [
            [column.name for column in constraint.columns]
            for constraint in table.constraints
            if isinstance(constraint, sa.UniqueConstraint | sa.PrimaryKeyConstraint)
        ]
        for fk in table.foreign_key_constraints:
            if not covered([column.name for column in fk.columns], leading):
                # The naming convention always gives foreign keys a real name.
                missing.append(str(fk.name))
    assert missing == []


@pytest.mark.db
async def test_vector_extension_is_installed(connection: AsyncConnection) -> None:
    result = await connection.execute(
        sa.text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
    )
    assert result.scalar_one() == 1


@pytest.mark.db
async def test_the_database_has_exactly_these_tables(connection: AsyncConnection) -> None:
    names = await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())
    assert set(names) - {"alembic_version"} == TABLES_SO_FAR


@pytest.mark.db
async def test_every_foreign_key_in_the_database_has_an_index(
    connection: AsyncConnection,
) -> None:
    def query(sync: Any) -> list[tuple[str, str, list[str]]]:
        result = sync.execute(
            sa.text(
                "SELECT c.conrelid::regclass::text, c.conname,"
                " ARRAY(SELECT a.attname::text FROM unnest(c.conkey) AS k(n)"
                " JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.n)"
                " FROM pg_constraint AS c"
                " WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace"
            )
        )
        return [tuple(row) for row in result.all()]

    foreign_keys = await connection.run_sync(query)

    def index_query(sync: Any) -> list[tuple[str, list[str]]]:
        result = sync.execute(
            sa.text(
                "SELECT i.indrelid::regclass::text,"
                " ARRAY(SELECT a.attname::text FROM unnest(i.indkey) AS k(n)"
                " JOIN pg_attribute AS a ON a.attrelid = i.indrelid AND a.attnum = k.n)"
                " FROM pg_index AS i WHERE i.indpred IS NULL"
            )
        )
        return [tuple(row) for row in result.all()]

    indexes = await connection.run_sync(index_query)
    by_table: dict[str, list[list[str]]] = {}
    for table, columns in indexes:
        by_table.setdefault(table, []).append(list(columns))

    missing = [
        name
        for table, name, columns in foreign_keys
        if not covered(list(columns), by_table.get(table, []))
    ]
    assert missing == []


def test_the_embedding_column_has_the_model_size() -> None:
    column_type = TABLES["chunks"].c.embedding.type
    assert isinstance(column_type, Vector)
    assert column_type.dim == EMBEDDING_DIM == 1024


@pytest.mark.db
async def test_search_indexes_exist(connection: AsyncConnection) -> None:
    # alembic check can't compare postgresql_using/ops/where, so this reads pg_catalog
    # directly. Declaring them in models.py too keeps a future migration from drifting.
    pg_indexes = sa.table(
        "pg_indexes",
        sa.column("schemaname"),
        sa.column("indexname"),
        sa.column("indexdef"),
        schema="pg_catalog",
    )
    result = await connection.execute(
        sa.select(pg_indexes.c.indexname, pg_indexes.c.indexdef).where(
            pg_indexes.c.schemaname == "public"
        )
    )
    definitions: dict[str, str] = {row.indexname: row.indexdef for row in result}
    assert "USING hnsw (embedding vector_cosine_ops)" in definitions["ix_chunks_embedding"]
    assert "USING gin (tsv)" in definitions["ix_chunks_tsv"]


@pytest.mark.db
async def test_chunk_text_is_searchable_in_english(connection: AsyncConnection) -> None:
    documents, chunks = TABLES["documents"], TABLES["chunks"]
    document_id = (
        await connection.execute(
            sa.insert(documents)
            .values(
                original_filename="invoice.pdf",
                content_type="application/pdf",
                size_bytes=10,
                storage_key="originals/x.pdf",
                uploaded_by="Tushar",
            )
            .returning(documents.c.id)
        )
    ).scalar_one()
    await connection.execute(
        sa.insert(chunks).values(
            document_id=document_id,
            chunk_no=1,
            page_from=1,
            page_to=1,
            header="Shinde Traders",
            text="Invoice total for October",
        )
    )

    async def matches(query: sa.ColumnElement[Any]) -> bool:
        found = await connection.execute(
            sa.select(sa.func.count()).where(chunks.c.tsv.bool_op("@@")(query))
        )
        return bool(found.scalar_one())

    # English stemming: "invoices" finds "Invoice".
    assert await matches(sa.func.plainto_tsquery("english", "invoices"))
    # The header text is searchable too.
    assert await matches(sa.func.plainto_tsquery("english", "shinde"))
    assert not await matches(sa.func.plainto_tsquery("english", "purchase order"))
