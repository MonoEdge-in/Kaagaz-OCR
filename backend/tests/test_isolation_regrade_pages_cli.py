"""``regrade-pages`` (PRD 9 F-11, D5) keeps customers apart: run for one customer it reads and
changes only that customer's pages; run for all, each customer in its own transaction with the
customer set first, and each line names only that customer's own documents."""

import pytest

from kaagaz.db.engine import Database
from tests.db_support import insert_document, new_customer
from tests.test_regrade_pages_cli_db import grades, page, run

pytestmark = pytest.mark.db


def test_one_customers_run_never_reads_or_changes_another_customers_pages(db: Database) -> None:
    mine, my_user = new_customer(db)
    theirs, their_user = new_customer(db)
    with db.customer(mine) as conn:
        my_document = insert_document(conn, mine, uploaded_by=my_user)
    with db.customer(theirs) as conn:
        their_document = insert_document(conn, theirs, uploaded_by=their_user)
    page(db, mine, my_document, 1, ["unpinned"])
    page(db, theirs, their_document, 1, ["unpinned"])

    said = run(db, "--customer", mine, "--apply")

    assert theirs not in said and their_document not in said
    assert grades(db, mine) == [(my_document, 1, "good")]
    assert grades(db, theirs) == [(their_document, 1, "uncertain")]

    everyone = run(db)  # a dry run over every customer: each line its own customer's
    assert f"customer {theirs}: 1 page to grade good\n  document {their_document}: 1 page\n" in everyone
    assert my_document not in everyone
