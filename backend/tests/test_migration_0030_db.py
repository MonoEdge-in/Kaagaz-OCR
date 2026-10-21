"""Migration 0030 (word review and Ensemble after the scan, 16 Oct 2026): word changes,
Ensemble runs and readings.

Up, down (0029's schema and comments exactly) and up again, with rows in the new tables;
every new CHECK refuses a bad row; one queued or running Ensemble run per document; a
document deleted takes its rows with it. The grants and policies are pinned in
test_isolation_schema; the rules in test_word_review_db; customer separation in
test_isolation_api_word_review. Rows are written through the app role with the customer set.
"""

import json
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer, violation
from tests.test_migration_0004_db import _current, _head
from tests.test_migration_0007_db import _dictionary

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
NEW_TABLES = {"word_changes", "ensemble_runs", "ensemble_readings"}
KEY = "a" * 64
REGION = json.dumps({"kind": "box", "page": 1, "box": [0.1, 0.1, 0.2, 0.2]})

_CHANGE = text(
    "INSERT INTO word_changes (customer_id, document_id, word_key, kind, page_no, region, as_read, action,"
    " before_text, after_text, reading_id, user_id, undone_at, undone_by)"
    " VALUES (:c, :d, :key, :kind, 1, CAST(:region AS jsonb), 'Rs 4,850', :action, :before, :after,"
    " :reading, :u, :undone_at, :undone_by) RETURNING id"
)
_RUN = text(
    "INSERT INTO ensemble_runs (customer_id, document_id, scope, pages, pages_done, models, status,"
    " requested_by, finished_at) VALUES (:c, :d, :scope, CAST(:pages AS integer[]), CAST(:done AS integer[]),"
    " CAST(:models AS text[]), :status, :u, :finished_at) RETURNING id"
)
_READING = text(
    "INSERT INTO ensemble_readings (customer_id, document_id, run_id, word_key, page_no, region, before_text,"
    " outcome, proposal, decision, decided_by, decided_at)"
    " VALUES (:c, :d, :run, :key, 1, CAST(:region AS jsonb), 'Rs 4,850', :outcome, :proposal, :decision,"
    " :decided_by, :decided_at) RETURNING id"
)


def _setup(db: Database) -> tuple[str, str, str]:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user)
    return customer, user, document


def _change(customer: str, user: str, document: str, **changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "c": customer,
        "d": document,
        "key": KEY,
        "kind": "mark",
        "region": REGION,
        "action": "edit",
        "before": "Rs 4,850",
        "after": "Rs 4,350",
        "reading": None,
        "u": user,
        "undone_at": None,
        "undone_by": None,
    }
    return row | changes


def _run(customer: str, user: str, document: str, **changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "c": customer,
        "d": document,
        "scope": "flagged",
        "pages": [1, 2],
        "done": [],
        "models": ["model/a", "model/b"],
        "status": "queued",
        "u": user,
        "finished_at": None,
    }
    return row | changes


def _reading(customer: str, document: str, run: object, **changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "c": customer,
        "d": document,
        "run": run,
        "key": KEY,
        "region": REGION,
        "outcome": "changed",
        "proposal": "Rs 4,350",
        "decision": None,
        "decided_by": None,
        "decided_at": None,
    }
    return row | changes


def test_0030_up_down_up_with_rows(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0029")
    at_0029, comments_0029 = m0002._schema(database), _dictionary(database)

    migrate(url, "head")
    assert _current(url) == _head()
    at_head, comments_head = m0002._schema(database), _dictionary(database)
    assert m0002._names(at_head, "relations") >= NEW_TABLES
    assert "ensemble_runs_one_active" in m0002._names(at_head, "indexes", 1)
    customer, user, document = _setup(database)
    with database.customer(customer) as conn:
        run: object = conn.execute(_RUN, _run(customer, user, document)).scalar_one()
        reading: object = conn.execute(_READING, _reading(customer, document, run)).scalar_one()
        conn.execute(_CHANGE, _change(customer, user, document, action="keep", reading=reading))

    m0002._downgrade(url, "0029")
    assert m0002._schema(database) == at_0029
    assert _dictionary(database) == comments_0029
    migrate(url, "head")
    assert m0002._schema(database) == at_head
    assert _dictionary(database) == comments_head


BAD_CHANGES = [
    ({"key": "not-a-key"}, "word_changes_word_key_check"),
    ({"kind": "page"}, "word_changes_kind_check"),
    ({"action": "guess"}, "word_changes_action_check"),
    ({"after": ""}, "word_changes_after_text_check"),
    ({"action": "confirm"}, "word_changes_same_text"),  # confirm keeps the text
    ({"action": "keep"}, "word_changes_reading"),  # keep needs its reading
    ({"undone_at": "2026-10-16T10:00:00+00:00"}, "word_changes_undone_pair"),
    ({"u": None}, "word_changes_person"),  # only Ensemble's resolve has no person
]


@pytest.mark.parametrize(("bad", "constraint"), BAD_CHANGES)
def test_every_word_change_check_refuses_a_bad_row(
    db: Database, bad: dict[str, Any], constraint: str
) -> None:
    customer, user, document = _setup(db)
    with db.customer(customer) as conn:
        conn.execute(_CHANGE, _change(customer, user, document))  # a good one first
    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        conn.execute(_CHANGE, _change(customer, user, document, **bad))
    assert violation(refused.value) == ("23514", constraint)


BAD_RUNS = [
    ({"scope": "page"}, "ensemble_runs_scope_check"),
    ({"pages": []}, "ensemble_runs_pages_check"),
    ({"models": []}, "ensemble_runs_models_check"),
    ({"done": [3]}, "ensemble_runs_pages_done"),
    ({"status": "done"}, "ensemble_runs_finished"),  # done needs its finish time
]


@pytest.mark.parametrize(("bad", "constraint"), BAD_RUNS)
def test_every_ensemble_run_check_refuses_a_bad_row(
    db: Database, bad: dict[str, Any], constraint: str
) -> None:
    customer, user, document = _setup(db)
    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        conn.execute(_RUN, _run(customer, user, document, **bad))
    assert violation(refused.value) == ("23514", constraint)


def test_one_queued_or_running_run_per_document(db: Database) -> None:
    customer, user, document = _setup(db)
    with db.customer(customer) as conn:
        conn.execute(
            _RUN, _run(customer, user, document, status="done", finished_at="2026-10-16T10:00:00+00:00")
        )
        conn.execute(_RUN, _run(customer, user, document))
    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        conn.execute(_RUN, _run(customer, user, document, status="running"))
    assert violation(refused.value) == ("23505", "ensemble_runs_one_active")


BAD_READINGS = [
    ({"outcome": "maybe"}, "ensemble_readings_outcome_check"),
    ({"proposal": None}, "ensemble_readings_proposal"),  # a change names its text
    ({"outcome": "agreed"}, "ensemble_readings_proposal"),  # only a change has one
    ({"decision": "kept"}, "ensemble_readings_decided_pair"),  # a decision says who and when
    ({"decided_at": "2026-10-16T10:00:00+00:00"}, "ensemble_readings_decided_both"),
]


@pytest.mark.parametrize(("bad", "constraint"), BAD_READINGS)
def test_every_ensemble_reading_check_refuses_a_bad_row(
    db: Database, bad: dict[str, Any], constraint: str
) -> None:
    customer, user, document = _setup(db)
    with db.customer(customer) as conn:
        run: object = conn.execute(_RUN, _run(customer, user, document)).scalar_one()
    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        conn.execute(_READING, _reading(customer, document, run, **bad))
    assert violation(refused.value) == ("23514", constraint)


def test_a_decision_on_an_unresolved_reading_is_refused_and_a_kept_change_is_accepted(db: Database) -> None:
    customer, user, document = _setup(db)
    when = "2026-10-16T10:00:00+00:00"
    with db.customer(customer) as conn:
        run: object = conn.execute(_RUN, _run(customer, user, document)).scalar_one()
        conn.execute(
            _READING, _reading(customer, document, run, decision="kept", decided_by=user, decided_at=when)
        )
    unresolved = {"key": "b" * 64, "outcome": "unresolved", "proposal": None}
    with pytest.raises(IntegrityError) as refused, db.customer(customer) as conn:
        conn.execute(
            _READING,
            _reading(
                customer, document, run, **unresolved, decision="kept", decided_by=user, decided_at=when
            ),
        )
    assert violation(refused.value) == ("23514", "ensemble_readings_decision_on_change")


def test_a_document_deleted_takes_its_word_review_rows_with_it(db: Database) -> None:
    customer, user, document = _setup(db)
    with db.customer(customer) as conn:
        run: object = conn.execute(_RUN, _run(customer, user, document)).scalar_one()
        reading: object = conn.execute(_READING, _reading(customer, document, run)).scalar_one()
        conn.execute(_CHANGE, _change(customer, user, document, action="keep", reading=reading))
        conn.execute(_CHANGE, _change(customer, user, document))
        conn.execute(text("DELETE FROM documents WHERE id = :d"), {"d": document})
        left: object = conn.execute(
            text(
                "SELECT (SELECT count(*) FROM word_changes) + (SELECT count(*) FROM ensemble_runs)"
                " + (SELECT count(*) FROM ensemble_readings)"
            )
        ).scalar_one()
    assert left == 0
