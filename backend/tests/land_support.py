"""Land values and their ``value_keys`` rows exactly as #245 and migration 0049 define them (PRD 6
E's tests)."""

import json
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.extraction.links import normalise_identifier
from tests.db_support import insert_document

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
