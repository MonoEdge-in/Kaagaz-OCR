"""The reading pause after product QA (4 Nov 2026), on a real database: a disabled or deleting
organisation's held documents are never released (T1); "Read again" of a ready document while
reading is paused is refused, so it stays ready and searchable (P1, C3); a released document is
audited and counts as asked for from then, so Retry is never offered while it is read (P2); a
spreadsheet is never held by the daily page limit (P4); a released document stops saying it is
paused (P5); and a Mistral document that waits mid-read gives its claimed page back (test d)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from kaagaz.db import models, reading_days
from kaagaz.db.documents import NotReprocessable, mark_for_reprocess
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.reading_pause import BUDGET, DAILY_LIMIT, ReadingPaused, release_held
from kaagaz.scanning.gate import DocumentStatus
from tests.db_support import new_customer
from tests.fakes import MemoryStorage
from tests.pdf_builder import make_pdf
from tests.test_api_page_fixes import read_again, scanned
from tests.test_api_reprocess import reprocess
from tests.test_api_support import Api
from tests.test_api_usage import add_spend
from tests.test_api_usage import set_budget as set_budget_usd
from tests.test_migration_0023_db import as_owner
from tests.test_model_choice_pipeline import _Reading
from tests.test_pipeline import FakeConverter
from tests.test_pipeline_gemini import (
    MODEL,
    SCANNED,
    TOTAL,
    FakeChecker,
    ScriptedReader,
    beat,
    page_read,
    rig,
)
from tests.test_reading_pause_db import NIGHT, counter, document, gate, held, mine, set_budget, set_limit
from tests.test_reading_pause_review_db import count_today, hold
from tests.xlsx_builder import make_zip

pytestmark = pytest.mark.db

READING_PAUSED = {
    DAILY_LIMIT: "Reading is paused, so this document can't be read again now. It stays searchable."
    " Try again when the daily limit resets.",
    BUDGET: "Reading is paused, so this document can't be read again now. It stays searchable."
    " Try again when the monthly AI limit is raised or the new month begins.",
}


def _document(db: Database, document_id: str) -> tuple[Any, ...]:
    [row] = as_owner(
        db, "SELECT status, reading_held, progress FROM documents WHERE id = :d", {"d": document_id}
    )
    return tuple(row)


# --- T1: a disabled or deleting organisation ----------------------------------------------------


@pytest.mark.parametrize("status", ["disabled", "deleting"])
def test_a_disabled_or_deleting_organisations_held_documents_are_never_released(
    db: Database, status: str
) -> None:
    customer, _ = new_customer(db)
    waiting = hold(db, customer)
    deleting = ", delete_after = now() + interval '30 days'" if status == "deleting" else ""
    as_owner(
        db,
        f"UPDATE customers SET status = :s, disabled_at = now(){deleting} WHERE id = :c",  # noqa: S608 - fixed text
        {"c": customer, "s": status},
    )
    sent: list[Job] = []
    assert mine(release_held(db, sent.append, now=NIGHT), customer) == []
    assert held(db, customer, waiting) == DAILY_LIMIT  # kept: released if it is enabled again
    as_owner(
        db,
        "UPDATE customers SET status = 'active', disabled_at = NULL, delete_after = NULL WHERE id = :c",
        {"c": customer},
    )
    assert mine(release_held(db, sent.append, now=NIGHT), customer) == [waiting]


# --- P1: "Read again" while reading is paused ----------------------------------------------------


@pytest.mark.parametrize("reason", [DAILY_LIMIT, BUDGET])
def test_read_again_of_a_ready_document_while_paused_is_refused_and_it_stays_searchable(
    db: Database, reason: str
) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    customer = reviewer.customer_id
    if reason == DAILY_LIMIT:
        set_limit(db, customer, 1)
        with db.customer(customer) as conn:
            reading_days.count(conn, customer, reading_days.india_day(api.clock()), pages=1, cost_usd=0.0)
    else:
        set_budget_usd(db, customer, 1.0)
        add_spend(db, customer, at=api.clock(), cost=2.0)
    ready = scanned(api, customer)
    for reply in (reprocess(reviewer, ready), read_again(reviewer, ready, 1)):
        assert reply.status_code == 409, reply.text
        assert reply.json() == {"error": {"code": "reading_paused", "message": READING_PAUSED[reason]}}
    assert _document(db, ready) == ("ready", None, None)  # still in search and Ask
    assert api.queue.jobs == []
    assert as_owner(db, "SELECT count(*) FROM audit_log WHERE target_id = :d", {"d": ready}) == [(0,)]

    failed = api.add_document(customer, status="failed", failure_code="corrupt_file")
    assert reprocess(reviewer, failed).status_code == 200  # never in search: it waits for the pause


# --- P2 and P5: a released document ---------------------------------------------------------


def test_a_released_document_is_audited_never_stuck_and_no_longer_says_paused(db: Database) -> None:
    customer, _ = new_customer(db)
    long_ago = datetime.now(UTC) - timedelta(hours=7)  # older than RETRY_STUCK_HOURS
    waiting, reading = hold(db, customer, uploaded=long_ago), hold(db, customer, uploaded=long_ago)
    for each, progress in (
        (waiting, '{"step": "waiting"}'),
        (reading, '{"step": "reading", "done": 1, "total": 3}'),
    ):
        # Waiting as the pipeline leaves it; the other, already started by a worker in the moment.
        as_owner(
            db, "UPDATE documents SET progress = CAST(:p AS jsonb) WHERE id = :d", {"d": each, "p": progress}
        )
    sent: list[Job] = []
    assert mine(release_held(db, sent.append, now=NIGHT), customer) == sorted([waiting, reading])
    assert _document(db, waiting) == ("received", None, None)  # the row stops saying paused
    assert _document(db, reading)[2] == {"step": "reading", "done": 1, "total": 3}  # only "waiting" goes
    rows = as_owner(
        db,
        "SELECT target_id, actor_user_id, details FROM audit_log WHERE customer_id = :c"
        " AND action = 'document.reading_released' ORDER BY target_id",
        {"c": customer},
    )
    assert rows == [(each, None, {}) for each in sorted([waiting, reading])]  # ids only
    with pytest.raises(NotReprocessable), db.customer(customer) as conn:
        mark_for_reprocess(conn, customer, waiting, per_hour=100)  # being read: no Retry
    assert mine(release_held(db, sent.append, now=NIGHT), customer) == []  # audited once


# --- P4: a spreadsheet ----------------------------------------------------------------------------


def test_a_spreadsheet_is_never_claimed_or_held_by_the_daily_limit(db: Database) -> None:
    customer, _ = new_customer(db)
    set_limit(db, customer, 1)
    count_today(db, customer, NIGHT, 1)  # the day's pages are read
    sheet = document(db, customer)
    with gate(db, NIGHT).admit(customer, sheet, ai_pages=False):
        assert counter(db, customer, NIGHT) == (1, Decimal(0))  # no page claimed
    assert held(db, customer, sheet) is None and counter(db, customer, NIGHT) == (1, Decimal(0))
    now = datetime.now(UTC)  # the ledger's month is the real one
    set_budget(db, customer, "1.00")
    add_spend(db, customer, at=now, cost=2.0)
    with (
        pytest.raises(ReadingPaused) as paused,
        gate(db, now).admit(customer, document(db, customer), ai_pages=False),
    ):
        pass
    assert paused.value.reason == BUDGET  # its card and search still spend: the budget holds it


# --- test d: a Mistral document that waits mid-read -------------------------------------------------


def test_a_retried_mistral_page_gives_its_claim_back(db: Database) -> None:
    customer, _ = new_customer(db)
    set_limit(db, customer, 1)
    started = document(db, customer)
    pause = gate(db, NIGHT)

    def admit(_customer: str, _document: str, *, ai_pages: bool = True) -> Any:
        return pause.admit(customer, started, ai_pages=ai_pages)  # the rig's job is this document

    lead = models.Agent(MODEL, "google-vertex", "Gemini")
    job_reading = _Reading("u1", MODEL)
    mistral = models.Reading("single", lead, scan_reader="mistral", mistral_model="mistral-ocr-2512")
    job_reading.reading = replace(job_reading.reading, reading=mistral)
    reader = ScriptedReader({1: BackendUnavailable("mistral")})
    system = rig(reader, checker=FakeChecker(), job_reading=job_reading, reading_pause=admit)
    job = system.job(make_pdf([SCANNED]))
    with pytest.raises(BackendUnavailable):
        system.processor(job, beat)  # Mistral waits: the job comes back later
    assert counter(db, customer, NIGHT) == (0, Decimal(0))  # its claimed page given back
    assert held(db, customer, started) is None
    count_today(db, customer, NIGHT, 1)  # another document reads the day's last page meanwhile
    reader.pages[1] = page_read(TOTAL, read_by="mistral/mistral-ocr-2512")
    system.processor(job, beat)  # the retry that day: let in again with no claim, read to its end
    assert system.status(job) == DocumentStatus.READY
    assert counter(db, customer, NIGHT) == (1, Decimal(0))


# --- #522: a Word file at the daily limit ------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "upload"),
    [
        ("a 3-page Word file", make_zip({"word/document.xml": b"<w/>"})),
        ("a 3-page PDF", make_pdf([SCANNED, SCANNED, SCANNED])),
    ],
    ids=["word", "pdf"],
)
def test_an_organisation_at_its_daily_limit_holds_a_word_file_as_it_holds_a_pdf(
    db: Database, name: str, upload: bytes
) -> None:
    """#522: a .docx is sniffed as a zip, as an .xlsx is, so it was let in as a spreadsheet,
    past the daily page limit. Once its kind is told and LibreOffice made its 3-page PDF, it is
    asked as that PDF is: held, nothing drawn or read, and no page claimed."""
    customer, _ = new_customer(db)
    set_limit(db, customer, 1)
    count_today(db, customer, NIGHT, 1)  # the day's pages are read
    started = document(db, customer)
    pause = gate(db, NIGHT)

    def admit(_customer: str, _document: str, *, ai_pages: bool = True) -> Any:
        return pause.admit(customer, started, ai_pages=ai_pages)  # the rig's job is this document

    reader = ScriptedReader({page: page_read(TOTAL) for page in (1, 2, 3)})
    rendition = make_pdf([SCANNED, SCANNED, SCANNED])
    system = rig(
        reader,
        checker=FakeChecker(),
        reading_pause=admit,
        office=FakeConverter(rendition),
        renditions=MemoryStorage(),
    )
    job = system.job(upload)
    system.processor(job, beat)
    assert held(db, customer, started) == DAILY_LIMIT, name
    assert reader.opened == [] and system.pages(job) == []
    assert system.totals(job).error_code == "paused_daily_limit"
    assert counter(db, customer, NIGHT) == (1, Decimal(0))  # nothing claimed past the limit
