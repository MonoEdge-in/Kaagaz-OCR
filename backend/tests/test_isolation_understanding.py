"""Customer B never reaches customer A's invoices, lines, links, runs or card (PRD 2 WP2.1).

B uses A's exact ids. The row level policy (42501) and the composite foreign keys
(23503, the constraint named) are each shown on their own. Marked ``isolation``
by its file name.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.runs import PgRuns, is_run_open
from kaagaz.extraction.values import DocumentFacts, LinkIdentifier
from kaagaz.jobs.worker import JobFailed, Superseded
from kaagaz.pipeline import DocumentContent, FinishedRun, Progress
from tests.db_support import count_rows, new_customer, violation
from tests.test_isolation_schema import FROM_0004
from tests.test_pipeline_store_db import (
    FACT_COLUMNS,
    FACTS,
    content,
    counts,
    links,
    new_document,
    numbered,
    read,
    rows,
)
from tests.test_runs_db import totals

pytestmark = pytest.mark.db

T = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
TABLES = ("invoices", "invoice_lines", "document_links", "processing_runs")
INSERTS = {
    "invoices": "INSERT INTO invoices (customer_id, document_id, label, check_status)"
    " VALUES (:who, :bare, 'needs_review', 'not_checked')",
    "invoice_lines": "INSERT INTO invoice_lines (customer_id, invoice_id, document_id, line_no, label)"
    " VALUES (:who, :invoices, :doc, 9, 'needs_review')",
    "document_links": "INSERT INTO document_links (customer_id, document_id, linked_document_id,"
    " identifier_kind, identifier_value) VALUES (:who, :b_doc, :doc, 'invoice_number', 'inv7')",
    "processing_runs": "INSERT INTO processing_runs (customer_id, document_id, started_at, status)"
    " VALUES (:who, :doc, :now, 'running')",
}
# B's own row pointing at A's rows: (the statement, the foreign key that refuses it).
FOREIGN_KEYS = {
    "invoices": (INSERTS["invoices"], "invoices_customer_id_document_id_fkey"),
    "invoice_lines": (INSERTS["invoice_lines"], "invoice_lines_customer_id_document_id_invoice_id_fkey"),
    "link_to_a": (INSERTS["document_links"], "document_links_customer_id_linked_document_id_fkey"),
    "link_from_a": (
        "INSERT INTO document_links (customer_id, document_id, linked_document_id, identifier_kind,"
        " identifier_value) VALUES (:who, :doc, :b_doc, 'invoice_number', 'inv7')",
        "document_links_customer_id_document_id_fkey",
    ),
    "processing_runs": (INSERTS["processing_runs"], "processing_runs_customer_id_document_id_fkey"),
}


def world(db: Database) -> dict[str, str]:
    """A with two linked documents (card, invoice, lines, links), a run and a bare
    document; B with one document of its own. Returns every id by name."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    doc, other, bare = new_document(db, a), new_document(db, a), new_document(db, a)
    read(db, numbered(a, other))  # a link target is ready
    PgPipelineStore(db).save(replace(numbered(a, doc), facts=FACTS))
    ids = {"a": a, "b": b, "doc": doc, "other": other, "bare": bare, "b_doc": new_document(db, b)}
    ids["processing_runs"] = PgRuns(db).start(a, doc, now=T)
    with db.customer(a) as conn:
        for table in ("invoices", "invoice_lines", "document_links"):
            first = f"SELECT id::text FROM {table} WHERE document_id = :d ORDER BY id LIMIT 1"  # noqa: S608 - fixed name
            ids[table] = str(conn.execute(text(first), {"d": doc}).scalar_one())
    return ids


def a_rows(db: Database, ids: dict[str, str]) -> tuple[object, ...]:
    """Everything of A's that B's calls could touch."""
    a, doc = ids["a"], ids["doc"]
    return (
        counts(db, a, doc),
        rows(db, a, FACT_COLUMNS, doc),
        links(db, a, doc),
        rows(db, a, "SELECT total, check_status FROM invoices WHERE document_id = :d", doc),
        rows(db, a, "SELECT status, finished_at FROM processing_runs WHERE document_id = :d", doc),
        rows(db, a, "SELECT status, progress FROM documents WHERE id = :d", doc),
    )


@pytest.mark.parametrize("table", TABLES)
def test_b_cannot_see_update_or_delete_a_rows_by_id(db: Database, table: str) -> None:
    ids = world(db)
    before = a_rows(db, ids)
    by_id = {"id": ids[table]}
    _select, _insert, may_update, may_delete = FROM_0004[table]

    with db.customer(ids["b"]) as conn:
        assert count_rows(conn, f"SELECT count(*) FROM {table} WHERE id = :id", by_id) == 0  # noqa: S608 - fixed table name
    for statement, granted in (
        (f"UPDATE {table} SET customer_id = customer_id WHERE id = :id", may_update),  # noqa: S608 - fixed table name
        (f"DELETE FROM {table} WHERE id = :id", may_delete),  # noqa: S608 - fixed table name
    ):
        if granted:
            with db.customer(ids["b"]) as conn:
                assert conn.execute(text(statement), by_id).rowcount == 0
        else:  # the app role may not do this to any row (grants, A9)
            with pytest.raises(DBAPIError) as refused, db.customer(ids["b"]) as conn:
                conn.execute(text(statement), by_id)
            assert violation(refused.value)[0] == "42501"
    with db.customer(ids["a"]) as conn:
        assert count_rows(conn, f"SELECT count(*) FROM {table} WHERE id = :id", by_id) == 1  # noqa: S608 - fixed table name
    assert a_rows(db, ids) == before


@pytest.mark.parametrize("table", TABLES)
def test_b_writing_a_row_as_a_is_refused_by_the_policy(db: Database, table: str) -> None:
    ids = world(db)
    with pytest.raises(DBAPIError) as refused, db.customer(ids["b"]) as conn:
        conn.execute(text(INSERTS[table]), {**ids, "who": ids["a"], "now": T})
    assert violation(refused.value) == ("42501", None)
    assert "row-level security" in str(refused.value.orig)


@pytest.mark.parametrize("case", FOREIGN_KEYS)
def test_b_pointing_its_own_row_at_a_document_is_refused_by_the_foreign_key(db: Database, case: str) -> None:
    ids = world(db)
    before = a_rows(db, ids)
    statement, constraint = FOREIGN_KEYS[case]
    with pytest.raises(IntegrityError) as refused, db.customer(ids["b"]) as conn:
        conn.execute(text(statement), {**ids, "who": ids["b"], "now": T})
    assert violation(refused.value) == ("23503", constraint)
    assert a_rows(db, ids) == before


def test_a_line_belongs_to_its_invoices_document(db: Database) -> None:
    """AR13, inside one customer: a line of one document's invoice can't name another document."""
    ids = world(db)
    with pytest.raises(IntegrityError) as refused, db.customer(ids["a"]) as conn:
        conn.execute(text(INSERTS["invoice_lines"]), {**ids, "who": ids["a"], "doc": ids["other"]})
    assert violation(refused.value) == ("23503", "invoice_lines_customer_id_document_id_invoice_id_fkey")


def test_b_saving_or_clearing_with_a_document_id_leaves_a_card_invoice_and_links(db: Database) -> None:
    ids = world(db)
    before = a_rows(db, ids)
    store = PgPipelineStore(db)
    b, doc = ids["b"], ids["doc"]
    other_facts = DocumentFacts("receipt", ("mr",), "B's words.", None, {"title": "B"})

    with pytest.raises(IntegrityError):  # the (customer, document) foreign key
        store.save(replace(numbered(b, doc), facts=other_facts))
    # Nothing to insert: no error, and nothing of A's is touched either.
    store.save(DocumentContent(b, doc, [], [], [], [], "", links=(LinkIdentifier("invoice_number", "inv7"),)))
    store.clear(b, doc)

    assert a_rows(db, ids) == before
    assert rows(db, ids["a"], FACT_COLUMNS, doc)[0]["doc_type"] == "invoice"


def test_same_invoice_number_at_b_never_links_to_a(db: Database) -> None:
    ids = world(db)
    b, b_doc = ids["b"], ids["b_doc"]
    b_other = new_document(db, b)
    read(db, numbered(b, b_doc))
    PgPipelineStore(db).save(numbered(b, b_other))

    assert links(db, b, b_doc) == {
        (b_doc, b_other, "invoice_number", "inv7"),
        (b_other, b_doc, "invoice_number", "inv7"),
    }
    assert links(db, ids["a"], ids["doc"]) == {
        (ids["doc"], ids["other"], "invoice_number", "inv7"),
        (ids["other"], ids["doc"], "invoice_number", "inv7"),
    }


def test_b_cannot_start_finish_fence_or_show_progress_on_a_document(db: Database) -> None:
    ids = world(db)
    before = a_rows(db, ids)
    b, doc, run = ids["b"], ids["doc"], ids["processing_runs"]

    with pytest.raises(JobFailed) as failed:
        PgRuns(db).start(b, doc, now=T)
    assert failed.value.code == "document_missing"
    PgRuns(db).finish(b, FinishedRun(run, totals("failed", error_code="interrupted"), T))
    PgDocuments(db).set_progress(b, doc, Progress("reading", 1, 2))
    PgDocuments(db).mark_ready(b, doc, 2, run=FinishedRun(run, totals(), T))
    with db.customer(b) as conn:
        assert not is_run_open(conn, b, doc, run)
    with pytest.raises(Superseded):
        PgPipelineStore(db).save(content(b, doc), run_id=run)
    with pytest.raises(Superseded):
        PgDocuments(db).set_failed(b, doc, "corrupt_file", run_id=run)

    assert a_rows(db, ids) == before
    assert before[-1] == [{"status": "received", "progress": None}]
