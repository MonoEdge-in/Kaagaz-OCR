"""Trash and versions against a real PostgreSQL: what search, the fixed queries,
review and the admin numbers can see (PRD 1 F5 and F6, plan 9.7).

Each test builds one customer with three read documents that say the same
thing: version 1 and version 2 (current) of one family, and a third document in
Trash. Search, the fixed queries and review see only version 2
(``LIVE_CURRENT``). The admin numbers count each family once by its current
version, and count older versions and Trash on their own.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from kaagaz.answering import fixed_queries
from kaagaz.answering.fixed_queries import PgFixedQueries
from kaagaz.db import admin, review
from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from kaagaz.search.hybrid import search
from tests.db_support import insert_document, new_customer
from tests.test_answering_fixed_queries_db import add_values, cell
from tests.test_search_db import EF_SEARCH, RECIPE, add_pieces, axis

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
AUGUST = (date(2026, 8, 1), date(2026, 9, 1))
DATES = ("date",)
PARTIES = ("party_name",)
ROW = "sheet:1:row:2"
PROFITS = {"old": 100, "current": 200, "binned": 400}


@dataclass(frozen=True, slots=True)
class Library:
    customer: str
    documents: dict[str, str]  # "old", "current", "binned" -> document id
    pieces: dict[str, str]  # the same keys -> piece id
    values: dict[str, list[str]]  # the same keys -> value ids
    to_review: dict[str, str]  # the same keys -> the needs-review value id


def _library(db: Database) -> Library:
    customer, admin_user = new_customer(db)
    with db.customer(customer) as conn:
        old = insert_document(conn, customer, detected_type="zip", is_current=False)
        current = insert_document(conn, customer, detected_type="zip", family_id=old, version_no=2)
        binned = insert_document(conn, customer, detected_type="zip", deleted_at=NOW, deleted_by=admin_user)
        documents = {"old": old, "current": current, "binned": binned}
        for document in documents.values():
            conn.execute(
                text(
                    "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, has_image)"
                    " VALUES (:c, :d, 1, 10, 10, 'cells', false)"
                ),
                {"c": customer, "d": document},
            )
    pieces: dict[str, str] = {}
    values: dict[str, list[str]] = {}
    to_review: dict[str, str] = {}
    for key, document in documents.items():
        [pieces[key]] = add_pieces(db, customer, document, [("Invoice from Sharma Traders", axis(0))])
        rows = [
            cell(ROW, "party_name", "Sharma Traders", column=1),
            cell(ROW, "profit", str(PROFITS[key]), column=2, number=PROFITS[key]),
            cell(ROW, "date", "2026-08-10", column=3, day=date(2026, 8, 10)),
            cell(ROW, f"{key}_only", "x", column=4),
            cell("document", "gstin", "27X", column=5, label="needs_review"),
        ]
        add_values(db, customer, document, rows)
        values[key] = [row["id"] for row in rows]
        to_review[key] = rows[-1]["id"]
    return Library(customer, documents, pieces, values, to_review)


def _found(db: Database, library: Library, query: str, embedding: list[float] | None) -> set[str]:
    with db.customer(library.customer) as conn:
        hits = search(
            conn,
            customer_id=library.customer,
            query_text=query,
            embedding=embedding,
            limit=10,
            recipe=RECIPE,
            ef_search=EF_SEARCH,
        )
    return {hit.document_id for hit in hits}


# --- search and chat --------------------------------------------------------------


def test_each_search_finds_only_the_current_version_of_documents_not_in_trash(db: Database) -> None:
    library = _library(db)
    current = {library.documents["current"]}

    assert _found(db, library, "Sharma Traders", None) == current  # exact words
    assert _found(db, library, "Sharma Tradres", None) == current  # typo tolerant only
    assert _found(db, library, "", axis(0)) == current  # meaning only
    assert _found(db, library, "invoice", axis(0)) == current


def test_the_search_details_lookup_leaves_out_trash_and_older_versions(db: Database) -> None:
    library = _library(db)
    with db.customer(library.customer) as conn:
        rows = conn.execute(
            hybrid._DETAILS, {"c": library.customer, "ids": list(library.pieces.values())}
        ).all()
    assert [str(row[0]) for row in rows] == [library.pieces["current"]]


# --- the fixed queries --------------------------------------------------------------


def test_the_fixed_queries_count_only_the_current_version_of_documents_not_in_trash(db: Database) -> None:
    library = _library(db)
    customer, fixed = library.customer, PgFixedQueries(db)

    [total] = fixed.total(customer, "profit", DATES, *AUGUST)
    assert (total.number, total.records) == (Decimal(200), 1)
    [top] = fixed.top_group(customer, "profit", PARTIES, DATES, *AUGUST, True)
    assert (top.group, top.number) == ("Sharma Traders", Decimal(200))
    assert [row.number for row in fixed.extreme_value(customer, "profit", DATES, *AUGUST, True)] == [
        Decimal(200)
    ]
    [count] = fixed.count_documents(customer, *AUGUST, date_fields=DATES)
    assert count.number == Decimal(1)
    cited = {ref.document_id for row in (total, top, count) for ref in row.values}
    assert cited == {library.documents["current"]}
    assert fixed.available_fields(customer) == {"party_name", "profit", "date", "current_only", "gstin"}


def test_the_cited_values_lookup_leaves_out_trash_and_older_versions(db: Database) -> None:
    library = _library(db)
    every_value = [value for ids in library.values.values() for value in ids]
    with db.customer(library.customer) as conn:
        rows = conn.execute(
            fixed_queries._VALUE_REFS, {"customer_id": library.customer, "ids": every_value}
        ).all()
    assert {str(row.value_id) for row in rows} == set(library.values["current"])


# --- review and the admin numbers ---------------------------------------------------------


def test_review_lists_only_the_current_version_not_older_ones_or_trash(db: Database) -> None:
    library = _library(db)
    customer = library.customer
    with db.customer(customer) as conn:
        rows, total = review.list_queue(conn, customer, limit=10, offset=0, document_id=None)
        older, older_total = review.list_queue(
            conn, customer, limit=10, offset=0, document_id=library.documents["old"]
        )
        in_trash, trash_total = review.list_queue(
            conn, customer, limit=10, offset=0, document_id=library.documents["binned"]
        )

        assert [str(row["id"]) for row in rows] == [library.to_review["current"]]
        assert total == 1
        assert (list(older), older_total) == ([], 0)
        assert (list(in_trash), trash_total) == ([], 0)
        assert review.lock_value(conn, customer, library.to_review["binned"]) is None
        assert review.lock_value(conn, customer, library.to_review["old"]) is None
        assert review.lock_value(conn, customer, library.to_review["current"]) is not None
        # By id there is no Trash filter (plan 9.7): the read-back after a resolve.
        assert review.get_item(conn, customer, library.to_review["binned"]) is not None


def test_review_leaves_out_a_current_version_that_is_not_read(db: Database) -> None:
    library = _library(db)
    customer = library.customer
    with db.customer(customer) as conn:
        conn.execute(
            text("UPDATE documents SET status = 'failed' WHERE id = :d"), {"d": library.documents["current"]}
        )
        rows, total = review.list_queue(conn, customer, limit=10, offset=0, document_id=None)
        assert (list(rows), total) == ([], 0)
        assert review.lock_value(conn, customer, library.to_review["current"]) is None
        assert admin.stats(conn, customer)["open_review_items"] == 0


def test_the_admin_numbers_count_each_family_once_and_older_versions_and_trash_apart(db: Database) -> None:
    library = _library(db)
    with db.customer(library.customer) as conn:
        insert_document(conn, library.customer, status="received")  # its own family, current
        numbers = admin.stats(conn, library.customer)
    assert numbers["documents_by_status"] == {"ready": 1, "received": 1}
    assert numbers["open_review_items"] == 1
    assert (numbers["older_versions"], numbers["in_trash"]) == (1, 1)
