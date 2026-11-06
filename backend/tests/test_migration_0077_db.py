"""Migration 0077 (#522, Tushar, 6 Nov 2026): ``documents.detected_type`` takes the file kinds
the worker tells after the virus scan, and the sniffed types still. A ready zip becomes xlsx and
a ready text file csv, with ``updated_at`` kept; every other document keeps its type. Up, down
(every kind back to its sniffed type) and up again: the schema, comments and grants as before
each time."""

from typing import Any

import pytest

from kaagaz.db.engine import Database
from kaagaz.ingestion import resolve, sniff
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer
from tests.test_migration_0023_db import as_owner
from tests.test_migration_0037 import _load
from tests.test_migration_0070_db import _refused
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0077 = _load("0077_file_kinds.py")
_SET = "UPDATE documents SET detected_type = :t WHERE id = :d"
_COLUMN_COMMENT = (
    "SELECT col_description('documents'::regclass, attnum) FROM pg_attribute"
    " WHERE attrelid = 'documents'::regclass AND attname = 'detected_type'"
)


def _types(db: Database, ids: dict[str, str]) -> dict[str, tuple[Any, ...]]:
    """Each document's type and updated_at, by its name in ``ids``."""
    return {
        name: as_owner(db, "SELECT detected_type, updated_at FROM documents WHERE id = :d", {"d": d})[0]
        for name, d in ids.items()
    }


def test_the_revision_follows_0076_and_names_every_sniffed_type_and_kind() -> None:
    assert (M0077.revision, M0077.down_revision) == ("0077", "0076")  # after the versioning PR's 0076
    assert set(M0077.SNIFFED) == sniff.KNOWN_TYPES - {sniff.HEIC}  # 0001's
    assert set(M0077.SNIFFED) | set(M0077.KINDS) == sniff.KNOWN_TYPES | resolve.KINDS
    assert set(M0077.KINDS.values()) <= set(M0077.SNIFFED)  # each goes back to a type 0001 takes


def test_up_tells_ready_documents_down_puts_the_sniffed_types_back_and_up_again(
    fresh: tuple[str, Database],
) -> None:
    url, db = fresh
    migrate(url, "0076")
    at_0076_before = _state(db)
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        ids = {
            "sheet": insert_document(conn, customer, detected_type="zip", status="ready"),
            "word": insert_document(conn, customer, detected_type="zip", status="needs_reader"),
            "failed_zip": insert_document(conn, customer, detected_type="zip", status="failed"),
            "csv": insert_document(conn, customer, detected_type="text", status="ready"),
            "waiting": insert_document(conn, customer, detected_type="text", status="received"),
            "pdf": insert_document(conn, customer, detected_type="pdf", status="ready"),
        }
    assert _refused(db, _SET, {"t": "heic", "d": ids["pdf"]}) == "23514"
    before = _types(db, ids)

    migrate(url, "0077")
    at_0077 = _state(db)
    after = _types(db, ids)
    told = {name: row[0] for name, row in after.items()}
    assert told == {
        "sheet": "xlsx",
        "word": "zip",
        "failed_zip": "zip",
        "csv": "csv",
        "waiting": "text",
        "pdf": "pdf",
    }
    assert {name: row[1] for name, row in after.items()} == {name: row[1] for name, row in before.items()}
    assert as_owner(db, _COLUMN_COMMENT) == [(M0077.COMMENT,)]
    for kind in sorted(resolve.KINDS | sniff.KNOWN_TYPES):
        as_owner(db, _SET, {"t": kind, "d": ids["pdf"]})
    for refused in (resolve.UNSUPPORTED, "exe", "PDF"):
        assert _refused(db, _SET, {"t": refused, "d": ids["pdf"]}) == "23514"
    for name, kind in (("pdf", "heic"), ("waiting", "txt"), ("word", "docx"), ("failed_zip", "doc")):
        as_owner(db, _SET, {"t": kind, "d": ids[name]})

    base._downgrade(url, "0076")
    assert _state(db) == at_0076_before
    assert {name: row[0] for name, row in _types(db, ids).items()} == {
        "sheet": "zip",
        "word": "zip",
        "failed_zip": "ole2",
        "csv": "text",
        "waiting": "text",
        "pdf": "ole2",  # a HEIC photo: kept, never read before 0077
    }
    assert _refused(db, _SET, {"t": "xlsx", "d": ids["pdf"]}) == "23514"

    migrate(url, "0077")
    assert _state(db) == at_0077
    assert as_owner(db, "SELECT detected_type FROM documents WHERE id = :d", {"d": ids["sheet"]}) == [
        ("xlsx",)
    ]
