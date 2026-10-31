"""The accuracy check against max settings and the quality of past scans against PostgreSQL
(#463, Tushar, 31 Oct 2026; 0064): only an operator starts one, with a fresh code; one runs at
a time per organisation and a few an hour on the platform; the job reads only that
organisation's pages, at the draft's settings and at max, keeps numbers only (never a word,
in the table or the log), bills the organisation as accuracy_check, never its budget; and the
estimate shows the latest done check at the same settings and the share of clean pages.

Each test moves its checks out of the last hour when it ends (``old_checks``), so the platform's
hourly limit never reaches another test."""

import io
import logging
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.ai import usage
from kaagaz.ai.openrouter import PageReply
from kaagaz.db import operator_insights
from kaagaz.db import operator_models as ops
from kaagaz.db import usage as usage_sql
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.ingestion.ports import BlobNotFound
from kaagaz.jobs.accuracy import AccuracyChecks
from kaagaz.reading.gemini_ocr import GeminiEngine
from kaagaz.reading.ocr import as_page_engine
from kaagaz.settings import Settings
from tests.db_support import insert_document
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, Api, TestClient, wrong_code
from tests.test_gemini_ocr import FakeFinder, FakeRapidOcr, line, reply, value
from tests.test_isolation_ai_settings import platform, set_org
from tests.test_migration_0023_db import as_owner
from tests.test_operator_auth_db import _Operator, _owner
from tests.test_operator_models_db import _signed_in
from tests.test_scan_cost_settings_db import scan_model

pytestmark = pytest.mark.db
__all__ = ["platform"]  # the fixture, used by name
GEMINI = make_settings(ocr_engine="gemini")
MAX_TEXT = "Bill to Sharma Traders Total ₹12,450 GSTIN 27AAACO1234"
DRAFT_TEXT = "Bill to Sharma Traders Total ₹8,000 GSTIN 27AAACO1234"


@pytest.fixture
def old_checks(db: Database) -> Iterator[None]:
    """Every check this test started is moved out of the last hour when it ends."""
    try:
        yield
    finally:
        as_owner(db, "UPDATE scan_accuracy_checks SET started_at = started_at - interval '2 hours'")


class PageImages:
    """Stored pictures by (customer, document, page); every one asked for is noted."""

    def __init__(self) -> None:
        self.pictures: dict[tuple[str, str, int], bytes] = {}
        self.asked: list[tuple[str, str, int]] = []

    def add(self, customer: str, document: str, page_no: int) -> None:
        out = io.BytesIO()
        Image.new("RGB", (200, 300), (255, 255, 255)).save(out, "WEBP")
        self.pictures[(customer, document, page_no)] = out.getvalue()

    def get_page_image(self, customer_id: str, document_id: str, page_no: int) -> bytes:
        self.asked.append((customer_id, document_id, page_no))
        try:
            return self.pictures[(customer_id, document_id, page_no)]
        except KeyError:
            raise BlobNotFound(document_id) from None


class Reads:
    """The model client: max settings (high thinking) read MAX_TEXT, anything else DRAFT_TEXT;
    each call noted as (model, thinking, picture detail)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str, str]] = []
        self._lock = threading.Lock()

    def now(self) -> float:
        return 1000.0

    def read_page(
        self, *, media_resolution: str, thinking_level: str, model: str | None = None, **_: Any
    ) -> PageReply:
        with self._lock:
            self.calls.append((model, thinking_level, media_resolution))
        usage.record_call(str(model), None, 10, 20, 0, 0.001)  # billed as the real client bills
        words = MAX_TEXT if thinking_level == "high" else DRAFT_TEXT
        return reply(value(line(words, (100, 100, 130, 900))))


def model_page(
    db: Database, images: PageImages, customer: str, *, image: bool = True, stored: bool = True, **page: Any
) -> str:
    """A ready document with page 1 read by a model, its picture kept (``stored`` False: the row
    says so but the picture is gone), read in the last days."""
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
    columns = {
        "read_by": "google/x",
        "read_thinking": None,
        "read_resolution": None,
        "read_quality": "good",
        **page,
    }
    _owner(
        db,
        "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, has_image, route,"
        " read_by, read_quality, read_thinking, read_resolution) VALUES (:c, :d, 1, 200, 300, 'px', :i,"
        " 'ocr', :by, :q, :t, :r)",
        {
            "c": customer,
            "d": document,
            "i": image,
            "by": columns["read_by"],
            "q": columns["read_quality"],
            "t": columns["read_thinking"],
            "r": columns["read_resolution"],
        },
    )
    _owner(
        db,
        "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status)"
        " VALUES (:c, :d, now(), now(), 'ready')",
        {"c": customer, "d": document},
    )
    if image and stored:
        images.add(customer, document, 1)
    return document


def start(api: Api, operator: _Operator, client: TestClient, customer: str, **body: Any) -> Any:
    api.clock.advance(30)  # a fresh code each time
    path = f"/api/operator/orgs/{customer}/accuracy-check"
    return client.post(path, json={**body, "code": api.code(operator.secret)}, headers=CSRF)


def run(db: Database, api: Api, images: PageImages, reads: Reads, settings: Settings = GEMINI) -> None:
    """The worker runs the last job the API queued."""
    local = FakeRapidOcr()
    engine = GeminiEngine(settings, client=reads, pool=FakeFinder(), local=lambda: as_page_engine(local))
    AccuracyChecks(db, images, engine, settings=settings)(api.queue.jobs[-1], lambda: None)


def _org(db: Database, platform: str, api: Api, name: str, **choice: Any) -> tuple[str, str]:
    lead = scan_model(db)
    customer = api.customer(name)[0]
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead, **choice)).code == "done"
    return customer, lead


def test_a_check_reads_only_that_organisations_pages_both_ways_and_keeps_numbers_only(
    db: Database, platform: str, old_checks: None, caplog: pytest.LogCaptureFixture
) -> None:
    api = Api(db, settings=GEMINI)
    operator, client = _signed_in(api)
    images, reads = PageImages(), Reads()
    customer, lead = _org(db, platform, api, "Check Org A", scan_thinking="minimal", scan_resolution="low")
    other, _ = _org(db, platform, api, "Check Org B")
    mine = [model_page(db, images, customer, read_by=lead) for _ in range(3)]
    gone = model_page(db, images, customer, read_by=lead, stored=False)  # skipped and counted
    model_page(db, images, customer, read_by=lead, image=False)  # its picture is not kept: never picked
    model_page(db, images, customer, read_by="rapidocr")  # read locally: never picked
    model_page(db, images, other, read_by=lead)  # another organisation's: never read

    started = start(api, operator, client, customer, scan_thinking="minimal", scan_resolution="low", pages=20)
    assert started.status_code == 202, started.text
    check_id = started.json()["id"]
    assert api.queue.jobs[-1].check_id == check_id and api.queue.jobs[-1].customer_id == customer
    running = client.get(f"/api/operator/orgs/{customer}/accuracy-check/latest").json()
    assert (running["status"], running["finished_at"], running["max_pages"]) == ("running", None, 20)

    with caplog.at_level(logging.DEBUG):
        run(db, api, images, reads)
    assert {asked[:2] for asked in images.asked} == {(customer, document) for document in (*mine, gone)}
    # The draft's settings, the way a scan reads (single: Dynamic is off), and max for the same lead.
    assert sorted(set(reads.calls)) == [(lead, "high", "high"), (lead, "minimal", "low")]
    assert len(reads.calls) == 6

    done = client.get(f"/api/operator/orgs/{customer}/accuracy-check/latest").json()
    assert done["status"] == "done" and done["finished_at"] is not None
    assert (done["pages_checked"], done["pages_failed"]) == (3, 1)
    assert done["cost_usd"] == pytest.approx(0.006)  # six reads, OpenRouter's cost of each
    assert done["word_match"] == pytest.approx(1 - 1 / 8)  # one word of eight
    assert done["numbers_match"] == pytest.approx(1 / 2)  # 12450 lost, the GSTIN kept
    assert done["settings"] == {
        "model": lead,
        "scan_thinking": "minimal",
        "scan_resolution": "low",
        "careful_thinking": GEMINI.ocr_retry_thinking_level,
        "careful_resolution": GEMINI.ocr_retry_media_resolution,
        "page_max_output_tokens": 65536,
    }
    # Numbers only: no word read is in the table or the log.
    stored = as_owner(
        db, "SELECT row_to_json(a)::text FROM scan_accuracy_checks a WHERE a.id = :k", {"k": check_id}
    )
    for word in ("Sharma", "Traders", "12,450", "8,000"):
        assert word not in stored[0][0] and word not in caplog.text
    # Billed to the organisation as accuracy_check, never its budget.
    kinds = as_owner(db, "SELECT DISTINCT kind FROM model_usage WHERE customer_id = :c", {"c": customer})
    assert kinds == [("accuracy_check",)]
    as_owner(db, "UPDATE model_usage SET cost_usd = 5 WHERE customer_id = :c", {"c": customer})
    window = {"start": datetime(2000, 1, 1, tzinfo=UTC), "end": datetime(2100, 1, 1, tzinfo=UTC)}
    with db.customer(customer) as conn:
        assert usage_sql.spend(conn, customer, **window) == (0.0, False)
    with OperatorDatabase(db.engine).operator() as conn:
        health = operator_insights.org_health(conn, operator.id, datetime.now(UTC).date(), "UTC")
    [row] = [row for row in health if str(row["customer_id"]) == customer]
    assert row["month_cost_usd"] is None

    # The estimate shows it at exactly these settings, and nothing at others; the quality too.
    body = {"customer_id": customer, "scan_thinking": "minimal", "scan_resolution": "low"}
    estimate = client.post("/api/operator/scan-cost", json=body, headers=CSRF).json()
    assert estimate["accuracy"] == done
    other_settings = client.post(
        "/api/operator/scan-cost", json={**body, "scan_resolution": "high"}, headers=CSRF
    )
    assert other_settings.json()["accuracy"] is None
    platform_estimate = client.post("/api/operator/scan-cost", json={}, headers=CSRF).json()
    assert platform_estimate["accuracy"] is None


def test_a_check_takes_a_fresh_code_one_at_a_time_and_a_few_an_hour(
    db: Database, platform: str, old_checks: None
) -> None:
    api = Api(db, settings=GEMINI)
    operator, client = _signed_in(api)
    images = PageImages()
    customer, lead = _org(db, platform, api, "Check Limits")
    model_page(db, images, customer, read_by=lead)
    path = f"/api/operator/orgs/{customer}/accuracy-check"
    stale = client.post(path, json={"code": wrong_code(api, operator.secret)}, headers=CSRF)
    assert (stale.status_code, stale.json()["error"]["code"]) == (403, "step_up_failed")
    assert client.post(path, json={}, headers=CSRF).status_code == 400  # no code at all
    assert start(api, operator, client, customer, pages=50).status_code == 202
    again = start(api, operator, client, customer)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "check_running")
    assert start(api, operator, client, customer, pages=30).status_code == 400  # 20, 50 or 100

    # The platform's limit: ACCURACY_CHECKS_PER_HOUR started in the last hour, any organisation.
    others = [_org(db, platform, api, f"Check Busy {n}") for n in range(6)]
    for other, other_lead in others:
        model_page(db, images, other, read_by=other_lead)
    codes = [start(api, operator, client, other).status_code for other, _lead in others]
    assert codes[:5] == [202] * 5
    assert codes[5] == 429
    limited = start(api, operator, client, others[5][0])
    assert limited.json()["error"]["code"] == "rate_limited"
    audited = operator.sql(
        "SELECT count(*) FROM platform_audit WHERE operator_id = :o AND action = 'org.accuracy_check'"
    )
    assert audited == [(6,)]


def test_a_check_is_refused_without_a_page_or_with_a_level_the_model_does_not_take(
    db: Database, platform: str, old_checks: None
) -> None:
    api = Api(db, settings=GEMINI)
    operator, client = _signed_in(api)
    customer, _lead = _org(db, platform, api, "Check No Pages")
    empty = start(api, operator, client, customer)
    assert (empty.status_code, empty.json()["error"]["code"]) == (400, "no_pages")
    low_high = scan_model(db, ("low", "high"))
    assert set_org(db, platform, customer, ops.AiChoice([low_high], low_high)).code == "done"
    refused = start(api, operator, client, customer, scan_thinking="minimal")
    assert (refused.status_code, refused.json()["error"]["code"]) == (400, "level_not_supported")
    unknown = start(api, operator, client, str(uuid.uuid4()))
    assert unknown.status_code == 404
    assert client.get(f"/api/operator/orgs/{customer}/accuracy-check/latest").status_code == 404


def test_the_quality_is_the_share_of_clean_pages_at_these_and_at_max_settings(
    db: Database, platform: str
) -> None:
    api = Api(db, settings=GEMINI)
    _, client = _signed_in(api)
    images = PageImages()
    customer, lead = _org(db, platform, api, "Quality Org", scan_thinking="low")
    at: dict[str, Any] = {"read_by": lead, "read_thinking": "low", "read_resolution": "high"}
    model_page(db, images, customer, **at)
    model_page(db, images, customer, **at, read_quality="uncertain")
    model_page(db, images, customer, read_by=lead, read_thinking="high", read_resolution="high")
    model_page(db, images, customer, read_by=lead)  # an older page: no settings kept
    body = {"customer_id": customer, "scan_thinking": "low"}
    quality = client.post("/api/operator/scan-cost", json=body, headers=CSRF).json()["quality"]
    assert quality == {"clean_share": 0.5, "pages": 2, "max_clean_share": 1.0, "max_pages": 1}
    none = client.post("/api/operator/scan-cost", json={**body, "scan_resolution": "low"}, headers=CSRF)
    assert none.json()["quality"]["clean_share"] is None and none.json()["quality"]["pages"] == 0


@pytest.mark.isolation
def test_no_customer_session_starts_or_reads_a_check(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    admin = api.new_admin("Check Admin")
    member = api.sign_in(admin.customer_id)
    for signed in (admin, member):
        path = f"/api/operator/orgs/{signed.customer_id}/accuracy-check"
        posted = signed.client.post(path, json={"code": "123456"}, headers=CSRF)
        read = signed.client.get(f"{path}/latest")
        assert (posted.status_code, read.status_code) == (401, 401)
    # The app's own role reaches the table only through its organisation's claim and finish.
    with pytest.raises(DBAPIError), db.customer(admin.customer_id) as conn:
        conn.execute(text("SELECT count(*) FROM scan_accuracy_checks"))
