"""Land values and their ``value_keys`` rows exactly as #245 defines them (PRD 6 E's tests). Until
package D1's migration is on this branch's base, ``ensure_value_keys`` creates the same table,
keys, CHECKs, indexes, policy and grants in the test database (else it does nothing)."""

import json
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.extraction.links import normalise_identifier
from tests.db_support import APP_ROLE, insert_document

_VALUE_KEYS = (
    """
    CREATE TABLE value_keys (
        customer_id uuid NOT NULL,
        value_id uuid NOT NULL,
        document_id uuid NOT NULL,
        kind text NOT NULL CHECK (kind IN ('person', 'place', 'property')),
        place_level text NULL CHECK (place_level IN ('village', 'taluka', 'district', 'city')),
        latin text NOT NULL CHECK (length(latin) BETWEEN 1 AND 200),
        sound text NOT NULL CHECK (length(sound) <= 400),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, value_id),
        CHECK ((kind = 'place') = (place_level IS NOT NULL)),
        FOREIGN KEY (customer_id, value_id) REFERENCES extracted_values (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, document_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX value_keys_latin ON value_keys (customer_id, kind, latin)",
    "CREATE INDEX value_keys_sound ON value_keys (customer_id, kind, sound)",
    "CREATE INDEX value_keys_document ON value_keys (customer_id, document_id)",
    "ALTER TABLE value_keys ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY customer_only ON value_keys USING (customer_id = kaagaz_current_customer())"
    " WITH CHECK (customer_id = kaagaz_current_customer())",
    f"GRANT SELECT, INSERT ON value_keys TO {APP_ROLE}",
    f"GRANT UPDATE (latin, sound) ON value_keys TO {APP_ROLE}",
)
_VALUE = text(
    "INSERT INTO extracted_values (id, customer_id, document_id, record_key, field, value_text,"
    " value_raw, page_no, region, reader, label, label_reason)"
    " VALUES (:id, :c, :d, :key, :field, :text, :text, 1, CAST(:region AS jsonb), 'test', :label,"
    " CASE WHEN :label = 'verified' THEN 'readers_agree' ELSE 'single_reader' END)"
)
_KEY = text(
    "INSERT INTO value_keys (customer_id, value_id, document_id, kind, place_level, latin, sound)"
    " VALUES (:c, :id, :d, :kind, :level, :latin, '')"
)


def ensure_value_keys(db: Database) -> None:
    with db.engine.begin() as conn:  # the owner, as a migration: schema only, no customer's rows
        if conn.execute(text("SELECT to_regclass('public.value_keys')")).scalar() is None:
            for statement in _VALUE_KEYS:
                conn.execute(text(statement))


def add_land(
    db: Database,
    customer: str,
    title: str,
    owners: Sequence[tuple[str, str | None]] = (),
    *,
    buyers: Sequence[str] = (),
    sellers: Sequence[str] = (),
    places: Sequence[tuple[str, str]] = (),
    survey: str | None = None,
    unowned: str | None = None,
    label: str = "verified",
    **document: Any,
) -> str:
    """One land document with one property, ``land:1``: its owners (name, share as printed),
    buyers and sellers as parties, its places ((level, name)), survey number and unowned
    share, each value with its own box on page 1, and the keys #245 writes for them."""
    values: list[dict[str, Any]] = []
    keys: list[dict[str, Any]] = []

    def value(key: str, field: str, said: str, kind: str | None = None, level: str | None = None) -> None:
        top = 0.02 * len(values)
        box = json.dumps({"kind": "box", "page": 1, "box": [0.1, top, 0.5, top + 0.015]})
        values.append({"id": str(uuid.uuid4()), "key": key, "field": field, "text": said, "region": box})
        latin = normalise_identifier(said) if kind == "property" else " ".join(said.casefold().split())
        if kind is not None:
            keys.append({"id": values[-1]["id"], "kind": kind, "level": level, "latin": latin})

    parties = [("owner", name, share) for name, share in owners]
    parties += [("buyer", name, None) for name in buyers] + [("seller", name, None) for name in sellers]
    for o, (role, name, share) in enumerate(parties, start=1):
        value(f"land:1:party:{o}", f"land_{role}_name", name, "person")
        if share is not None:
            value(f"land:1:party:{o}", "land_share", share)
    for level, name in places:
        value("land:1", f"land_{level}", name, "place", level)
    if survey is not None:
        value("land:1", "land_survey_number", survey, "property")
    if unowned is not None:
        value("land:1", "land_unowned_share", unowned)
    with db.customer(customer) as conn:
        document_id = insert_document(conn, customer, display_name=title, **document)
        conn.execute(_VALUE, [{**row, "c": customer, "d": document_id, "label": label} for row in values])
        if keys:
            conn.execute(_KEY, [{**key, "c": customer, "d": document_id} for key in keys])
    return document_id
