"""The cost guard rails, attacked (#520): the reading pause (#478) against the ways round it the
review of #495 named (a failed document read again, a Careful reading, Ensemble after the scan),
and the rate limits against sharing the wrong way (chat a person, read again an organisation, the
AI request allowance an organisation and a user). No model is called.
"""

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.api.app import create_app
from kaagaz.api.auth import CurrentUser, current_user
from kaagaz.api.views import held_message, run_json
from kaagaz.db import ensemble as ensemble_sql
from kaagaz.db import reading_days
from kaagaz.db.documents import mark_for_reprocess
from kaagaz.db.engine import Database, make_engine
from kaagaz.db.models import Agent, Reading
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.reading_pause import BUDGET, CODES, DAILY_LIMIT, ReadingPaused, release_held
from kaagaz.pipeline import JobReading
from tests.db_support import insert_document, new_customer
from tests.pdf_builder import make_pdf
from tests.settings_factory import make_settings
from tests.test_api_ensemble import ready, start
from tests.test_api_page_fixes import read_again, scanned
from tests.test_api_reprocess import reprocess
from tests.test_api_support import CSRF, Api, ApiFakeQueue, ApiFakeStorage, FakeAnswers
from tests.test_api_word_review import post
from tests.test_dynamic_reading import CAREFUL, STANDARD, TOTAL_BOX, ModelClient, engine
from tests.test_ensemble_job_db import READ_AGAIN, T0, FakeReader, deliver, run_row
from tests.test_ensemble_job_db import org as ensemble_org
from tests.test_ensemble_job_db import start as ensemble_start
from tests.test_gemini_ocr import line, reply, value
from tests.test_migration_0023_db import as_owner
from tests.test_pipeline_gemini import SHARP, beat, rig
from tests.test_reading_pause_db import NIGHT, counter, document, gate, held, mine, set_budget, set_limit
from tests.test_reading_pause_qa_db import READING_PAUSED
from tests.test_reading_pause_review_db import count_today
from tests.test_word_review_db import scanned as scanned_words

# --- Rail 11: the reading pause -------------------------------------------------------------------


def _fail_inside(db: Database, customer: str, document_id: str, at: datetime) -> None:
    """The document is let in at ``at``, reads a page, and its reading fails for good (a corrupt
    page, retries used up): the pipeline marks it failed. Its day stays on it (reading_admitted)."""
    with pytest.raises(RuntimeError), gate(db, at).admit(customer, document_id):
        count_today(db, customer, at, 1)
        raise RuntimeError("corrupt_file")
    with db.customer(customer) as conn:
        conn.execute(
            text("UPDATE documents SET status = 'failed', failure_code = 'corrupt_file' WHERE id = :d"),
            {"d": document_id},
        )


@pytest.mark.db
@pytest.mark.parametrize("reason", [DAILY_LIMIT, BUDGET])
def test_rail11_read_again_of_a_document_that_failed_today_is_asked_again(db: Database, reason: str) -> None:
    """#495: a document let in today and failed keeps its day. "Read again" of a failed document
    is allowed while paused (it waits for the pause), but it must wait: a new reading is asked
    again, never let in as a retry of the old one, past the limit or the budget."""
    customer, _ = new_customer(db)
    if reason == DAILY_LIMIT:
        set_limit(db, customer, 1)  # its page reaches the limit
    else:
        set_budget(db, customer, "1.00")  # under it when let in
    failed = document(db, customer)
    _fail_inside(db, customer, failed, NIGHT)
    if reason == BUDGET:
        with db.customer(customer) as conn:  # other documents' reading reaches the budget meanwhile
            reading_days.count(conn, customer, reading_days.india_day(NIGHT), pages=0, cost_usd=2.0)

    with db.customer(customer) as conn:  # Read again, as POST /documents/{id}/reprocess does
        assert mark_for_reprocess(conn, customer, failed, per_hour=20) == "failed"

    with pytest.raises(ReadingPaused) as paused, gate(db, NIGHT).admit(customer, failed):
        pytest.fail("a new reading of a failed document was let in past the pause")
    assert paused.value.reason == reason
    assert held(db, customer, failed) == reason


@pytest.mark.db
def test_rail11_a_retry_of_the_same_reading_is_still_never_held_half_read(db: Database) -> None:
    """The fix keeps #478's rule: a delivery retried the same day (no Read again) is let in."""
    customer, _ = new_customer(db)
    set_limit(db, customer, 1)
    started = document(db, customer)
    with pytest.raises(RuntimeError), gate(db, NIGHT).admit(customer, started):
        count_today(db, customer, NIGHT, 1)
        raise RuntimeError("reader dropped out")
    with gate(db, NIGHT).admit(customer, started):
        pass
    assert held(db, customer, started) is None and counter(db, customer, NIGHT)[0] == 1  # type: ignore[index]


class Paused:
    """A reading pause that holds every document, noting each one asked about."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    @contextmanager
    def __call__(self, customer_id: str, document_id: str, *, ai_pages: bool = True) -> Iterator[None]:
        self.asked.append(document_id)
        raise ReadingPaused(BUDGET)
        yield  # never


def test_rail11_a_careful_reading_of_a_paused_organisation_calls_no_model() -> None:
    """Dynamic reading (Standard, then Careful on the hard pages) asked for by a person: held
    before any page is drawn, so neither model is called."""
    client = ModelClient(
        {
            STANDARD: reply(value(line("Total: 1,180.00", TOTAL_BOX, unsure=("1,180.00",)))),
            CAREFUL: reply(value(line("Total: 1,180.00", TOTAL_BOX))),
        }
    )
    reading = Reading(
        "dynamic",
        Agent(STANDARD, "google-vertex", "Standard"),
        careful=Agent(CAREFUL, "google-vertex", "Careful"),
        thinking="low",
    )
    pause = Paused()
    system = rig(
        engine(client, ocr_engine="gemini"),
        checker=None,
        ocr_model=STANDARD,
        job_reading=lambda customer, document: JobReading("u1", STANDARD, "google-vertex", reading=reading),
        reading_pause=pause,
    )
    job = system.job(make_pdf([SHARP]))

    system.processor(job, beat)

    assert pause.asked == [job.document_id]
    assert client.calls == [] and system.pages(job) == []
    assert system.totals(job).error_code == "paused_budget"


def _pause(db: Database, customer: str, reason: str, at: datetime) -> None:
    """The organisation's reading paused at ``at``: its daily page limit reached, or its budget."""
    if reason == DAILY_LIMIT:
        set_limit(db, customer, 1)
        count_today(db, customer, at, 1)
    else:
        set_budget(db, customer, "1.00")
        with db.customer(customer) as conn:
            reading_days.count(conn, customer, reading_days.india_day(at), pages=0, cost_usd=5.0)


@pytest.mark.db
@pytest.mark.parametrize("reason", [DAILY_LIMIT, BUDGET])
@pytest.mark.parametrize("scope", ["document", "flagged"])
def test_rail11_ensemble_start_and_retry_are_refused_while_reading_is_paused(
    db: Database, reason: str, scope: str
) -> None:
    """Tushar, 6 Nov 2026 (#520): starting or retrying an Ensemble run while the organisation is
    paused is 409 reading_paused, in Read again's words; nothing is queued or audited. Stop works."""
    api, reviewer, document_id = ready(db)
    customer = reviewer.customer_id
    first = start(reviewer, document_id, scope=scope)
    assert first.status_code == 202, first.text
    run_id = first.json()["id"]
    assert post(reviewer, f"/api/documents/{document_id}/ensemble/{run_id}/stop").status_code == 200
    queued = len(api.queue.jobs)
    _pause(db, customer, reason, api.clock())

    for refused in (
        start(reviewer, document_id, scope=scope),
        post(reviewer, f"/api/documents/{document_id}/ensemble/{run_id}/retry"),
    ):
        assert refused.status_code == 409, refused.text
        assert refused.json() == {"error": {"code": "reading_paused", "message": READING_PAUSED[reason]}}
    assert len(api.queue.jobs) == queued
    with db.customer(customer) as conn:
        runs = conn.execute(
            text("SELECT count(*) FROM ensemble_runs WHERE document_id = :d"), {"d": document_id}
        )
        assert runs.scalar_one() == 1


@pytest.mark.db
@pytest.mark.parametrize("reason", [DAILY_LIMIT, BUDGET])
def test_rail11_a_queued_ensemble_run_picked_up_while_paused_is_held_then_released(
    db: Database, reason: str
) -> None:
    """Queued before the pause, picked up during it: held (still queued, nothing read, its words
    say why), the job done; the release sweep queues it again once the pause ends, and it reads."""
    with ensemble_org(db) as o:
        run_id = ensemble_start(db, o, scope="document")
        _pause(db, o["c"], reason, T0)
        reader = FakeReader(READ_AGAIN)

        deliver(db, o, reader, run_id)

        held_run = run_row(db, o, run_id)
        assert (held_run["status"], held_run["error_code"], reader.reads) == ("queued", CODES[reason], [])
        with db.customer(o["c"]) as conn:
            error = run_json(conn, o["c"], held_run)["error"]
        assert error == {"code": CODES[reason], "message": held_message(reason)}
        sent: list[Job] = []
        assert [job for job in release_held(db, sent.append, now=T0) if job.customer_id == o["c"]] == []

        if reason == DAILY_LIMIT:
            set_limit(db, o["c"], None)
        else:
            set_budget(db, o["c"], None)
        released = [job for job in release_held(db, sent.append, now=T0) if job.customer_id == o["c"]]

        assert released == [Job(o["c"], o["d"], run_id=run_id)]
        assert run_row(db, o, run_id)["error_code"] is None
        deliver(db, o, reader, run_id)
        assert run_row(db, o, run_id)["status"] == "done" and reader.reads


class Racing:
    """A queue whose send hands ``customer``'s job straight to another worker (``worker``, on its
    own thread and connection) once the organisation is paused again (``pause_again``), and returns
    when that worker is done or waits on a row lock; other organisations' jobs (the shared test
    database's) are only queued. ``join`` waits for the worker and raises what it raised."""

    def __init__(
        self, db: Database, customer: str, pause_again: Callable[[], None], worker: Callable[[], None]
    ) -> None:
        self._db, self._customer, self._pause_again, self._worker = db, customer, pause_again, worker
        self._threads: list[threading.Thread] = []
        self._errors: list[BaseException] = []

    def _run(self) -> None:
        try:
            self._worker()
        except BaseException as error:  # noqa: BLE001 - re-raised by join
            self._errors.append(error)

    def __call__(self, job: Job) -> None:
        if job.customer_id != self._customer:
            return
        self._pause_again()
        thread = threading.Thread(target=self._run)
        self._threads.append(thread)
        thread.start()
        deadline = time.monotonic() + 20
        while thread.is_alive() and time.monotonic() < deadline:
            [(locked,)] = as_owner(
                self._db,
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                " AND wait_event_type = 'Lock' AND state = 'active'",
            )
            if locked:
                return  # it waits on the row the release holds, until that commits
            time.sleep(0.02)

    def join(self) -> None:
        for thread in self._threads:
            thread.join(30)
            assert not thread.is_alive()
        if self._errors:
            raise self._errors[0]


@pytest.mark.db
@pytest.mark.parametrize("reason", [DAILY_LIMIT, BUDGET])
def test_rail11_a_released_ensemble_run_paused_again_at_once_stays_held(db: Database, reason: str) -> None:
    """Review of #520: the sweep queues a held run; another worker takes the job at once, finds
    the organisation paused again and holds it. That hold must survive the sweep ending the old
    one, or the run is queued with no job and no hold, and blocks every later Ensemble on it."""
    with ensemble_org(db) as o:
        run_id = ensemble_start(db, o, scope="document")
        _pause(db, o["c"], reason, T0)
        reader = FakeReader(READ_AGAIN)
        deliver(db, o, reader, run_id)
        _unpause(db, o["c"], reason)
        send = Racing(
            db, o["c"], lambda: _pause_again(db, o["c"], reason), lambda: deliver(db, o, reader, run_id)
        )

        released = [job for job in release_held(db, send, now=T0) if job.customer_id == o["c"]]
        send.join()

        assert released == [Job(o["c"], o["d"], run_id=run_id)]
        held_run = run_row(db, o, run_id)
        assert (held_run["status"], held_run["error_code"], reader.reads) == ("queued", CODES[reason], [])
        _unpause(db, o["c"], reason)  # the next sweep finds it again
        again = release_held(db, lambda job: None, now=T0)
        assert [job for job in again if job.customer_id == o["c"]] == released


@pytest.mark.db
def test_rail11_a_released_document_paused_again_at_once_stays_held(db: Database) -> None:
    """The same race for a held document: the worker's hold lands after the sweep's commit."""
    customer, _ = new_customer(db)
    _pause(db, customer, DAILY_LIMIT, NIGHT)
    waiting = document(db, customer)
    with pytest.raises(ReadingPaused), gate(db, NIGHT).admit(customer, waiting):
        pytest.fail("let in while paused")
    _unpause(db, customer, DAILY_LIMIT)

    def worker() -> None:
        with pytest.raises(ReadingPaused), gate(db, NIGHT).admit(customer, waiting):
            pytest.fail("let in while paused")

    send = Racing(db, customer, lambda: _pause_again(db, customer, DAILY_LIMIT), worker)
    assert mine(release_held(db, send, now=NIGHT), customer) == [waiting]
    send.join()

    assert held(db, customer, waiting) == DAILY_LIMIT
    _unpause(db, customer, DAILY_LIMIT)
    assert mine(release_held(db, lambda job: None, now=NIGHT), customer) == [waiting]


@pytest.mark.db
def test_rail11_held_ensemble_runs_are_released_only_into_the_pages_left_today(db: Database) -> None:
    """Review of #520: three held runs, one page left today: one is queued (one page each at
    least, as documents), the other two stay held."""
    with ensemble_org(db) as o:
        runs = [ensemble_start(db, o, scope="document")]
        for _ in range(2):
            o["d"] = _another_scan(db, o)
            runs.append(ensemble_start(db, o, scope="document"))
        with db.customer(o["c"]) as conn:
            for run in runs:
                ensemble_sql.hold(conn, o["c"], run, CODES[DAILY_LIMIT])
        set_limit(db, o["c"], 5)
        count_today(db, o["c"], T0, 4)

        released = [job for job in release_held(db, lambda job: None, now=T0) if job.customer_id == o["c"]]

        [queued] = [job.run_id for job in released]
        codes = as_owner(
            db, "SELECT id::text, error_code FROM ensemble_runs WHERE customer_id = :c", {"c": o["c"]}
        )
        assert sorted(codes) == sorted((run, None if run == queued else CODES[DAILY_LIMIT]) for run in runs)


def _another_scan(db: Database, o: dict[str, Any]) -> str:
    """One more ready scan in ``o``'s organisation (test_ensemble_job_db.org's); no file stored."""
    with db.customer(o["c"]) as conn:
        made = insert_document(conn, o["c"], uploaded_by=o["u"], detected_type="tiff")
    PgPipelineStore(db).save(scanned_words(o["c"], made, quality="illegible"))
    return made


def _unpause(db: Database, customer: str, reason: str) -> None:
    if reason == DAILY_LIMIT:
        set_limit(db, customer, None)
    else:
        set_budget(db, customer, None)


def _pause_again(db: Database, customer: str, reason: str) -> None:
    """``_pause``'s limit or budget set again (its pages or spend are still counted)."""
    if reason == DAILY_LIMIT:
        set_limit(db, customer, 1)
    else:
        set_budget(db, customer, "1.00")


@pytest.mark.db
def test_rail11_a_held_ensemble_run_can_be_stopped_and_a_running_one_is_read_to_its_end(db: Database) -> None:
    with ensemble_org(db) as o:
        held_id = ensemble_start(db, o, scope="document")
        _pause(db, o["c"], BUDGET, T0)
        deliver(db, o, FakeReader(READ_AGAIN), held_id)
        with db.customer(o["c"]) as conn:
            ensemble_sql.stop(conn, o["c"], o["d"], held_id, user_id=o["u"], now=T0)
        assert (run_row(db, o, held_id)["status"], run_row(db, o, held_id)["error_code"]) == ("stopped", None)

    with ensemble_org(db) as o:
        running = ensemble_start(db, o, scope="document")
        with db.customer(o["c"]) as conn:  # a delivery started it, then lost its lease
            ensemble_sql.claim(conn, o["c"], o["d"], running, now=T0)
        _pause(db, o["c"], BUDGET, T0)
        reader = FakeReader(READ_AGAIN)
        deliver(db, o, reader, running)  # let in already: read to its end, never held half read
        assert run_row(db, o, running)["status"] == "done" and reader.reads


# --- Rail 12: the rate limits --------------------------------------------------------------------


def _chat_client(user_id: str, customer_id: str) -> TestClient:
    """An app whose database is never reached, signed in as ``user_id`` of ``customer_id``. An
    empty question is 400 after the limit is asked, before the database."""
    app = create_app(
        make_settings(chat_questions_per_minute=20),
        db=Database(make_engine("postgresql://unused@localhost:1/unused"), "kaagaz_app"),
        storage=ApiFakeStorage(),
        queue=ApiFakeQueue(),
        answers=FakeAnswers(),
        clock=lambda: NIGHT,
    )
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        user_id=user_id,
        customer_id=customer_id,
        email="a@b.c",
        display_name="A",
        role="member",
        customer_name="C",
        scope="app",
        session_version=1,
        must_change_password=False,
        totp_enabled=False,
        delete_forever_allowed=False,
    )
    return TestClient(app)


def _ask(client: TestClient) -> int:
    return client.post("/api/chat", json={"question": " "}, headers=CSRF).status_code


def test_rail12_chat_is_20_a_minute_for_each_person_never_shared_and_never_reset_by_a_new_session() -> None:
    org, other_org = str(uuid.uuid4()), str(uuid.uuid4())
    me, colleague, stranger = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    mine = _chat_client(me, org)

    assert [_ask(mine) for _ in range(20)] == [400] * 20  # each one asked the limit, then refused empty
    assert _ask(mine) == 429
    assert _ask(_chat_client(me, org)) == 429  # a fresh app and session: the same person, still limited
    assert _ask(_chat_client(colleague, org)) == 400  # another person of the same organisation
    assert _ask(_chat_client(stranger, other_org)) == 400


@pytest.mark.db
def test_rail12_read_again_is_counted_for_the_organisation_across_people_and_pages_never_another(
    db: Database,
) -> None:
    """REPROCESS_PER_HOUR (2 here, 20 in production): two reviewers of one organisation share it,
    a page's read again counts as the document's does, and another organisation has its own."""
    api = Api(db, settings=make_settings(reprocess_per_hour=2))
    first = api.new_signed("reviewer")
    customer = first.customer_id
    second = api.sign_in(customer, "reviewer")
    stranger = api.new_signed("reviewer")
    paged, whole, third = scanned(api, customer), api.add_document(customer), api.add_document(customer)

    assert read_again(first, paged, 1).status_code == 200
    assert reprocess(second, whole).status_code == 200
    refused = reprocess(second, third)
    assert (refused.status_code, refused.json()["error"]["code"]) == (429, "rate_limited")
    assert read_again(first, scanned(api, customer), 2).status_code == 429
    assert reprocess(stranger, api.add_document(stranger.customer_id)).status_code == 200
    assert len(api.queue.jobs) == 3


def _slot(db: Database, customer: str, user: str | None) -> float:
    return db.model_rate_slot(str(uuid.uuid4()), customer, user, 120.0, 1200)


@pytest.mark.db
def test_rail12_an_organisations_ai_request_cap_holds_it_back_and_a_borrowed_user_never_lifts_it(
    db: Database,
) -> None:
    """0046: an organisation capped at 1 a minute waits for its second request; naming another
    organisation's user (with a generous cap of its own) is refused; the other organisation never
    waits for the capped one."""
    capped, capped_user = new_customer(db, name="Capped allowance")
    other, other_user = new_customer(db, name="Other allowance")
    with db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO model_rate_overrides (customer_id, rpm) VALUES (:c, 1)"), {"c": capped}
        )
        conn.execute(
            text("INSERT INTO user_model_rate_overrides (customer_id, user_id, rpm) VALUES (:c, :u, 120)"),
            {"c": other, "u": other_user},
        )

    assert _slot(db, capped, capped_user) == 0
    assert _slot(db, capped, capped_user) == 5.0  # held back: a minute to wait, asked again every 5 s
    with pytest.raises(DBAPIError, match="invalid model rate scope"):
        _slot(db, capped, other_user)  # B's user under A's organisation: refused outright
    assert _slot(db, other, other_user) < 1  # the platform's spacing only, never the capped one's minute
