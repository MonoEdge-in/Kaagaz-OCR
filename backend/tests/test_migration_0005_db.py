"""Migration 0005: a deleted document's runs go with it (Vrushit's decision: "if a
document is deleted, instantly forget everything about it").

The upgrade deletes the runs 0004 kept without a document and makes the key
cascade; the downgrade gives back 0004's schema exactly. Every key that points
at documents now deletes with the document. The fresh database and the schema
snapshot come from test_migration_0002_db (a plain import). Rows are written
through the app role with the customer set.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
T0 = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
RUNS = "SELECT id::text, document_id::text FROM processing_runs ORDER BY started_at"
DELETE = text("DELETE FROM documents WHERE customer_id = :c AND id = :d")
# Every table with a key to documents (0001 to 0009, and 0021's page cache, sections and topics);
# invoice_lines points at invoices.
TO_DOCUMENTS = {
    "pages",
    "reader_outputs",
    "pieces",
    "extracted_values",
    "corrections",
    "document_tags",
    "invoices",
    "document_links",
    "processing_runs",
    "user_word_spots",
    "document_stories",
    "page_reads",
    "document_sections",
    "document_topics",
    # Changed on purpose (#239, 27 Oct 2026): the keys added since, each cascading: 0030's word
    # review and Ensemble, 0036's pins, 0041's version suggestions, 0044's organisation word
    # suggestions, 0049's map keys, 0051's learning removals, 0056's page blocks and table cells.
    "word_changes",
    "ensemble_runs",
    "ensemble_readings",
    "source_pins",
    "version_suggestions",
    "organization_word_suggestions",
    "value_keys",
    "learning_removals",
    "page_blocks",
    "table_cells",
    # Changed on purpose (#590, 0084): a file given and a lock grant go with their version.
    "document_shares",
    "lock_grants",
    # Changed on purpose (#600, 0088): a page turn goes with its version.
    "page_rotations",
}
# Changed on purpose (#239, 27 Oct 2026): 0023's spend ledger keeps its rows when a document
# goes: SET NULL (document_id), never a cascade.
# Changed on purpose (#590, 0084): and a remembered correction keeps its row, losing only the
# document it was made on (SET NULL (source_document_id)), as one made before PRD 7.
KEEPS_ITS_ROWS = {"model_usage", "learned_corrections"}


def _runs(database: Database, customer_id: str) -> list[tuple[str, str | None]]:
    with database.customer(customer_id) as conn:
        return [(str(row[0]), row[1]) for row in conn.execute(text(RUNS))]


def test_upgrade_forgets_the_runs_of_deleted_documents_and_downgrade_gives_back_0004(
    fresh: tuple[str, Database],
) -> None:
    url, database = fresh
    migrate(url, "0004")
    at_0004 = m0002._schema(database)
    customer, _ = new_customer(database)
    with database.customer(customer) as conn:
        kept, deleted = insert_document(conn, customer), insert_document(conn, customer)
    runs = PgRuns(database)
    kept_run = runs.start(customer, kept, now=T0)
    orphan = runs.start(customer, deleted, now=T0 + timedelta(minutes=1))
    with database.customer(customer) as conn:
        conn.execute(DELETE, {"c": customer, "d": deleted})
    assert _runs(database, customer) == [(kept_run, kept), (orphan, None)]  # 0004 kept it (AR12)

    migrate(url, "head")
    at_head = m0002._schema(database)
    assert _runs(database, customer) == [(kept_run, kept)]
    with database.customer(customer) as conn:
        conn.execute(DELETE, {"c": customer, "d": kept})
    assert _runs(database, customer) == []

    m0002._downgrade(url, "0004")
    assert m0002._schema(database) == at_0004
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_every_key_to_documents_deletes_with_the_document(db: Database) -> None:
    with db.system() as conn:
        keys = conn.execute(
            text(
                "SELECT conrelid::regclass::text, confdeltype::text FROM pg_constraint"
                " WHERE contype = 'f' AND confrelid = 'documents'::regclass"
                " AND connamespace = 'public'::regnamespace"
            )
        ).all()
    assert {table for table, _ in keys} == TO_DOCUMENTS | KEEPS_ITS_ROWS
    assert {(table, action) for table, action in keys} == {(table, "c") for table in TO_DOCUMENTS} | {
        (table, "n") for table in KEEPS_ITS_ROWS
    }
