"""The cost guard rails, attacked (#520): the reading pause (#478) against the ways round it the
review of #495 named (a failed document read again, a Careful reading, Ensemble after the scan),
and the rate limits against sharing the wrong way (chat a person, read again an organisation, the
AI request allowance an organisation and a user). No model is called.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.api.app import create_app
from kaagaz.api.auth import CurrentUser, current_user
from kaagaz.db import reading_days
from kaagaz.db.documents import mark_for_reprocess
from kaagaz.db.engine import Database, make_engine
from kaagaz.db.models import Agent, Reading
from kaagaz.jobs.reading_pause import BUDGET, DAILY_LIMIT, ReadingPaused
from kaagaz.pipeline import JobReading
from tests.db_support import new_customer
from tests.pdf_builder import make_pdf
from tests.settings_factory import make_settings
from tests.test_api_ensemble import ready, start
from tests.test_api_page_fixes import read_again, scanned
from tests.test_api_reprocess import reprocess
from tests.test_api_support import CSRF, Api, ApiFakeQueue, ApiFakeStorage, FakeAnswers
from tests.test_dynamic_reading import CAREFUL, STANDARD, TOTAL_BOX, ModelClient, engine
from tests.test_gemini_ocr import line, reply, value
from tests.test_pipeline_gemini import SHARP, beat, rig
from tests.test_reading_pause_db import NIGHT, counter, document, gate, held, set_budget, set_limit
from tests.test_reading_pause_review_db import count_today

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


@pytest.mark.db
@pytest.mark.xfail(
    strict=True,
    reason="OPEN #520/#495: Ensemble after the scan is counted, never paused (kaagaz/jobs/reading_pause.py,"
    " 'Ensemble's runs after the scan are counted, not paused'); refusing it past the budget is a product"
    " decision",
)
@pytest.mark.parametrize("reason", [DAILY_LIMIT, BUDGET])
def test_rail11_ensemble_after_the_scan_is_refused_while_reading_is_paused(db: Database, reason: str) -> None:
    api, reviewer, document_id = ready(db)
    customer = reviewer.customer_id
    if reason == DAILY_LIMIT:
        set_limit(db, customer, 1)
        count_today(db, customer, api.clock(), 1)
    else:
        set_budget(db, customer, "1.00")
        with db.customer(customer) as conn:
            reading_days.count(conn, customer, reading_days.india_day(api.clock()), pages=0, cost_usd=5.0)

    refused = start(reviewer, document_id, scope="document")

    assert refused.status_code == 409, refused.text  # today: 202, the run queued and read
    assert refused.json()["error"]["code"] == "reading_paused"
    assert api.queue.jobs == []


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
