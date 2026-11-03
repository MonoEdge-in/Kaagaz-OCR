"""Deleting an organisation against PostgreSQL (super admin PR 4, 0018): scheduling and
cancelling; after the grace period every row and both storage prefixes of A go, and B's
rows and files stay byte-for-byte the same, except A's audit rows, kept for 365 days where no
role can read them (0068, #482), and its open uploads, aborted. ``KeyStorage`` keys files as
``S3Storage`` does. The purge covers every due customer of the shared test database; asserts look at
A and B only."""

import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import IO

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from kaagaz.adapters.storage import original_key, page_image_key, quarantine_key, resumable_key
from kaagaz.api import totp
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobNotFound
from kaagaz.jobs.purge import forget_kept_audit, purge_customer, purge_deleted_customers
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, insert_folder, insert_tag, migrate, tag_document
from tests.test_api_support import CSRF, Api, TestClient, wrong_code
from tests.test_document_stories_db import put
from tests.test_operator_auth_db import _Operator, _owner
from tests.test_operator_orgs_db import ORGS, _platform, _signed_in

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
FUNCTIONS = {
    "kaagaz_op_schedule_delete",
    "kaagaz_op_cancel_delete",
    "kaagaz_customer_ids_to_purge",
    "kaagaz_finish_customer_delete",
}
# The grace period ends: what seven days do, as the table owner.
DUE = "UPDATE customers SET delete_after = now() - interval '1 minute' WHERE id = :c"
# Per table with customer_id (from the catalog): "rows:md5 of the rows", as the owner,
# which row level security does not filter. Static SQL: format() quotes the names.
_SNAPSHOT = text(
    "SELECT c.relname::text, (xpath('/row/h/text()', query_to_xml(format("
    "'SELECT count(*) || '':'' || coalesce(md5(string_agg(t::text, '','' ORDER BY t::text)), '''') AS h"
    " FROM public.%I t WHERE t.customer_id = %L', c.relname, CAST(:c AS text)), false, true, '')))[1]::text"
    " FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid"
    " WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')"
    " AND a.attname = 'customer_id' AND NOT a.attisdropped"
    " UNION ALL SELECT 'customers', count(*) || ':' || coalesce(md5(string_agg(c::text, ',')), '')"
    " FROM customers c WHERE c.id = CAST(:c AS uuid)"
)
# Every key into a table 0018's finish deletes from that does not cascade: (table, parent).
# Each table on the left is emptied before its parent (or has no row left by then).
NOT_CASCADING = {
    *[(table, "users") for table in ("documents", "corrections", "learned_corrections", "conversations")],
    *[(table, "users") for table in ("feedback", "folders", "tags")],
    ("documents", "folders"),
    ("folders", "folders"),
    # 0023: the ledger keeps its rows (SET NULL) until the customer's delete cascades them.
    ("model_usage", "users"),
    ("model_usage", "conversations"),
    # 0043: organisation-wide word suggestions have no document; the finish deletes them (0047).
    ("organization_word_suggestions", "users"),
}
NO_ACTION_TO_USERS = {table for table, _ in NOT_CASCADING}
# Keys whose rows are gone before the finish runs: they cascade from documents, and the
# finish refuses while a document is left (0018); or they SET NULL, as the ledger does (0038).
GONE_FIRST = {
    *[(table, "users") for table in ("pages", "ensemble_runs", "ensemble_readings", "word_changes")],
    ("ai_task_settings", "users"),
    ("learning_removals", "users"),  # 0051, PRD 6 F3: cascades from its document, SET NULL to its admin
    ("user_identities", "users"),  # 0062, #457: cascades with its user, SET NULL to the admin who approved it
}


class KeyStorage:
    """Files by their S3 keys. A document's delete removes its prefix and its quarantined
    copy, a customer's both of its prefixes (as ``S3Storage``). ``failing`` documents'
    deletes and, with ``prefixes_down``, the customer's fail as storage that is down."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.failing: set[str] = set()
        self.prefixes_down = False
        self.aborted: list[tuple[str, str]] = []  # (key, upload id) of each resumable upload aborted

    def put(self, customer_id: str, document_id: str, data: IO[bytes]) -> None:
        self.objects[original_key(customer_id, document_id)] = data.read()

    def get(self, customer_id: str, document_id: str) -> bytes:
        try:
            return self.objects[original_key(customer_id, document_id)]
        except KeyError:
            raise BlobNotFound(document_id) from None

    def delete(self, customer_id: str, document_id: str) -> None:
        if document_id in self.failing:
            raise BackendUnavailable("storage")
        prefix = original_key(customer_id, document_id).removesuffix("original")
        self._drop((prefix, quarantine_key(customer_id, document_id)))

    def delete_customer(self, customer_id: str) -> None:
        if self.prefixes_down:
            raise BackendUnavailable("storage")
        self._drop((f"customers/{customer_id}/", f"quarantine/{customer_id}/"))

    # CustomerStorage's resumable upload parts (#222); this purge never starts an upload.
    def abort_resumable(self, key: str, upload_id: str) -> None:
        self.aborted.append((key, upload_id))
        self.objects.pop(key, None)

    def delete_resumable(self, key: str) -> None:
        self.objects.pop(key, None)

    def listing(self, customer_id: str) -> list[tuple[str, bytes]]:
        prefixes = (f"customers/{customer_id}/", f"quarantine/{customer_id}/")
        return sorted((key, data) for key, data in self.objects.items() if key.startswith(prefixes))

    def _drop(self, prefixes: tuple[str, ...]) -> None:
        for key in [key for key in self.objects if key.startswith(prefixes)]:
            del self.objects[key]


def _now() -> datetime:
    return datetime.now(UTC)


def _always() -> bool:
    """A purge with no time limit."""
    return True


def _code(api: Api, operator: _Operator) -> str:
    """A code of a step not used yet."""
    api.clock.advance(totp.STEP_SECONDS)
    return api.code(operator.secret)


def _snapshot(db: Database, customer_id: str) -> dict[str, str]:
    with db.engine.begin() as conn:
        return {str(name): str(value) for name, value in conn.execute(_SNAPSHOT, {"c": customer_id})}


def _organisation(api: Api, storage: KeyStorage, label: str) -> tuple[str, str, list[str]]:
    """An organisation with a row in every kind of table a customer fills, and files under
    both prefixes: (id, name, its documents)."""
    name = f"Org {label} {uuid.uuid4().hex[:8]}"
    customer, admin = api.customer(name)
    member, _ = api.add_user(customer, "member")
    session = str(uuid.uuid4())
    upload = resumable_key(customer, member, session)
    with api.db.user(customer, member) as conn:  # the member's own rows (my words) too
        folder = insert_folder(conn, customer, member, "GST")
        tag = insert_tag(conn, customer, member, "Paid")
        first = insert_document(conn, customer, folder_id=folder, uploaded_by=member)
        second = insert_document(conn, customer, family_id=first, version_no=2, is_current=False)
        trashed = insert_document(conn, customer, deleted_at=_now(), deleted_by=member)
        tag_document(conn, customer, first, tag)
        for statement in (
            "INSERT INTO conversations (customer_id, user_id, title) VALUES (:c, :u, 'Q')",
            "INSERT INTO messages (customer_id, conversation_id, role, text)"
            " SELECT :c, id, 'user', 'hello' FROM conversations WHERE customer_id = :c",
            "INSERT INTO feedback (customer_id, message_id, user_id, rating)"
            " SELECT :c, id, :u, 'up' FROM messages WHERE customer_id = :c",
            "INSERT INTO learned_corrections (customer_id, field, raw_value, corrected_value, created_by)"
            " VALUES (:c, 'total', '1O0', '100', :u)",
            "INSERT INTO corrections (customer_id, document_id, field, old_value, new_value, choice,"
            " corrected_by) VALUES (:c, :d, 'total', '1O0', '100', 'typed', :u)",
            "INSERT INTO user_words (customer_id, user_id, word, word_fold, meaning, meaning_fold,"
            " source) VALUES (:c, :u, 'Daregaon', 'daregaon', 'village', 'village', 'manual')",
            "INSERT INTO organization_word_suggestions (customer_id, submitted_by, word, word_fold, meaning,"
            " meaning_fold) VALUES (:c, :u, 'Daregaon', 'daregaon', 'village', 'village')",
            "INSERT INTO model_usage (customer_id, user_id, document_id, conversation_id, kind, model, calls,"
            " input_tokens, output_tokens, reasoning_tokens, cost_usd) SELECT :c, :u, :d, id, 'chat',"
            " 'google/gemini-3.1-pro-preview', 1, 10, 5, 0, 0.01 FROM conversations WHERE customer_id = :c",
            # #482: an upload still open, whose parts no listing of the prefix shows, and a view.
            "INSERT INTO upload_sessions (id, customer_id, user_id, storage_key, storage_upload_id,"
            " file_size, part_count, metadata, expires_at) VALUES (:s, :c, :u, :k, :upload_id, 10, 1, '{}',"
            " now() + interval '7 days')",
            "INSERT INTO audit_log (customer_id, actor_user_id, action, target_type, target_id)"
            " VALUES (:c, :u, 'document.view', 'document', :d)",
        ):
            conn.execute(
                text(statement),
                {
                    "c": customer,
                    "u": member,
                    "d": first,
                    "s": session,
                    "k": upload,
                    "upload_id": f"upload-{customer}",
                },
            )
    put(api.db, customer, admin, first, f"The papers of {label}")
    documents = [first, second, trashed]
    for document in documents:
        storage.put(customer, document, io.BytesIO(f"{label} {document}".encode()))
        storage.objects[page_image_key(customer, document, 1)] = b"page " + label.encode()
    storage.objects[quarantine_key(customer, str(uuid.uuid4()))] = b"held " + label.encode()
    storage.objects[page_image_key(customer, str(uuid.uuid4()), 1)] = b"no row: a race"
    return customer, name, documents


def _schedule(api: Api, operator: _Operator, client: TestClient, customer: str, name: str) -> None:
    for path, body in (("disable", {}), ("schedule-delete", {"name": name})):
        reply = client.post(
            f"{ORGS}/{customer}/{path}", json={**body, "code": _code(api, operator)}, headers=CSRF
        )
        assert reply.status_code == 200, reply.text


def _gone(db: Database, storage: KeyStorage, customer: str) -> None:
    """No row in any table, nor customers, but the audit rows kept (0068, #482); no file."""
    left = _snapshot(db, customer)
    left.pop("audit_log_kept")
    assert set(left.values()) == {"0:"}
    assert storage.listing(customer) == []


def _organisation_gone(db: Database, storage: KeyStorage, customer: str) -> None:
    """``_gone`` for an ``_organisation``: its audit rows kept, its open upload aborted (#482)."""
    _gone(db, storage, customer)
    kept = _owner(db, "SELECT count(*) FROM audit_log_kept WHERE customer_id = :c", {"c": customer})
    assert kept[0][0] >= 2  # at least the view and the deletion's own rows
    assert f"upload-{customer}" in {upload for _, upload in storage.aborted}  # safe to repeat


def test_0018_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh

    migrate(url, "0017")
    at_0017 = m0002._schema(database)
    migrate(url, "head")
    at_head = m0002._schema(database)
    assert m0002._names(at_head, "functions") >= FUNCTIONS
    m0002._downgrade(url, "0017")
    assert m0002._schema(database) == at_0017
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_every_key_into_a_table_the_finish_deletes_from_cascades_or_is_listed(db: Database) -> None:
    """A new key without CASCADE into users, customers or a table emptied before them must be
    added to 0018's finish (and here) first."""
    keys = _owner(
        db,
        "SELECT conrelid::regclass::text, confrelid::regclass::text FROM pg_constraint WHERE contype = 'f'"
        " AND connamespace = 'public'::regnamespace AND confdeltype <> 'c'"
        " AND confrelid = ANY(CAST(:t AS regclass[]))",
        {"t": ["users", "customers", "folders", "tags", "conversations", "learned_corrections"]},
    )
    assert {tuple(key) for key in keys} == NOT_CASCADING | GONE_FIRST


def test_a_deletion_needs_a_disabled_organisation_its_exact_name_and_a_fresh_code(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    name = f"Org S {uuid.uuid4().hex[:8]}"
    a, _ = api.customer(name)

    def post(path: str, body: dict[str, str]) -> tuple[int, str | None]:
        reply = client.post(f"{ORGS}/{a}/{path}", json=body, headers=CSRF)
        return reply.status_code, reply.json().get("error", {}).get("code")

    def state() -> list[tuple[str, bool | None]]:
        return _owner(
            db,
            "SELECT status, delete_after - now() BETWEEN interval '6 days 23 hours' AND interval '7 days'"
            " FROM customers WHERE id = :c",
            {"c": a},
        )

    assert post("schedule-delete", {"name": name, "code": _code(api, operator)}) == (
        409,
        "conflict",
    )  # active
    assert post("disable", {"code": _code(api, operator)}) == (200, None)
    assert post("schedule-delete", {"name": name}) == (400, "bad_request")  # no code
    assert post("schedule-delete", {"name": name, "code": wrong_code(api, operator.secret)})[0] == 403
    typed = {"name": name.lower(), "code": _code(api, operator)}
    assert post("schedule-delete", typed) == (409, "name_mismatch")
    assert state() == [("disabled", None)]
    code = _code(api, operator)
    assert post("schedule-delete", {"name": name, "code": code}) == (200, None)
    assert state() == [("deleting", True)]  # seven days on the database clock
    assert post("cancel-delete", {"code": code}) == (403, "step_up_failed")  # the same code again
    assert post("schedule-delete", {"name": name, "code": _code(api, operator)}) == (409, "conflict")
    assert post("enable", {}) == (409, "conflict")  # being deleted
    assert post("cancel-delete", {"code": _code(api, operator)}) == (200, None)
    assert state() == [("disabled", None)]
    assert post("cancel-delete", {"code": _code(api, operator)}) == (409, "conflict")  # not being deleted
    assert post("schedule-delete", {"name": name, "code": _code(api, operator)}) == (200, None)
    _owner(db, DUE, {"c": a})
    assert post("cancel-delete", {"code": _code(api, operator)}) == (409, "conflict")  # too late: purging

    failed = ("operator.step_up_failed", None, "operator", operator.id)
    row = ("customer", a)
    assert _platform(operator) == [
        ("customer.disable", a, *row),
        failed,
        ("customer.delete_refused", a, *row),  # the wrong name, audited
        ("customer.delete_scheduled", a, *row),
        failed,
        ("customer.delete_cancelled", a, *row),
        ("customer.delete_scheduled", a, *row),
    ]
    by_operator = [entry[0] for entry in api.audit_rows(a) if entry[3].get("by") == "operator"]
    assert by_operator == [
        "customer.disable",
        "customer.delete_scheduled",
        "customer.delete_cancelled",
        "customer.delete_scheduled",
    ]
    purge_customer(db, KeyStorage(), a, now=_now(), keep_going=_always)  # leave nothing due behind


def test_after_the_grace_period_everything_of_a_goes_and_b_stays_byte_for_byte(db: Database) -> None:
    api, storage = Api(db), KeyStorage()
    operator, client = _signed_in(api)
    a, a_name, _ = _organisation(api, storage, "A")
    b, _, _ = _organisation(api, storage, "B")
    _schedule(api, operator, client, a, a_name)
    a_before, b_before = _snapshot(db, a), (_snapshot(db, b), storage.listing(b))
    assert all(value != "0:" for table, value in a_before.items() if table in NO_ACTION_TO_USERS)

    purge_deleted_customers(db, storage, _now)  # within the grace period: nothing of A
    assert (_snapshot(db, a), len(storage.listing(a))) == (a_before, 8)

    _owner(db, DUE, {"c": a})
    audit_before = _owner(db, _AUDIT.format(table="audit_log"), {"c": a})
    assert purge_deleted_customers(db, storage, _now) >= 2  # A's two families at least
    _organisation_gone(db, storage, a)
    assert _owner(db, _AUDIT.format(table="audit_log_kept"), {"c": a}) == audit_before  # as they were
    assert (_snapshot(db, b), storage.listing(b)) == b_before
    purged = "SELECT operator_id, target_type, target_id FROM platform_audit WHERE subject_customer_id = :c"
    assert _owner(db, purged + " AND action = 'org.purged'", {"c": a}) == [(None, "customer", a)]


def test_a_storage_failure_rolls_that_step_back_and_the_next_run_goes_on(db: Database) -> None:
    api, storage = Api(db), KeyStorage()
    operator, client = _signed_in(api)
    a, name, documents = _organisation(api, storage, "F")
    g, g_name, _ = _organisation(api, storage, "G")
    _schedule(api, operator, client, a, name)
    before, files = _snapshot(db, a), storage.listing(a)
    ahead = _now() + timedelta(days=8)  # a worker clock running ahead of the database's
    assert purge_customer(db, storage, a, now=ahead, keep_going=_always) == (0, False)
    purge_deleted_customers(db, storage, lambda: ahead)
    assert (_snapshot(db, a), storage.listing(a)) == (before, files)
    cancel = client.post(f"{ORGS}/{a}/cancel-delete", json={"code": _code(api, operator)}, headers=CSRF)
    assert cancel.status_code == 200, cancel.text  # still in its grace period: given back whole
    again = {"name": name, "code": _code(api, operator)}
    assert client.post(f"{ORGS}/{a}/schedule-delete", json=again, headers=CSRF).status_code == 200
    _schedule(api, operator, client, g, g_name)
    for customer in (a, g):
        _owner(db, DUE, {"c": customer})
    before = _snapshot(db, a)

    storage.failing = set(documents)  # A's files can't be deleted: A fails, G goes all the same
    purge_deleted_customers(db, storage, _now)
    assert (_snapshot(db, a), storage.listing(a)) == (before, files)  # that family came back whole
    _organisation_gone(db, storage, g)

    storage.failing = set()
    one_family = iter([True, False]).__next__  # the time is up after one family
    assert purge_customer(db, storage, a, now=_now(), keep_going=one_family) == (1, False)
    storage.prefixes_down = True
    with pytest.raises(BackendUnavailable):
        purge_customer(db, storage, a, now=_now(), keep_going=_always)
    assert _owner(db, "SELECT count(*) FROM documents WHERE customer_id = :c", {"c": a}) == [(0,)]
    assert _owner(db, "SELECT status FROM customers WHERE id = :c", {"c": a}) == [("deleting",)]
    assert storage.listing(a) != []  # the stray page and the held copy wait for the next run

    storage.prefixes_down = False
    assert purge_customer(db, storage, a, now=_now(), keep_going=_always) == (0, True)
    _organisation_gone(db, storage, a)
    assert purge_customer(db, storage, a, now=_now(), keep_going=_always) == (0, False)  # gone: nothing to do


# Every column audit_log and audit_log_kept share, oldest first.
_AUDIT = (
    "SELECT id, customer_id, actor_user_id, action, target_type, target_id, details, created_at, folder_id,"
    " family_id FROM {table} WHERE customer_id = :c ORDER BY id"
)
_KEPT = "SELECT count(*) FROM audit_log_kept WHERE customer_id = :c"
_A_YEAR_AGO = (
    "UPDATE audit_log_kept SET deleted_at = now() - interval '365 days 1 minute' WHERE customer_id = :c"
)


def test_a_deleted_organisations_audit_rows_are_seen_by_no_role_and_go_after_365_days(db: Database) -> None:
    """#482 (Tushar, 3 Nov 2026): kept where neither the app, as any customer, nor the operator
    role can read them; the worker's sweep removes them 365 days after the deletion, and only
    them (both clocks)."""
    api, storage = Api(db), KeyStorage()
    operator, client = _signed_in(api)
    a, a_name, _ = _organisation(api, storage, "K")
    b, b_name, _ = _organisation(api, storage, "L")
    for customer, name in ((a, a_name), (b, b_name)):
        _schedule(api, operator, client, customer, name)
        _owner(db, DUE, {"c": customer})
        assert purge_customer(db, storage, customer, now=_now(), keep_going=_always)[1]
    for customer in (a, b):
        with pytest.raises(ProgrammingError, match="permission denied"), db.customer(customer) as conn:
            conn.execute(text(_KEPT), {"c": customer})
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        OperatorDatabase(db.engine).operator() as conn,
    ):
        conn.execute(text(_KEPT), {"c": a})

    _owner(db, _A_YEAR_AGO, {"c": a})
    b_kept = _owner(db, _KEPT, {"c": b})
    assert forget_kept_audit(db, lambda: _now() - timedelta(days=1)) == 0  # not due on the worker's clock
    assert forget_kept_audit(db, _now) >= 2
    assert forget_kept_audit(db, lambda: _now() + timedelta(days=400)) == 0  # running ahead: the database's
    assert _owner(db, _KEPT, {"c": a}) == [(0,)]
    assert _owner(db, _KEPT, {"c": b}) == b_kept  # deleted today: kept
