"""The break-glass ``purge-customer --now`` and the second sweep a day after a purge,
against PostgreSQL (super admin PR 4b, 0019). Files live in ``KeyStorage`` (the purge
tests'), keyed as ``S3Storage`` keys them."""

import io
from datetime import timedelta

import pytest
from sqlalchemy import text

from kaagaz.adapters.storage import page_image_key, quarantine_key
from kaagaz.cli import main
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.purge import sweep_purged_customers
from kaagaz.jobs.worker import JobFailed
from kaagaz.pipeline import DocumentProcessor
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer
from tests.fakes import FakePreparer, fake_sheet_pages
from tests.test_isolation_customer_purge import KeyStorage, _gone, _now
from tests.test_operator_auth_db import _owner
from tests.test_pipeline import build, real_socket

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
# What scheduling does (0018), straight in the table as its owner.
DELETING = (
    "UPDATE customers SET status = 'deleting', disabled_at = now(), delete_after = now() + interval '7 days'"
    " WHERE id = :c"
)
_ROWS = "SELECT operator_id, action FROM platform_audit WHERE subject_customer_id = :c ORDER BY id"


def _cli(db: Database, storage: KeyStorage, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    return main(["purge-customer", *args], db=db, environ={}, out=out, storage=storage), out.getvalue()


def _scheduled(db: Database, storage: KeyStorage) -> tuple[str, str]:
    """A customer with one stored document, scheduled for deletion: (customer, document)."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
    storage.put(customer, document, io.BytesIO(b"file"))
    _owner(db, DELETING, {"c": customer})
    return customer, document


def _purged(db: Database, storage: KeyStorage) -> tuple[str, str]:
    """As ``_scheduled``, then purged by the CLI."""
    customer, document = _scheduled(db, storage)
    line = f"customer {customer}: purged, document families removed: 1\n"
    assert _cli(db, storage, "--customer-id", customer, "--now") == (0, line)
    return customer, document


def test_0019_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh

    def sweep_checks_status() -> bool:
        with database.system() as conn:
            source = conn.execute(
                text("SELECT prosrc FROM pg_proc WHERE proname = 'kaagaz_stories_to_embed'")
            )
            return "status" in str(source.scalar_one())

    migrate(url, "0018")
    at_0018 = m0002._schema(database)
    migrate(url, "head")
    at_head = m0002._schema(database)
    assert m0002._names(at_head, "functions") >= {"kaagaz_purged_ids_to_sweep", "kaagaz_customer_swept"}
    assert sweep_checks_status()  # the story sweep skips customers that are not active
    m0002._downgrade(url, "0018")
    assert m0002._schema(database) == at_0018 and not sweep_checks_status()
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_purge_now_refuses_any_organisation_whose_deletion_is_not_scheduled(db: Database) -> None:
    storage, (a, _) = KeyStorage(), new_customer(db)
    with db.customer(a) as conn:
        document = insert_document(conn, a)
    storage.put(a, document, io.BytesIO(b"file"))
    for refused in (["--customer-id", "not-an-id", "--now"], ["--customer-id", a, "--now"]):
        assert _cli(db, storage, *refused) == (2, "")  # not an id; active
    assert main(["disable-customer", "--customer-id", a], db=db, environ={}, out=io.StringIO()) == 0
    assert _cli(db, storage, "--customer-id", a, "--now") == (2, "")  # disabled, not scheduled
    state = "SELECT status, delete_after FROM customers WHERE id = :c"
    assert _owner(db, state, {"c": a}) == [("disabled", None)]
    assert len(storage.listing(a)) == 1
    assert [row[1] for row in _owner(db, _ROWS, {"c": a})] == ["customer.disable"]  # the CLI's own


def test_purge_now_removes_a_scheduled_organisation_at_once_and_says_so(db: Database) -> None:
    storage = KeyStorage()
    a, document = _purged(db, storage)
    _gone(db, storage, a)
    assert [tuple(row) for row in _owner(db, _ROWS, {"c": a})] == [
        (None, "org.purge_now"),
        (None, "org.purged"),
    ]
    assert _cli(db, storage, "--customer-id", a, "--now") == (2, "")  # no such customer now

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
    documents.record_attempt(a, document, 1, 5)
    with pytest.raises(JobFailed) as failed:
        processor(Job(a, document), lambda: None)
    assert (failed.value.code, failed.value.retryable) == ("document_missing", False)
    documents.mark_exhausted(a, document, now=_now(), max_attempts=5)
    assert documents.customer_active(a) is False


def test_purge_now_with_storage_down_says_so_in_one_line_and_a_rerun_finishes(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = KeyStorage()
    a, _ = _scheduled(db, storage)
    storage.prefixes_down = True
    assert _cli(db, storage, "--customer-id", a, "--now") == (2, "")
    error = "error: storage unavailable; the worker or a re-run will finish it\n"
    assert capsys.readouterr().err == error
    assert _owner(db, "SELECT status FROM customers WHERE id = :c", {"c": a}) == [("deleting",)]
    storage.prefixes_down = False
    assert _cli(db, storage, "--customer-id", a, "--now")[0] == 0
    _gone(db, storage, a)


def test_a_day_after_a_purge_both_prefixes_are_swept_once_more_and_once_only(db: Database) -> None:
    storage = KeyStorage()
    a, _ = _purged(db, storage)
    live, _ = new_customer(db)  # an org.purged row about a customer that still exists
    _owner(
        db,
        "INSERT INTO platform_audit (action, subject_customer_id, created_at)"
        " VALUES ('org.purged', :c, now() - interval '2 days')",
        {"c": live},
    )
    storage.objects[page_image_key(live, live, 1)] = b"a live organisation's page"
    late = {page_image_key(a, a, 3): b"written by a job still running", quarantine_key(a, a): b"held"}
    storage.objects.update(late)

    sweep_purged_customers(db, storage, _now)  # within the day: not yet
    assert len(storage.listing(a)) == 2
    sweep_purged_customers(db, storage, lambda: _now() + timedelta(hours=25))
    assert storage.listing(a) == []
    storage.objects.update(late)
    sweep_purged_customers(db, storage, lambda: _now() + timedelta(hours=25))
    assert len(storage.listing(a)) == 2  # swept once only
    assert [row[1] for row in _owner(db, _ROWS, {"c": a})] == ["org.purge_now", "org.purged", "org.swept"]
    assert len(storage.listing(live)) == 1  # never swept: it still exists
    assert [row[1] for row in _owner(db, _ROWS, {"c": live})] == ["org.purged"]
