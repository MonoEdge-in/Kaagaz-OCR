"""The #463 council review's backend fixes against PostgreSQL (Tushar, 31 Oct 2026; 0064):

* the estimate prices a page, not a call (C3); every token figure is the scope's (P2, U5, S5);
  the 99th percentile of a first read's output tokens is measured (L1); the free signal
  counts a page by its first read, a re-read's rescue on the draft side and never on the max
  side (U6, C9); its reads have their indexes (P1); the draft's Cost saving is priced and
  checked (U2); and it never reads the defaults' counts (P6);
* an accuracy check: starts count the hour's checks under one lock (L4); a check whose job
  stopped beating shows as failed and frees the next start, and one the queue could not take
  ends failed (L2, S1, C7); a redelivery reads nothing again, a broken picture is one page not
  compared, and a check that compared none or met a key problem fails (S3, S4);
* Careful's level is checked against Careful's model, not the lead's (C8), and a platform
  level an organisation's model does not take reaches it only as one it does (P4, S7, C2);
* the organisation's own Usage screen, its file and its audit log never show the operator's
  checks or the scan cost settings (C4, X3, X1).

Each test that starts a check moves it out of the last hour when it ends (``old_checks``),
and each that saves the platform defaults puts them back (``platform``)."""

import dataclasses
import threading
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.ai.replies import ModelUnavailable
from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.db.listing import usage_rows
from kaagaz.db.usage import usage_report
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.pipeline import JobReading
from kaagaz.reading.ocr import DocumentPages
from tests.db_support import insert_document, new_customer, violation
from tests.pdf_builder import make_pdf
from tests.settings_factory import make_settings
from tests.test_accuracy_check_db import PageImages, Reads, model_page, old_checks, run, start
from tests.test_api_support import CSRF, Api, ApiFakeQueue
from tests.test_ensemble_job_db import FakeReader, deliver, org
from tests.test_ensemble_job_db import start as start_run
from tests.test_gemini_ocr import FakeCache, page
from tests.test_isolation_ai_settings import platform, set_defaults, set_org
from tests.test_migration_0023_db import add_usage, as_owner, catalog_model, new_operator
from tests.test_migration_0037 import _load
from tests.test_operator_auth_db import _owner
from tests.test_operator_models_db import _signed_in
from tests.test_pipeline_gemini import SHARP, FakeChecker, rig
from tests.test_scan_cost_settings_db import SendsClient, _engine, reading_of, scan_model

pytestmark = pytest.mark.db
__all__ = ["old_checks", "platform"]  # the fixtures, used by name
GEMINI = make_settings(ocr_engine="gemini")
M0064 = _load("0064_scan_cost_settings.py")
NO_PLATFORM = ops.AiChoice(chat_levels=["low"], chat_default_level="low", dynamic=False)
SCAN_COST_KEYS = (
    "scan_thinking",
    "scan_resolution",
    "careful_thinking",
    "careful_resolution",
    "page_max_output_tokens",
)
_USAGE = (
    "INSERT INTO model_usage (customer_id, kind, model, cached, calls, pages, input_tokens, output_tokens,"
    " reasoning_tokens, cost_usd, cost_estimated, thinking, resolution, limit_hits)"
    " VALUES (:c, :kind, :m, false, :calls, :pages, :tin, :tout, 0, :cost, false, :t, 'high', :hits)"
)


def ledger(
    db: Database,
    customer: str,
    model: str,
    *,
    calls: int,
    pages: int,
    cost: str,
    tokens: tuple[int, int] = (10, 10),
    hits: int = 0,
    kind: str = "scan",
    thinking: str = "low",
) -> None:
    params = {"c": customer, "kind": kind, "m": model, "calls": calls, "pages": pages, "cost": cost}
    _owner(db, _USAGE, {**params, "tin": tokens[0], "tout": tokens[1], "t": thinking, "hits": hits})


def read_pages(db: Database, customer: str, model: str, pages: list[tuple[Any, ...]]) -> None:
    """One document read in the last days, its pages (first read's thinking, picture detail,
    the pass that produced the text, quality, the first read's output tokens)."""
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
    for page_no, (thinking, resolution, read_pass, quality, tokens) in enumerate(pages, 1):
        _owner(
            db,
            "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, has_image, route,"
            " read_by, read_quality, read_thinking, read_resolution, read_pass, read_output_tokens)"
            " VALUES (:c, :d, :n, 200, 300, 'px', true, 'ocr', :by, :q, :t, :r, :p, :o)",
            {"c": customer, "d": document, "n": page_no, "by": model, "q": quality, "t": thinking,
             "r": resolution, "p": read_pass, "o": tokens},
        )  # fmt: skip
    _owner(
        db,
        "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status)"
        " VALUES (:c, :d, now(), now(), 'ready')",
        {"c": customer, "d": document},
    )


def estimate(client: Any, **body: Any) -> dict[str, Any]:
    reply = client.post("/api/operator/scan-cost", json=body, headers=CSRF)
    assert reply.status_code == 200, reply.text
    result: dict[str, Any] = reply.json()
    return result


# --- the estimate ---------------------------------------------------------------------------


def test_a_page_is_priced_once_and_every_token_figure_is_the_scopes(db: Database, platform: str) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    lead = scan_model(db)
    mine, other = api.customer("Pages Org")[0], api.customer("Pages Other")[0]
    assert set_org(db, platform, mine, ops.AiChoice([lead], lead)).code == "done"
    # A page read in halves: three calls, one page, every call's cost and tokens.
    ledger(db, mine, lead, calls=3, pages=1, cost="0.03", tokens=(3000, 900), hits=1)
    ledger(db, other, lead, calls=2, pages=2, cost="0.02", tokens=(8000, 8000), hits=5)
    reply = estimate(client, customer_id=mine, scan_thinking="low")
    assert reply["first_read"]["pages"] == 3  # every organisation's pages price it
    assert reply["first_read"]["usd_per_page"] == pytest.approx(0.05 / 3)
    tokens = reply["tokens"]
    assert (tokens["pages"], tokens["input_per_page"], tokens["output_per_page"], tokens["limit_hits"]) == (
        1,
        3000,
        900,
        1,
    )  # the organisation's own, a page counted once


def test_the_99th_percentile_of_a_first_reads_output_tokens(db: Database, platform: str) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    lead = scan_model(db)
    customer = api.customer("Percentile Org")[0]
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead)).code == "done"
    read_pages(db, customer, lead, [("low", "high", "first", "good", n) for n in range(1, 20)])
    assert estimate(client, customer_id=customer, scan_thinking="low")["tokens"]["output_p99"] is None  # 19
    read_pages(db, customer, lead, [("low", "high", "first", "good", n) for n in range(20, 101)])
    read_pages(db, customer, lead, [("high", "high", "first", "good", 99_999)])  # max settings: not these
    read_pages(db, customer, lead, [("low", "high", "again", "good", None)])  # read again: no first read's
    assert estimate(client, customer_id=customer, scan_thinking="low")["tokens"]["output_p99"] == 99


def test_the_free_signal_counts_a_re_read_page_on_the_draft_side_and_never_on_max(
    db: Database, platform: str
) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    lead = scan_model(db)
    customer = api.customer("Signal Org")[0]
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead)).code == "done"
    read_pages(
        db,
        customer,
        lead,
        [
            ("low", "high", "again", "good", 10),  # first read at the draft's, rescued by a re-read
            ("low", "high", "first", "uncertain", 10),
            ("high", "high", "again", "good", None),  # read again at max: never the max side
            ("high", "high", "first", "good", 10),
            ("high", "high", "first", "uncertain", 10),
        ],
    )
    quality = estimate(client, customer_id=customer, scan_thinking="low")["quality"]
    assert quality == {"clean_share": 0.5, "pages": 2, "max_clean_share": 0.5, "max_pages": 2}


def test_the_estimates_reads_have_their_indexes(db: Database) -> None:
    indexes = dict(
        as_owner(
            db,
            "SELECT indexname, indexdef FROM pg_indexes"
            " WHERE indexname IN ('pages_read_settings', 'model_usage_page_reads')",
        )
    )
    assert (
        "(read_by, read_thinking, read_resolution) WHERE (read_thinking IS NOT NULL)"
        in indexes["pages_read_settings"]
    )
    assert "(created_at) WHERE" in indexes["model_usage_page_reads"]
    plans = []
    with db.engine.begin() as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        for query in (
            "SELECT count(*) FROM pages p WHERE p.read_by = 'google/x'"
            " AND p.read_thinking IN ('low', 'high')",
            "SELECT count(*) FROM model_usage u WHERE u.kind IN ('scan', 'careful') AND NOT u.cached"
            " AND u.created_at >= now() - interval '30 days'",
        ):
            plans.append(" ".join(row[0] for row in conn.execute(text(f"EXPLAIN {query}"))))
    assert "pages_read_settings" in plans[0] and "model_usage_page_reads" in plans[1]


def test_the_drafts_cost_saving_is_priced_and_checked(db: Database, platform: str, old_checks: None) -> None:
    api = Api(db, settings=GEMINI)
    operator, client = _signed_in(api)
    lead = scan_model(db)
    customer = api.customer("Draft Dynamic Org")[0]
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead, dynamic=False)).code == "done"
    assert estimate(client, customer_id=customer)["re_read"] is None  # null: the platform's (off)
    assert estimate(client, customer_id=customer, dynamic=True)["re_read"] is not None  # the draft's
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead, dynamic=True)).code == "done"
    assert estimate(client, customer_id=customer, dynamic=False)["re_read"] is None

    images = PageImages()
    model_page(db, images, customer, read_by=lead)
    started = start(api, operator, client, customer, dynamic=False)
    assert started.status_code == 202, started.text
    run(db, api, images, Reads())
    [(dynamic,)] = as_owner(
        db, "SELECT dynamic FROM scan_accuracy_checks WHERE id = :k", {"k": started.json()["id"]}
    )
    assert dynamic is False
    assert estimate(client, customer_id=customer, dynamic=False)["accuracy"]["id"] == started.json()["id"]
    assert estimate(client, customer_id=customer, dynamic=True)["accuracy"] is None  # other settings


def test_the_estimate_never_reads_the_defaults_counts(
    db: Database, platform: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    lead = scan_model(db)
    customer = api.customer("No Counts Org")[0]
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead)).code == "done"

    def counts(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the estimate reads the defaults' counts")

    monkeypatch.setattr(ops, "ai_defaults", counts)
    assert estimate(client)["first_read"] is not None
    assert estimate(client, customer_id=customer)["first_read"]["model"] == lead


# --- an accuracy check ----------------------------------------------------------------------


def test_a_start_counts_the_hours_checks_under_the_platforms_lock(db: Database) -> None:
    operator = new_operator(db)
    customer, _ = new_customer(db)
    asked = ("google/x", "low", "high", "high", "high", 65536, False)
    with db.engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": M0064.ACCURACY_LOCK})
        try:
            with pytest.raises(DBAPIError) as waited, OperatorDatabase(db.engine).operator() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '300ms'"))
                ops.start_accuracy_check(conn, operator, customer, asked, 20, step=1)
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": M0064.ACCURACY_LOCK})
    assert violation(waited.value)[0] == "55P03"  # lock_not_available: it waited for the lock


def test_a_check_that_stopped_beating_shows_failed_and_frees_the_next_start(
    db: Database, platform: str, old_checks: None
) -> None:
    api = Api(db, settings=GEMINI)
    operator, client = _signed_in(api)
    lead = scan_model(db)
    customer = api.customer("Stale Check Org")[0]
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead)).code == "done"
    images = PageImages()
    model_page(db, images, customer, read_by=lead)
    latest = f"/api/operator/orgs/{customer}/accuracy-check/latest"

    first = start(api, operator, client, customer).json()["id"]
    run(db, api, images, Reads())  # the job beats as it reads
    [(beat,)] = as_owner(db, "SELECT heartbeat_at FROM scan_accuracy_checks WHERE id = :k", {"k": first})
    assert beat is not None

    # A long check that still beats keeps running and blocks a second one, however old.
    second = start(api, operator, client, customer).json()["id"]
    as_owner(
        db,
        "UPDATE scan_accuracy_checks SET started_at = now() - interval '2 hours'"
        " - CASE WHEN id = :k THEN interval '0' ELSE interval '1 hour' END,"
        " heartbeat_at = CASE WHEN id = :k THEN now() ELSE heartbeat_at END"
        " WHERE subject_customer_id = :c",
        {"k": second, "c": customer},
    )
    assert client.get(latest).json()["status"] == "running"
    assert start(api, operator, client, customer).status_code == 409

    # No beat for ten minutes: failed for the console, and the next start ends it and goes on.
    as_owner(
        db,
        "UPDATE scan_accuracy_checks SET heartbeat_at = now() - interval '11 minutes' WHERE id = :k",
        {"k": second},
    )
    assert client.get(latest).json()["status"] == "failed"
    third = start(api, operator, client, customer)
    assert third.status_code == 202, third.text
    [(status, finished)] = as_owner(
        db, "SELECT status, finished_at FROM scan_accuracy_checks WHERE id = :k", {"k": second}
    )
    assert status == "failed" and finished is not None


def test_a_check_the_queue_could_not_take_ends_failed(db: Database, platform: str, old_checks: None) -> None:
    queue = ApiFakeQueue()
    api = Api(db, settings=GEMINI, queue=queue)
    operator, client = _signed_in(api)
    lead = scan_model(db)
    customer = api.customer("Queue Down Org")[0]
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead)).code == "done"
    model_page(db, PageImages(), customer, read_by=lead)
    queue.fail_enqueue = True
    assert start(api, operator, client, customer).status_code == 503
    assert client.get(f"/api/operator/orgs/{customer}/accuracy-check/latest").json()["status"] == "failed"
    queue.fail_enqueue = False
    assert start(api, operator, client, customer).status_code == 202  # nothing left running


def test_a_check_reads_nothing_twice_and_a_broken_picture_is_one_page_not_compared(
    db: Database, platform: str, old_checks: None
) -> None:
    api = Api(db, settings=GEMINI)
    operator, client = _signed_in(api)
    lead = scan_model(db)
    customer = api.customer("Once Org")[0]
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead)).code == "done"
    images = PageImages()
    model_page(db, images, customer, read_by=lead)
    broken = model_page(db, images, customer, read_by=lead)
    images.pictures[(customer, broken, 1)] = b"not a picture"
    latest = f"/api/operator/orgs/{customer}/accuracy-check/latest"

    assert start(api, operator, client, customer).status_code == 202
    reads = Reads()
    run(db, api, images, reads)
    done = client.get(latest).json()
    assert (done["status"], done["pages_checked"], done["pages_failed"]) == ("done", 1, 1)
    calls = len(reads.calls)
    run(db, api, images, reads)  # the queue delivers it again: nothing is read or billed again
    assert len(reads.calls) == calls

    # A redelivery while the first delivery still reads it (claimed) reads nothing either.
    assert start(api, operator, client, customer).status_code == 202
    check = api.queue.jobs[-1].check_id
    as_owner(db, "UPDATE scan_accuracy_checks SET heartbeat_at = now() WHERE id = :k", {"k": check})
    run(db, api, images, reads)
    assert len(reads.calls) == calls and client.get(latest).json()["status"] == "running"


def test_a_check_that_compared_no_page_or_met_a_key_problem_fails(
    db: Database, platform: str, old_checks: None
) -> None:
    api = Api(db, settings=GEMINI)
    operator, client = _signed_in(api)
    lead = scan_model(db)
    customer = api.customer("Nothing Compared Org")[0]
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead)).code == "done"
    images = PageImages()
    model_page(db, images, customer, read_by=lead, stored=False)  # its picture is gone
    latest = f"/api/operator/orgs/{customer}/accuracy-check/latest"
    assert start(api, operator, client, customer).status_code == 202
    run(db, api, images, Reads())
    assert client.get(latest).json()["status"] == "failed"

    model_page(db, images, customer, read_by=lead)

    class KeyRefused(Reads):
        def read_page(self, **request: Any) -> Any:
            raise ModelUnavailable("key_rejected")

    assert start(api, operator, client, customer).status_code == 202
    with pytest.raises(BackendUnavailable) as key:
        run(db, api, images, KeyRefused())
    assert str(key.value) == "gemini_key"  # up to the worker, which waits as for any job
    assert client.get(latest).json()["status"] == "failed"


# --- Careful's level, and a platform level an organisation's model does not take -------------


def test_careful_level_is_checked_against_careful_models_not_the_leads(
    db: Database, platform: str, old_checks: None
) -> None:
    api = Api(db, settings=GEMINI)
    operator, client = _signed_in(api)
    lead = scan_model(db, ("minimal", "low"))
    dear = catalog_model(db, price_in=20, price_out=120)  # the Careful tier
    as_owner(db, "UPDATE model_catalog SET efforts = ARRAY['low', 'high'] WHERE id = :m", {"m": dear})
    customer = api.customer("Careful Level Org")[0]
    choice = ops.AiChoice([lead, dear], lead, dynamic=True, careful_thinking="high")
    assert set_org(db, platform, customer, choice).code == "done"  # the lead does not take high
    medium = ops.AiChoice([lead, dear], lead, dynamic=True, careful_thinking="medium")
    assert set_org(db, platform, customer, medium).code == "level_not_supported"  # no model takes it

    re_read = estimate(client, customer_id=customer, careful_thinking="high", dynamic=True)["re_read"]
    assert (re_read["model"], re_read["thinking"]) == (dear, "high")
    model_page(db, PageImages(), customer, read_by=lead)
    started = start(api, operator, client, customer, careful_thinking="high", dynamic=True)
    assert started.status_code == 202, started.text


class _LowUnsure(SendsClient):
    """``SendsClient``, a first read at low unsure of its total, so Careful reads it again."""

    def read_page(self, **request: Any) -> Any:
        if request["thinking_level"] == "low":
            request = {**request, "thinking_level": "minimal"}  # SendsClient's unsure read
            with self._lock:
                self.calls.append((request.get("model"), "low", request["media_resolution"]))
            got = super().read_page(**request)
            with self._lock:
                self.calls.pop()  # noted once, at the level really sent
            return got
        return super().read_page(**request)


def test_a_platform_level_an_organisations_model_does_not_take_reaches_it_as_one_it_does(
    db: Database, platform: str
) -> None:
    lead = scan_model(db, ("low", "high"))
    customer, _ = new_customer(db)
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead, dynamic=True)).code == "done"
    # The platform's Careful level is saved later, checked against the platform's own models.
    assert set_defaults(db, platform, dataclasses.replace(NO_PLATFORM, careful_thinking="minimal")) == "done"
    reading = reading_of(db, customer, mode="dynamic")
    assert reading.careful_thinking == "minimal"

    sends = _LowUnsure()
    system = rig(
        _engine(sends, GEMINI),
        checker=FakeChecker("Total: 1,180.00"),
        ocr_model=lead,
        settings=GEMINI,
        job_reading=lambda customer, document: JobReading(None, lead, "google-vertex", reading=reading),
    )
    system.run(make_pdf([SHARP]))
    assert sorted(level for _model, level, _detail in sends.calls) == ["high", "low"]  # Careful: the env's

    again = SendsClient()
    pages = DocumentPages(
        customer_id=customer,
        document_id=str(uuid.uuid4()),
        cache=FakeCache(),
        read_again=frozenset({1}),
        model=lead,
        careful_thinking=reading.careful_thinking,
        levels=reading.accepted(),
    )
    engine = _engine(again, GEMINI)
    with engine.document(pages):
        engine.read_page(page(), page_no=1)
    assert [level for _model, level, _detail in again.calls] == [GEMINI.ocr_retry_thinking_level]


class _NotesThinking(FakeReader):
    """``FakeReader``, noting the level each document is read at."""

    def __init__(self, texts: dict[int, str]) -> None:
        super().__init__(texts)
        self.levels: list[str | None] = []
        self._noted = threading.Lock()

    def document(self, pages: DocumentPages) -> Any:
        with self._noted:
            self.levels.append(pages.thinking)
        return super().document(pages)


def test_ensembles_other_models_read_a_platform_level_only_as_one_they_take(
    db: Database, platform: str
) -> None:
    with org(db) as o:  # three models that take low only
        assert set_defaults(db, platform, dataclasses.replace(NO_PLATFORM, scan_thinking="minimal")) == "done"
        reader = _NotesThinking({1: "Total Rs 4,350 paid 12", 2: "Bill to Patil Stores"})
        deliver(db, o, reader, start_run(db, o))
        assert reader.levels and set(reader.levels) == {GEMINI.ocr_thinking_level}


# --- the organisation's own screens ---------------------------------------------------------


def test_the_admins_usage_screen_and_file_leave_the_operators_checks_out(db: Database) -> None:
    customer, _ = new_customer(db)
    when = datetime(2026, 10, 3, 6, 0, tzinfo=UTC)
    with db.customer(customer) as conn:
        add_usage(conn, customer, kind="scan", cost_usd=0.25, created_at=when)
        add_usage(conn, customer, kind="accuracy_check", cost_usd=5, created_at=when)
        start, end = datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 5, tzinfo=UTC)
        report = usage_report(conn, customer, start=start, end=end, timezone="UTC")
        rows = usage_rows(conn, customer, start=start, end=end, timezone="UTC", user_id=None)
    assert [row["kind"] for row in report["by_kind"]] == ["scan"]
    assert report["totals"]["cost_usd"] == pytest.approx(0.25)
    assert [row["kind"] for row in rows] == ["scan"]
    assert date(2026, 10, 3) == rows[0]["day"]


def test_the_organisations_audit_never_holds_the_scan_cost_settings(db: Database, platform: str) -> None:
    api = Api(db)
    admin = api.new_admin("Audit Org")
    lead = scan_model(db)
    choice = ops.AiChoice(
        [lead],
        lead,
        scan_thinking="minimal",
        scan_resolution="low",
        careful_thinking="high",
        careful_resolution="low",
        page_max_output_tokens=8192,
    )
    assert set_org(db, platform, admin.customer_id, choice).code == "done"
    listed = admin.client.get("/api/admin/audit")
    assert listed.status_code == 200 and "org.ai_settings_set" in listed.text
    exported = admin.client.get("/api/admin/audit/export")
    assert exported.status_code == 200 and "org.ai_settings_set" in exported.text
    for key in SCAN_COST_KEYS:
        assert key not in listed.text and key not in exported.text, key
    [(details,)] = as_owner(
        db, "SELECT details FROM platform_audit WHERE subject_customer_id = :c", {"c": admin.customer_id}
    )
    assert {key: details[key] for key in SCAN_COST_KEYS} == {
        "scan_thinking": "minimal",
        "scan_resolution": "low",
        "careful_thinking": "high",
        "careful_resolution": "low",
        "page_max_output_tokens": 8192,
    }
