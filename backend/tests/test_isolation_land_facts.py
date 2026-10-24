"""Land facts across customers (0049, #245): company B can never read, write or link company
A's land values or their keys, even with A's ids, and even when both companies have an
owner of the same name. Row level security and the (customer_id, id) foreign keys each
refuse; the control, B's own rows, always works."""

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from kaagaz.db.engine import Database
from tests.db_support import insert_document, new_customer, violation

pytestmark = pytest.mark.db

NAME = "Krishna Sohoni"  # the same owner in both companies
LATIN, SOUND = "krishna sohoni", "krisn sohoni"
_REGION = '{"page": 1, "x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.2}'
_VALUE = text(
    "INSERT INTO extracted_values (customer_id, document_id, record_key, field, value_text, value_raw,"
    " page_no, region, reader, label, label_reason) VALUES (:c, :d, 'land:1:party:1', 'land_owner_name',"
    " :t, :t, 1, CAST(:r AS jsonb), 'gemini', 'needs_review', 'model_chosen') RETURNING id"
)
_KEY = text(
    "INSERT INTO value_keys (customer_id, value_id, document_id, kind, latin, sound)"
    " VALUES (:c, :v, :d, 'person', :latin, :sound)"
)


def _company(db: Database) -> dict[str, str]:
    """A customer with one land record whose owner is NAME, keyed."""
    customer_id, _user = new_customer(db)
    with db.customer(customer_id) as conn:
        document_id = insert_document(conn, customer_id, doc_type="land_record")
        value_id = str(
            conn.execute(_VALUE, {"c": customer_id, "d": document_id, "t": NAME, "r": _REGION}).scalar()
        )
        conn.execute(
            _KEY, {"c": customer_id, "v": value_id, "d": document_id, "latin": LATIN, "sound": SOUND}
        )
    return {"customer": customer_id, "document": document_id, "value": value_id}


def _keys_by_name(conn: Connection) -> list[tuple[str, str]]:
    rows = conn.execute(
        text("SELECT customer_id::text, value_id::text FROM value_keys WHERE kind = 'person' AND latin = :l"),
        {"l": LATIN},
    )
    return sorted((str(c), str(v)) for c, v in rows)


def test_the_same_owner_name_in_two_companies_finds_only_ones_own(db: Database) -> None:
    a, b = _company(db), _company(db)
    for me in (a, b):
        with db.customer(me["customer"]) as conn:
            assert _keys_by_name(conn) == [(me["customer"], me["value"])]
            by_sound = conn.execute(
                text("SELECT value_id::text FROM value_keys WHERE sound = :s"), {"s": SOUND}
            )
            assert [row[0] for row in by_sound] == [me["value"]]
            owners = conn.execute(
                text(
                    "SELECT id::text FROM extracted_values"
                    " WHERE field = 'land_owner_name' AND value_text = :t"
                ),
                {"t": NAME},
            )
            assert [row[0] for row in owners] == [me["value"]]


def test_b_cannot_read_as_ids_or_name_even_naming_a_as_customer(db: Database) -> None:
    a, b = _company(db), _company(db)
    with db.customer(b["customer"]) as conn:
        for statement in (
            "SELECT count(*) FROM value_keys WHERE customer_id = :ac OR value_id = :av OR document_id = :ad",
            "SELECT count(*) FROM extracted_values WHERE customer_id = :ac OR id = :av OR document_id = :ad",
        ):
            params = {"ac": a["customer"], "av": a["value"], "ad": a["document"]}
            assert conn.execute(text(statement), params).scalar_one() == 0


def test_b_cannot_key_or_link_as_value_or_document(db: Database) -> None:
    a, b = _company(db), _company(db)
    with db.customer(b["customer"]) as conn:  # a second owner of B's, with no key yet
        params = {"c": b["customer"], "d": b["document"], "t": NAME, "r": _REGION}
        unkeyed = str(conn.execute(_VALUE, params).scalar())
    tries = [
        # A's value under A's customer: row level security's WITH CHECK refuses the row.
        ({"c": a["customer"], "v": a["value"], "d": a["document"]}, ("42501", None)),
        # A's value, or A's document, under B's customer: the (customer_id, id) keys refuse it.
        (
            {"c": b["customer"], "v": a["value"], "d": b["document"]},
            ("23503", "value_keys_customer_id_value_id_fkey"),
        ),
        (
            {"c": b["customer"], "v": unkeyed, "d": a["document"]},
            ("23503", "value_keys_customer_id_document_id_fkey"),
        ),
    ]
    for params, expected in tries:
        with pytest.raises(DBAPIError) as refused, db.customer(b["customer"]) as conn:
            conn.execute(_KEY, {**params, "latin": "x", "sound": ""})
        assert violation(refused.value) == expected, params
    # B's own key again is refused only as a duplicate: the control.
    with pytest.raises(DBAPIError) as again, db.customer(b["customer"]) as conn:
        conn.execute(
            _KEY, {"c": b["customer"], "v": b["value"], "d": b["document"], "latin": "x", "sound": ""}
        )
    assert violation(again.value) == ("23505", "value_keys_pkey")


def test_b_cannot_change_or_delete_as_keys_or_values(db: Database) -> None:
    a, b = _company(db), _company(db)
    with db.customer(b["customer"]) as conn:
        changed = conn.execute(
            text("UPDATE value_keys SET latin = 'mallory', sound = 'mlr' WHERE value_id = :v"),
            {"v": a["value"]},
        )
        assert changed.rowcount == 0
        changed = conn.execute(
            text("UPDATE extracted_values SET value_text = 'Mallory' WHERE id = :v"), {"v": a["value"]}
        )
        assert changed.rowcount == 0
        mine = conn.execute(
            text("UPDATE value_keys SET latin = latin WHERE value_id = :v"), {"v": b["value"]}
        )
        assert mine.rowcount == 1  # the control
    with pytest.raises(ProgrammingError) as denied, db.customer(b["customer"]) as conn:
        conn.execute(text("DELETE FROM value_keys WHERE value_id = :v"), {"v": b["value"]})
    assert violation(denied.value)[0] == "42501"  # no DELETE grant at all: a key goes with its value
    with db.customer(a["customer"]) as conn:
        assert _keys_by_name(conn) == [(a["customer"], a["value"])]
        kept = conn.execute(text("SELECT value_text FROM extracted_values WHERE id = :v"), {"v": a["value"]})
        assert kept.scalar_one() == NAME


def test_deleting_bs_value_takes_only_bs_key(db: Database) -> None:
    a, b = _company(db), _company(db)
    with db.customer(b["customer"]) as conn:
        conn.execute(text("DELETE FROM extracted_values WHERE id = :v"), {"v": b["value"]})
        assert conn.execute(text("SELECT count(*) FROM value_keys")).scalar_one() == 0
    with db.customer(a["customer"]) as conn:
        assert _keys_by_name(conn) == [(a["customer"], a["value"])]
