"""Migration 0099 (V17, #648, 16 Nov 2026; 0098 on its branch): the version finder's runs, pairs and
group members, and ``kaagaz_clear_derived`` deleting a locked version's. Up, down (the schema as at
0098, the function body too) and up again on a fresh database; the CHECKs. Grants and row level
security: test_isolation_schema; the lock's clearing: test_isolation_locked_files; the organisation
delete: test_isolation_customer_purge."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz import versions
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer, violation
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0099 = _load("0099_version_finder.py")
_TABLES = (
    "SELECT count(*) FROM pg_class"
    " WHERE relname IN ('version_finder_runs', 'version_pairs', 'version_group_members')"
)
_CLEARS = "SELECT prosrc LIKE '%version_group_members%' FROM pg_proc WHERE proname = 'kaagaz_clear_derived'"
_PAIR = (
    "INSERT INTO version_pairs (customer_id, first_id, second_id, text_overlap, name_similarity)"
    " VALUES (:c, :a, :b, :t, :n)"
)
_MEMBER = (
    "INSERT INTO version_group_members (customer_id, document_id, group_id, position, state, decided_by,"
    " decided_at) VALUES (:c, :d, :g, :p, :s, :by,"
    " CASE WHEN CAST(:by AS uuid) IS NULL THEN NULL ELSE now() END)"
)


def test_the_revision_follows_0098() -> None:  # Changed on purpose: renumbered at merge
    assert (M0099.revision, M0099.down_revision) == ("0099", "0098")
    assert M0099.MAX_GROUP == versions.MAX_GROUP


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0098")
    at_0098 = _state(db)
    migrate(url, "0099")
    at_0099 = _state(db)
    with db.system() as conn:
        assert conn.execute(text(_TABLES)).scalar_one() == 3
        assert conn.execute(text(_CLEARS)).scalar_one() is True

    base._downgrade(url, "0098")
    assert _state(db) == at_0098  # 0093's kaagaz_clear_derived back, body and all
    with db.system() as conn:
        assert conn.execute(text(_TABLES)).scalar_one() == 0
        assert conn.execute(text(_CLEARS)).scalar_one() is False

    migrate(url, "0099")
    assert _state(db) == at_0099


def _two(db: Database) -> tuple[str, str, str, str]:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        a, b = sorted(insert_document(conn, customer, uploaded_by=user) for _ in range(2))
    return customer, user, a, b


@pytest.mark.parametrize(
    ("swap", "overlap", "name", "constraint"),
    [
        (True, 0.9, None, "version_pairs_order"),  # the lower id first, so a pair is stored once
        (False, 1.5, None, "version_pairs_text_overlap"),
        (False, None, -0.1, "version_pairs_name_similarity"),
    ],
)
def test_a_pair_is_stored_once_with_scores_from_0_to_1(
    db: Database, swap: bool, overlap: float | None, name: float | None, constraint: str
) -> None:
    customer, _, a, b = _two(db)
    first, second = (b, a) if swap else (a, b)
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text(_PAIR), {"c": customer, "a": first, "b": second, "t": overlap, "n": name})
    assert violation(refused.value) == ("23514", constraint)


@pytest.mark.parametrize(
    ("position", "state", "decided", "constraint"),
    [
        (0, "open", False, "version_group_members_position"),
        (21, "open", False, "version_group_members_position"),
        (1, "linked_maybe", False, "version_group_members_state"),
        (1, "open", None, "version_group_members_decided"),  # who decided, never without when
    ],
)
def test_a_member_has_a_place_up_to_20_and_a_known_decision(
    db: Database, position: int, state: str, decided: bool | None, constraint: str
) -> None:
    customer, user, a, _ = _two(db)
    params = {"c": customer, "d": a, "g": str(uuid.uuid4()), "p": position, "s": state, "by": None}
    statement = _MEMBER
    if decided is None:  # decided_by without decided_at
        statement = _MEMBER.replace("CASE WHEN CAST(:by AS uuid) IS NULL THEN NULL ELSE now() END", "NULL")
        params["by"] = user
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text(statement), params)
    assert violation(refused.value) == ("23514", constraint)


def test_a_file_is_in_one_group_and_its_rows_go_with_it(db: Database) -> None:
    customer, user, a, b = _two(db)
    group = str(uuid.uuid4())
    with db.customer(customer) as conn:
        conn.execute(text(_PAIR), {"c": customer, "a": a, "b": b, "t": 0.9, "n": None})
        for position, document in enumerate((a, b), 1):
            conn.execute(
                text(_MEMBER),
                {"c": customer, "d": document, "g": group, "p": position, "s": "open", "by": user},
            )
    again = {"c": customer, "d": a, "g": str(uuid.uuid4()), "p": 1, "s": "open", "by": None}
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text(_MEMBER), again)
    assert violation(refused.value)[0] == "23505"
    with db.customer(customer) as conn:
        conn.execute(
            text("DELETE FROM documents WHERE customer_id = :c AND id = :d"), {"c": customer, "d": b}
        )
        left = conn.execute(
            text(
                "SELECT (SELECT count(*) FROM version_pairs WHERE customer_id = :c),"
                " (SELECT count(*) FROM version_group_members WHERE customer_id = :c)"
            ),
            {"c": customer},
        ).one()
    assert tuple(left) == (0, 1)
