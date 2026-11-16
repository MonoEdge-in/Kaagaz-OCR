"""Kaagaz on WhatsApp's rows stay in their organisation (#603, migration 0097): another organisation
sees, writes and ends none of them; a message is seen and written only in its own person's
transaction (not by a colleague, an Admin included, and not with no person). The webhook's side:
tests/test_isolation_whatsapp_webhook.py."""

from typing import Any

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests.test_api_support import Api
from tests.test_migration_0097_db import connect, new_number

pytestmark = pytest.mark.db
TABLES = (
    "whatsapp_org_settings",
    "whatsapp_links",
    "whatsapp_link_codes",
    "whatsapp_messages",
    "whatsapp_pickers",
)
_MESSAGE = (
    "INSERT INTO whatsapp_messages (customer_id, link_id, user_id, wamid, kind, sent_at)"
    " VALUES (:c, :l, :u, :w, 'text', now())"
)


def _counts(conn: Connection) -> dict[str, int]:
    return {table: int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()) for table in TABLES}  # noqa: S608 - fixed names


def _filled(api: Api) -> tuple[str, str, str]:
    """An organisation with a row in each WhatsApp table, written in its person's transaction:
    (customer, person, connection)."""
    customer, _ = api.customer()
    person, _ = api.add_user(customer, "member")
    link = connect(api.db, customer, person, new_number())
    params: dict[str, Any] = {"c": customer, "u": person, "l": link, "w": f"wamid.{link}"}
    with api.db.user(customer, person) as conn:
        for statement in (
            _MESSAGE,
            "INSERT INTO whatsapp_link_codes (customer_id, user_id, phone_hash, phone_sealed, code_hash,"
            " expires_at) VALUES (:c, :u, sha256('n'), 'v1:x', sha256('c'), now() + interval '10 minutes')",
            "INSERT INTO whatsapp_pickers (customer_id, user_id, expires_at)"
            " VALUES (:c, :u, now() + interval '15 minutes')",
        ):
            conn.execute(text(statement), params)
    return customer, person, link


def test_another_organisation_sees_writes_and_ends_none_of_the_rows(db: Database) -> None:
    api = Api(db)
    a, a_person, a_link = _filled(api)
    b, b_admin = api.customer()
    with db.user(a, a_person) as conn:
        assert set(_counts(conn).values()) == {1}
    with db.user(b, b_admin) as conn:
        assert set(_counts(conn).values()) == {0}
        end = text("SELECT kaagaz_whatsapp_end_link(:l, 'admin')")
        assert conn.execute(end, {"l": a_link}).scalar_one() is False
        for statement in (
            "UPDATE whatsapp_links SET last_used_at = now() WHERE id = :l",
            "UPDATE whatsapp_messages SET state = 'done' WHERE link_id = :l",
            "DELETE FROM whatsapp_pickers WHERE user_id = :u",
        ):
            assert conn.execute(text(statement), {"l": a_link, "u": a_person}).rowcount == 0
        for statement in (  # a row written into A from B
            "INSERT INTO whatsapp_org_settings (customer_id, enabled) VALUES (:c, true)",
            "INSERT INTO whatsapp_pickers (customer_id, user_id, expires_at) VALUES (:c, :u, now())",
        ):
            with pytest.raises(DBAPIError, match="row-level security"), conn.begin_nested():
                conn.execute(text(statement), {"c": a, "u": a_person})
    with db.user(a, a_person) as conn:
        live = conn.execute(text("SELECT ended_at IS NULL FROM whatsapp_links WHERE id = :l"), {"l": a_link})
        assert live.scalar_one() is True


def test_a_message_is_seen_and_written_only_in_its_own_persons_transaction(db: Database) -> None:
    api = Api(db)
    customer, person, link = _filled(api)
    colleague, _ = api.add_user(customer, "admin")
    seen = text("SELECT count(*) FROM whatsapp_messages")
    with db.user(customer, person) as conn:
        assert conn.execute(seen).scalar_one() == 1
    with db.user(customer, colleague) as conn:  # an Admin of the same organisation
        assert conn.execute(seen).scalar_one() == 0
        with pytest.raises(DBAPIError, match="row-level security"), conn.begin_nested():
            conn.execute(text(_MESSAGE), {"c": customer, "u": person, "l": link, "w": "wamid.colleague"})
    with db.customer(customer) as conn:  # no person named
        assert conn.execute(seen).scalar_one() == 0
    with (
        db.user(customer, person) as conn,
        pytest.raises(DBAPIError, match="row-level security"),
        conn.begin_nested(),
    ):
        conn.execute(text(_MESSAGE), {"c": customer, "u": colleague, "l": link, "w": "wamid.handed"})
