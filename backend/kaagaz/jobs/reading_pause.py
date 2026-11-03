"""The reading pause (#478, Tushar, 3 Nov 2026): an organisation's reading stops for the day
at its daily page limit, and for the month at its monthly budget; Ask keeps working.

* **Pause, never reject.** ``ReadingPause.admit`` runs when a document's job starts, before any
  page is drawn: the budget first (this month's spend at or over it), then one page of the
  day's limit is claimed in one statement (``reading_days.claim``), so two workers can never
  both pass it. A document let in is read to its end, whatever its pages: nothing is half
  read, so the most the limit is passed by is the documents already being read. Its claimed
  page is given back when it ends; its pages were counted as they were read.
* **Its own signal.** A paused document raises ``ReadingPaused``, never ``ModelUnavailable``
  (which starts the reader's outage clock, then reads locally with RapidOCR and reads again
  later, paid twice) and never a Mistral wait. The pipeline ends the try (its run's code
  ``paused_daily_limit`` or ``paused_budget``, the list's words: kaagaz/api/views.py) and the
  job is acked: the document is held in the database (``documents.reading_held``), not in
  the queue, so the server can still switch itself off at night (infra/ec2/idle-stop.sh).
* **Released by a sweep.** ``ReleaseHeld`` runs ``release_held`` every RELEASE_EVERY between
  jobs under the sweep lock: each organisation with held documents and no pause now (a new
  India day, a raised limit or budget, a new month) has every hold ended and its waiting
  documents queued again, in one transaction per organisation (a queue that is down rolls the
  release back). The first turn after a start runs it, so a server started in the morning
  queues them at once.
* **The super admin is told** once per organisation per India day (``reading_days.alert_once``),
  by email to OPS_ALERT_EMAIL through the SES mailer; a failure is logged, never raised.
* The operator's accuracy checks never count (kaagaz.ai.usage) and are never paused (their own
  job); Ensemble's runs after the scan are counted, not paused.

Logs hold ids and codes only.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.exc import SQLAlchemyError

from kaagaz.adapters.email import Mailer
from kaagaz.db import reading_days
from kaagaz.db.engine import Database, unavailable
from kaagaz.db.reading_days import State, india_day
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.fair_share import SweepLock, no_lock
from kaagaz.jobs.worker import RunsJobs

_log = logging.getLogger(__name__)

DAILY_LIMIT = "daily_limit"
BUDGET = "budget"
# The run's error code while a document is held (kaagaz/api/views.py says why to its people).
CODES = {DAILY_LIMIT: "paused_daily_limit", BUDGET: "paused_budget"}
# PROVISIONAL: how often held documents are looked at (a raised limit reads them this soon).
RELEASE_EVERY = timedelta(minutes=5)

# Queues one job (SqsQueue.enqueue).
Send = Callable[[Job], None]
# Emails the super admin that an organisation paused, with why (``email_text``).
Alert = Callable[[str, State], None]


class ReadingPaused(Exception):
    """The document's organisation is paused (``reason``: DAILY_LIMIT or BUDGET); the document
    is held. ``code`` is the run's error code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = CODES[reason]


def pause_reason(now: State) -> str | None:
    """Why the organisation is paused now (BUDGET first), or None."""
    if now.budget is not None and now.spent is not None and now.spent >= now.budget:
        return BUDGET
    if now.page_limit is not None and now.pages_today >= now.page_limit:
        return DAILY_LIMIT
    return None


class ReadingPause:
    """The pause around one document's reading (the module notes). ``clock``: aware times."""

    def __init__(self, db: Database, *, clock: Callable[[], datetime], alert: Alert | None = None) -> None:
        self._db = db
        self._clock = clock
        self._alert = alert

    @contextmanager
    def admit(self, customer_id: str, document_id: str) -> Iterator[None]:
        """Let the document be read, holding one page of today's limit to the end of the block;
        or hold it and raise ReadingPaused (the super admin emailed on the day's first pause).
        One transaction of 2 to 4 statements; one more when the block ends."""
        now = self._clock()
        day = india_day(now)
        reason, first, claimed = None, False, False
        with unavailable(), self._db.customer(customer_id) as conn:
            org = reading_days.state(conn, customer_id, day, document_id)
            if pause_reason(org) == BUDGET:
                reason = BUDGET
            elif org.page_limit is not None:
                claimed = reading_days.claim(conn, customer_id, day, org.page_limit)
                reason = None if claimed else DAILY_LIMIT
            if reason is not None:
                reading_days.hold(conn, customer_id, document_id, reason)
                first = reading_days.alert_once(conn, customer_id, day, now=now)
            elif org.held is not None:
                reading_days.unhold(conn, customer_id, document_id)
        if reason is not None:
            _log.warning(
                "reading paused",
                extra={"customer_id": customer_id, "document_id": document_id, "code": CODES[reason]},
            )
            if first and self._alert is not None:
                self._alert(reason, org)
            raise ReadingPaused(reason)
        try:
            yield
        finally:
            if claimed:
                self._give_back(customer_id, document_id, day)

    def _give_back(self, customer_id: str, document_id: str, day: date) -> None:
        try:
            with unavailable(), self._db.customer(customer_id) as conn:
                reading_days.give_back(conn, customer_id, day)
        except (SQLAlchemyError, BackendUnavailable):
            # One page too many on the counter for the day: never a failed document.
            _log.warning(
                "claimed page not given back",
                extra={"customer_id": customer_id, "document_id": document_id, "code": "page_not_given_back"},
            )


def email_text(reason: str, org: State) -> tuple[str, str]:
    """The super admin's email (subject, text): the organisation's name, why and what happens
    next. Never a document or a person."""
    subject = f"Kaagaz: reading is paused for {org.name}"
    if reason == DAILY_LIMIT:
        why = (
            f"It reached its daily page limit: {org.pages_today:,} of {org.page_limit:,} pages read today"
            " (India time). New files wait and are read next morning, or as soon as you raise the limit."
        )
    else:
        why = (
            f"It reached its monthly AI budget: ${org.spent:,.2f} of ${org.budget:,.2f} used this month."
            " New files wait until next month, or until you raise the budget. Ask keeps working."
        )
    return subject, f"Reading is paused for {org.name}. {why}\n\nChange it in the operator console.\n\nKaagaz"


def mail_alert(mailer: Mailer, to: str) -> Alert:
    """``Alert`` by ``mailer`` to ``to`` (OPS_ALERT_EMAIL); a failure is logged by its code only."""

    def alert(reason: str, org: State) -> None:
        subject, body = email_text(reason, org)
        try:
            mailer.send(to=to, subject=subject, text=body)
        except (BotoCoreError, ClientError) as error:
            code = error.response.get("Error", {}).get("Code") if isinstance(error, ClientError) else None
            _log.error(
                "email failed",
                extra={
                    "event": "email_failed",
                    "purpose": "reading_paused",
                    "error": code or type(error).__name__,
                },
            )

    return alert


def release_held(db: Database, send: Send, *, now: datetime) -> list[Job]:
    """End the holds of every organisation no longer paused at ``now`` and queue its waiting
    documents (the module notes); the jobs sent. Database and queue errors propagate: the
    caller logs them. One statement for the organisations with holds, then one each, and one more
    and a send per document of those no longer paused."""
    day = india_day(now)
    with db.system() as conn:
        customers = reading_days.held_customers(conn)
    sent: list[Job] = []
    for customer in customers:
        with db.customer(customer) as conn:
            org = reading_days.state(conn, customer, day)
            if pause_reason(org) is not None:
                continue
            for document in reading_days.release(conn, customer):
                job = Job(customer, document)
                send(job)  # inside the transaction: a queue that is down keeps the holds
                sent.append(job)
    return sent


class ReleaseHeld:
    """Wraps the worker: before a turn, runs ``release`` (``release_held`` bound to the
    database, the queue and the clock) at most every RELEASE_EVERY on ``clock``, under
    ``lock("release_held")``. The first turn runs it; the time is noted first, so a pass that
    fails waits too. O(1) per turn, plus one pass."""

    def __init__(
        self,
        worker: RunsJobs,
        release: Callable[[], list[Job]],
        clock: Callable[[], datetime],
        *,
        lock: SweepLock = no_lock,
    ) -> None:
        self._worker = worker
        self._release = release
        self._clock = clock
        self._lock = lock
        self._next: datetime | None = None

    def run_once(self) -> bool:
        now = self._clock()
        if self._next is None or now >= self._next:
            self._next = now + RELEASE_EVERY
            try:
                with self._lock("release_held") as mine:
                    for job in self._release() if mine else []:
                        _log.info(
                            "held document queued again",
                            extra={
                                "customer_id": job.customer_id,
                                "document_id": job.document_id,
                                "code": "released",
                            },
                        )
            except (SQLAlchemyError, BackendUnavailable) as error:
                _log.error("held documents not released", extra={"code": type(error).__name__})
        return self._worker.run_once()
