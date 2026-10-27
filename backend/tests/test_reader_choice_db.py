"""The reader choice (#398) against PostgreSQL: every account chooses how its scans are read, as
the scan level of the AI settings (0038, 0058). "AI reads every page" (Low, High, Max; today's
reading, the default), "Save cost" (``save``: the local reader keeps clean printed pages, the AI
reads the rest) and "Local reader only" (None).

* The rule: the organisation's default and lock, a member's own choice when not locked, the
  ceiling (Cost saving), the platform's PAGE_ROUTING permission; nothing saved is today's.
* The audit row of each change.
* The worker: the uploader's choice reaches the job's reading (``build_processor``'s own query),
  then the reader: AI never routes, Save routes (a clean page makes no model call), Local never
  calls a model.

The rule without a database: test_ai_settings; another organisation's choice:
test_isolation_ai_task_settings."""

import dataclasses
import sys
import uuid
from typing import Any, cast
from unittest import mock

import pytest

from kaagaz.db.engine import Database
from kaagaz.pipeline import JobReading, build_processor
from kaagaz.reading.gemini_ocr import GeminiEngine
from kaagaz.reading.ocr import OcrLine, as_page_engine
from kaagaz.reading.wiring import GEMINI_OCR_MODULE
from kaagaz.scanning.gate import Verdict
from kaagaz.settings import Settings
from tests.fakes import FakeScanner
from tests.pdf_builder import make_pdf
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, Api, Signed
from tests.test_gemini_ocr import FakeClient, FakeFinder, FakeRapidOcr, line, reply, value
from tests.test_operator_auth_db import _owner
from tests.test_pipeline_gemini import MODEL, SCANNED, FakeGeminiModule, PictureStorage, rig

pytestmark = pytest.mark.db

GEMINI = make_settings(ocr_engine="gemini")  # PAGE_ROUTING on: its default since #398


def cost_saving(db: Database, customer_id: str, on: bool = True) -> None:
    """The organisation's Dynamic (Cost saving), which Save reads with."""
    _owner(
        db,
        "INSERT INTO customer_ai_settings (customer_id, dynamic) VALUES (:c, :on)"
        " ON CONFLICT (customer_id) DO UPDATE SET dynamic = EXCLUDED.dynamic",
        {"c": customer_id, "on": on},
    )


def scan_of(signed: Signed) -> dict[str, Any]:
    reply_ = signed.client.get("/api/ai-settings")
    assert reply_.status_code == 200, reply_.text
    scan: dict[str, Any] = {task["task"]: task for task in reply_.json()["tasks"]}["scan"]
    return scan


def put(signed: Signed, path: str, scan: Any) -> Any:
    return signed.client.put(f"/api/ai-settings/{path}", json={"tasks": {"scan": scan}}, headers=CSRF)


def test_nothing_saved_reads_every_page_with_the_ai_and_save_is_offered(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed()
    cost_saving(db, member.customer_id)
    scan = scan_of(member)
    assert (scan["effective"], scan["mine"], scan["organisation"]) == ("low", None, None)
    assert scan["allowed"][:3] == ["none", "save", "low"]


def test_the_organisations_default_and_lock_and_a_members_own_choice_audited(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id)
    cost_saving(db, admin.customer_id)

    assert put(admin, "organisation", {"level": "save", "locked": False}).status_code == 200
    assert scan_of(member)["effective"] == "save"  # the organisation's default
    assert put(member, "me", "none").status_code == 200
    assert scan_of(member)["effective"] == "none"  # not locked: the member's own wins
    assert put(admin, "organisation", {"level": "save", "locked": True}).status_code == 200
    shown = scan_of(member)
    assert (shown["effective"], shown["locked"], shown["mine"]) == ("save", True, "none")
    refused = put(member, "me", "high")
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "locked")

    audited = [row[3] for row in api.audit_rows(admin.customer_id) if row[0].startswith("ai_settings.")]
    assert audited == [
        {"task": "scan", "old": None, "new": "save", "locked": False},
        {"task": "scan", "old": None, "new": "none", "locked": False},
        {"task": "scan", "old": "save", "new": "save", "locked": True},
    ]


def test_save_is_not_available_without_the_platforms_permission_or_cost_saving(db: Database) -> None:
    on = Api(db, settings=GEMINI)
    off = Api(db, settings=dataclasses.replace(GEMINI, page_routing=False))
    admin = on.new_admin()
    cost_saving(db, admin.customer_id)
    assert put(admin, "organisation", {"level": "save", "locked": True}).status_code == 200

    member = off.sign_in(admin.customer_id)  # the platform turned PAGE_ROUTING off since
    shown = scan_of(member)
    assert "save" not in shown["allowed"]
    assert (shown["organisation"], shown["effective"]) == ("save", "low")  # every page by the AI
    other = off.new_signed()
    cost_saving(db, other.customer_id)
    refused = put(other, "me", "save")
    assert (refused.status_code, refused.json()["error"]["code"]) == (400, "not_allowed")

    cost_saving(db, admin.customer_id, on=False)  # the organisation's Cost saving off
    capped = scan_of(on.sign_in(admin.customer_id))
    assert "save" not in capped["allowed"] and capped["effective"] != "save"


# --- the worker -------------------------------------------------------------------------------


def _upload(signed: Signed) -> str:
    sent = signed.client.post(
        "/api/documents",
        data={"user_id": signed.user_id},
        files={"file": ("bill.pdf", b"%PDF-1.7\n" + uuid.uuid4().hex.encode() * 8, "application/pdf")},
        headers=CSRF,
    )
    assert sent.status_code == 201, sent.text
    return str(sent.json()["document"]["id"])


def _job_reading(db: Database, customer_id: str, document_id: str) -> JobReading:
    """The worker's own query for a job (``build_processor``'s ``read_job``)."""
    with mock.patch.dict(sys.modules, {GEMINI_OCR_MODULE: FakeGeminiModule().module}):
        processor = build_processor(
            settings=GEMINI, db=db, storage=PictureStorage(), scanner=FakeScanner(Verdict.CLEAN), model=None
        )
    assert processor._job_reading is not None
    return processor._job_reading(customer_id, document_id)


CLEAN = [
    OcrLine(f"Clean printed line number {n} here", 20, 40 + 40 * n, 590, 60 + 40 * n, 0.99) for n in range(8)
]
BY_AI = reply(value(line("Read by the model", (100, 100, 130, 900))))


def _read(job_reading: JobReading) -> tuple[list[Any], list[str | None], str]:
    """(the model calls, each page's reader, the run config's reader) of a one-page clean scan
    read as ``job_reading``."""
    client = FakeClient(respond=lambda _image: BY_AI)
    local = FakeRapidOcr()
    local.lines = CLEAN
    settings: Settings = dataclasses.replace(GEMINI, ocr_thinking_level="low", page_image_dpi=72, ocr_dpi=72)
    engine = GeminiEngine(
        settings, client=cast(Any, client), pool=FakeFinder(), local=lambda: as_page_engine(local)
    )
    system = rig(engine, ocr_model=MODEL, settings=settings, job_reading=lambda _c, _d: job_reading)
    job = system.run(make_pdf([SCANNED]))
    config = system.totals(job).config["reading"]
    return client.calls, [record.read_by for record in system.pages(job)], config["reader"]


def test_the_uploaders_choice_decides_how_the_worker_reads_their_upload(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed()
    colleague = api.sign_in(member.customer_id)
    cost_saving(db, member.customer_id)
    assert put(colleague, "me", "none").status_code == 200  # a colleague's choice is theirs alone

    # Nothing saved: the upload's own reading, as before (single with no "Read with" sent).
    expected = {
        None: ("ai", "single", False),
        "save": ("save", "dynamic", True),
        "none": ("local", "local", False),
    }
    for level, (reader, mode, routes) in expected.items():
        assert put(member, "me", level).status_code == 200
        job = _job_reading(db, member.customer_id, _upload(member))
        assert job.reading is not None and job.user_id == member.user_id
        scan = job.reading
        assert (scan.reader, scan.mode, scan.routes) == (reader, mode, routes), level

        calls, read_by, recorded = _read(job)
        assert recorded == reader  # in the run's config
        if reader == "ai":  # every page by the AI, never routed
            assert calls and read_by != ["rapidocr"], level
        else:  # Save keeps the clean page local; Local never calls a model
            assert (calls, read_by) == ([], ["rapidocr"]), level
