"""The reading pause without a database (#478, Tushar, 3 Nov 2026): a paused document is held,
never read and never read locally by RapidOCR, and its job is done (acked, not waiting in the
queue); a document let in is read inside the pause's block; each person's words; the super
admin's email (sent by the one operations alert rule, kaagaz.ops_alert); and the release sweep's
timing."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

from kaagaz.adapters.email import MemoryMailer
from kaagaz.api import views
from kaagaz.db import models
from kaagaz.db.reading_days import State
from kaagaz.ingestion.ports import Job
from kaagaz.jobs import reading_pause
from kaagaz.jobs.reading_pause import (
    BUDGET,
    DAILY_LIMIT,
    RELEASE_EVERY,
    ReadingPaused,
    ReleaseHeld,
    email_text,
    pause_alert,
    pause_reason,
)
from kaagaz.scanning.gate import DocumentStatus
from kaagaz.settings import Settings
from tests.pdf_builder import make_pdf
from tests.settings_factory import make_settings
from tests.test_model_choice_pipeline import _Reading
from tests.test_pipeline import CSV
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

MONEY = views._MONEY_KEY  # usd, price, budget, cost: never to an organisation
ORG = State(
    name="Sharma Traders",
    page_limit=2000,
    pages_today=2000,
    budget=Decimal("50.00"),
    spent=Decimal("12.40"),
    held=None,
)


def test_a_paused_document_is_held_never_read_nor_read_locally_and_its_job_is_done() -> None:
    asked: list[str] = []

    @contextmanager
    def paused(customer_id: str, document_id: str, *, ai_pages: bool = True) -> Iterator[None]:
        asked.append(document_id)
        raise ReadingPaused(DAILY_LIMIT)
        yield  # never: a paused document is not read

    reader = ScriptedReader({1: page_read(TOTAL)})
    system = rig(reader, checker=FakeChecker(), reading_pause=paused)
    job = system.job(make_pdf([SCANNED]))
    system.processor(job, beat)  # returns: the worker acks the job, nothing waits in the queue
    assert asked == [job.document_id]
    assert reader.opened == [] and system.pages(job) == []  # no Gemini, no RapidOCR, no page drawn
    assert system.totals(job).error_code == "paused_daily_limit"
    assert system.progress.waiting[(job.customer_id, job.document_id)] == "reader"  # shown waiting
    assert system.status(job) is None  # never marked: still waiting to be read


def test_a_document_let_in_is_read_inside_the_pauses_block() -> None:
    inside: list[bool] = []
    reader = ScriptedReader({1: page_read(TOTAL)})

    @contextmanager
    def admit(customer_id: str, document_id: str, *, ai_pages: bool = True) -> Iterator[None]:
        inside.append(True)
        yield
        inside.append(bool(reader.opened))  # its claimed page is given back after the reading

    system = rig(reader, checker=FakeChecker(), reading_pause=admit)
    job = system.run(make_pdf([SCANNED]))
    assert inside == [True, True] and system.status(job) == DocumentStatus.READY


class Asked:
    """A reading pause that notes each document asked about and whether it has AI pages;
    ``paused``: it raises ReadingPaused, the document held."""

    def __init__(self, *, paused: bool = False) -> None:
        self.paused = paused
        self.asked: list[tuple[str, bool]] = []

    @contextmanager
    def __call__(self, customer_id: str, document_id: str, *, ai_pages: bool = True) -> Iterator[None]:
        self.asked.append((document_id, ai_pages))
        if self.paused:
            raise ReadingPaused(DAILY_LIMIT)
        yield


def test_an_upgrade_run_is_never_held_so_its_ready_document_stays_in_search() -> None:
    """QA P1 (4 Nov 2026): the upgrade sweep's read again (no one asked) is counted, never paused;
    a reading someone asked for is asked. (Ensemble after the scan is paused since #520.)"""
    pause = Asked(paused=True)
    upgrade = rig(
        ScriptedReader({1: page_read(TOTAL)}),
        checker=FakeChecker(),
        job_reading=_Reading(None, MODEL),
        reading_pause=pause,
    )
    job = upgrade.run(make_pdf([SCANNED]))
    assert pause.asked == [] and upgrade.status(job) == DocumentStatus.READY
    asked = rig(
        ScriptedReader({1: page_read(TOTAL)}),
        checker=FakeChecker(),
        job_reading=_Reading("u1", MODEL),
        reading_pause=pause,
    )
    held = asked.job(make_pdf([SCANNED]))
    asked.processor(held, beat)
    assert pause.asked == [(held.document_id, True)] and asked.status(held) is None  # held


def test_a_paused_mistral_document_never_calls_mistral() -> None:
    """Missing test c (QA, 4 Nov 2026): held before any page is drawn, so Mistral reads nothing."""
    lead = models.Agent(MODEL, "google-vertex", "Gemini")
    job_reading = _Reading("u1", MODEL)
    mistral = models.Reading("single", lead, scan_reader="mistral", mistral_model="mistral-ocr-2512")
    job_reading.reading = replace(job_reading.reading, reading=mistral)
    reader, pause = ScriptedReader({1: page_read(TOTAL)}), Asked(paused=True)
    system = rig(reader, checker=FakeChecker(), job_reading=job_reading, reading_pause=pause)
    job = system.job(make_pdf([SCANNED]))
    system.processor(job, beat)
    assert reader.opened == [] and reader.read_inside == [] and system.pages(job) == []
    assert system.totals(job).error_code == "paused_daily_limit"


def test_a_spreadsheet_is_asked_with_no_ai_pages() -> None:
    """QA P4 (4 Nov 2026): a spreadsheet has no page for AI to read, so the daily page limit is
    never asked for it (the budget is: its card and search still call a model)."""
    pause = Asked()
    system = rig(ScriptedReader({1: page_read(TOTAL)}), checker=FakeChecker(), reading_pause=pause)
    sheet = system.run(CSV)
    scan = system.run(make_pdf([SCANNED]))
    assert pause.asked == [(sheet.document_id, False), (scan.document_id, True)]
    assert system.status(sheet) == DocumentStatus.READY


@pytest.mark.parametrize(
    ("code", "words"),
    [
        # Changed on purpose (QA C4, 4 Nov 2026): never "tomorrow morning" (a weekend, a raised
        # limit), and the monthly AI limit named, never money.
        (
            "paused_daily_limit",
            "Reading is paused. Your organisation has reached its daily page limit. This file will be"
            " read on its own when the limit resets, or sooner if it is raised.",
        ),
        (
            "paused_budget",
            "Reading is paused. Your organisation has reached its monthly AI limit. It starts again"
            " when the limit is raised or the new month begins.",
        ),
    ],
)
def test_the_people_of_the_organisation_are_told_in_plain_words_never_money(code: str, words: str) -> None:
    said = views.status_message(
        "received",
        None,
        detected_type="pdf",
        max_pages=50,
        attempts=1,
        progress={"step": "waiting"},
        waiting_code=code,
    )
    assert said == words and not MONEY.search(said)
    assert views.in_progress("received", {"step": "waiting"}) is None  # no page count while it waits


def test_why_an_organisation_is_paused() -> None:
    assert pause_reason(ORG) == DAILY_LIMIT
    assert pause_reason(replace(ORG, spent=Decimal("50.00"))) == BUDGET  # the budget first
    assert pause_reason(replace(ORG, pages_today=1999)) is None
    assert pause_reason(replace(ORG, page_limit=None, budget=None, spent=None)) is None


def test_the_super_admins_email_says_who_why_and_what_happens_next() -> None:
    subject, text = email_text(DAILY_LIMIT, ORG)
    assert subject == "Kaagaz: reading is paused for Sharma Traders"
    assert "2,000 of 2,000 pages read today (India time)" in text and "next morning" not in text
    assert "once the server is on again" in text  # QA P5: it stays off over weekends
    _, budget = email_text(BUDGET, ORG)
    assert "$12.40 of $50.00 used this month" in budget and "Ask keeps working" in budget
    mailer = MemoryMailer()
    alert = pause_alert(ALERTING, mailer)
    assert alert is not None and alert(BUDGET, ORG) is True
    assert [(sent.to, sent.subject) for sent in mailer.sent] == [("ops@example.test", subject)]


ALERTING = make_settings(ops_alert_email="ops@example.test", email_from="no-reply@example.test")


def test_an_email_that_fails_is_logged_by_its_code_and_never_raised(caplog: pytest.LogCaptureFixture) -> None:
    class Refused(MemoryMailer):
        def send(self, *, to: str, subject: str, text: str) -> None:
            raise ClientError({"Error": {"Code": "MessageRejected", "Message": "x"}}, "SendEmail")

    alert = pause_alert(ALERTING, Refused())
    assert alert is not None
    with caplog.at_level(logging.ERROR):
        assert alert(DAILY_LIMIT, ORG) is False  # not sent: the day is not noted as told
    [record] = caplog.records
    assert (record.__dict__["purpose"], record.__dict__["error"]) == ("reading_paused", "MessageRejected")
    assert "ops@example.test" not in caplog.text


@pytest.mark.parametrize(
    ("settings", "named"),
    [
        (replace(ALERTING, ops_alert_email=None), "OPS_ALERT_EMAIL"),
        (replace(ALERTING, email_from=None), "EMAIL_FROM"),
    ],
)
def test_alerts_that_are_off_are_said_at_the_start(
    settings: Settings, named: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=reading_pause.__name__):
        assert pause_alert(settings, MemoryMailer()) is None
    [record] = caplog.records
    assert record.levelno == logging.WARNING and record.__dict__["code"] == "pause_alert_off"
    assert named in record.getMessage()


def test_email_enabled_off_never_stops_an_alert() -> None:
    """One rule for every operations alert (kaagaz.ops_alert): OPS_ALERT_EMAIL and EMAIL_FROM;
    EMAIL_ENABLED is for the emails people get."""
    mailer = MemoryMailer()
    alert = pause_alert(replace(ALERTING, email_enabled=False), mailer)
    assert alert is not None and alert(DAILY_LIMIT, ORG) and len(mailer.sent) == 1


def test_the_release_sweep_runs_on_the_first_turn_then_every_five_minutes() -> None:
    now = [datetime(2026, 11, 3, 18, 0, tzinfo=UTC)]
    passes: list[datetime] = []

    class Jobs:
        def run_once(self) -> bool:
            return False

    def release() -> list[Job]:
        passes.append(now[0])
        return [Job("c", "d")]

    sweep = ReleaseHeld(Jobs(), release, lambda: now[0])
    for minutes in (0, 4, 5, 9, 10):
        now[0] = datetime(2026, 11, 3, 18, 0, tzinfo=UTC) + timedelta(minutes=minutes)
        sweep.run_once()
    assert timedelta(minutes=5) == RELEASE_EVERY and [at.minute for at in passes] == [0, 5, 10]
