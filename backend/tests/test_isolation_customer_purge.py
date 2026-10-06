"""Deleting an organisation against PostgreSQL (super admin PR 4, 0018): scheduling needs a
disabled organisation, its exact name and a fresh code; cancelling gives it back during
the grace period; after it, the purge removes every row of it in every table and both of
its storage prefixes, and organisation B's rows and files stay byte-for-byte the same.

Files live in ``KeyStorage``, which keys them as ``S3Storage`` does (its key functions),
so a storage prefix is a real prefix. The purge covers every customer that is due in
the shared test database; the asserts look at A and B only.
"""

import io
import uuid
from datetime import UTC, datetime
from typing import IO

import pytest
from sqlalchemy import text

from kaagaz.adapters.storage import original_key, page_image_key, quarantine_key
from kaagaz.api import totp
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobNotFound, Job
from kaagaz.jobs.purge import purge_customer, purge_deleted_customers
from kaagaz.jobs.worker import JobFailed
from kaagaz.pipeline import DocumentProcessor
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, insert_folder, insert_tag, migrate, tag_document
from tests.fakes import FakePreparer, fake_sheet_pages
from tests.test_api_support import CSRF, Api, TestClient, wrong_code
from tests.test_document_stories_db import put
from tests.test_operator_auth_db import _Operator, _owner
from tests.test_operator_orgs_db import ORGS, _platform, _signed_in
from tests.test_pipeline import build, real_socket

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
)
# Rows that point at users with NO ACTION: 0018's finish empties these first, by name.
NO_ACTION_TO_USERS = {
    "documents", "corrections", "learned_corrections", "conversations", "feedback", "folders", "tags",
}  # fmt: skip


class KeyStorage:
    """Files by their S3 keys. A document's delete removes its prefix and its quarantined
    copy, a customer's both of its prefixes (as ``S3Storage``). ``failing`` documents'
    deletes and, with ``prefixes_down``, the customer's fail as storage that is down."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.failing: set[str] = set()
        self.prefixes_down = False

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

    def listing(self, customer_id: str) -> list[tuple[str, bytes]]:
        prefixes = (f"customers/{customer_id}/", f"quarantine/{customer_id}/")
        return sorted((key, data) for key, data in self.objects.items() if key.startswith(prefixes))

    def _drop(self, prefixes: tuple[str, ...]) -> None:
        for key in [key for key in self.objects if key.startswith(prefixes)]:
            del self.objects[key]


def _now() -> datetime:
    return datetime.now(UTC)


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
        ):
            conn.execute(text(statement), {"c": customer, "u": member, "d": first})
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
    assert set(_snapshot(db, customer).values()) == {"0:"}  # no row in any table
    assert _owner(db, "SELECT count(*) FROM customers WHERE id = :c", {"c": customer}) == [(0,)]
    assert storage.listing(customer) == []


def test_0018_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh

    def sweep_checks_status() -> bool:
        with database.system() as conn:
            source = conn.execute(
                text("SELECT prosrc FROM pg_proc WHERE proname = 'kaagaz_stories_to_embed'")
            )
            return "status" in str(source.scalar_one())

    migrate(url, "0017")
    at_0017 = m0002._schema(database)
    migrate(url, "head")
    at_head = m0002._schema(database)
    assert m0002._names(at_head, "functions") >= FUNCTIONS and sweep_checks_status()
    m0002._downgrade(url, "0017")
    assert m0002._schema(database) == at_0017 and not sweep_checks_status()
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_every_key_to_users_or_customers_cascades_or_its_rows_go_first(db: Database) -> None:
    """A new table pointing at users without CASCADE must be added to 0018's finish."""
    keys = _owner(
        db,
        "SELECT conrelid::regclass::text, confdeltype::text FROM pg_constraint WHERE contype = 'f'"
        " AND connamespace = 'public'::regnamespace"
        " AND confrelid IN ('users'::regclass, 'customers'::regclass)",
        {},
    )
    assert {table for table, action in keys if action != "c"} == NO_ACTION_TO_USERS


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
    purge_customer(db, KeyStorage(), a, now=_now(), most=10)  # leave nothing due behind


def test_after_the_grace_period_everything_of_a_goes_and_b_stays_byte_for_byte(db: Database) -> None:
    api, storage = Api(db), KeyStorage()
    operator, client = _signed_in(api)
    a, a_name, a_documents = _organisation(api, storage, "A")
    b, _, _ = _organisation(api, storage, "B")
    _schedule(api, operator, client, a, a_name)
    a_before, b_before = _snapshot(db, a), (_snapshot(db, b), storage.listing(b))
    assert all(value != "0:" for table, value in a_before.items() if table in NO_ACTION_TO_USERS)

    purge_deleted_customers(db, storage, _now)  # within the grace period: nothing of A
    assert (_snapshot(db, a), len(storage.listing(a))) == (a_before, 8)

    _owner(db, DUE, {"c": a})
    assert purge_deleted_customers(db, storage, _now) >= 2  # A's two families at least
    _gone(db, storage, a)
    assert (_snapshot(db, b), storage.listing(b)) == b_before
    purged = "SELECT operator_id, target_type, target_id FROM platform_audit WHERE subject_customer_id = :c"
    assert _owner(db, purged + " AND action = 'org.purged'", {"c": a}) == [(None, "customer", a)]

    # A job of A still in the queue fails cleanly, and the worker's other calls do nothing.
    system, documents = build(), PgDocuments(db)
    processor = DocumentProcessor(
        gate=system.gate,
        statuses=documents,
        documents=documents,
        socket=real_socket(),
        prepare=FakePreparer().prepare,
        sheet_pages=fake_sheet_pages,
        page_images=system.page_images,
        store=system.store,
        customer_active=documents.customer_active,
    )
    documents.record_attempt(a, a_documents[0], 1, 5)
    with pytest.raises(JobFailed) as failed:
        processor(Job(a, a_documents[0]), lambda: None)
    assert (failed.value.code, failed.value.retryable) == ("document_missing", False)
    documents.mark_exhausted(a, a_documents[0], now=_now(), max_attempts=5)
    assert documents.customer_active(a) is False
    _gone(db, storage, a)


def test_a_storage_failure_rolls_that_step_back_and_the_next_run_goes_on(db: Database) -> None:
    api, storage = Api(db), KeyStorage()
    operator, client = _signed_in(api)
    a, name, documents = _organisation(api, storage, "F")
    _schedule(api, operator, client, a, name)
    assert purge_customer(db, storage, a, now=_now(), most=10) == (0, False)  # not due yet
    _owner(db, DUE, {"c": a})
    before, files = _snapshot(db, a), storage.listing(a)

    storage.failing = set(documents)
    with pytest.raises(BackendUnavailable):
        purge_customer(db, storage, a, now=_now(), most=10)
    assert (_snapshot(db, a), storage.listing(a)) == (before, files)  # that family came back whole

    storage.failing = set()
    assert purge_customer(db, storage, a, now=_now(), most=1) == (1, False)  # one family a run
    storage.prefixes_down = True
    with pytest.raises(BackendUnavailable):
        purge_customer(db, storage, a, now=_now(), most=10)
    assert _owner(db, "SELECT count(*) FROM documents WHERE customer_id = :c", {"c": a}) == [(0,)]
    assert _owner(db, "SELECT status FROM customers WHERE id = :c", {"c": a}) == [("deleting",)]
    assert storage.listing(a) != []  # the stray page and the held copy wait for the next run

    storage.prefixes_down = False
    assert purge_customer(db, storage, a, now=_now(), most=10) == (0, True)
    _gone(db, storage, a)
    assert purge_customer(db, storage, a, now=_now(), most=10) == (0, False)  # gone: nothing to do
