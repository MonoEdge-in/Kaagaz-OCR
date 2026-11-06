"""Migration 0077 (#521, Tushar, 6 Nov 2026): ``version_suggestions.text_overlap_exact``, and the
comments of ``text_overlap`` and ``outcome``. Up, down and up again with an automatic link made
before it: the row is kept as it was (``auto``, accepted, its column NULL), the app may write the
new column, and the schema and comments are as before each time."""

from typing import Any

import pytest

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer
from tests.test_migration_0037 import _load
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
fresh = base.fresh
M0077 = _load("0077_version_overlap_exact.py")
_COMMENTS = (
    "SELECT a.attname, col_description(a.attrelid, a.attnum) FROM pg_attribute a"
    " WHERE a.attrelid = 'version_suggestions'::regclass AND a.attnum > 0 AND NOT a.attisdropped"
    " ORDER BY a.attname"
)
_ROW = "SELECT outcome, status, text_overlap FROM version_suggestions WHERE id = :s"


def _comments(db: Database) -> dict[str, Any]:
    return dict(_owner(db, _COMMENTS, {}))


def _auto_link(db: Database) -> tuple[str, str, str]:
    """A suggestion linked on its own before 0077: (customer, document, suggestion)."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        old, new = insert_document(conn, customer), insert_document(conn, customer)
    suggestion = _owner(
        db,
        "INSERT INTO version_suggestions (customer_id, document_id, candidate_family_id, text_overlap,"
        " outcome, status) VALUES (:c, :n, :o, 0.96875, 'auto', 'accepted') RETURNING id::text",
        {"c": customer, "n": new, "o": old},
    )[0][0]
    return customer, new, suggestion


def test_the_revision_follows_0075() -> None:
    assert (M0077.revision, M0077.down_revision) == ("0077", "0075")


def test_up_down_and_up_again_keep_an_old_automatic_link_as_it_was(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0075")
    at_0075, comments_0075 = base._schema(db), _comments(db)
    customer, new, suggestion = _auto_link(db)
    before = _owner(db, _ROW, {"s": suggestion})

    migrate(url, "0077")
    at_0077, comments_0077 = base._schema(db), _comments(db)
    column = [row for row in at_0077["columns"] if row[:2] == ("version_suggestions", "text_overlap_exact")]
    assert column == [("version_suggestions", "text_overlap_exact", "boolean", False, None)]
    assert _owner(db, _ROW, {"s": suggestion}) == before  # no data change: still auto, accepted
    exact = "SELECT text_overlap_exact FROM version_suggestions WHERE id = :s"
    assert _owner(db, exact, {"s": suggestion}) == [(None,)]
    assert comments_0077 == {**comments_0075, **M0077.COMMENTS}
    assert all(comments_0077.values())  # 0007's rule: every column says what it holds
    with db.customer(customer) as conn:  # the app's own right on the table covers the column
        conn.exec_driver_sql(
            "UPDATE version_suggestions SET text_overlap_exact = true WHERE id = %(s)s", {"s": suggestion}
        )
    constraint = [row for row in at_0077["constraints"] if row[1] == "version_suggestions_outcome_check"]
    assert constraint and "'auto'" in constraint[0][2]  # old rows stay allowed

    base._downgrade(url, "0075")
    assert base._schema(db) == at_0075
    assert _comments(db) == comments_0075
    assert _owner(db, _ROW, {"s": suggestion}) == before
    assert _owner(db, "SELECT count(*) FROM version_suggestions WHERE document_id = :d", {"d": new}) == [(1,)]

    migrate(url, "0077")
    assert base._schema(db) == at_0077
    assert _comments(db) == comments_0077
