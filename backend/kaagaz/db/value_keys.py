"""The company vocabulary (``value_keys``, 0049, #245): each land person, place or property
value's Latin spelling and sound keys (``extraction.land.value_key``) from its text as stored.

``key_document`` runs in ``PgPipelineStore.save`` after the values and the word changes
written again (a read again's old keys went with their values, ON DELETE CASCADE);
``key_values`` wherever a value's text changes later (``review.resolve_value``, word review
and its undo). Each key is upserted from the value's own row, so its document is the value's
and its customer this one (the filter and row level security). The app never deletes a key
(0049): a value corrected to blank text keeps its old key until the value goes.

Cost: one indexed read of the values, then one upsert per keyed value in one ``executemany``.
"""

from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy import Connection, text

from kaagaz.extraction.land import KEY_KINDS, LAND_PREFIX, value_key

_FIELDS = sorted(KEY_KINDS)
_KEY_COLUMNS = ("kind", "level", "latin", "sound")  # value_key's order
_BY_DOCUMENT = text(
    "SELECT id, field, value_text FROM extracted_values WHERE customer_id = :c AND document_id = :d"
    " AND record_key LIKE :prefix AND field = ANY(CAST(:fields AS text[]))"
)
_BY_ID = text(
    "SELECT id, field, value_text FROM extracted_values WHERE customer_id = :c"
    " AND id = ANY(CAST(:ids AS uuid[])) AND record_key LIKE :prefix AND field = ANY(CAST(:fields AS text[]))"
)
_UPSERT = text(
    "INSERT INTO value_keys (customer_id, value_id, document_id, kind, place_level, latin, sound)"
    " SELECT v.customer_id, v.id, v.document_id, :kind, :level, :latin, :sound FROM extracted_values v"
    " WHERE v.customer_id = :c AND v.id = :v"
    " ON CONFLICT (customer_id, value_id) DO UPDATE SET latin = EXCLUDED.latin, sound = EXCLUDED.sound"
)


def key_document(conn: Connection, customer_id: str, document_id: str) -> int:
    """Key every land person, place and property value of one document; how many."""
    params = {"c": customer_id, "d": document_id, "prefix": LAND_PREFIX + "%", "fields": _FIELDS}
    return _write(conn, customer_id, conn.execute(_BY_DOCUMENT, params).all())


def key_values(conn: Connection, customer_id: str, value_ids: Collection[str]) -> int:
    """Key these values again, those that are land person, place or property values; how many."""
    if not value_ids:
        return 0
    params = {"c": customer_id, "ids": sorted(value_ids), "prefix": LAND_PREFIX + "%", "fields": _FIELDS}
    return _write(conn, customer_id, conn.execute(_BY_ID, params).all())


def _write(conn: Connection, customer_id: str, rows: Sequence[Any]) -> int:
    upserts = []
    for value_id, field, value_text in rows:
        key = value_key(field, value_text)
        if key is not None:
            upserts.append({"c": customer_id, "v": value_id, **dict(zip(_KEY_COLUMNS, key, strict=True))})
    if upserts:
        conn.execute(_UPSERT, upserts)
    return len(upserts)
