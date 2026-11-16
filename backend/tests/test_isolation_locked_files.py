"""PRD 7 PR 12 (#605): a lock is real and safe. 0093's functions and triggers, the lock, unlock and
grant routes, every route against a locked file, and Ask.

The organisation is test_isolation_access_views' ``_walled`` one. Its Sales file ("sales", in an
open department: everyone sees it today) is the file locked here, as two versions, each with a
row in every content table (test_isolation_content_views' ``_content`` and the rest below) and the
rows a lock keeps. The plain reviewer is the person allowed to open it; the Finance member, the
Admin and Management are not given it. Every refusal to a person who may not open it is checked
against an id that never was, byte for byte."""

import hashlib
import json
import uuid
from typing import Any
from unittest import mock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.answering.service import AnswerService
from kaagaz.api.views import LOCKED_LINE, LOCKED_NOTE, NO_ACCESS_LINE
from kaagaz.db import locks as locks_sql
from kaagaz.db.documents import STUCK as _STUCK
from kaagaz.db.documents import PgDocuments, stuck_documents
from kaagaz.db.engine import Database
from kaagaz.db.page_reads import PageKey, PgPageReads
from kaagaz.ingestion.ports import Job
from kaagaz.search.scope import Scope
from tests.db_support import rule_on
from tests.settings_factory import make_settings
from tests.test_amount_guard_db import TOTAL, add_bill
from tests.test_answering_fixed_queries_db import NOW
from tests.test_api_support import ANY_ID, CSRF, PASSWORD, Api, Signed, answer_json
from tests.test_isolation_access_api import _audit, _call, _version
from tests.test_isolation_access_views import Org, _app, _walled
from tests.test_isolation_content_views import _content
from tests.test_migration_0037 import _load
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
M0093 = _load("0093_locked_files.py")
M0099 = _load("0099_version_finder.py")

# Appendix B (and the corrections made on the file): every table with a key into documents is
# cleared by a lock, or kept by it. A table added later fails the completeness test until it is
# put on one of the two lists (and, if cleared, into 0093's kaagaz_clear_derived).
CLEARED = {
    "reader_outputs",
    "pieces",
    "extracted_values",
    "value_keys",
    "corrections",
    "invoices",
    "invoice_lines",
    "document_links",
    "page_reads",
    "document_sections",
    "document_topics",
    "page_blocks",
    "table_cells",
    "ensemble_runs",
    "ensemble_readings",
    "word_changes",
    "version_suggestions",
    "organization_word_suggestions",
    "user_word_spots",
    "learned_corrections",
    "version_pairs",  # Changed on purpose (#648, 0099): the version finder's, worked out from the text
    "version_group_members",
}
KEPT = {
    "pages",  # its sizes and pictures; every reading result reset (PAGE_RESET)
    "document_tags",
    "document_stories",
    "processing_runs",
    "model_usage",
    "source_pins",
    "learning_removals",
    "page_rotations",
    "document_shares",
    "lock_grants",
    "whatsapp_messages",  # Changed on purpose (#603, 0097): the person's own message that saved it
    "version_links",  # Changed on purpose (#648, 0100): a link's ids for its Undo, never content
}
CLEARED_COLUMNS = {
    "doc_type",
    "languages",
    "summary",
    "document_date",
    "card",
    "sensitivity",  # the reader's label only; a person's stays
    "text_minhash",
    "text_bands",
    "autofile",
    "progress",
    "failure_code",
}
KEPT_COLUMNS = {
    "id", "customer_id", "display_name", "sha256", "size_bytes", "detected_type", "status", "page_count",
    "uploaded_by", "created_at", "updated_at", "folder_id", "deleted_at", "deleted_by", "family_id",
    "version_no", "is_current", "attempts", "max_attempts", "ocr_choice", "batch_id", "read_model",
    "read_requested_by", "read_mode", "read_also", "owner_id", "sensitivity_set_by_person",
    "details_updated_by", "details_updated_at", "version_note", "name_key", "reading_held",
    "reading_admitted", "trashed_folder_id", "trash_id", "department_id", "private_folder_id", "held_for",
    "locked_at", "locked_by",
}  # fmt: skip
PAGE_RESET = (
    "route IS NULL AND read_by IS NULL AND read_quality = 'good' AND read_issues = '{}' AND unsure_words = 0"
    " AND illegible_words = 0 AND unpinned_lines = 0 AND accepted_by IS NULL"
    " AND read_again_requested_at IS NULL"
    " AND read_thinking IS NULL AND read_pass IS NULL AND read_output_tokens IS NULL"
)
_SHA = "a" * 64
_CHANGE = "b" * 64


def _more(api: Api, org: Org, document: str, other: str) -> None:
    """The content rows ``_content`` leaves out, the card, and a row of each table a lock keeps."""
    db, c, admin = api.db, org.c, org.people["admin"].user_id
    p = {"c": c, "d": document, "o": other, "u": admin, "k": _CHANGE, "s": _SHA, "r": f"Zebra {document}"}
    for statement in (
        "UPDATE documents SET doc_type = 'invoice', summary = 'Zebra summary', card = '{\"k\": 1}',"
        " languages = '{en}', document_date = '2026-01-01', text_minhash = '{1}', text_bands = '{1}',"
        " autofile = 'filed', sensitivity = 'confidential' WHERE id = :d",
        "UPDATE pages SET route = 'ocr', read_by = 'm', unsure_words = 2 WHERE document_id = :d",
        "INSERT INTO page_reads (customer_id, document_id, page_no, image_sha256, model, prompt_version,"
        " result) VALUES (:c, :d, 1, :s, 'm', 'g1', '{\"lines\": [\"Zebra\"]}')",
        "INSERT INTO document_sections (customer_id, document_id, seq, path, page_from, page_to,"
        " piece_seq_from, piece_seq_to, summary) VALUES (:c, :d, 0, '{Top}', 1, 1, 0, 0, 'Zebra section')",
        "INSERT INTO document_topics (customer_id, document_id, topic, piece_seqs)"
        " VALUES (:c, :d, 'zebra', '{0}')",
        "INSERT INTO page_blocks (customer_id, document_id, page_no, seq, kind, region)"
        " VALUES (:c, :d, 1, 0, 'text', '{}')",
        "INSERT INTO table_cells (customer_id, document_id, page_no, table_id, row_no, col_no, is_header,"
        " text, region) VALUES (:c, :d, 1, 't1', 1, 1, false, 'Zebra cell', '{}')",
        "INSERT INTO word_changes (customer_id, document_id, word_key, kind, page_no, region, as_read,"
        " action, before_text, after_text, user_id)"
        " VALUES (:c, :d, :k, 'mark', 1, '{}', 'Zebra', 'edit', 'Zebra', 'Zebu', :u)",
        "INSERT INTO value_keys (customer_id, value_id, document_id, kind, latin, sound)"
        " SELECT :c, v.id, :d, 'person', 'zebra', 'zbr' FROM extracted_values v WHERE v.document_id = :d",
        "INSERT INTO learned_corrections (customer_id, field, raw_value, corrected_value, created_by,"
        " source_document_id) VALUES (:c, 'total_amount', :r, 'Zebra 2', :u, :d)",
        "INSERT INTO document_links (customer_id, document_id, linked_document_id, identifier_kind,"
        " identifier_value)"
        " VALUES (:c, :d, :o, 'invoice_number', 'Z-1'), (:c, :o, :d, 'invoice_number', 'Z-1')",
        "INSERT INTO version_suggestions (customer_id, document_id, candidate_family_id, outcome, status)"
        " SELECT :c, :o, family_id, 'suggested', 'pending' FROM documents WHERE id = :d"
        " ON CONFLICT DO NOTHING",
        "INSERT INTO document_stories (customer_id, user_id, family_id, story, story_latin)"
        " SELECT :c, :u, family_id, 'my own note', 'my own note' FROM documents WHERE id = :d"
        " ON CONFLICT DO NOTHING",
        "INSERT INTO page_rotations (customer_id, document_id, page_no, turn, set_by)"
        " VALUES (:c, :d, 1, 90, :u)",
        "INSERT INTO learning_removals (customer_id, document_id) VALUES (:c, :d)",
        "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status)"
        " VALUES (:c, :d, now(), now(), 'ready')",
        # Changed on purpose (#648, 0099): the version finder's pair and group membership.
        "INSERT INTO version_pairs (customer_id, first_id, second_id, text_overlap)"
        " SELECT :c, least(a, b), greatest(a, b), 0.9 FROM (SELECT CAST(:d AS uuid) a, CAST(:o AS uuid) b) x",
        "INSERT INTO version_group_members (customer_id, document_id, group_id, position)"
        " VALUES (:c, :d, gen_random_uuid(), 1)",
    ):
        _owner(db, statement, p)


def _family(api: Api, org: Org) -> tuple[str, str]:
    """The Sales file as version 1 and a version 2 (current), each with every row; (v1, v2)."""
    db, c, first = api.db, org.c, org.people["admin"].user_id
    v1 = org.files["sales"]
    v2 = api.add_document(
        c,
        name="sales v2.pdf",
        folder_id=org.folders["sales"],
        family_id=v1,
        version_no=2,
        is_current=False,
        uploaded_by=first,
    )
    _owner(db, "UPDATE documents SET is_current = false WHERE id = :v1", {"v1": v1})
    _owner(db, "UPDATE documents SET is_current = true WHERE id = :v2", {"v2": v2})
    for version in (v1, v2):
        _content(api, org, version)
        _more(api, org, version, org.files["loose"])
    return v1, v2


def _lock(admin: Signed, document: str, open_to: list[str] | None = None) -> tuple[int, Any]:
    body: dict[str, Any] = {"password": PASSWORD}
    if open_to is not None:
        body["open_to"] = open_to
    return _call(admin, "POST", f"/api/documents/{document}/lock", body)


def _count(db: Database, table: str, documents: list[str], column: str = "document_id") -> int:
    statement = f"SELECT count(*) FROM {table} WHERE {column} = ANY(CAST(:d AS uuid[]))"  # noqa: S608 - fixed
    rows = _owner(db, statement, {"d": documents})
    return int(rows[0][0])


@pytest.fixture
def locked(db: Database) -> tuple[Api, Org, str, str]:
    """The organisation with the Sales family locked by its Admin, open to the plain reviewer."""
    api = Api(db)
    org = _walled(api)
    v1, v2 = _family(api, org)
    status, reply = _lock(org.people["admin"], v2, [org.people["plain"].user_id])
    assert status == 200, reply
    return api, org, v1, v2


# --- 0093: the lock, its clearing and what it keeps --------------------------------------------


def test_the_kinds_a_lock_takes_are_the_migrations() -> None:
    assert locks_sql.LOCKABLE == M0093.LOCKABLE and locks_sql.MAX_OPEN_TO == M0093.MAX_OPEN_TO


def test_a_lock_clears_appendix_b_on_every_version_and_keeps_the_rest(
    locked: tuple[Api, Org, str, str],
) -> None:
    api, org, v1, v2 = locked
    db, versions = api.db, [v1, v2]
    by_other_column = {"learned_corrections", "document_links", "version_suggestions", "version_pairs"}
    for table in sorted(CLEARED - by_other_column):
        assert _count(db, table, versions) == 0, table
    assert _count(db, "learned_corrections", versions, "source_document_id") == 0
    assert _count(db, "document_links", versions, "linked_document_id") == 0
    assert _count(db, "version_suggestions", versions, "candidate_family_id") == 0
    for side in ("first_id", "second_id"):  # Changed on purpose (#648, 0099)
        assert _count(db, "version_pairs", versions, side) == 0, side
    cleared = _owner(
        db,
        "SELECT count(*) FROM documents WHERE id = ANY(CAST(:d AS uuid[])) AND doc_type IS NULL"
        " AND summary IS NULL AND card IS NULL AND languages = '{}' AND document_date IS NULL"
        " AND text_minhash IS NULL"
        " AND text_bands IS NULL AND autofile IS NULL AND sensitivity = 'internal' AND status = 'locked'"
        " AND locked_by IS NOT NULL",
        {"d": versions},
    )
    assert cleared[0][0] == 2
    reset = f"SELECT count(*) FROM pages WHERE document_id = ANY(CAST(:d AS uuid[])) AND {PAGE_RESET}"  # noqa: S608
    assert _owner(db, reset + " AND has_image AND width = 595", {"d": versions})[0][0] == 2
    for table in ("document_tags", "page_rotations", "learning_removals", "processing_runs"):
        assert _count(db, table, versions) >= 2, table
    assert _count(db, "document_stories", [v1], "family_id") >= 1
    assert _count(db, "lock_grants", versions) == 2  # the plain reviewer on each version
    assert api.storage.has(org.c, v1) and api.storage.has(org.c, v2)  # the originals stay


def test_every_key_into_documents_is_cleared_or_kept(db: Database) -> None:
    """The completeness guard: a table added with a key into documents fails here until listed."""
    rows = _owner(
        db,
        "SELECT DISTINCT conrelid::regclass::text FROM pg_constraint WHERE contype = 'f'"
        " AND confrelid = 'documents'::regclass AND conrelid <> 'documents'::regclass",
        {},
    )
    assert {str(row[0]) for row in rows} | {"invoice_lines"} == CLEARED | KEPT
    assert not CLEARED & KEPT
    for table in CLEARED:
        assert f"DELETE FROM public.{table} x" in M0099.CLEAR_DERIVED, table  # noqa: S608 - a text search
    # Changed on purpose (#648): 0099 replaced 0093's function, adding the version finder's tables.
    assert M0099.OLD_CLEAR_DERIVED.replace("CREATE OR REPLACE", "CREATE") == M0093.CLEAR_DERIVED


def test_every_document_column_is_cleared_or_kept(db: Database) -> None:
    rows = _owner(
        db,
        "SELECT attname FROM pg_attribute WHERE attrelid = 'documents'::regclass AND attnum > 0"
        " AND NOT attisdropped",
        {},
    )
    assert {str(row[0]) for row in rows} == CLEARED_COLUMNS | KEPT_COLUMNS
    assert not CLEARED_COLUMNS & KEPT_COLUMNS


# Review (15 Nov 2026): every column of ``pages`` is reset by the lock (0093's pages UPDATE) or kept.
PAGES_RESET = {
    "route", "read_by", "read_quality", "read_issues", "unsure_words", "illegible_words", "unpinned_lines",
    "accepted_by", "accepted_at", "read_again_requested_at", "read_again_regions", "read_thinking",
    "read_resolution", "read_pass", "read_output_tokens",
}  # fmt: skip
PAGES_KEPT = {
    "customer_id", "document_id", "page_no", "width", "height", "unit", "has_image", "effective_dpi",
    "low_resolution",
}  # fmt: skip


def test_every_pages_column_is_reset_or_kept(db: Database) -> None:
    rows = _owner(
        db,
        "SELECT attname FROM pg_attribute WHERE attrelid = 'pages'::regclass AND attnum > 0"
        " AND NOT attisdropped",
        {},
    )
    assert {str(row[0]) for row in rows} == PAGES_RESET | PAGES_KEPT
    reset = M0093.CLEAR_DERIVED[M0093.CLEAR_DERIVED.index("UPDATE public.pages p") :]
    for column in PAGES_RESET:
        assert f" {column} = " in reset, column


def test_a_label_a_person_set_is_kept(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    file = org.files["sales"]
    _owner(
        db,
        "UPDATE documents SET sensitivity = 'restricted', sensitivity_set_by_person = true WHERE id = :d",
        {"d": file},
    )
    api.add_page(org.c, file)
    assert _lock(org.people["admin"], file)[0] == 200
    assert _owner(db, "SELECT sensitivity FROM documents WHERE id = :d", {"d": file})[0][0] == "restricted"


def test_only_an_admin_of_this_organisation_runs_the_lock_functions(
    locked: tuple[Api, Org, str, str],
) -> None:
    api, org, _, v2 = locked
    db = api.db
    for user in (org.people["management"].user_id, org.people["plain"].user_id):
        for statement in (
            "SELECT * FROM kaagaz_unlock(CAST(:d AS uuid[]))",
            "SELECT * FROM kaagaz_lock(CAST(:d AS uuid[]), CAST('{}' AS uuid[]))",
            "SELECT kaagaz_lock_grant(:x, :x)",
            "SELECT kaagaz_lock_revoke(:x, :x)",
        ):
            with pytest.raises(DBAPIError) as refused, _app(db).user(org.c, user) as conn:
                conn.execute(text(statement), {"d": [v2], "x": v2})
            assert getattr(refused.value.orig, "sqlstate", None) == "42501", statement
    with pytest.raises(DBAPIError) as worker, _app(db).customer(org.c) as conn:  # no person: the worker
        conn.execute(text("SELECT * FROM kaagaz_unlock(CAST(:d AS uuid[]))"), {"d": [v2]})
    assert getattr(worker.value.orig, "sqlstate", None) == "42501"
    with pytest.raises(DBAPIError) as internal, _app(db).user(org.c, org.people["admin"].user_id) as conn:
        conn.execute(text("SELECT kaagaz_clear_derived(:d)"), {"d": v2})
    assert getattr(internal.value.orig, "sqlstate", None) == "42501"  # no role may run it
    # Another organisation's Admin: our file is not found, and nothing changes.
    other_org = api.customer()
    with _app(db).user(other_org[0], other_org[1]) as conn:
        rows = conn.execute(
            text("SELECT document_id, outcome FROM kaagaz_unlock(CAST(:d AS uuid[]))"), {"d": [v2]}
        ).all()
    assert [row[1] for row in rows] == ["not_found"]
    assert _owner(db, "SELECT locked_at IS NOT NULL FROM documents WHERE id = :d", {"d": v2})[0][0] is True


def test_a_lock_is_all_or_nothing(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    sheet = api.add_document(org.c, name="sheet.xlsx", detected_type="xlsx", folder_id=org.folders["sales"])
    good = org.files["loose"]
    api.add_page(org.c, good)
    before = _version(db, org.c)
    status, reply = _call(
        org.people["admin"],
        "POST",
        "/api/documents/lock",
        {"document_ids": [good, sheet], "password": PASSWORD},
    )
    assert status == 409 and reply["error"]["details"]["refused"] == [{"id": sheet, "reason": "kind"}]
    assert (
        _owner(
            db,
            "SELECT count(*) FROM documents WHERE locked_at IS NOT NULL AND id IN (:a, :b)",
            {"a": good, "b": sheet},
        )[0][0]
        == 0
    )
    assert _version(db, org.c) == before
    status, reply = _lock(org.people["admin"], sheet)
    assert (status, reply["error"]["code"]) == (409, "not_lockable")


def test_a_file_being_read_is_locked_to_be_prepared_and_its_reading_run_ends(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    file = api.add_document(org.c, name="reading.pdf", status="clean", folder_id=org.folders["sales"])
    run = _owner(
        db,
        "INSERT INTO processing_runs (customer_id, document_id, started_at, status)"
        " VALUES (:c, :d, now(), 'running') RETURNING id",
        {"c": org.c, "d": file},
    )[0][0]
    assert _lock(org.people["admin"], file)[0] == 200
    state = _owner(db, "SELECT status, reading_held FROM documents WHERE id = :d", {"d": file})[0]
    assert tuple(state) == ("received", None)  # no picture yet: the worker prepares it
    assert tuple(
        _owner(db, "SELECT status, error_code FROM processing_runs WHERE id = :r", {"r": run})[0]
    ) == (
        "superseded",
        "locked",
    )
    assert [job.document_id for job in api.queue.jobs] == [file]  # the prepare-only job


def test_a_locked_file_being_prepared_is_never_stuck_nor_read_again(db: Database) -> None:
    """F3: hours after it was asked for, a locked version still waiting to be prepared is not
    offered Retry, and Read again refuses it whatever the route says."""
    from kaagaz.db.documents import NotReprocessable, mark_for_reprocess

    api = Api(db)
    org = _walled(api)
    file = api.add_document(org.c, name="slow.pdf", status="clean", folder_id=org.folders["sales"])
    assert _lock(org.people["admin"], file)[0] == 200
    _owner(db, "UPDATE documents SET created_at = now() - interval '2 days' WHERE id = :d", {"d": file})
    listed = {
        row["id"]: row
        for row in org.people["admin"].client.get("/api/documents", headers=CSRF).json()["items"]
    }
    assert file not in listed or listed[file]["stuck"] is False  # the Admin was not given it
    with db.customer(org.c) as conn:
        stuck = conn.execute(text(f"SELECT {_STUCK} FROM documents d WHERE d.id = :d"), {"d": file})  # noqa: S608
        assert stuck.scalar_one() is False
        with pytest.raises(NotReprocessable):
            mark_for_reprocess(conn, org.c, file, per_hour=100)


# --- who sees it, and every route ---------------------------------------------------------------


def _same_as_random(signed: Signed, method: str, path: str, document: str, body: Any = None) -> None:
    hidden = signed.client.request(method, path.replace("{id}", document), json=body, headers=CSRF)
    never = signed.client.request(method, path.replace("{id}", ANY_ID), json=body, headers=CSRF)
    assert (hidden.status_code, hidden.content) == (never.status_code, never.content), path


def test_a_locked_file_is_found_by_its_name_only_by_those_given_it(locked: tuple[Api, Org, str, str]) -> None:
    _, org, _, v2 = locked
    plain = org.people["plain"]
    listed = {row["id"]: row for row in plain.client.get("/api/documents", headers=CSRF).json()["items"]}
    row = listed[v2]
    assert row["locked"] is True and row["status"] == "locked"
    assert (row["doc_type"], row["languages"], row["values_to_review"], row["pages_uncertain"]) == (
        None,
        [],
        0,
        0,
    )
    detail = plain.client.get(f"/api/documents/{v2}", headers=CSRF).json()
    assert detail["locked"] is True
    assert (detail["values"], detail["summary"], detail["card"], detail["invoice"], detail["links"]) == (
        [], None, None, None, [],
    )  # fmt: skip
    assert detail["pages"] == [
        {"page_no": 1, "width": 595.0, "height": 842.0, "unit": "pt", "has_image": True}
    ]
    for name in ("member", "admin", "management", "head"):
        seen = org.people[name].client.get("/api/documents", headers=CSRF).json()["items"]
        assert v2 not in {each["id"] for each in seen}, name
        _same_as_random(org.people[name], "GET", "/api/documents/{id}", v2)


# Every route of a file and how it treats a locked one: refused (409 locked to a person who may
# open it), allowed (works as for any file), or the Admin's own (lock and access routes, tested
# with their writes). A route added later is not in this table, and the test fails until it is.
REFUSED = {
    ("GET", "/api/documents/{document_id}/file"),
    ("GET", "/api/documents/{document_id}/pages/{page_no}/image"),
    ("GET", "/api/documents/{document_id}/pages/{page_no}/readings"),
    ("GET", "/api/documents/{document_id}/pages/{page_no}/cells"),
    ("GET", "/api/documents/{document_id}/words"),
    ("GET", "/api/documents/{document_id}/questions"),
    ("POST", "/api/documents/{document_id}/reprocess"),
    ("POST", "/api/documents/{document_id}/pages/{page_no}/read-again"),
    ("POST", "/api/documents/{document_id}/pages/{page_no}/accept"),
    ("GET", "/api/documents/{document_id}/clear-copy"),
    ("GET", "/api/documents/{document_id}/ensemble/estimate"),
    ("POST", "/api/documents/{document_id}/ensemble"),
    ("GET", "/api/documents/{document_id}/ensemble"),
    ("POST", "/api/documents/{document_id}/ensemble/{run_id}/stop"),
    ("POST", "/api/documents/{document_id}/ensemble/{run_id}/retry"),
    ("POST", "/api/documents/{document_id}/ensemble/{run_id}/keep-all"),
    ("POST", "/api/documents/{document_id}/ensemble/readings/{reading_id}/keep"),
    ("POST", "/api/documents/{document_id}/ensemble/readings/{reading_id}/revert"),
    ("GET", "/api/documents/{document_id}/review-status"),
    ("GET", "/api/documents/{document_id}/flags"),
    ("POST", "/api/documents/{document_id}/flags/{word_key}/confirm"),
    ("POST", "/api/documents/{document_id}/flags/{word_key}/edit"),
    ("POST", "/api/documents/{document_id}/lines/{line_key}/edit"),
    ("GET", "/api/documents/{document_id}/changes"),
    ("POST", "/api/documents/{document_id}/changes/{change_id}/undo"),
    ("GET", "/api/documents/{document_id}/my-words"),
    ("POST", "/api/documents/{document_id}/version-undo"),
    ("POST", "/api/documents/{document_id}/make-current"),
    ("POST", "/api/documents/{document_id}/separate"),
    ("POST", "/api/documents/{document_id}/separate/undo"),
    ("GET", "/api/documents/{document_id}/compare"),
}
# Changed on purpose (PR 13): what a person who may open it does only while their 10-minute check
# is live (403 locked_open_required without it): its page pictures, and turning its pages (no
# whole-file DELETE since #600's fix).
OPENED = {
    ("GET", "/api/documents/{document_id}/locked-pages/{page_no}"),
    ("PATCH", "/api/documents/{document_id}/page-turns"),
}
ALLOWED = {
    ("GET", "/api/documents/{document_id}"),
    ("GET", "/api/documents/{document_id}/summary"),
    ("GET", "/api/documents/{document_id}/activity"),
    ("PATCH", "/api/documents/{document_id}"),
    ("POST", "/api/documents/{document_id}/autofile/undo"),
    ("DELETE", "/api/documents/{document_id}"),
    ("POST", "/api/documents/{document_id}/restore"),
    ("DELETE", "/api/documents/{document_id}/permanent"),
    ("POST", "/api/documents/{document_id}/versions"),
    ("GET", "/api/documents/{document_id}/my-story"),
    ("PUT", "/api/documents/{document_id}/my-story"),
    ("DELETE", "/api/documents/{document_id}/my-story"),
    ("PUT", "/api/documents/{document_id}/tags"),
    ("DELETE", "/api/admin/learned/{document_id}"),
}
ADMINS = {
    ("POST", "/api/documents/{document_id}/lock"),
    ("POST", "/api/documents/{document_id}/unlock"),
    ("GET", "/api/documents/{document_id}/lock-grants"),
    ("PUT", "/api/documents/{document_id}/lock-grants/{user_id}"),
    ("DELETE", "/api/documents/{document_id}/lock-grants/{user_id}"),
    ("GET", "/api/documents/{document_id}/shares"),
    ("PUT", "/api/documents/{document_id}/shares/{user_id}"),
    ("DELETE", "/api/documents/{document_id}/shares/{user_id}"),
    ("GET", "/api/documents/{document_id}/audience"),
    ("GET", "/api/documents/{document_id}/move-preview"),
    ("GET", "/api/documents/{document_id}/lock-preview"),  # PR 13: the lock dialog's people
    # Changed on purpose (PR 9b, #606): the names list's Move and Send to Trash, an Admin's, found by
    # kaagaz_admin_document (a locked file is moved and trashed as any).
    ("POST", "/api/documents/{document_id}/kept/move"),
    ("POST", "/api/documents/{document_id}/kept/trash"),
    # Changed on purpose (#648, V-A): Admins' and Management's, 409 locked once a linked family is locked
    # (test_isolation_version_links).
    ("POST", "/api/documents/{document_id}/link-versions/undo"),
}
# The body or query a refused route needs to reach its lookup (an empty one is 422 first).
_BODIES: dict[str, Any] = {
    "/api/documents/{document_id}/ensemble": {"scope": "document"},
    "/api/documents/{document_id}/flags/{word_key}/confirm": {"expected_text": "x"},
    "/api/documents/{document_id}/flags/{word_key}/edit": {"text": "x", "expected_text": "y"},
    "/api/documents/{document_id}/lines/{line_key}/edit": {"text": "x", "expected_text": "y"},
    "/api/documents/{document_id}/separate": {"name": "apart.pdf"},
    "/api/documents/{document_id}/separate/undo": {"family_id": ANY_ID},
    "/api/documents/{document_id}/page-turns": {"changes": [{"page_no": 1, "turn": 90}]},
}
_QUERIES = {
    "/api/documents/{document_id}/my-words": "?page=1",
    "/api/documents/{document_id}/ensemble/estimate": "?scope=document",
    "/api/documents/{document_id}/compare": "?with={v1}",
}


def _templates(api: Api) -> set[tuple[str, str]]:
    """Every (method, path) of the app with a file's id in it, walked as route_calls walks them."""
    from fastapi.routing import APIRoute, iter_route_contexts

    found: set[tuple[str, str]] = set()
    for context in iter_route_contexts(api.app.routes):
        path = context.path
        if isinstance(context.original_route, APIRoute) and path is not None and "{document_id}" in path:
            found |= {(method, path) for method in context.methods or ()}
    return found


def _filled(path: str, document: str, v1: str) -> str:
    query = _QUERIES.get(path, "").replace("{v1}", v1)
    for name, value in (
        ("{document_id}", document),
        ("{page_no}", "1"),
        ("{word_key}", _CHANGE),
        ("{line_key}", _CHANGE),
    ):
        path = path.replace(name, value)
    while "{" in path:
        start = path.index("{")
        path = path[:start] + ANY_ID + path[path.index("}") + 1 :]
    return path + query


def test_every_route_of_a_file_is_classified_and_refuses_a_locked_one(
    locked: tuple[Api, Org, str, str],
) -> None:
    api, org, v1, v2 = locked
    assert _templates(api) == REFUSED | OPENED | ALLOWED | ADMINS
    plain, member = org.people["plain"], org.people["member"]
    api.storage.page_images[(org.c, v2, 1)] = b"RIFF made-up page"
    for checked in (False, True):  # PR 13: a live 10-minute check opens nothing REFUSED
        if checked:
            opened = plain.client.post(
                "/api/documents/locked-open", json={"password": PASSWORD}, headers=CSRF
            )
            assert opened.status_code == 200, opened.text
        for method, path in sorted(REFUSED | OPENED):
            body = _BODIES.get(path)
            reply = plain.client.request(method, _filled(path, v2, v1), json=body, headers=CSRF)
            if (method, path) in OPENED:
                code = None if reply.status_code < 400 else reply.json()["error"]["code"]
                assert (reply.status_code < 400) if checked else code == "locked_open_required", (
                    path,
                    reply.text,
                )
                continue
            assert reply.status_code == 409 and reply.json()["error"]["code"] == "locked", (
                method,
                path,
                reply.text,
            )
            for who in (member, org.people["admin"]):
                hidden = who.client.request(method, _filled(path, v2, v1), json=body, headers=CSRF)
                never = who.client.request(method, _filled(path, ANY_ID, v1), json=body, headers=CSRF)
                assert (hidden.status_code, hidden.content) == (never.status_code, never.content), (
                    method,
                    path,
                )
    for method, path in sorted(ALLOWED):
        if method == "GET":
            reply = plain.client.request(method, _filled(path, v2, v1), headers=CSRF)
            assert reply.status_code < 400, (path, reply.text)


def test_a_version_change_on_a_locked_family_says_unlock_it_first(locked: tuple[Api, Org, str, str]) -> None:
    _, org, v1, v2 = locked
    plain = org.people["plain"]
    for path, body in (("make-current", None), ("version-undo", None), ("separate", {"name": "x.pdf"})):
        status, reply = _call(plain, "POST", f"/api/documents/{v1}/{path}", body)
        assert (status, reply["error"]["code"], reply["error"]["message"]) == (
            409, "locked", "This file is locked. Unlock it first.",
        ), path  # fmt: skip
    assert v2


# --- lock, unlock and grants: the routes, their audit and their jobs -----------------------------


def test_locking_is_an_admins_with_the_password_audited_and_raises_the_access_version(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    file = org.files["loose"]
    api.add_page(org.c, file)
    admin, plain = org.people["admin"], org.people["plain"]
    assert _call(plain, "POST", f"/api/documents/{file}/lock", {"password": PASSWORD})[0] == 403
    assert (
        _call(
            org.people["member"], "POST", f"/api/documents/{org.files['sal']}/lock", {"password": PASSWORD}
        )[0]
        == 404
    )
    status, reply = _call(admin, "POST", f"/api/documents/{file}/lock", {"password": "not the password"})
    assert (status, reply["error"]["code"]) == (400, "wrong_password")
    before = _version(db, org.c)
    status, reply = _lock(admin, file)  # open_to left out: the Admin alone (ticked by default)
    assert (status, reply) == (200, {"locked": True, "versions": 1, "open_to": 1})
    assert _version(db, org.c) > before
    assert [row[1:3] for row in _audit(db, org.c, "lock.lock")] == [("document", file)]
    assert _audit(db, org.c, "lock.lock")[0][4] == {"versions": 1, "open_to": 1}
    grants = _owner(db, "SELECT user_id::text FROM lock_grants WHERE document_id = :d", {"d": file})
    assert [row[0] for row in grants] == [admin.user_id]
    status, reply = _lock(admin, file)
    assert (status, reply["error"]["code"]) == (409, "already_locked")
    assert api.queue.jobs == []  # pictured already: nothing to prepare


def test_unlocking_reads_the_current_version_again_billed_to_the_admin_past_the_hourly_limit(
    locked: tuple[Api, Org, str, str],
) -> None:
    api, org, v1, v2 = locked
    db, admin = api.db, org.people["admin"]
    # The hourly limit used up: an unlock's read is never counted against it (p2:31).
    for _ in range(api.settings.reprocess_per_hour):
        _owner(
            db,
            "INSERT INTO audit_log (customer_id, action, target_type, target_id)"
            " VALUES (:c, 'document.reprocess', 'document', :d)",
            {"c": org.c, "d": org.files["top"]},
        )
    status, reply = _call(admin, "POST", f"/api/documents/{v2}/unlock", {"password": PASSWORD})
    assert (status, reply) == (200, {"unlocked": True, "reading": True})
    rows = {
        str(row[0]): tuple(row[1:])
        for row in _owner(
            db,
            "SELECT id, status, failure_code, read_requested_by::text, locked_at IS NULL FROM documents"
            " WHERE id IN (:a, :b)",
            {"a": v1, "b": v2},
        )
    }
    assert rows == {v2: ("received", None, admin.user_id, True), v1: ("failed", "unlocked", None, True)}
    assert _count(db, "lock_grants", [v1, v2]) == 0
    assert [job.document_id for job in api.queue.jobs] == [v2]
    assert [row[2] for row in _audit(db, org.c, "lock.unlock")] == [v2]
    assert _audit(db, org.c, "document.reprocess")[-1][2:] == (v2, None, {"via": "unlock"})
    status, reply = _call(admin, "POST", f"/api/documents/{v2}/unlock", {"password": PASSWORD})
    assert (status, reply["error"]["code"]) == (409, "not_locked")


def test_a_lock_grant_is_given_and_taken_on_every_version_and_audited(
    locked: tuple[Api, Org, str, str],
) -> None:
    api, org, v1, v2 = locked
    db, admin, member = api.db, org.people["admin"], org.people["member"]
    url = f"/api/documents/{v2}/lock-grants/{member.user_id}"
    assert _call(admin, "PUT", url, {})[1] == {"changed": True}
    assert _count(db, "lock_grants", [v1, v2]) == 4  # plain and the member, on both versions
    assert _audit(db, org.c, "lock.grant")[-1][2:4] == (v2, member.user_id)
    assert (
        _call(admin, "PUT", f"/api/documents/{v2}/lock-grants/{admin.user_id}", {})[0] == 403
    )  # self: password
    assert (
        _call(admin, "PUT", f"/api/documents/{v2}/lock-grants/{admin.user_id}", {"password": PASSWORD})[0]
        == 200
    )
    names = {
        each["user"]["id"] for each in _call(admin, "GET", f"/api/documents/{v2}/lock-grants")[1]["items"]
    }
    assert names == {org.people["plain"].user_id, member.user_id, admin.user_id}
    assert _call(admin, "DELETE", url)[0] == 204
    assert _call(admin, "DELETE", url)[0] == 404
    assert _audit(db, org.c, "lock.revoke")[-1][2:4] == (v2, member.user_id)
    loose = org.files["loose"]
    status, reply = _call(admin, "PUT", f"/api/documents/{loose}/lock-grants/{member.user_id}", {})
    assert (status, reply["error"]["code"]) == (409, "not_locked")


def test_giving_a_locked_file_also_gives_its_lock(locked: tuple[Api, Org, str, str]) -> None:
    api, org, v1, v2 = locked
    admin, member = org.people["admin"], org.people["member"]
    assert _call(admin, "PUT", f"/api/documents/{v2}/shares/{member.user_id}", {})[0] == 200
    held = _owner(api.db, "SELECT count(*) FROM lock_grants WHERE user_id = :u", {"u": member.user_id})
    assert held[0][0] == 2
    assert member.client.get(f"/api/documents/{v2}", headers=CSRF).status_code == 200
    # Taking the file back takes its lock grants too (fails closed).
    assert _call(admin, "DELETE", f"/api/documents/{v2}/shares/{member.user_id}")[0] == 204
    held = _owner(api.db, "SELECT count(*) FROM lock_grants WHERE user_id = :u", {"u": member.user_id})
    assert held[0][0] == 0 and v1


def test_leaving_a_department_or_a_private_folder_takes_its_lock_grants(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    c, admin = org.c, org.people["admin"]
    fin, sal, member, named = org.files["fin"], org.files["sal"], org.people["member"], org.people["named"]
    for file in (fin, sal):
        api.add_page(c, file)
    assert _lock(admin, fin, [member.user_id])[0] == 200
    assert _lock(admin, sal, [named.user_id, member.user_id])[0] == 200
    # A file given to the member directly keeps its grant when they leave the department.
    _owner(
        db,
        "INSERT INTO document_shares (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :u)",
        {"c": c, "d": sal, "u": member.user_id},
    )
    _owner(db, "DELETE FROM department_members WHERE user_id = :u", {"u": member.user_id})
    left = _owner(db, "SELECT document_id::text FROM lock_grants WHERE user_id = :u", {"u": member.user_id})
    assert [row[0] for row in left] == [sal]
    _owner(db, "DELETE FROM private_folder_members WHERE user_id = :u", {"u": named.user_id})
    assert _owner(db, "SELECT count(*) FROM lock_grants WHERE user_id = :u", {"u": named.user_id})[0][0] == 0


def test_a_word_or_powerpoint_files_pdf_goes_with_its_lock(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    deck = api.add_document(org.c, name="deck.pptx", detected_type="pptx", folder_id=org.folders["sales"])
    api.add_page(org.c, deck)
    api.storage.put_rendition(org.c, deck, b"%PDF-1.7 words")
    assert _lock(org.people["admin"], deck)[0] == 200
    assert (org.c, deck) not in api.storage.renditions


# --- late writes the database refuses ------------------------------------------------------------


def test_a_locked_versions_content_is_never_written(locked: tuple[Api, Org, str, str]) -> None:
    api, org, _, v2 = locked
    db = api.db
    with pytest.raises(DBAPIError) as refused, db.customer(org.c) as conn:
        conn.execute(
            text(
                "INSERT INTO pieces (customer_id, document_id, seq, page_from, page_to, text, regions, label)"
                " VALUES (:c, :d, 0, 1, 1, 'Zebra again', '[{\"page\": 1}]', 'verified')"
            ),
            {"c": org.c, "d": v2},
        )
    assert getattr(refused.value.orig, "sqlstate", None) == "23514"
    PgPageReads(db).put(PageKey(org.c, v2, 1, _SHA, "m", "g2"), {"lines": ["Zebra"]}, 1, 1)
    assert _count(db, "page_reads", [v2]) == 0  # the page cache writes nothing for a locked file
    with pytest.raises(DBAPIError), db.customer(org.c) as conn:  # a card on a locked version
        conn.execute(text("UPDATE documents SET summary = 'Zebra' WHERE id = :d"), {"d": v2})


def test_an_ensemble_run_stops_once_its_file_is_locked_or_its_run_gone(
    locked: tuple[Api, Org, str, str],
) -> None:
    from kaagaz.db import ensemble as ensemble_sql

    api, org, _, _ = locked
    with api.db.customer(org.c) as conn:
        assert ensemble_sql.stop_asked(conn, org.c, str(uuid.uuid4())) is True  # gone: the lock deleted it


# --- Ask ----------------------------------------------------------------------------------------


def _cites(document: str) -> dict[str, Any]:
    reply = answer_json()
    reply["citations"] = [
        {"n": 1, "document_id": document, "page_no": 1, "snippet": "Zebra 1,200", "regions": []}
    ]
    reply["sentences"] = [{"text": "Zebra paid 1,200.", "citations": [1]}]
    return reply


def test_an_old_answer_from_a_file_now_locked_is_replaced_whole_and_the_line_shows_to_openers(
    db: Database,
) -> None:
    api = Api(db)
    org = _walled(api)
    file, plain, member = org.files["sales"], org.people["plain"], org.people["member"]
    api.add_page(org.c, file)
    api.answers.reply = _cites(file)
    asked = {}
    for name, who in (("plain", plain), ("member", member)):
        reply = who.client.post("/api/chat", json={"question": "who paid zebra?"}, headers=CSRF).json()
        # The plain reviewer may open the organisation's other locked file already; the member none.
        assert reply["locked_note"] == (LOCKED_NOTE if name == "plain" else None)
        asked[name] = reply["conversation_id"]
    assert _lock(org.people["admin"], file, [plain.user_id])[0] == 200
    for name, who in (("plain", plain), ("member", member)):
        shown = who.client.get(f"/api/conversations/{asked[name]}", headers=CSRF).json()
        answer = shown["messages"][-1]
        # Changed on purpose (PR 8's states through the view, #602): a person who may not open it
        # gets the line of any hidden source; the person given it, the locked line.
        line = LOCKED_LINE if name == "plain" else NO_ACCESS_LINE
        assert answer["answer"]["status"] == "no_access" and answer["text"] == line
        assert "Zebra" not in json.dumps(shown)
        assert shown["locked_note"] == (LOCKED_NOTE if name == "plain" else None)  # never saved
    saved = _owner(db, "SELECT count(*) FROM messages WHERE answer::text LIKE '%locked%'", {})
    assert saved[0][0] == 0
    # A follow-up gets nothing of it: the turn is its question only, no document cited.
    api.answers.reply = answer_json()
    reply = plain.client.post(
        "/api/chat", json={"question": "and then?", "conversation_id": asked["plain"]}, headers=CSRF
    ).json()
    assert reply["locked_note"] == LOCKED_NOTE
    assert api.answers.cited[-1] == [] and all(turn.answer is None for turn in api.answers.histories[-1])


def test_ask_never_reads_a_locked_file_for_anyone(db: Database) -> None:
    """The red team: a total and a search before and after the lock; the locked bill's words and
    amount are gone from every answer, for the organisation as a whole (what Ask reads)."""
    api = Api(db)
    org = _walled(api)
    c = org.c
    add_bill(db, c, "zebra.pdf", "Invoice No: Z-777", "Grand Total: 7,77,777")
    add_bill(db, c, "other.pdf", "Invoice No: B-1", "Grand Total: 1,000")
    zebra = _owner(
        db, "SELECT id::text FROM documents WHERE customer_id = :c AND display_name = 'zebra.pdf'", {"c": c}
    )[0][0]
    _owner(
        db,
        "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, has_image)"
        " VALUES (:c, :d, 1, 595, 842, 'pt', true)"
        " ON CONFLICT (document_id, page_no) DO UPDATE SET has_image = true",
        {"c": c, "d": zebra},
    )
    _owner(
        db,
        "UPDATE documents SET page_count = (SELECT count(*) FROM pages WHERE document_id = :d) WHERE id = :d",
        {"d": zebra},
    )
    service = AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)
    assert "7,78,777" in json.dumps(service.answer(c, TOTAL).to_json())  # both, before
    scope = Scope(document_ids=(zebra,))  # Ask scoped to the one file (the /chat scope)
    assert "7,77,777" in json.dumps(service.answer(c, TOTAL, scope=scope).to_json())
    with _app(db).user(c, org.people["admin"].user_id) as conn:
        assert not locks_sql.lock(conn, [zebra], []).refused
    after = json.dumps(service.answer(c, TOTAL).to_json())
    assert "7,77,777" not in after and "Z-777" not in after and "zebra" not in after and zebra not in after
    scoped = json.dumps(service.answer(c, TOTAL, scope=scope).to_json())
    assert "7,77,777" not in scoped and "Z-777" not in scoped and zebra not in scoped
    assert _count(db, "extracted_values", [zebra]) == 0


def test_a_duplicate_of_a_locked_file_says_so_only_to_those_given_it(
    locked: tuple[Api, Org, str, str],
) -> None:
    api, org, _, v2 = locked
    data = api.storage.get(org.c, v2)
    # The copy is found by its bytes' fingerprint (add_document makes one up).
    _owner(
        api.db,
        "UPDATE documents SET sha256 = :h WHERE id = :d",
        {"h": hashlib.sha256(data).hexdigest(), "d": v2},
    )
    for name, expected in (("plain", {"id": v2}), ("member", None)):
        reply = org.people[name].client.post(
            "/api/documents", files={"file": ("again.pdf", data, "application/pdf")}, headers=CSRF
        )
        body = reply.json()
        assert body["duplicate"] is True and body["document"] is None
        assert body.get("locked") == expected, name


# --- review fixes (15 Nov 2026) -------------------------------------------------------------------


def test_a_correction_remembered_on_review_names_its_file_and_goes_with_the_lock(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    c, file, plain = org.c, org.files["sales"], org.people["plain"]
    api.add_page(c, file)
    total = api.add_value(c, file, raw="1,2OO", value="1,2OO")
    number = api.add_value(c, file, field_name="invoice_number", raw="Z-1O", value="Z-1O")
    for value, typed in ((total, "1,200"), (number, "Z-10")):
        body = {"choice": "typed", "value": typed, "remember": True}
        reply = plain.client.post(f"/api/review/{value}", json=body, headers=CSRF)
        assert reply.status_code == 200, reply.text
    learned = (
        "SELECT field, source_document_id::text FROM learned_corrections WHERE customer_id = :c"
        " ORDER BY field"
    )
    rows = [tuple(row) for row in _owner(db, learned, {"c": c})]
    assert rows == [("invoice_number", file), ("total_amount", file)]
    # One remembered before a correction named its file (0084's old rows), and another file's old row.
    _owner(
        db,
        "UPDATE learned_corrections SET source_document_id = NULL WHERE customer_id = :c"
        " AND field = 'invoice_number'",
        {"c": c},
    )
    _owner(
        db,
        "INSERT INTO learned_corrections (customer_id, field, raw_value, corrected_value, created_by)"
        " VALUES (:c, 'invoice_number', 'Q-1', 'Q-2', :u)",
        {"c": c, "u": org.people["admin"].user_id},
    )
    assert _lock(org.people["admin"], file)[0] == 200
    left = _owner(db, "SELECT corrected_value FROM learned_corrections WHERE customer_id = :c", {"c": c})
    assert [row[0] for row in left] == ["Q-2"]


def test_a_rejected_version_keeps_its_code_when_locked(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    file = org.files["loose"]
    api.add_page(org.c, file)
    rejected = api.add_document(
        org.c,
        name="loose v2.pdf",
        folder_id=org.folders["loose"],
        family_id=file,
        version_no=2,
        is_current=False,
    )
    _owner(
        db,
        "UPDATE documents SET status = 'rejected', failure_code = 'infected' WHERE id = :d",
        {"d": rejected},
    )
    assert _lock(org.people["admin"], file)[0] == 200
    state = "SELECT status, failure_code, locked_at IS NOT NULL FROM documents WHERE id = :d"
    assert tuple(_owner(db, state, {"d": rejected})[0]) == ("rejected", "infected", True)


def test_a_link_to_a_locked_file_or_a_suggestion_of_its_family_is_never_written(
    locked: tuple[Api, Org, str, str],
) -> None:
    api, org, _, v2 = locked
    for statement in (
        "INSERT INTO document_links (customer_id, document_id, linked_document_id, identifier_kind,"
        " identifier_value) VALUES (:c, :o, :d, 'invoice_number', 'Z-2')",
        "INSERT INTO version_suggestions (customer_id, document_id, candidate_family_id, outcome, status)"
        " SELECT :c, :o, family_id, 'suggested', 'pending' FROM documents WHERE id = :d",
    ):
        with pytest.raises(DBAPIError) as refused:
            _owner(api.db, statement, {"c": org.c, "o": org.files["top"], "d": v2})
        assert getattr(refused.value.orig, "sqlstate", None) == "23514", statement


def test_storage_down_rolls_a_lock_back_and_a_queue_down_leaves_its_job_to_the_sweep(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admin = org.people["admin"]
    deck = api.add_document(org.c, name="deck.pptx", detected_type="pptx", folder_id=org.folders["sales"])
    api.add_page(org.c, deck)
    api.storage.put_rendition(org.c, deck, b"%PDF-1.7 words")
    api.storage.fail_delete = True
    assert _lock(admin, deck)[0] == 503
    state = _owner(db, "SELECT locked_at IS NULL, status FROM documents WHERE id = :d", {"d": deck})
    assert tuple(state[0]) == (True, "ready") and (org.c, deck) in api.storage.renditions
    assert _audit(db, org.c, "lock.lock") == []
    api.storage.fail_delete = False
    # The queue down after the commit: the lock stands, and the stuck sweep finds its prepare job.
    reading = api.add_document(org.c, name="reading.pdf", status="clean", folder_id=org.folders["sales"])
    api.queue.fail_enqueue = True
    assert _lock(admin, reading)[0] == 200
    state = _owner(db, "SELECT locked_at IS NOT NULL, status FROM documents WHERE id = :d", {"d": reading})
    assert tuple(state[0]) == (True, "received") and api.queue.jobs == []
    with db.customer(org.c) as conn:
        assert reading in {found for found, _ in stuck_documents(conn, org.c, stale_seconds=0, limit=50)}


def test_a_bulk_lock_and_unlock_reply_with_their_counts_and_audit_each_file(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admin, files = org.people["admin"], sorted([org.files["loose"], org.files["top"]])
    for file in files:
        api.add_page(org.c, file)
    reply = _call(admin, "POST", "/api/documents/lock", {"document_ids": files, "password": PASSWORD})
    assert reply == (200, {"locked": 2})
    locks = _audit(db, org.c, "lock.lock")
    assert sorted(row[2] for row in locks) == files
    assert all(row[4] == {"versions": 1, "open_to": 1, "batch": 2} for row in locks)
    reply = _call(admin, "POST", "/api/documents/unlock", {"document_ids": files, "password": PASSWORD})
    assert reply == (200, {"unlocked": 2})
    unlocks = _audit(db, org.c, "lock.unlock")
    assert sorted(row[2] for row in unlocks) == files
    assert all(row[4] == {"versions": 1, "batch": 2} for row in unlocks)
    reads = [row for row in _audit(db, org.c, "document.reprocess") if row[4] == {"via": "unlock"}]
    assert sorted(row[2] for row in reads) == files
    assert sorted(job.document_id for job in api.queue.jobs) == files


def test_a_bulk_unlock_with_one_refused_file_changes_nothing(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admin = org.people["admin"]
    kept, trashed, open_file = org.files["loose"], org.files["top"], org.files["sales"]
    for file in (kept, trashed):
        api.add_page(org.c, file)
        assert _lock(admin, file)[0] == 200
    assert _call(admin, "DELETE", f"/api/documents/{trashed}")[0] < 300  # Trash keeps the lock
    body = {"document_ids": [kept, trashed, open_file], "password": PASSWORD}
    status, reply = _call(admin, "POST", "/api/documents/unlock", body)
    refused = [{"id": trashed, "reason": "in_trash"}, {"id": open_file, "reason": "not_locked"}]
    assert status == 409 and reply["error"]["details"]["refused"] == sorted(
        refused, key=lambda each: each["id"]
    )
    still = "SELECT count(*) FROM documents WHERE id IN (:a, :b) AND locked_at IS NOT NULL"
    assert _owner(db, still, {"a": kept, "b": trashed})[0][0] == 2
    assert _audit(db, org.c, "lock.unlock") == [] and api.queue.jobs == []


def test_an_unlocks_read_is_queued_after_it_commits_so_the_worker_reads_it(
    locked: tuple[Api, Org, str, str],
) -> None:
    """A worker that takes the job at once reads what is committed: before the fix it found the
    version still locked (settled) and acked the job unread."""
    api, org, _, v2 = locked
    seen: list[tuple[Any, bool | None]] = []
    enqueue = api.queue.enqueue

    def worker_takes_it(job: Job, *, express: bool = False) -> None:
        worker = PgDocuments(api.db)  # the worker's own session, as DocumentProcessor.__call__ starts
        record = worker.get(job.customer_id, job.document_id)
        status = worker.get_status(job.customer_id, job.document_id)
        seen.append((status, None if record is None else record.locked))
        enqueue(job, express=express)

    body = {"document_ids": [v2], "password": PASSWORD}
    with mock.patch.object(api.queue, "enqueue", worker_takes_it):
        assert _call(org.people["admin"], "POST", "/api/documents/unlock", body) == (200, {"unlocked": 1})
    assert seen == [(None, False)]  # received (not settled) and not locked: it is read


def test_an_ordinary_read_again_still_works_after_a_bulk_unlock(db: Database) -> None:
    """p2:31: an unlock's read is never counted on the hourly Read again limit, after it either."""
    api = Api(db, settings=make_settings(reprocess_per_hour=1))
    org = _walled(api)
    admin = org.people["admin"]
    files = [org.files["loose"], org.files["top"]]
    for file in files:
        api.add_page(org.c, file)
        assert _lock(admin, file)[0] == 200
    body = {"document_ids": files, "password": PASSWORD}
    assert _call(admin, "POST", "/api/documents/unlock", body)[0] == 200
    status, reply = _call(admin, "POST", f"/api/documents/{org.files['sales']}/reprocess")
    assert status == 200, reply


def test_a_locked_version_whose_preparing_failed_is_prepared_again_on_retry(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    file, plain = org.files["sales"], org.people["plain"]
    _owner(db, "UPDATE documents SET status = 'clean' WHERE id = :d", {"d": file})  # no picture yet
    assert _lock(org.people["admin"], file, [plain.user_id])[0] == 200
    failed = "UPDATE documents SET status = 'failed', failure_code = 'stuck_no_job' WHERE id = :d"
    _owner(db, failed, {"d": file})
    listed = {row["id"]: row for row in plain.client.get("/api/documents", headers=CSRF).json()["items"]}
    assert (
        listed[file]["status_message"]
        == "Locked. Its page pictures couldn't be made yet. Retry to try again."
    )
    api.queue.jobs.clear()
    status, reply = _call(plain, "POST", f"/api/documents/{file}/reprocess")
    assert status == 200, reply
    state = _owner(db, "SELECT status, locked_at IS NOT NULL FROM documents WHERE id = :d", {"d": file})
    assert tuple(state[0]) == ("received", True)  # the worker prepares it, never reads it
    assert [job.document_id for job in api.queue.jobs] == [file]
    # Read again of a locked version that has not failed is still refused.
    _owner(db, "UPDATE documents SET status = 'clean' WHERE id = :d", {"d": file})
    status, reply = _call(plain, "POST", f"/api/documents/{file}/reprocess")
    assert (status, reply["error"]["code"]) == (409, "locked")


def test_the_allowed_writes_work_on_a_locked_file(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admin, file = org.people["admin"], org.files["loose"]
    api.add_page(org.c, file)
    assert _lock(admin, file)[0] == 200  # the Admin may open it
    assert _call(admin, "PATCH", f"/api/documents/{file}", {"display_name": "renamed.pdf"})[0] == 200
    tag = api.add_tag(org.c, admin.user_id, "deeds")
    assert _call(admin, "PUT", f"/api/documents/{file}/tags", {"tag_ids": [tag]})[0] == 200
    assert _call(admin, "DELETE", f"/api/documents/{file}")[0] < 300
    assert _call(admin, "POST", f"/api/documents/{file}/restore")[0] == 200
    files = {"file": ("v2.pdf", b"%PDF-1.7\n" + b"2" * 200, "application/pdf")}
    reply = admin.client.post(f"/api/documents/{file}/versions", files=files, headers=CSRF)
    assert reply.status_code == 201, reply.text
    row = _owner(db, "SELECT display_name, locked_at IS NOT NULL FROM documents WHERE id = :d", {"d": file})
    assert tuple(row[0]) == ("renamed.pdf", True)
    versions = "SELECT bool_and(locked_at IS NOT NULL), count(*) FROM documents WHERE family_id = :d"
    assert tuple(_owner(db, versions, {"d": file})[0]) == (True, 2)  # the new version locked from the start


def test_routes_that_name_a_locked_file_in_their_body_refuse_it(locked: tuple[Api, Org, str, str]) -> None:
    _, org, _, v2 = locked
    plain, member = org.people["plain"], org.people["member"]
    region = {"kind": "box", "page": 1, "box": [0.1, 0.1, 0.2, 0.2]}
    word = {"word": "zebu", "meaning": "a cow", "spot": {"document_id": v2, "page_no": 1, "region": region}}
    status, reply = _call(plain, "POST", "/api/me/words", word)
    assert (status, reply["error"]["code"]) == (409, "locked")
    never = {**word, "spot": {"document_id": ANY_ID, "page_no": 1, "region": region}}
    hidden, missing = (member.client.post("/api/me/words", json=each, headers=CSRF) for each in (word, never))
    assert hidden.status_code == 404 and (hidden.status_code, hidden.content) == (
        missing.status_code,
        missing.content,
    )
    # A source put on top of an answer: never a locked file, even for a person who may open it.
    asked = plain.client.post("/api/chat", json={"question": "who paid zebra?"}, headers=CSRF).json()
    pin = {"document_id": v2, "page_no": 1, "previous_rank": 2}
    assert _call(plain, "POST", f"/api/messages/{asked['answer']['id']}/pin", pin)[0] == 404
    # Search inside one locked file, and Ask scoped to it: as for an id that never was.
    for who in (plain, member):
        hidden = who.client.get(f"/api/search?q=Zebra&document_ids={v2}", headers=CSRF)
        missing = who.client.get(f"/api/search?q=Zebra&document_ids={ANY_ID}", headers=CSRF)
        assert hidden.status_code == 200 and hidden.json() == missing.json()


def test_a_version_suggestion_of_a_locked_family_goes_with_the_lock(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admin, file = org.people["admin"], org.files["loose"]
    api.add_page(org.c, file)
    suggestion = _owner(
        db,
        "INSERT INTO version_suggestions (customer_id, document_id, candidate_family_id, outcome, status)"
        " SELECT :c, :o, family_id, 'suggested', 'pending' FROM documents WHERE id = :d RETURNING id",
        {"c": org.c, "o": org.files["top"], "d": file},
    )[0][0]
    assert _lock(admin, file)[0] == 200
    assert _call(admin, "POST", f"/api/version-suggestions/{suggestion}/accept", {})[0] == 404


def test_lock_unlock_and_a_locked_copy_work_with_the_database_rule_on(db: Database) -> None:
    """PR 9a's per-person rule on (whatever the session's default): the Admin locks and unlocks a
    file they may not open, its rows narrowed to them by the rule; a hidden locked file answers as
    an id that never was; a copy of it names it to the person given it and hides it from the rest."""
    with rule_on():
        api = Api(db)
        org = _walled(api)
        admin, plain, member = org.people["admin"], org.people["plain"], org.people["member"]
        v1, v2 = _family(api, org)
        assert _lock(admin, v2, [plain.user_id]) == (200, {"locked": True, "versions": 2, "open_to": 1})
        assert _count(db, "pieces", [v1, v2]) == 0 and _count(db, "lock_grants", [v1, v2]) == 2
        for who in (member, admin):
            _same_as_random(who, "GET", "/api/documents/{id}", v2)
            _same_as_random(who, "GET", "/api/documents/{id}/pages/1/image", v2)
        assert plain.client.get(f"/api/documents/{v2}", headers=CSRF).json()["locked"] is True
        data = api.storage.get(org.c, v2)
        sha = hashlib.sha256(data).hexdigest()
        _owner(db, "UPDATE documents SET sha256 = :h WHERE id = :d", {"h": sha, "d": v2})
        for name, expected in (("plain", {"id": v2}), ("member", None)):
            files = {"file": ("again.pdf", data, "application/pdf")}
            body = org.people[name].client.post("/api/documents", files=files, headers=CSRF).json()
            assert (
                body["duplicate"] is True and body["document"] is None and body.get("locked") == expected
            ), name
        status, reply = _call(admin, "POST", f"/api/documents/{v2}/unlock", {"password": PASSWORD})
        assert (status, reply) == (200, {"unlocked": True, "reading": True})
        state = "SELECT count(*) FROM documents WHERE id IN (:a, :b) AND locked_at IS NULL"
        assert _owner(db, state, {"a": v1, "b": v2})[0][0] == 2
        assert _count(db, "lock_grants", [v1, v2]) == 0
