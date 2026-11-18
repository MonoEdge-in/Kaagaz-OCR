"""``read-again-failed`` (#667, Tushar, 18 Nov 2026): the documents that failed ``no_text`` on
Mistral before their organisation's backup reader (#664) was set, read again in bulk, each queued
as the Read again button queues it (``mark_for_reprocess``), audited as the command line's, never
twice. A dry run without ``--apply`` changes nothing."""

import io
import json
import uuid

import pytest

from kaagaz.cli import main
from kaagaz.db import audit
from kaagaz.db import operator_models as ops
from kaagaz.db.documents import mark_for_reprocess
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import Job
from kaagaz.reading.ocr import READER_PROMPT_VERSION
from kaagaz.settings import Settings
from tests.db_support import insert_document, new_customer
from tests.fakes import MemoryQueue
from tests.settings_factory import make_settings
from tests.test_isolation_ai_settings import platform, set_org
from tests.test_migration_0023_db import as_owner
from tests.test_scan_cost_settings_db import SETTINGS, scan_model

pytestmark = pytest.mark.db
__all__ = ["platform"]  # the fixture, used by name

_AUDIT = (
    "SELECT target_id, actor_user_id, details FROM audit_log WHERE customer_id = :c"
    " AND action = 'document.reprocess' ORDER BY created_at"
)


def _org(db: Database, operator: str, model: str | None) -> tuple[str, str]:
    """An organisation reading with Mistral, with ``model`` as its backup reader (None: none)."""
    customer, admin = new_customer(db)
    choice = ops.AiChoice(
        scan_reader="mistral", backup_reader=None if model is None else "openrouter", backup_model=model
    )
    assert set_org(db, operator, customer, choice).code == "done"
    return customer, admin


def _failed(
    db: Database,
    customer: str,
    asker: str | None,
    *,
    code: str = "no_text",
    scan_reader: str = "mistral",
    mode: str = "single",
    pages_ocr: int = 2,
    **columns: object,
) -> str:
    """A document whose last run failed ``code``, read by ``scan_reader`` in ``mode``."""
    with db.customer(customer) as conn:
        given = {"status": "failed", "failure_code": code, "display_name": "secret-name.pdf", **columns}
        document = insert_document(conn, customer, **given)
    as_owner(db, "UPDATE documents SET read_requested_by = :u WHERE id = :d", {"u": asker, "d": document})
    config = {"read_model": "mistral/mistral-ocr-4-1", "reading": {"scan_reader": scan_reader, "mode": mode}}
    as_owner(
        db,
        "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status, error_code,"
        " config, pages_ocr) VALUES (:c, :d, now(), now(), 'failed', :e, CAST(:config AS jsonb), :p)",
        {"c": customer, "d": document, "e": code, "config": json.dumps(config), "p": pages_ocr},
    )
    return document


def _status(db: Database, document: str) -> tuple[str, str | None]:
    [(status, asker)] = as_owner(
        db, "SELECT status, read_requested_by FROM documents WHERE id = :d", {"d": document}
    )
    return status, None if asker is None else str(asker)


def _run(db: Database, *args: str, queue: MemoryQueue | None = None, settings: Settings = SETTINGS) -> str:
    out = io.StringIO()
    code = main(["read-again-failed", *args], db=db, environ={}, out=out, settings=settings, queue=queue)
    assert code == 0
    return out.getvalue()


def _audit(db: Database, customer: str) -> list[tuple[str, str | None, dict[str, object]]]:
    return [(str(t), a, d) for t, a, d in as_owner(db, _AUDIT, {"c": customer})]


def test_a_dry_run_lists_ids_and_pages_and_changes_nothing(db: Database, platform: str) -> None:
    customer, admin = _org(db, platform, scan_model(db))
    document = _failed(db, customer, admin, pages_ocr=3)
    queue = MemoryQueue()

    said = _run(db, "--customer", customer, queue=queue)

    assert f"customer {customer}: 1 document, 3 scanned pages to read again" in said
    assert f"  document {document}: 3 scanned pages" in said
    assert said.endswith("1 document, 3 scanned pages, not queued (add --apply)\n")
    assert "secret-name" not in said
    assert _status(db, document) == ("failed", admin)
    assert queue.jobs == [] and _audit(db, customer) == []


def test_apply_queues_only_the_current_live_unlocked_documents_mistral_failed_no_text(
    db: Database, platform: str
) -> None:
    customer, admin = _org(db, platform, scan_model(db))
    matching = _failed(db, customer, admin, page_count=2)
    others = [
        _failed(db, customer, admin, code="too_many_pages"),  # another failure
        _failed(db, customer, admin, status="received"),  # being read (again) now, not failed
        _failed(db, customer, admin, scan_reader="openrouter"),  # read by Gemini
        _failed(db, customer, admin, mode="local"),  # read by the local reader
        _failed(db, customer, admin, pages_ocr=0),  # no page went to Mistral (a text file)
        _failed(db, customer, admin, is_current=False),  # an older version
        _failed(db, customer, admin, deleted_at="2026-11-01T00:00:00Z", deleted_by=admin),  # in Trash
    ]
    locked = _failed(db, customer, admin)
    as_owner(
        db, "UPDATE documents SET locked_at = now(), locked_by = :u WHERE id = :d", {"u": admin, "d": locked}
    )
    others.append(locked)
    before = [_status(db, other) for other in others]
    queue = MemoryQueue()

    said = _run(db, "--customer", customer, "--apply", queue=queue)

    assert queue.jobs == [Job(customer, matching)] and queue.lanes == [True]  # 2 pages: express
    assert _status(db, matching) == ("received", admin)  # billed to who asked, as before
    assert [_status(db, other) for other in others] == before
    assert _audit(db, customer) == [(matching, None, {"status": "failed", "by": "cli"})]
    assert f"customer {customer}: 1 queued, 0 not queued" in said
    assert said.endswith("1 document queued\n")


def test_running_it_twice_queues_each_once(db: Database, platform: str) -> None:
    customer, admin = _org(db, platform, scan_model(db))
    document = _failed(db, customer, admin)
    queue = MemoryQueue()

    _run(db, "--customer", customer, "--apply", queue=queue)
    again = _run(db, "--customer", customer, "--apply", queue=queue)

    assert queue.jobs == [Job(customer, document)]
    assert len(_audit(db, customer)) == 1
    assert again == "0 documents queued\n"  # no longer failed: not even found
    assert _run(db, "--customer", customer).startswith("0 documents, 0 scanned pages")
    assert again.endswith("0 documents queued\n")


def test_organisations_without_a_usable_backup_reader_are_skipped_with_the_reason(
    db: Database, platform: str
) -> None:
    model, unverified = scan_model(db), scan_model(db)
    none = _org(db, platform, None)
    stale = _org(db, platform, unverified)
    as_owner(db, "UPDATE model_catalog SET verified_prompt_version = 'old' WHERE id = :m", {"m": unverified})
    gemini = new_customer(db)  # no longer reads with Mistral
    choice = ops.AiChoice(backup_reader="openrouter", backup_model=model)
    assert set_org(db, platform, gemini[0], choice).code == "done"
    documents = {org: _failed(db, org, asker) for org, asker in (none, stale, gemini)}
    usable, admin = _org(db, platform, model)
    queued = _failed(db, usable, admin)
    queue = MemoryQueue()

    said = _run(db, "--apply", queue=queue)  # every organisation

    assert [job for job in queue.jobs if job.customer_id in {*documents, usable}] == [Job(usable, queued)]
    assert f"customer {none[0]}: 1 document skipped: no backup reader" in said
    assert f"customer {stale[0]}: 1 document skipped: its backup reader is not usable now" in said
    assert f"customer {gemini[0]}: 1 document skipped: it no longer reads with Mistral" in said
    for org, document in documents.items():
        assert _status(db, document)[0] == "failed" and _audit(db, org) == []
    as_owner(
        db, "UPDATE model_catalog SET verified_prompt_version = :p WHERE id = :m",
        {"p": READER_PROMPT_VERSION, "m": unverified},
    )  # fmt: skip


def test_a_document_with_no_one_to_bill_is_not_queued(db: Database, platform: str) -> None:
    """The reading pause and the budget hold a document by who asked for it (SP1): one with no
    asker would be read as the upgrade sweep's, past them, so it is left and said."""
    customer, _ = _org(db, platform, scan_model(db))
    document = _failed(db, customer, None)
    queue = MemoryQueue()

    said = _run(db, "--customer", customer, "--apply", queue=queue)

    assert queue.jobs == [] and _status(db, document)[0] == "failed"
    assert f"customer {customer}: 1 document skipped: no one asked for its reading" in said


def test_customer_limits_the_run(db: Database, platform: str) -> None:
    model = scan_model(db)
    (one, one_admin), (two, two_admin) = _org(db, platform, model), _org(db, platform, model)
    mine, theirs = _failed(db, one, one_admin), _failed(db, two, two_admin)
    queue = MemoryQueue()

    _run(db, "--customer", one, "--apply", queue=queue)

    assert queue.jobs == [Job(one, mine)]
    assert _status(db, theirs)[0] == "failed" and _audit(db, two) == []


def test_the_users_hourly_limit_never_stops_it(db: Database, platform: str) -> None:
    customer, admin = _org(db, platform, scan_model(db))
    documents = [_failed(db, customer, admin) for _ in range(3)]
    queue = MemoryQueue()
    with db.customer(customer) as conn:  # a user's Read again has used the hour already
        audit.record(
            conn, customer, actor_user_id=admin, action="document.reprocess", target_type="document",
            target_id=_failed(db, customer, admin, code="too_many_pages"),
        )  # fmt: skip

    _run(db, "--customer", customer, "--apply", queue=queue, settings=make_settings(reprocess_per_hour=1))

    assert sorted(job.document_id for job in queue.jobs) == sorted(documents)


def test_its_rows_never_count_against_the_users_hourly_read_again(db: Database, platform: str) -> None:
    customer, admin = _org(db, platform, scan_model(db))
    _failed(db, customer, admin)
    other = _failed(db, customer, admin, code="too_many_pages")
    _run(db, "--customer", customer, "--apply", queue=MemoryQueue())

    with db.customer(customer) as conn:  # a user's Read again, at one an hour
        assert mark_for_reprocess(conn, customer, other, per_hour=1, requested_by=admin) == "failed"


def test_a_customer_id_that_is_not_one_is_refused(db: Database) -> None:
    assert main(["read-again-failed", "--customer", "nope"], db=db, environ={}, settings=SETTINGS) == 2
    said = _run(db, "--customer", str(uuid.uuid4()))
    assert said == "0 documents, 0 scanned pages, not queued (add --apply)\n"
