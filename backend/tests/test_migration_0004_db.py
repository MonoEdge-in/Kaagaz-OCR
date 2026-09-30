"""Migration 0004 (PRD 2 WP2.1): rows survive upgrade, downgrade and upgrade again, the
downgrade refuses while rows use what 0003 can't hold, every new CHECK refuses a bad row,
and a document can be marked failed with every code the pipeline emits.

The fresh database and the schema snapshot come from test_migration_0002_db (a
plain import). Rows are written through the app role with the customer set.
"""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, TextClause, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.runs import PgRuns
from tests import test_migration_0002_db as m0002
from tests.db_support import BACKEND, insert_document, migrate, new_customer, violation
from tests.test_pipeline_store_db import new_document, numbered, rows
from tests.test_runs_db import emitted_codes

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
NEW_TABLES = {"invoices", "invoice_lines", "document_links", "processing_runs"}
REASONS = (
    "direct_cell", "readers_agree", "readers_disagree", "single_reader", "low_confidence",
    "corrected", "learned_correction", "check_failed", "model_chosen", "ocr_script",
)  # fmt: skip
T0 = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)

_INSERT_VALUE = text(
    "INSERT INTO extracted_values (customer_id, document_id, record_key, field, value_text, value_raw,"
    " page_no, region, reader, label, label_reason)"
    " VALUES (:c, :d, 'document', 'total_amount', '1,180', '1,180', 1, CAST(:r AS jsonb), 'pdfplumber',"
    " 'needs_review', :reason)"
)
_BOX = '{"kind": "box", "page": 1, "box": [0.1, 0.4, 0.9, 0.42]}'


def _rows_at_0003(conn: Connection, customer_id: str) -> str:
    """A document with one page, one piece and one value, as 0003 knows them."""
    doc = insert_document(conn, customer_id)
    params = {"c": customer_id, "d": doc, "r": _BOX, "reason": "readers_agree"}
    conn.execute(
        text(
            "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit)"
            " VALUES (:c, :d, 1, 612, 792, 'pt')"
        ),
        params,
    )
    conn.execute(
        text(
            "INSERT INTO pieces (customer_id, document_id, seq, page_from, page_to, text, regions, label)"
            " VALUES (:c, :d, 0, 1, 1, 'Total 1,180', jsonb_build_array(CAST(:r AS jsonb)), 'verified')"
        ),
        params,
    )
    conn.execute(_INSERT_VALUE, params)
    return doc


_OLD_ROWS = (
    "SELECT (SELECT count(*) FROM pages WHERE document_id = :d), (SELECT count(*) FROM pieces WHERE"
    " document_id = :d), (SELECT count(*) FROM extracted_values WHERE document_id = :d)"
)
_NEW_COLUMNS = (
    "SELECT d.doc_type, d.languages, d.summary, d.document_date, d.card, d.progress, p.route,"
    " p.effective_dpi, p.low_resolution, pc.context_header, pc.text_latin, v.label_page_no, v.label_region"
    " FROM documents d JOIN pages p ON p.document_id = d.id JOIN pieces pc ON pc.document_id = d.id"
    " JOIN extracted_values v ON v.document_id = d.id WHERE d.id = :d"
)
DEFAULTS: tuple[object, ...] = (None, [], None, None, None, None, None, None, False, "", None, None, None)


def _read(database: Database, customer_id: str, statement: str, doc: str) -> tuple[object, ...]:
    with database.customer(customer_id) as conn:
        return tuple(conn.execute(text(statement), {"d": doc}).one())


def test_upgrade_downgrade_and_upgrade_again_keep_the_rows(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0003")
    at_0003 = m0002._schema(database)
    customer, _ = new_customer(database)
    with database.customer(customer) as conn:
        doc = _rows_at_0003(conn, customer)

    migrate(url, "head")
    at_head = m0002._schema(database)
    assert m0002._names(at_head, "relations") >= NEW_TABLES
    assert _read(database, customer, _NEW_COLUMNS, doc) == DEFAULTS
    # Rows in every new table and column, so the downgrade has something to drop.
    linked = [new_document(database, customer), new_document(database, customer)]
    for other in linked:
        PgPipelineStore(database).save(numbered(customer, other))
    PgRuns(database).start(customer, linked[0], now=T0)
    with database.customer(customer) as conn:
        conn.execute(text("UPDATE documents SET languages = '{hi}', card = '{}' WHERE id = :d"), {"d": doc})

    m0002._downgrade(url, "0003")
    assert m0002._schema(database) == at_0003
    assert _read(database, customer, _OLD_ROWS, doc) == (1, 1, 1)

    migrate(url, "head")
    assert m0002._schema(database) == at_head
    assert _read(database, customer, _NEW_COLUMNS, doc) == DEFAULTS


def _current(url: str) -> str:
    """What ``alembic current`` prints for the database at ``url``."""
    done = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
    )
    return done.stdout.decode()


_FAILED_WITH = text("UPDATE documents SET status = 'failed', failure_code = :reason WHERE id = :d")


@pytest.mark.parametrize(
    ("statement", "reason", "message"),
    [
        (_INSERT_VALUE, "check_failed", b"values labelled check_failed"),
        (_FAILED_WITH, "not_utf8", b"documents have failure codes 0003 does not allow"),
    ],
    ids=["check_failed", "not_utf8"],
)
def test_downgrade_refuses_while_rows_use_what_0003_cannot_hold(
    fresh: tuple[str, Database], statement: TextClause, reason: str, message: bytes
) -> None:
    url, database = fresh
    migrate(url, "head")
    at_head = m0002._schema(database)
    customer, _ = new_customer(database)
    with database.customer(customer) as conn:
        doc = insert_document(conn, customer)
        conn.execute(statement, {"c": customer, "d": doc, "r": _BOX, "reason": reason})

    with pytest.raises(subprocess.CalledProcessError) as refused:
        m0002._downgrade(url, "0003")

    assert message in refused.value.stderr
    assert "0004 (head)" in _current(url)
    assert m0002._schema(database) == at_head


def test_every_label_reason_and_the_superseded_status_are_accepted(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer)
        for reason in REASONS:
            conn.execute(_INSERT_VALUE, {"c": customer, "d": doc, "r": _BOX, "reason": reason})
        conn.execute(
            text(
                "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status)"
                " VALUES (:c, :d, :t, :t, 'superseded')"
            ),
            {"c": customer, "d": doc, "t": T0},
        )
    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        conn.execute(_INSERT_VALUE, {"c": customer, "d": doc, "r": _BOX, "reason": "bogus"})
    assert violation(refused.value) == ("23514", "extracted_values_label_reason_check")


_PAGE = "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, {})"
_PAGE += " VALUES (:c, :d, 1, 1, 1, 'pt', {})"
_DOCUMENT = "UPDATE documents SET {} WHERE id = :d"
_INVOICE = "INSERT INTO invoices (customer_id, document_id, label, check_status{})"
_INVOICE += " VALUES (:c, :d, 'needs_review', {})"
_RUN = "INSERT INTO processing_runs (customer_id, document_id, started_at, status, finished_at{})"
_RUN += " VALUES (:c, :d, :t, {})"
_LINE = "INSERT INTO invoice_lines (customer_id, invoice_id, document_id, line_no, label, description)"
_LINE += " VALUES (:c, :i, :d, 1, 'verified', repeat('x', 1001))"
_LINK = "INSERT INTO document_links (customer_id, document_id, linked_document_id, identifier_kind,"
_LINK += " identifier_value) VALUES (:c, :d, :d, 'invoice_number', 'x')"
BAD_ROWS = [
    ("pages_route_check", _PAGE.format("route", "'fax'")),
    ("pages_effective_dpi_check", _PAGE.format("effective_dpi", "0")),
    ("documents_languages_check", _DOCUMENT.format("languages = '{fr}'")),
    ("documents_summary_check", _DOCUMENT.format("summary = repeat('x', 601)")),
    ("documents_card_check", _DOCUMENT.format("card = '[]'")),
    ("documents_card_check", _DOCUMENT.format("card = jsonb_build_object('s', repeat('x', 65536))")),
    ("documents_progress_check", _DOCUMENT.format("""progress = '{"step": "reading", "done": 1}'""")),
    (
        "documents_progress_check",
        _DOCUMENT.format("""progress = '{"step": "reading", "done": -1, "total": 2}'"""),
    ),
    (
        "documents_progress_check",
        _DOCUMENT.format("""progress = '{"step": "reading", "done": 1.5, "total": 2}'"""),
    ),
    (
        "documents_progress_check",
        _DOCUMENT.format("""progress = '{"step": "reading", "done": 1, "total": "2"}'"""),
    ),
    (
        "documents_progress_check",
        _DOCUMENT.format("""progress = '{"step": "napping", "done": 1, "total": 2}'"""),
    ),
    ("documents_progress_check", _DOCUMENT.format("""progress = '{"step": null, "done": 1, "total": 2}'""")),
    ("documents_progress_check", _DOCUMENT.format("progress = '[1, 2]'")),
    ("extracted_values_label_both", "UPDATE extracted_values SET label_page_no = 1 WHERE id = :v"),
    ("invoices_check", _INVOICE.format("", "'failed'")),
    ("invoices_currency_check", _INVOICE.format(", currency", "'not_checked', 'inr'")),
    ("invoices_seller_name_check", _INVOICE.format(", seller_name", "'not_checked', repeat('x', 201)")),
    ("invoice_lines_description_check", _LINE),
    ("document_links_check", _LINK),
    ("processing_runs_status_check", _RUN.format("", "'paused', :t")),
    ("processing_runs_check", _RUN.format("", "'running', :t")),
    ("processing_runs_check1", _RUN.format("", "'ready', :t - interval '1 second'")),
    ("processing_runs_ocr_engine_check", _RUN.format(", ocr_engine", "'ready', :t, repeat('x', 101)")),
    ("processing_runs_config_check", _RUN.format(", config", "'ready', :t, '[]'")),
]


@pytest.mark.parametrize(("constraint", "statement"), BAD_ROWS)
def test_new_checks_refuse_bad_rows(db: Database, constraint: str, statement: str) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer)
        value = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO extracted_values (id, customer_id, document_id, record_key, field, value_text,"
                " value_raw, page_no, region, reader, label, label_reason) VALUES (:v, :c, :d, 'document',"
                " 'total_amount', '1', '1', 1, '{}', 'pdfplumber', 'needs_review', 'single_reader')"
            ),
            {"v": value, "c": customer, "d": doc},
        )
        invoice = str(
            conn.execute(
                text(
                    "INSERT INTO invoices (customer_id, document_id, label, check_status)"
                    " VALUES (:c, :d, 'needs_review', 'not_checked') RETURNING id"
                ),
                {"c": customer, "d": doc},
            ).scalar_one()
        )
    params = {"c": customer, "d": doc, "v": value, "i": invoice, "t": T0}

    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        conn.execute(text(statement), params)
    assert violation(refused.value) == ("23514", constraint)


FAILURE_CODES = sorted(emitted_codes())


@pytest.mark.parametrize("code", FAILURE_CODES)
def test_a_document_can_be_marked_failed_with_every_code_the_pipeline_emits(db: Database, code: str) -> None:
    """0004 fixes a bug on main: 0001's pattern had no digits, so a CSV that is not
    UTF-8 (``not_utf8``) could not be marked failed."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer, status="clean")
    PgDocuments(db).set_failed(customer, doc, code)
    assert rows(db, customer, "SELECT status, failure_code FROM documents WHERE id = :d", doc) == [
        {"status": "failed", "failure_code": code}
    ]


def test_a_failure_code_outside_the_pattern_is_refused(db: Database) -> None:
    assert "not_utf8" in FAILURE_CODES
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer, status="clean")
    with pytest.raises(IntegrityError) as refused:
        PgDocuments(db).set_failed(customer, doc, "Bad-Code")
    assert violation(refused.value) == ("23514", "documents_failure_code_check")


def test_a_document_has_at_most_one_running_run(db: Database) -> None:
    """The backstop behind start_run's lock: a second running run is refused."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer)
    running = text(_RUN.format("", "'running', NULL"))
    with db.customer(customer) as conn:
        conn.execute(running, {"c": customer, "d": doc, "t": T0})
    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        conn.execute(running, {"c": customer, "d": doc, "t": T0})
    assert violation(refused.value) == ("23505", "processing_runs_one_running")
