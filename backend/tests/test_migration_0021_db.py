"""Migration 0021 (PRD 5 section 8): the Gemini reader's data.

Up, down (0020's schema and comments exactly) and up again; the upgrade clears every
stored ``ocr_choice`` (RT3); the downgrade refuses while a value carries a PRD 5
label reason; every new CHECK refuses a bad row; the new label reasons and the
failure code ``unreadable`` (F4) are accepted. The grants and the policies of the new
tables are pinned in test_isolation_schema, and their customer separation in
test_isolation_page_reads and test_isolation_sections_topics.

The fresh database and the schema snapshot come from test_migration_0002_db (a plain
import). Rows are written through the app role with the customer set.
"""

import subprocess
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns
from kaagaz.pipeline import FinishedRun
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer, violation
from tests.test_migration_0004_db import _BOX, _INSERT_VALUE, _current, _head
from tests.test_migration_0007_db import _dictionary
from tests.test_runs_db import totals

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
T0 = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 8, 10, 5, tzinfo=UTC)
SHA = "a" * 64
NEW_TABLES = {"page_reads", "document_sections", "document_topics"}
NEW_INDEXES = {
    "documents_batch",
    "document_sections_embedding",
    "document_sections_tsv",
    "document_topics_aliases",
}
NEW_COLUMNS = {
    ("pages", "read_by"),
    ("pages", "read_quality"),
    ("pages", "read_issues"),
    ("pages", "unsure_words"),
    ("pages", "illegible_words"),
    ("pages", "unpinned_lines"),
    ("pages", "accepted_by"),
    ("pages", "accepted_at"),
    ("pages", "read_again_requested_at"),
    ("documents", "batch_id"),
    ("conversations", "summary"),
    ("conversations", "memory"),
    ("conversations", "summary_turn"),
    *{
        ("processing_runs", name)
        for name in (
            "pages_gemini",
            "pages_local",
            "pages_cached",
            "pages_read_again",
            "pages_unreadable",
            "input_tokens",
            "output_tokens",
        )
    },
}
PRD5_REASONS = ("unsure_text", "box_unanchored", "local_reader")

_CHOSEN_AT_0020 = text(
    "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes, detected_type, status,"
    " family_id, ocr_choice) VALUES (:d, :c, 'scan.png', :h, 10, 'png', 'ready', :d, 'rapidocr')"
)
_OCR_CHOICE = text("SELECT ocr_choice FROM documents WHERE id = :d")


def _columns(schema: dict[str, list[tuple[object, ...]]]) -> set[tuple[str, str]]:
    return {(str(row[0]), str(row[1])) for row in schema["columns"]}


def test_0021_up_down_up_and_the_reader_choices_are_cleared(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0020")
    at_0020, comments_0020 = m0002._schema(database), _dictionary(database)
    customer, _ = new_customer(database)
    chosen = str(uuid.uuid4())
    with database.customer(customer) as conn:
        conn.execute(_CHOSEN_AT_0020, {"d": chosen, "c": customer, "h": uuid.uuid4().hex * 2})

    migrate(url, "head")
    assert _current(url) == _head()
    at_head, comments_head = m0002._schema(database), _dictionary(database)
    assert m0002._names(at_head, "relations") >= NEW_TABLES
    assert m0002._names(at_head, "indexes", 1) >= NEW_INDEXES
    assert _columns(at_head) >= NEW_COLUMNS
    assert {"pages_accepted_both", "pages_accepted_by_fk"} <= m0002._names(at_head, "constraints", 1)
    assert "0021 cleared it" in str(comments_head[("documents", "ocr_choice")])
    with database.customer(customer) as conn:
        assert conn.execute(_OCR_CHOICE, {"d": chosen}).scalar_one() is None  # RT3

    m0002._downgrade(url, "0020")
    assert m0002._schema(database) == at_0020
    assert _dictionary(database) == comments_0020  # 0013's ocr_choice comment is back
    migrate(url, "head")
    assert m0002._schema(database) == at_head
    assert _dictionary(database) == comments_head


@pytest.mark.parametrize("reason", PRD5_REASONS)
def test_the_downgrade_refuses_while_a_value_has_a_prd5_reason(
    fresh: tuple[str, Database], reason: str
) -> None:
    url, database = fresh
    migrate(url, "head")
    at_head = m0002._schema(database)
    customer, _ = new_customer(database)
    with database.customer(customer) as conn:
        doc = insert_document(conn, customer)
        conn.execute(_INSERT_VALUE, {"c": customer, "d": doc, "r": _BOX, "reason": reason})

    with pytest.raises(subprocess.CalledProcessError) as refused:
        m0002._downgrade(url, "0020")

    assert b"values labelled unsure_text, box_unanchored or local_reader" in refused.value.stderr
    assert _current(url) == _head()  # the whole downgrade is one transaction
    assert m0002._schema(database) == at_head


def test_the_prd5_label_reasons_and_the_failure_code_unreadable_are_accepted(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer, status="clean")
        for reason in PRD5_REASONS:
            conn.execute(_INSERT_VALUE, {"c": customer, "d": doc, "r": _BOX, "reason": reason})
    run = PgRuns(db).start(customer, doc, now=T0)
    PgRuns(db).finish(customer, FinishedRun(run, totals("failed", error_code="unreadable"), T1))
    PgDocuments(db).set_failed(customer, doc, "unreadable")

    with db.customer(customer) as conn:
        reasons = conn.execute(
            text("SELECT label_reason FROM extracted_values WHERE document_id = :d ORDER BY label_reason"),
            {"d": doc},
        ).scalars()
        assert list(reasons) == sorted(PRD5_REASONS)
        failed = conn.execute(
            text(
                "SELECT d.status, d.failure_code, r.error_code FROM documents d"
                " JOIN processing_runs r ON r.customer_id = d.customer_id AND r.document_id = d.id"
                " WHERE d.id = :d"
            ),
            {"d": doc},
        ).one()
    assert tuple(failed) == ("failed", "unreadable", "unreadable")


# Each bad row, and the CHECK that refuses it. :c, :d (a document), :u (a user), :v (a
# conversation), :t (a time) are bound; a page 1 of the document exists.
_PAGE = "UPDATE pages SET {} WHERE document_id = :d AND page_no = 1"
_READ = (
    "INSERT INTO page_reads (customer_id, document_id, page_no, image_sha256, model, prompt_version,"
    " result{}) VALUES (:c, :d, {})"
)
_SECTION = (
    "INSERT INTO document_sections (customer_id, document_id, seq, path, page_from, page_to,"
    " piece_seq_from, piece_seq_to{}) VALUES (:c, :d, {})"
)
_TOPIC = "INSERT INTO document_topics (customer_id, document_id, topic, piece_seqs) VALUES (:c, :d, {})"
_CHAT = "UPDATE conversations SET {} WHERE id = :v"
_RUN = (
    "INSERT INTO processing_runs (customer_id, document_id, started_at, status, {})"
    " VALUES (:c, :d, :t, 'running', {})"
)
_GOOD_READ = f"1, '{SHA}', 'gemini-3.1-pro-preview', 'g1', '{{}}'"
_GOOD_SECTION = "0, '{Leave}', 1, 2, 0, 3"
BAD_ROWS = [
    ("pages_read_quality_check", _PAGE.format("read_quality = 'fair'")),
    ("pages_read_issues_check", _PAGE.format("read_issues = '{blurry, tilted}'")),
    ("pages_read_by_check", _PAGE.format("read_by = ''")),
    ("pages_unsure_words_check", _PAGE.format("unsure_words = -1")),
    ("pages_illegible_words_check", _PAGE.format("illegible_words = -1")),
    ("pages_unpinned_lines_check", _PAGE.format("unpinned_lines = -1")),
    ("pages_accepted_both", _PAGE.format("accepted_by = :u")),
    ("pages_accepted_both", _PAGE.format("accepted_at = :t")),
    ("page_reads_page_no_check", _READ.format("", f"0, '{SHA}', 'm', 'g1', '{{}}'")),
    ("page_reads_image_sha256_check", _READ.format("", "1, 'ABC', 'm', 'g1', '{}'")),
    ("page_reads_model_check", _READ.format("", f"1, '{SHA}', '', 'g1', '{{}}'")),
    ("page_reads_prompt_version_check", _READ.format("", f"1, '{SHA}', 'm', repeat('v', 65), '{{}}'")),
    ("page_reads_result_check", _READ.format("", f"1, '{SHA}', 'm', 'g1', '[]'")),
    ("page_reads_input_tokens_check", _READ.format(", input_tokens", _GOOD_READ + ", -1")),
    ("page_reads_output_tokens_check", _READ.format(", output_tokens", _GOOD_READ + ", -1")),
    ("document_sections_seq_check", _SECTION.format("", "-1, '{}', 1, 1, 0, 0")),
    ("document_sections_page_from_check", _SECTION.format("", "0, '{}', 0, 1, 0, 0")),
    ("document_sections_pages", _SECTION.format("", "0, '{}', 3, 2, 0, 0")),
    ("document_sections_piece_seq_from_check", _SECTION.format("", "0, '{}', 1, 1, -1, 0")),
    ("document_sections_pieces", _SECTION.format("", "0, '{}', 1, 1, 4, 3")),
    ("document_sections_summary_check", _SECTION.format(", summary", _GOOD_SECTION + ", repeat('x', 401)")),
    (
        "document_sections_embed_recipe_check",
        _SECTION.format(", embed_recipe", _GOOD_SECTION + ", repeat('x', 301)"),
    ),
    ("document_sections_vector_with_recipe", _SECTION.format(", embed_recipe", _GOOD_SECTION + ", 'gem1'")),
    ("document_topics_topic_check", _TOPIC.format("'', '{1}'")),
    ("document_topics_topic_check", _TOPIC.format("'Leave', '{1}'")),
    ("document_topics_piece_seqs_check", _TOPIC.format("'leave', '{}'")),
    ("conversations_summary_check", _CHAT.format("summary = repeat('x', 1201)")),
    ("conversations_memory_check", _CHAT.format("memory = '[]'")),
    ("conversations_memory_check", _CHAT.format("memory = jsonb_build_object('names', repeat('x', 16384))")),
    ("conversations_summary_turn_check", _CHAT.format("summary_turn = -1")),
    *[
        (f"processing_runs_{name}_check", _RUN.format(name, "-1"))
        for name in (
            "pages_gemini",
            "pages_local",
            "pages_cached",
            "pages_read_again",
            "pages_unreadable",
            "input_tokens",
            "output_tokens",
        )
    ],
]


@pytest.mark.parametrize(("constraint", "statement"), BAD_ROWS)
def test_new_checks_refuse_bad_rows(db: Database, constraint: str, statement: str) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer)
        conn.execute(
            text(
                "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit)"
                " VALUES (:c, :d, 1, 612, 792, 'pt')"
            ),
            {"c": customer, "d": doc},
        )
        chat = str(
            conn.execute(
                text(
                    "INSERT INTO conversations (customer_id, user_id, title)"
                    " VALUES (:c, :u, 'Leave') RETURNING id"
                ),
                {"c": customer, "u": user},
            ).scalar_one()
        )
    params = {"c": customer, "d": doc, "u": user, "v": chat, "t": T0}

    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        conn.execute(text(statement), params)
    assert violation(refused.value) == ("23514", constraint)


def test_good_rows_are_accepted_in_every_new_column(db: Database) -> None:
    """The other side of the CHECKs: a full reading result, a cached read, a section with
    its vector, a topic, a chat memory and the run counters are all stored."""
    customer, user = new_customer(db)
    vector = "[" + ",".join(["0.5"] * 1024) + "]"
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer)
        params = {"c": customer, "d": doc, "u": user, "t": T0, "b": str(uuid.uuid4()), "v": vector}
        conn.execute(text("UPDATE documents SET batch_id = :b WHERE id = :d"), params)
        conn.execute(
            text(
                "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, read_by,"
                " read_quality, read_issues, unsure_words, illegible_words, unpinned_lines, accepted_by,"
                " accepted_at, read_again_requested_at) VALUES (:c, :d, 1, 612, 792, 'pt',"
                " 'gemini-3.1-pro-preview', 'unreadable', '{blurry, illegible_words, no_text, read_locally}',"
                " 3, 2, 1, :u, :t, :t)"
            ),
            params,
        )
        conn.execute(text(_READ.format(", input_tokens, output_tokens", _GOOD_READ + ", 1700, 4000")), params)
        section = (
            f"{_GOOD_SECTION}, 'Leave rules.', CAST(:v AS halfvec(1024)), 'gemini-embedding-2#gem1',"
            " to_tsvector('simple', 'Leave rules')"
        )
        conn.execute(text(_SECTION.format(", summary, embedding, embed_recipe, tsv", section)), params)
        conn.execute(
            text(
                "INSERT INTO document_topics (customer_id, document_id, topic, aliases, piece_seqs)"
                " VALUES (:c, :d, 'छुट्टी', '{holidays, maternity leave}', '{7, 18, 41}')"
            ),
            params,
        )
        conn.execute(
            text(
                "INSERT INTO conversations (customer_id, user_id, title, summary, memory, summary_turn)"
                " VALUES (:c, :u, 'Leave', 'About leave.', '{\"names\": [\"Sharma\"]}', 6)"
            ),
            params,
        )
        conn.execute(
            text(
                "INSERT INTO processing_runs (customer_id, document_id, started_at, status, pages_gemini,"
                " pages_local, pages_cached, pages_read_again, pages_unreadable, input_tokens, output_tokens)"
                " VALUES (:c, :d, :t, 'running', 1, 2, 3, 4, 5, 6000000000, 7000000000)"
            ),
            params,
        )
        stored = conn.execute(
            text(
                "SELECT p.read_issues, r.result, s.path, t.piece_seqs, d.batch_id::text"
                " FROM pages p JOIN page_reads r ON r.document_id = p.document_id"
                " JOIN document_sections s ON s.document_id = p.document_id"
                " JOIN document_topics t ON t.document_id = p.document_id"
                " JOIN documents d ON d.id = p.document_id WHERE p.document_id = :d"
            ),
            params,
        ).one()
    assert tuple(stored) == (
        ["blurry", "illegible_words", "no_text", "read_locally"],
        {},
        ["Leave"],
        [7, 18, 41],
        params["b"],
    )


def test_a_page_accepted_by_a_user_of_another_customer_is_refused(db: Database) -> None:
    """The (customer_id, accepted_by) key: a page can only name its own customer's user."""
    customer, _ = new_customer(db)
    _, stranger = new_customer(db)
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer)
        conn.execute(
            text(
                "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit)"
                " VALUES (:c, :d, 1, 612, 792, 'pt')"
            ),
            {"c": customer, "d": doc},
        )
    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        conn.execute(
            text(_PAGE.format("accepted_by = :u, accepted_at = :t")), {"d": doc, "u": stranger, "t": T0}
        )
    assert violation(refused.value) == ("23503", "pages_accepted_by_fk")


@pytest.mark.parametrize(
    ("table", "statement"),
    [
        ("page_reads", _READ.format("", _GOOD_READ)),
        ("document_sections", _SECTION.format("", _GOOD_SECTION)),
        ("document_topics", _TOPIC.format("'leave', '{1}'")),
    ],
)
def test_each_new_table_holds_one_row_per_key(db: Database, table: str, statement: str) -> None:
    """page_reads: (document, page, image, model, prompt version); sections: (document,
    seq); topics: (document, topic)."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer)
        conn.execute(text(statement), {"c": customer, "d": doc})
    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        conn.execute(text(statement), {"c": customer, "d": doc})
    assert violation(refused.value)[0] == "23505"
    assert str(violation(refused.value)[1]).startswith(table)
