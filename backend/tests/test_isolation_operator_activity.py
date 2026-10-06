"""The operator's activity feed and health against PostgreSQL (super admin PR 5, 0020):
two organisations' audit rows and platform rows in one feed, newest first and paged by
the cursor (a tie of time across the two logs included), the filters, and what never
leaves the database: ``details``, ``target_id``, people and document names. Health:
counts only. Plus the migration and its index."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, text

from kaagaz.db import operator_activity as activity_sql
from kaagaz.db.engine import Database, OperatorDatabase
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate
from tests.test_api_support import Api, TestClient
from tests.test_operator_activity import ACTIVITY, ENTRY_KEYS, HEALTH
from tests.test_operator_auth_db import _DISABLED, _Operator, _owner
from tests.test_operator_orgs_db import _signed_in

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
FUNCTIONS = {"kaagaz_op_activity", "kaagaz_op_health", "kaagaz_op_purges"}
_INDEX = text(
    "SELECT pg_get_indexdef(x.indexrelid), x.indisvalid, obj_description(x.indexrelid, 'pg_class')"
    " FROM pg_index x WHERE x.indexrelid = to_regclass('public.audit_log_feed')"
)
_AUDIT = (
    "INSERT INTO audit_log (customer_id, actor_user_id, action, target_type, target_id, details, created_at)"
    " VALUES (:c, :u, :a, :t, :i, CAST(:d AS jsonb), :at)"
)
_PLATFORM = (
    "INSERT INTO platform_audit (operator_id, action, subject_customer_id, target_type, target_id,"
    " created_at) VALUES (:o, :a, :c, :t, :i, :at)"
)
BASE = datetime(2001, 1, 1, tzinfo=UTC)
SECRETS = {"name": "secret-bill.pdf", "email": "member-secret@example.test"}


def _index(database: Database) -> list[tuple[Any, ...]]:
    with database.system() as conn:
        return [tuple(row) for row in conn.execute(_INDEX)]


def test_0020_up_down_up_and_a_leftover_index_is_built_again(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0019")
    at_0019 = m0002._schema(database)
    assert _index(database) == []
    migrate(url, "head")
    at_head = m0002._schema(database)
    assert m0002._names(at_head, "functions") >= FUNCTIONS
    [(definition, valid, comment)] = _index(database)
    assert (
        definition == "CREATE INDEX audit_log_feed ON public.audit_log USING btree (created_at DESC, id DESC)"
    )
    assert valid and comment
    m0002._downgrade(url, "0019")
    assert m0002._schema(database) == at_0019
    with database.engine.begin() as conn:  # as a failed run would leave it
        conn.execute(text("CREATE INDEX audit_log_feed ON audit_log (id)"))
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def _letters() -> str:
    return "".join(chr(ord("a") + int(digit, 16)) for digit in uuid.uuid4().hex[:12])


def _feed(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    """Every page of the feed, following ``next``; each page checked for its keys."""
    items: list[dict[str, Any]] = []
    while True:
        reply = client.get(ACTIVITY, params=params)
        assert reply.status_code == 200, reply.text
        for hidden in (*SECRETS.values(), "secret-target", "details", "target_id"):
            assert hidden not in reply.text
        body = reply.json()
        assert all(set(item) == ENTRY_KEYS for item in body["items"])
        items += body["items"]
        if body["next"] is None:
            return items
        params = {**params, "before": body["next"]}


def _short(items: list[dict[str, Any]], prefix: str) -> list[tuple[Any, ...]]:
    return [
        (
            item["source"],
            datetime.fromisoformat(item["at"]) - BASE,
            item["action"].removeprefix(prefix),
            item["organisation_id"],
            item["organisation_name"],
            item["target_type"],
            item["by"],
        )
        for item in items
    ]


def test_the_feed_merges_both_logs_newest_first_by_pages_and_filters_and_names_nobody(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    (a, a_admin), (b, _) = api.customer("Org A feed"), api.customer("Org B feed")
    gone, prefix = str(uuid.uuid4()), f"feed.{_letters()}."
    for customer, actor, action, target, minutes in (
        (a, a_admin, "upload", "document", 3),
        (b, None, "disable", "customer", 2),
        (a, a_admin, "rename", "document", 1),
    ):
        row = {
            "c": customer,
            "u": actor,
            "a": prefix + action,
            "t": target,
            "i": "secret-target-1",
            # The organisation's copy of an operator's change is marked by: operator (0016).
            "d": json.dumps(SECRETS if actor else {**SECRETS, "by": "operator"}),
        }
        _owner(db, _AUDIT, {**row, "at": BASE + timedelta(minutes=minutes)})
    for by, action, customer, minutes in ((operator.id, "disable", b, 2), (None, "purged", gone, 0)):
        row = {"o": by, "a": prefix + action, "c": customer, "t": "customer", "i": "secret-target-2"}
        _owner(db, _PLATFORM, {**row, "at": BASE + timedelta(minutes=minutes)})

    every = [
        ("organisation", timedelta(minutes=3), "upload", a, "Org A feed", "document", "user"),
        ("platform", timedelta(minutes=2), "disable", b, "Org B feed", "customer", "operator"),
        (
            "organisation",
            timedelta(minutes=2),
            "disable",
            b,
            "Org B feed",
            "customer",
            "operator",
        ),  # same time
        ("organisation", timedelta(minutes=1), "rename", a, "Org A feed", "document", "user"),
        ("platform", timedelta(0), "purged", gone, None, "customer", "system"),  # its organisation is gone
    ]
    assert _short(_feed(client, action=prefix), prefix) == every
    assert _short(_feed(client, action=prefix, limit=2), prefix) == every  # three pages, the tie split
    assert _short(_feed(client, action=prefix, limit=1), prefix) == every
    assert _short(_feed(client, action=prefix, org=a), prefix) == [every[0], every[3]]
    assert _short(_feed(client, action=prefix, org=b), prefix) == every[1:3]
    assert _short(_feed(client, action=prefix + "dis"), prefix) == every[1:3]
    reply = client.get(ACTIVITY, params={"action": prefix})
    for hidden in (a_admin, operator.id, operator.email):
        assert hidden not in reply.text


def test_a_customer_admin_cookie_opens_neither_route_and_only_an_enabled_operator_gets_rows(
    db: Database,
) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    admin = api.new_admin()
    for path in (ACTIVITY, HEALTH):
        reply = admin.client.get(path)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), path
        assert client.get(path).status_code == 200, path
    disabled, unconfirmed = _Operator(api), _Operator(api, confirmed=False)
    disabled.sql(_DISABLED, d=True)
    with OperatorDatabase(db.engine).operator() as conn:
        assert _everything(conn, operator.id) != ([], [], None)
        for refused in (str(uuid.uuid4()), disabled.id, unconfirmed.id):
            assert _everything(conn, refused) == ([], [], None), refused


def _everything(conn: Connection, operator_id: str) -> tuple[Any, ...]:
    feed = activity_sql.activity(
        conn, operator_id, before=None, customer_id=None, action_prefix=None, limit=5
    )
    return list(feed), list(activity_sql.health(conn, operator_id)), activity_sql.purges(conn, operator_id)


def test_a_page_is_never_more_than_100_rows_and_no_limit_is_50(db: Database) -> None:
    api = Api(db)
    operator, (a, _) = _Operator(api), api.customer("Org many rows")
    prefix = f"many.{_letters()}."
    _owner(
        db,
        "INSERT INTO audit_log (customer_id, action, created_at)"
        " SELECT :c, :a, :at + n * interval '1 second' FROM generate_series(1, 120) AS n",
        {"c": a, "a": prefix + "upload", "at": BASE},
    )
    with OperatorDatabase(db.engine).operator() as conn:
        rows = activity_sql.activity(
            conn, operator.id, before=None, customer_id=a, action_prefix=prefix, limit=1000
        )
        unset = conn.execute(
            text("SELECT count(*) FROM kaagaz_op_activity(:o, NULL, NULL, NULL, :c, :a, NULL)"),
            {"o": operator.id, "c": a, "a": prefix},
        ).scalar_one()
    assert (len(rows), unset) == (100, 50)


def test_health_counts_every_version_out_of_trash_and_names_no_document(db: Database) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    (a, _), (b, _) = api.customer("Org A health"), api.customer("Org B health")
    member, _ = api.add_user(a, "member")
    oldest = api.clock() - timedelta(hours=2)
    documents: list[tuple[str, dict[str, Any]]] = [
        ("ready", {}),
        ("ready", {}),
        ("failed", {"failure_code": "corrupt_file"}),
        ("failed", {"failure_code": "ocr_timeout"}),
        ("needs_reader", {}),
        ("received", {"created_at": oldest}),
        ("clean", {}),
        (
            "failed",
            {"failure_code": "corrupt_file", "deleted_at": api.clock(), "deleted_by": member},
        ),  # Trash
    ]
    for number, (status, columns) in enumerate(documents):
        api.add_document(a, status=status, name=f"secret-plan-{number}.pdf", **columns)
    api.add_document(b, status="rejected", failure_code="virus_found", name="secret-other.pdf")
    purged = str(uuid.uuid4())  # purged and swept already, so no sweep in another test picks it up
    for action in ("org.purged", "org.swept"):
        _owner(db, _PLATFORM, {"o": None, "a": action, "c": purged, "t": None, "i": None, "at": BASE})

    reply = client.get(HEALTH)
    assert reply.status_code == 200, reply.text
    assert "secret-" not in reply.text
    body = reply.json()
    by_id = {org["id"]: org for org in body["organisations"]}
    assert by_id[a]["documents_by_status"] == {
        "ready": 2,
        "failed": 2,
        "needs_reader": 1,
        "received": 1,
        "clean": 1,
    }
    assert by_id[a]["failed_by_code"] == {"corrupt_file": 1, "ocr_timeout": 1, "unknown": 1}
    assert (by_id[a]["in_progress"], by_id[a]["oldest_in_progress_seconds"]) == (2, 2 * 3600)
    assert datetime.fromisoformat(by_id[a]["oldest_in_progress_at"]) == oldest
    assert (by_id[b]["failed_by_code"], by_id[b]["in_progress"]) == ({"virus_found": 1}, 0)
    assert body["totals"]["in_progress"] >= 2
    purges = body["purges"]
    assert purges["purged"] >= 1 and purges["swept"] >= 1 and purges["last_swept_at"] is not None
