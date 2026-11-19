"""``regrade-pages`` (PRD 9 F-11, D5): pages read before unpinned lines alone graded a page good
keep ``uncertain`` and stay on Needs attention until read again. The command grades their stored
issues again (``page_quality.grade``, no model call, no read): a dry run lists the counts and
changes nothing; ``--apply`` sets those pages good, and only those, never twice. Another
customer's pages: ``test_isolation_regrade_pages_cli.py``."""

import io
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.cli import main
from kaagaz.db.engine import Database
from tests.db_support import insert_document, new_customer, new_trashed_document

pytestmark = pytest.mark.db

KEPT = "2026-11-18T10:00:00Z"


def page(db: Database, customer: str, document: str, page_no: int, issues: list[str], **more: Any) -> None:
    """An uncertain page with ``issues`` (``more``: unsure_words, illegible_words, accepted_at...)."""
    columns = {
        "read_quality": "uncertain",
        "unsure_words": 0,
        "illegible_words": 0,
        "accepted_at": None,
        "accepted_by": None,
        **more,
    }
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, has_image,"
                " read_quality, read_issues, unsure_words, illegible_words, unpinned_lines, accepted_at,"
                " accepted_by)"
                " VALUES (:c, :d, :n, 595, 842, 'px', true, :q, :i, :u, :il, 3, CAST(:a AS timestamptz),"
                " CAST(:b AS uuid))"
            ),
            {
                "c": customer, "d": document, "n": page_no, "q": columns["read_quality"], "i": issues,
                "u": columns["unsure_words"], "il": columns["illegible_words"], "a": columns["accepted_at"],
                "b": columns["accepted_by"],
            },
        )  # fmt: skip


def grades(db: Database, customer: str) -> list[tuple[str, int, str]]:
    with db.customer(customer) as conn:
        rows = conn.execute(
            text(
                "SELECT document_id::text, page_no, read_quality FROM pages WHERE customer_id = :c"
                " ORDER BY document_id, page_no"
            ),
            {"c": customer},
        ).all()
    return [(document, page_no, quality) for document, page_no, quality in rows]


def run(db: Database, *args: str) -> str:
    out = io.StringIO()
    assert main(["regrade-pages", *args], db=db, environ={}, out=out) == 0
    return out.getvalue()


def scan(db: Database) -> tuple[str, str, str]:
    """A customer, a document with page 1 unpinned only and page 2 unpinned and blurry, and a
    second document whose page 1 is unpinned with an unsure word and page 2 was kept as is."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        first = insert_document(conn, customer, uploaded_by=user, display_name="secret-name.pdf")
        second = insert_document(conn, customer, uploaded_by=user)
    page(db, customer, first, 1, ["unpinned"])
    page(db, customer, first, 2, ["blurry", "unpinned"])
    page(db, customer, second, 1, ["unpinned"], unsure_words=1)
    page(db, customer, second, 2, ["unpinned"], accepted_at=KEPT, accepted_by=user)
    return customer, first, second


def test_a_dry_run_counts_the_unpinned_only_pages_and_changes_nothing(db: Database) -> None:
    customer, first, _ = scan(db)
    before = grades(db, customer)

    said = run(db, "--customer", customer)

    assert said.splitlines() == [
        f"customer {customer}: 1 page to grade good",
        f"  document {first}: 1 page",
        "1 page, not changed (add --apply)",
    ]
    assert grades(db, customer) == before


def test_apply_grades_good_only_the_pages_whose_one_problem_is_unpinned_lines(db: Database) -> None:
    customer, first, second = scan(db)
    trashed = new_trashed_document(db, customer, deleted_at=datetime(2026, 11, 18, tzinfo=UTC))
    page(db, customer, trashed, 1, ["unpinned"])

    said = run(db, "--customer", customer, "--apply")

    assert said == f"customer {customer}: 1 page graded good\n1 page graded good\n"
    assert "secret-name" not in said
    expected = {
        (first, 1, "good"), (first, 2, "uncertain"), (second, 1, "uncertain"), (second, 2, "uncertain"),
        (trashed, 1, "uncertain"),
    }  # fmt: skip
    assert set(grades(db, customer)) == expected
    # Idempotent: nothing is left to grade, so a second run changes nothing.
    assert run(db, "--customer", customer, "--apply") == "0 pages graded good\n"
    assert set(grades(db, customer)) == expected


def test_a_bad_customer_id_is_refused(db: Database) -> None:
    out = io.StringIO()
    assert main(["regrade-pages", "--customer", "nope"], db=db, environ={}, out=out) != 0
