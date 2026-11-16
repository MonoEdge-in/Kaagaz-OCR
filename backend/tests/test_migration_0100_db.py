"""Migration 0100 (V13 to V19, #648, V-A): the link snapshot (``version_links``), the ``separated``
status, the join trigger (``kaagaz_version_joined``) and the stories' move. Up, down (the schema as
at 0098, a pair made separate kept apart as ``rejected``) and up again on a fresh database; the
CHECKs; who may move stories. Grants and row level security: test_isolation_schema; the trigger and
the link: test_isolation_version_links; the organisation delete: test_isolation_customer_purge."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import APP_ROLE, insert_document, migrate, new_customer, violation
from tests.test_api_support import Api
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
fresh = base.fresh
M0100 = _load("0100_version_links.py")
_TABLE = "SELECT count(*) FROM pg_class WHERE relname = 'version_links'"
_SUGGEST = (
    "INSERT INTO version_suggestions (customer_id, document_id, candidate_family_id, outcome, status)"
    " VALUES (:c, :d, :f, 'suggested', :s)"
)
_LINK = (
    "INSERT INTO version_links (customer_id, family_id, document_ids, snapshot, linked_by, undone_by,"
    " undone_at)"
    " VALUES (:c, :f, CAST(:ids AS uuid[]), '{}', :u, :by, CASE WHEN :at THEN now() END)"
)


def test_the_revision_follows_0098_for_now() -> None:
    assert (M0100.revision, M0100.down_revision) == ("0100", "0098")


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0098")
    at_0098 = _state(db)
    migrate(url, "0100")
    at_0100 = _state(db)
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        a, b = (insert_document(conn, customer, uploaded_by=user) for _ in range(2))
        conn.execute(text(_SUGGEST), {"c": customer, "d": a, "f": b, "s": "separated"})
    with db.system() as conn:
        assert conn.execute(text(_TABLE)).scalar_one() == 1

    base._downgrade(url, "0098")
    with db.system() as conn:
        assert conn.execute(text(_TABLE)).scalar_one() == 0
    with db.customer(customer) as conn:
        kept = conn.execute(text("SELECT status FROM version_suggestions WHERE document_id = :d"), {"d": a})
        assert kept.scalar_one() == "rejected"  # still kept apart
    assert _state(db) == at_0098

    migrate(url, "0100")
    assert _state(db) == at_0100


def _two(db: Database) -> tuple[str, str, str, str]:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        a, b = (insert_document(conn, customer, uploaded_by=user) for _ in range(2))
    return customer, user, a, b


@pytest.mark.parametrize(
    ("ids", "undone_by", "undone_at", "constraint"),
    [
        (1, False, False, "version_links_documents"),  # a link holds two versions at least
        (2, True, False, "version_links_undone"),  # who undid it, never without when
        (2, False, True, "version_links_undone"),
    ],
)
def test_a_link_holds_two_versions_and_a_whole_undo(
    db: Database, ids: int, undone_by: bool, undone_at: bool, constraint: str
) -> None:
    customer, user, a, b = _two(db)
    params = {
        "c": customer,
        "f": a,
        "ids": [a, b][:ids],
        "u": user,
        "by": user if undone_by else None,
        "at": undone_at,
    }
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text(_LINK), params)
    assert violation(refused.value) == ("23514", constraint)


def test_a_suggestion_is_made_separate_or_one_of_the_old_states(db: Database) -> None:
    customer, _, a, b = _two(db)
    with db.customer(customer) as conn:
        conn.execute(text(_SUGGEST), {"c": customer, "d": a, "f": b, "s": "separated"})
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text(_SUGGEST), {"c": customer, "d": b, "f": a, "s": "parted"})
    assert violation(refused.value) == ("23514", "version_suggestions_status_check")


def test_only_an_admin_or_management_moves_stories_and_only_into_a_family(db: Database) -> None:
    """The worker (no person), a member, and a version that isn't its family's first are refused;
    an Admin moves the stories of every person but one with a story there already."""
    customer, admin, a, b = _two(db)
    with db.customer(customer) as conn:
        v2 = insert_document(conn, customer, family_id=a, version_no=2, is_current=False)
    member = Api(db).add_user(customer, "member")[0]
    for person, family in ((admin, a), (member, a), (member, b)):
        _owner(
            db,
            "INSERT INTO document_stories (customer_id, user_id, family_id, story, story_latin)"
            " VALUES (:c, :u, :f, 'a story', '')",
            {"c": customer, "u": person, "f": family},
        )
    move = text("SELECT kaagaz_move_stories(:from, :to, NULL)")
    app = Database(db.engine, APP_ROLE)
    for session in (app.customer(customer), app.user(customer, member)):
        with pytest.raises(DBAPIError) as refused, session as conn:
            conn.execute(move, {"from": a, "to": b})
        assert violation(refused.value)[0] == "42501"
    with pytest.raises(DBAPIError) as refused, app.user(customer, admin) as conn:
        conn.execute(move, {"from": a, "to": v2})
    assert violation(refused.value)[0] == "23514"
    with app.user(customer, admin) as conn:
        moved = [str(each) for each in conn.execute(move, {"from": a, "to": b}).scalars()]
    assert moved == [admin]
