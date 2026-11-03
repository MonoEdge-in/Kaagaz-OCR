"""The reading pause (#478, Tushar, 3 Nov 2026): an organisation's reading stops for the day
at its daily page limit, and for the month at its monthly budget; Ask keeps working.

* **Pause, never reject.** ``ReadingPause.admit`` runs when a document's job starts, before any
  page is drawn: the budget first (this month's spend at or over it), then one page of the
  day's limit is claimed in one statement (``reading_days.claim``), so two workers can never
  both pass it. A document let in is read to its end, whatever its pages: nothing is half
  read, so the most the limit is passed by is the documents already being read. Its claimed
  page is given back when it ends; its pages were counted as they were read. The day it was
  let in is kept on it (``documents.reading_admitted``) until it is read to its end, so a
  retry that day (a reader that dropped out, a Mistral wait, a lost lease) is let in again
  with no claim, never held half read (review, 3 Nov 2026).
* **Its own signal.** A paused document raises ``ReadingPaused``, never ``ModelUnavailable``
  (which starts the reader's outage clock, then reads locally with RapidOCR and reads again
  later, paid twice) and never a Mistral wait. The pipeline ends the try (its run's code
  ``paused_daily_limit`` or ``paused_budget``, the list's words: kaagaz/api/views.py) and the
  job is acked: the document is held in the database (``documents.reading_held``), not in
  the queue, so the server can still switch itself off at night (infra/ec2/idle-stop.sh).
* **Released by a sweep.** ``ReleaseHeld`` runs ``release_held`` every RELEASE_EVERY between
  jobs under the sweep lock: each organisation with held documents and no pause now (a new
  India day, a raised limit or budget, a new month) has its waiting documents queued again,
  oldest first, only as many as the pages left today (one page each at least) and at most
  RELEASE_PER_PASS, never one in the Trash. Each hold ends only once its job is queued, so a
  queue that is down keeps every hold it did not queue. One organisation's error is logged
  and the others go on (review, 3 Nov 2026). The first turn after a start runs it, so a
  server started in the morning queues them at once.
* **The super admin is told** once per organisation per India day, by email to
  OPS_ALERT_EMAIL (``pause_alert``, the operations alerts' one rule: kaagaz.ops_alert); the
  day is noted only once the email is sent, so one that failed is tried at the next pause. A
  failure is logged, never raised; alerts that are off are said once, when the worker starts.
* The operator's accuracy checks never count (kaagaz.ai.usage) and are never paused (their own
  job); Ensemble's runs after the scan are counted, not paused.

Logs hold ids and codes only.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError

from kaagaz import ops_alert
from kaagaz.adapters.email import Mailer
from kaagaz.db import reading_days
from kaagaz.db.engine import Database, unavailable
from kaagaz.db.reading_days import State, india_day
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.fair_share import SweepLock, no_lock
from kaagaz.jobs.worker import RunsJobs
from kaagaz.settings import Settings

_log = logging.getLogger(__name__)

DAILY_LIMIT = "daily_limit"
BUDGET = "budget"
# The run's error code while a document is held (kaagaz/api/views.py says why to its people).
CODES = {DAILY_LIMIT: "paused_daily_limit", BUDGET: "paused_budget"}
# PROVISIONAL: how often held documents are looked at (a raised limit reads them this soon).
RELEASE_EVERY = timedelta(minutes=5)
# PROVISIONAL: the most of one organisation's held documents one pass queues (each organisation
# its own, so one with thousands held never keeps the others waiting).
RELEASE_PER_PASS = 100

# Queues one job (SqsQueue.enqueue).
Send = Callable[[Job], None]
# Emails the super admin that an organisation paused, with why (``email_text``); whether it was sent.
Alert = Callable[[str, State], bool]


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
        A document let in today already is let in again with no claim. An organisation with no
        limit and no budget writes nothing. One transaction of 1 to 3 statements; one more when
        the block ends, if a page was claimed or the day noted."""
        now = self._clock()
        day = india_day(now)
        reason, claimed = None, False
        with unavailable(), self._db.customer(customer_id) as conn:
            org = reading_days.state(conn, customer_id, day, document_id)
            again = org.admitted == day  # a retry of a document let in today: read to its end
            if not again and pause_reason(org) == BUDGET:
                reason = BUDGET
            elif not again and org.page_limit is not None:
                claimed = reading_days.claim(conn, customer_id, day, org.page_limit)
                reason = None if claimed else DAILY_LIMIT
            limited = org.page_limit is not None or org.budget is not None
            noted = reason is None and (again or limited or org.held is not None)
            if reason is not None:
                reading_days.hold(conn, customer_id, document_id, reason)
            elif noted and (org.held is not None or not again):
                reading_days.let_in(conn, customer_id, document_id, day)
        if reason is not None:
            _log.warning(
                "reading paused",
                extra={"customer_id": customer_id, "document_id": document_id, "code": CODES[reason]},
            )
            if self._alert is not None and not org.alerted and self._alert(reason, org):
                self._note_alerted(customer_id, day, now)  # only once it was sent
            raise ReadingPaused(reason)
        read = False
        try:
            yield
            read = True
        finally:
            if claimed or (read and noted):
                self._end(customer_id, document_id, day, give_back=claimed, read=read and noted)

    def _note_alerted(self, customer_id: str, day: date, now: datetime) -> None:
        try:
            with unavailable(), self._db.customer(customer_id) as conn:
                reading_days.note_alerted(conn, customer_id, day, now=now)
        except (SQLAlchemyError, BackendUnavailable):
            # Told, but not noted: the next pause today tells them again.
            _log.warning("alert not noted", extra={"customer_id": customer_id, "code": "alert_not_noted"})

    def _end(self, customer_id: str, document_id: str, day: date, *, give_back: bool, read: bool) -> None:
        """The block ended: the claimed page given back, and, read to its end, the day cleared."""
        try:
            with unavailable(), self._db.customer(customer_id) as conn:
                if give_back:
                    reading_days.give_back(conn, customer_id, day)
                if read:
                    reading_days.read_to_end(conn, customer_id, document_id)
        except (SQLAlchemyError, BackendUnavailable):
            # One page too many on the counter for the day, or a later reading that day let in
            # with no claim: never a failed document.
            _log.warning(
                "reading's end not saved",
                extra={
                    "customer_id": customer_id,
                    "document_id": document_id,
                    "code": "reading_end_not_saved",
                },
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


def pause_alert(settings: Settings, mailer: Mailer | None = None) -> Alert | None:
    """The super admin's ``Alert`` (kaagaz.ops_alert's rule; ``mailer``, else SES); None, said
    once in the log as a warning, when alerts are off."""
    problem = ops_alert.missing(settings)
    if problem is not None:
        _log.warning("reading pause alerts are off: %s", problem, extra={"code": "pause_alert_off"})
        return None

    def alert(reason: str, org: State) -> bool:
        subject, body = email_text(reason, org)
        return ops_alert.send_ops_alert(settings, subject, body, purpose="reading_paused", mailer=mailer)

    return alert


def release_held(db: Database, send: Send, *, now: datetime) -> list[Job]:
    """Queue the waiting documents of every organisation no longer paused at ``now`` (the
    module notes); the jobs sent. One organisation's database or queue error is logged with its
    id and the next one goes on. One statement for the organisations with holds; then, for
    each, one transaction of 1 to 3 statements, and a send and a one-statement transaction per
    document queued."""
    day = india_day(now)
    with db.system() as conn:
        customers = reading_days.held_customers(conn)
    sent: list[Job] = []
    for customer in customers:
        try:
            _release(db, send, customer, day, sent)
        except (SQLAlchemyError, BackendUnavailable) as error:
            _log.error(
                "held documents not released",
                extra={"customer_id": customer, "code": type(error).__name__},
            )
    return sent


def _release(db: Database, send: Send, customer: str, day: date, sent: list[Job]) -> None:
    """One organisation's pass: each job queued is added to ``sent``, then its hold ended."""
    with unavailable(), db.customer(customer) as conn:
        org = reading_days.state(conn, customer, day)
        if pause_reason(org) is not None:
            return
        room = RELEASE_PER_PASS if org.page_limit is None else org.page_limit - org.pages_today
        reading_days.end_stale_holds(conn, customer)
        waiting = reading_days.waiting(conn, customer, min(room, RELEASE_PER_PASS))
    for document in waiting:
        job = Job(customer, document)
        send(job)  # first: a queue that is down keeps this hold and the rest
        sent.append(job)
        with unavailable(), db.customer(customer) as conn:
            reading_days.unhold(conn, customer, document)


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
