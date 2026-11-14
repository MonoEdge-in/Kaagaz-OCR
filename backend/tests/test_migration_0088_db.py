"""Migration 0088 (PROVISIONAL NUMBER; #603, PRD 7 section 15 W1): Kaagaz on WhatsApp's tables and
owner-rights functions. Up, down (the schema as at 0087) and up again; one live connection per
number and per person; and what each function reads, changes and refuses. Who may see which row:
tests/test_isolation_whatsapp.py; the rights and the functions' list: test_isolation_schema.py."""

import hashlib
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer
from tests.test_api_support import password_hash_of
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0088 = _load("0088_whatsapp_tables.py")


def _owner(db: Database, statement: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
    """As the table owner, whom row level security does not filter."""
    with db.engine.begin() as conn:
        result = conn.execute(text(statement), params or {})
        return [tuple(row) for row in result] if result.returns_rows else []


def new_number() -> str:
    """A number's stand-in: the migration only keeps what the channel gives it."""
    return uuid.uuid4().hex


def number_hash(number: str) -> bytes:
    """A 32-byte stand-in for the channel's keyed hash."""
    return hashlib.sha256(number.encode()).digest()


def connect(
    db: Database,
    customer_id: str,
    user_id: str,
    number: str,
    *,
    roles: tuple[str, ...] = ("member", "reviewer"),
) -> str:
    """A live connection of ``number`` to the person, and WhatsApp on for their organisation, in the
    person's transaction: the connection's id."""
    with db.user(customer_id, user_id) as conn:
        conn.execute(
            text(
                "INSERT INTO whatsapp_org_settings (customer_id, enabled, roles) VALUES (:c, true, :r)"
                " ON CONFLICT (customer_id) DO UPDATE SET enabled = true, roles = EXCLUDED.roles"
            ),
            {"c": customer_id, "r": list(roles)},
        )
        return str(
            conn.execute(
                text(
                    "INSERT INTO whatsapp_links (customer_id, user_id, phone_hash, phone_sealed,"
                    " session_version) SELECT :c, :u, :h, 'v1:sealed', u.session_version FROM users u"
                    " WHERE u.id = :u RETURNING id"
                ),
                {"c": customer_id, "u": user_id, "h": number_hash(number)},
            ).scalar_one()
        )


def _person(db: Database, customer_id: str, role: str = "member") -> str:
    with db.customer(customer_id) as conn:
        row = users_sql.create_user(
            conn,
            customer_id,
            email=f"{role}-{uuid.uuid4().hex}@example.test",
            display_name="Test",
            role=role,
            password_hash=password_hash_of("not used here at all"),
            must_change_password=False,
        )
    return str(row["id"])


def _link(db: Database, link_id: str) -> tuple[Any, ...]:
    return _owner(
        db,
        "SELECT ended_at IS NOT NULL, end_reason, phone_hash IS NULL AND phone_sealed IS NULL"
        " FROM whatsapp_links WHERE id = :l",
        {"l": link_id},
    )[0]


def _disconnections(db: Database, customer_id: str) -> list[tuple[Any, ...]]:
    return _owner(
        db,
        "SELECT actor_user_id::text, target_id, subject_user_id::text, details->>'reason',"
        " details->>'link_id' FROM audit_log WHERE customer_id = :c AND action = 'whatsapp.disconnected'"
        " ORDER BY id",
        {"c": customer_id},
    )


def test_the_revision_follows_0087_and_says_its_number_is_provisional() -> None:
    assert (M0088.revision, M0088.down_revision) == ("0088", "0087")
    assert (M0088.__doc__ or "").startswith("PROVISIONAL NUMBER: renumbered to follow PRD 7's last migration")


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0087")
    at_0087 = _state(db)
    migrate(url, "0088")
    at_0088 = _state(db)
    assert at_0088 != at_0087
    base._downgrade(url, "0087")
    assert _state(db) == at_0087
    migrate(url, "0088")
    assert _state(db) == at_0088


def test_one_live_connection_per_number_across_organisations_and_per_person(db: Database) -> None:
    a, a_admin = new_customer(db)
    b, b_admin = new_customer(db)
    number = new_number()
    connect(db, a, a_admin, number)
    with pytest.raises(IntegrityError):  # the same number in another organisation
        connect(db, b, b_admin, number)
    with pytest.raises(IntegrityError):  # a second number for the same person
        connect(db, a, a_admin, new_number())
    with pytest.raises(IntegrityError):  # an ended connection keeps no number
        _owner(
            db,
            "UPDATE whatsapp_links SET ended_at = now(), end_reason = 'person' WHERE customer_id = :c",
            {"c": a},
        )


def test_lookup_finds_only_a_live_connection_and_gives_ids_only(db: Database) -> None:
    customer, person = new_customer(db)
    number = new_number()
    link = connect(db, customer, person, number)
    lookup = "SELECT * FROM kaagaz_whatsapp_lookup(:h)"
    with db.system() as conn:
        found = conn.execute(text(lookup), {"h": number_hash(number)}).all()
        assert [tuple(map(str, row)) for row in found] == [(customer, person, link)]
        assert conn.execute(text(lookup), {"h": number_hash(new_number())}).all() == []
    with db.user(customer, person) as conn:
        assert conn.execute(text("SELECT kaagaz_whatsapp_end_link(:l, 'person')"), {"l": link}).scalar_one()
    with db.system() as conn:
        assert conn.execute(text(lookup), {"h": number_hash(number)}).all() == []


@pytest.mark.parametrize(("limit", "lease"), [(0, 60), (101, 60), (1, 0), (1, 901), (None, 60)])
def test_a_claim_out_of_range_is_refused(db: Database, limit: int | None, lease: int) -> None:
    with pytest.raises(DBAPIError, match="limit or lease out of range"), db.system() as conn:
        conn.execute(text("SELECT * FROM kaagaz_whatsapp_claim(:n, :s)"), {"n": limit, "s": lease})


def test_end_number_ends_its_live_connections_in_every_organisation_audited_in_each(db: Database) -> None:
    a, a_admin = new_customer(db)
    b, b_admin = new_customer(db)
    number, other = new_number(), new_number()
    ended = connect(db, a, a_admin, number)
    kept = connect(db, b, b_admin, other)
    with db.system() as conn:
        with pytest.raises(DBAPIError, match="unknown number or reason"), conn.begin_nested():
            conn.execute(text("SELECT kaagaz_whatsapp_end_number(:h, 'admin')"), {"h": number_hash(number)})
        count = conn.execute(
            text("SELECT kaagaz_whatsapp_end_number(:h, 'number_changed')"),
            {"h": number_hash(number)},
        ).scalar_one()
    assert count == 1
    assert _link(db, ended) == (True, "number_changed", True)
    assert _link(db, kept) == (False, None, False)
    assert _disconnections(db, a) == [(None, a_admin, a_admin, "number_changed", ended)]
    assert _disconnections(db, b) == []
    connect(db, b, _person(db, b), number)  # the number may be connected again


def test_end_link_ends_only_a_live_connection_of_the_current_organisation(db: Database) -> None:
    a, a_admin = new_customer(db)
    b, b_admin = new_customer(db)
    person = _person(db, a)
    link = connect(db, a, person, new_number())
    end = text("SELECT kaagaz_whatsapp_end_link(:l, :r)")
    with db.user(b, b_admin) as conn:  # another organisation's: nothing
        assert conn.execute(end, {"l": link, "r": "admin"}).scalar_one() is False
        with pytest.raises(DBAPIError, match="unknown connection or reason"), conn.begin_nested():
            conn.execute(end, {"l": link, "r": "number_changed"})  # the functions' own reason
    assert _link(db, link) == (False, None, False)
    with db.user(a, a_admin) as conn:  # an Admin disconnects them: the Admin is the actor
        assert conn.execute(end, {"l": link, "r": "admin"}).scalar_one() is True
        assert conn.execute(end, {"l": link, "r": "admin"}).scalar_one() is False  # ended already
    assert _link(db, link) == (True, "admin", True)
    again = connect(db, a, person, new_number())
    with db.user(a, person) as conn:  # a check failed: Kaagaz ended it, no actor
        assert conn.execute(end, {"l": again, "r": "session"}).scalar_one() is True
    assert _disconnections(db, a) == [
        (a_admin, person, person, "admin", link),
        (None, person, person, "session", again),
    ]


def test_the_sweep_ends_what_is_no_longer_allowed_and_deletes_what_is_old(db: Database) -> None:
    customer, admin = new_customer(db)
    people = {name: _person(db, customer) for name in ("fine", "silent", "disabled", "session", "role")}
    links = {name: connect(db, customer, person, new_number()) for name, person in people.items()}
    admin_link = connect(db, customer, admin, new_number(), roles=("member",))  # admins not ticked
    links["admin"] = admin_link
    p = {"c": customer}
    _owner(
        db,
        "UPDATE whatsapp_links SET linked_at = now() - interval '61 days' WHERE id = :l",
        {"l": links["silent"]},
    )
    _owner(db, "UPDATE users SET disabled = true WHERE id = :u", {"u": people["disabled"]})
    _owner(
        db, "UPDATE users SET session_version = session_version + 1 WHERE id = :u", {"u": people["session"]}
    )
    _owner(db, "UPDATE users SET role = 'admin' WHERE id = :u", {"u": people["role"]})
    # Old and recent rows: a message, an ended connection, a code and a picker each.
    old, recent = str(uuid.uuid4()), str(uuid.uuid4())
    for wamid, age in ((old, "31 days"), (recent, "29 days")):
        _owner(
            db,
            "INSERT INTO whatsapp_messages (customer_id, link_id, user_id, wamid, kind, sent_at, received_at)"
            " VALUES (:c, :l, :u, :w, 'text', now(), now() - CAST(:age AS interval))",
            {**p, "l": links["fine"], "u": people["fine"], "w": wamid, "age": age},
        )
    gone = connect(db, customer, _person(db, customer), new_number())
    _owner(
        db,
        "SELECT kaagaz_whatsapp_end_number(phone_hash, 'replaced') FROM whatsapp_links WHERE id = :l",
        {"l": gone},
    )
    _owner(db, "UPDATE whatsapp_links SET ended_at = now() - interval '31 days' WHERE id = :l", {"l": gone})
    for age in ("25 hours", "1 hour"):
        _owner(
            db,
            "INSERT INTO whatsapp_link_codes (customer_id, user_id, phone_hash, phone_sealed, code_hash,"
            " created_at, expires_at) VALUES (:c, :u, :h, 'v1:x', :h, now() - CAST(:age AS interval),"
            " now() + interval '1 day')",
            {**p, "u": admin, "h": number_hash(new_number()), "age": age},
        )
    picker = "INSERT INTO whatsapp_pickers (customer_id, user_id, expires_at) VALUES (:c, :u, now() + :left)"
    _owner(db, picker, {**p, "u": admin, "left": timedelta(seconds=-1)})
    _owner(db, picker, {**p, "u": people["fine"], "left": timedelta(minutes=15)})

    with db.system() as conn:
        changed = conn.execute(text("SELECT kaagaz_whatsapp_sweep()")).scalar_one()
    assert changed >= 5 + 1 + 1 + 1 + 1  # five ended; one message, connection, code and picker deleted
    ended = {name: _link(db, link)[1] for name, link in links.items() if name != "admin"}
    assert ended == {
        "fine": None,
        "silent": "silent",
        "disabled": "account",
        "session": "session",
        "role": "not_allowed",
    }
    assert _link(db, admin_link) == (True, "not_allowed", True)
    left = "SELECT {what} FROM {table} WHERE customer_id = :c"
    assert _owner(db, left.format(what="wamid", table="whatsapp_messages"), p) == [(recent,)]
    assert _owner(db, "SELECT count(*) FROM whatsapp_links WHERE id = :l", {"l": gone}) == [(0,)]
    assert _owner(db, left.format(what="count(*)", table="whatsapp_link_codes"), p) == [(1,)]
    assert _owner(db, left.format(what="user_id::text", table="whatsapp_pickers"), p) == [(people["fine"],)]
    assert sorted(row[3] for row in _disconnections(db, customer)) == sorted(
        ["silent", "account", "session", "not_allowed", "not_allowed", "replaced"]
    )
    with db.system() as conn:  # nothing left to do
        assert conn.execute(text("SELECT kaagaz_whatsapp_sweep()")).scalar_one() >= 0
    assert _link(db, links["fine"]) == (False, None, False)
