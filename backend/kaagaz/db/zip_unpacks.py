"""A zip unpacked into a folder (#522 PR 3, Tushar, 6 Nov 2026): the folder upload the worker
makes of it, and what it says.

Why here: the worker unpacks a zip as a folder upload of its own (``kaagaz/jobs/unpack.py``),
so the folders, the limits, the items and their states are the folder upload's
(``db/folder_uploads.py``, ``db/folder_upload_items.py``); this module only ties the upload to
the zip's document (``folder_uploads.zip_document_id``, 0080) and reads it back for the zip's
own document. Every function takes a connection from ``Database.customer(customer_id)`` and
also filters on ``customer_id`` (two locks, docs/architecture.md).

* ``unpack_id``: the upload's id, from the zip's document id (UUID5), so a job run again finds
  the same upload, folders and items.
* ``place``: what decides whether the zip is unpacked at all. Only a zip a person uploaded on its
  own is: a first version, with an uploader, out of Trash, and not a file of a folder upload
  (one of any upload's batches, ``batch_for``): a zip inside a zip, or inside a folder upload,
  is kept as an ordinary file and not read.
* ``unpacks``: each zip's upload with its items counted by state and failure code, and its first
  SHOWN_SKIPPED refused files in order, for the zip's document (the result line).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, text

# Fixed for good: unpack_id must give an upload the same id every time.
_NAMESPACE = uuid.UUID("5d0c7a52-0b8e-4f0e-9a3c-7b1f2e6d9c41")
SHOWN_SKIPPED = 20


def unpack_id(document_id: str) -> str:
    """The folder upload a zip document is unpacked as. O(1)."""
    return str(uuid.uuid5(_NAMESPACE, str(uuid.UUID(document_id))))


@dataclass(frozen=True, slots=True)
class Place:
    uploaded_by: str | None
    folder_id: str | None  # where it is now
    unpacks: bool  # a zip a person uploaded on its own (module notes)
    planned: bool  # unpacked before (a job run again): its upload exists
    parent_folder_id: str | None  # where it was unpacked to, when planned
    source_name: str  # the name its folder was planned with ("" when not planned)


_PLACE = text(
    "SELECT d.uploaded_by, d.folder_id,"
    " d.version_no = 1 AND d.uploaded_by IS NOT NULL AND d.deleted_at IS NULL AND NOT EXISTS ("
    "SELECT 1 FROM folder_upload_items i WHERE i.customer_id = :c AND i.batch_id = d.batch_id),"
    " u.id IS NOT NULL, u.parent_folder_id, coalesce(u.source_name, '')"
    " FROM documents d LEFT JOIN folder_uploads u ON u.customer_id = :c AND u.id = :u"
    " WHERE d.customer_id = :c AND d.id = :d"
)


def place(conn: Connection, customer_id: str, document_id: str) -> Place | None:
    """The zip document's place and whether it is unpacked (module notes); None for no such
    document of this customer. One statement: O(log D), and O(I) for the customer's I items when
    it has a batch (no index on an item's batch)."""
    params = {"c": customer_id, "d": document_id, "u": unpack_id(document_id)}
    row = conn.execute(_PLACE, params).first()
    if row is None:
        return None
    return Place(
        uploaded_by=None if row[0] is None else str(row[0]),
        folder_id=None if row[1] is None else str(row[1]),
        unpacks=bool(row[2]),
        planned=bool(row[3]),
        parent_folder_id=None if row[4] is None else str(row[4]),
        source_name=str(row[5]),
    )


_ATTACH = text(
    "UPDATE folder_uploads SET zip_document_id = :d WHERE customer_id = :c AND id = :u"
    " AND zip_document_id IS NULL"
)


def attach(conn: Connection, customer_id: str, upload_id: str, document_id: str) -> None:
    """Tie the upload just planned to its zip document. One statement, O(log U)."""
    conn.execute(_ATTACH, {"c": customer_id, "u": upload_id, "d": document_id})


@dataclass(frozen=True, slots=True)
class Unpack:
    upload_id: str
    root_folder_id: str | None  # None once that folder is deleted
    root_name: str
    file_count: int  # every file of the zip that isn't a Mac's own, refused ones too
    stored: int
    duplicate: int
    failed: int
    waiting: int
    reasons: dict[str, int] = field(default_factory=dict)  # failure code -> files
    skipped: list[tuple[str, str]] = field(default_factory=list)  # the first refused: (name, code)


_COUNTS = text(
    "SELECT u.zip_document_id, u.id, f.id, u.root_name, u.file_count, i.state, i.failure_code, count(i.seq)"
    " FROM folder_uploads u"
    " LEFT JOIN folders f ON f.customer_id = u.customer_id AND f.id = u.root_folder_id"
    " LEFT JOIN folder_upload_items i ON i.customer_id = u.customer_id AND i.folder_upload_id = u.id"
    " WHERE u.customer_id = :c AND u.zip_document_id = ANY(CAST(:ids AS uuid[]))"
    " GROUP BY u.zip_document_id, u.id, f.id, u.root_name, u.file_count, i.state, i.failure_code"
)
_SKIPPED = text(
    "SELECT u.zip_document_id, s.name, s.failure_code FROM folder_uploads u"
    " CROSS JOIN LATERAL (SELECT i.name, i.failure_code, i.seq FROM folder_upload_items i"
    " WHERE i.customer_id = :c AND i.folder_upload_id = u.id AND i.state = 'failed'"
    " ORDER BY i.seq LIMIT :n) s"
    " WHERE u.customer_id = :c AND u.zip_document_id = ANY(CAST(:ids AS uuid[]))"
    " ORDER BY u.zip_document_id, s.seq"
)


def unpacks(conn: Connection, customer_id: str, document_ids: Sequence[str]) -> dict[str, Unpack]:
    """Each of these zip documents' unpacking, by document id; a zip not unpacked has none.
    Two statements on the state index: O(z log U + items) for z zips."""
    if not document_ids:
        return {}
    params: dict[str, Any] = {"c": customer_id, "ids": list(document_ids), "n": SHOWN_SKIPPED}
    found: dict[str, dict[str, Any]] = {}
    for zip_id, upload, root, name, files, state, code, count in conn.execute(_COUNTS, params):
        entry = found.setdefault(
            str(zip_id),
            {"upload": str(upload), "root": None if root is None else str(root), "name": str(name)}
            | {"files": int(files), "states": {}, "reasons": {}},
        )
        if state is not None:
            entry["states"][str(state)] = entry["states"].get(str(state), 0) + int(count)
        if code is not None:
            entry["reasons"][str(code)] = int(count)
    skipped: dict[str, list[tuple[str, str]]] = {}
    for zip_id, name, code in conn.execute(_SKIPPED, params):
        skipped.setdefault(str(zip_id), []).append((str(name), str(code)))
    return {
        zip_id: Unpack(
            upload_id=entry["upload"],
            root_folder_id=entry["root"],
            root_name=entry["name"],
            file_count=entry["files"],
            stored=entry["states"].get("stored", 0),
            duplicate=entry["states"].get("duplicate", 0),
            failed=entry["states"].get("failed", 0),
            waiting=entry["states"].get("waiting", 0),
            reasons=entry["reasons"],
            skipped=skipped.get(zip_id, []),
        )
        for zip_id, entry in found.items()
    }
