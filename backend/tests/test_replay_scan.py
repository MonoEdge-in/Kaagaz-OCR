"""The replay test (fast scan, council 16 Oct 2026, technique 0): the same recorded model
replies, fed to the pipeline, must save byte-identical output, so a change that only makes
reading faster is proved not to change what is saved.

``ReplayClient`` is the model client: it answers each page request from the recorded
replies in tests/fixtures/replay_scan.json, keyed by a hash of the request (model,
provider, thinking level, media resolution, instruction, schema and the picture's size and
a coarse look at its pixels: robust to small drawing differences between pypdfium2
versions, never to a different page or half). A request that was never recorded fails the
test, and every request is counted, so the calls are the same too, in any thread order.

Three documents, as the council asked: a page whose reply is cut off (read in halves), an
Ensemble document (one other model fails on page 1) and a Dynamic document with Careful
pages. The snapshot is each one's saved pages, values (labels, Verified, boxes, votes), the
reader's output (lines and boxes), run_config.reading and the tokens.

The replies are synthetic (no live key here; ``respond`` below wrote them). To record them
again, with no other change, run ``KAAGAZ_REPLAY_RECORD=1 pytest tests/test_replay_scan.py``
on the old code: it writes the fixture, and the new code must then pass unchanged.
"""

import hashlib
import io
import json
import os
import threading
import unittest
from collections import Counter
from pathlib import Path
from typing import Any, ClassVar

from PIL import Image, ImageDraw, ImageStat

from kaagaz.ai.openrouter import PageReply
from kaagaz.ai.replies import ModelOutputError, ModelUnavailable
from kaagaz.db.models import Agent, Reading
from kaagaz.pipeline import JobReading
from kaagaz.reading.gemini_ocr import GeminiEngine
from kaagaz.reading.ocr import as_page_engine
from tests.pdf_builder import Page, Picture, make_pdf
from tests.test_gemini_ocr import FakeFinder, FakeRapidOcr, line, value
from tests.test_pipeline_gemini import CUSTOMER, SETTINGS, FakeChecker, rig

FIXTURE = Path(__file__).parent / "fixtures" / "replay_scan.json"
RECORD = os.environ.get("KAAGAZ_REPLAY_RECORD") == "1"
LEAD = "google/gemini-3.8-flash"
OTHER = "google/gemini-3.1-pro-preview"
THIRD = "anthropic/claude-sonnet-5.5"
CAREFUL = "google/gemini-3.1-pro-preview"
VERTEX = "google-vertex"
# The pages' grey (each page its own) and where the found box sits (pixels at 72 DPI).
TINTS = {250: 1, 225: 2, 200: 3}
FOUND = (60.0, 395.0, 400.0, 421.0)
TOTAL_BOX = (500, 100, 530, 650)


def picture(tint: int) -> Page:
    """A page of grey ``tint`` with a black band along its top and two dark bars of ink."""
    image = Image.new("RGB", (612, 792), (tint, tint, tint))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 611, 62), fill=(0, 0, 0))
    draw.rectangle((60, 300, 550, 312), fill=(40, 40, 40))
    draw.rectangle((60, 600, 500, 612), fill=(40, 40, 40))
    return Page(picture=Picture(image, 0, 0, 612, 792))


def look(png: bytes) -> tuple[int, int, int, int, int]:
    """(width, height, grey step, top dark, bottom dark): coarse on purpose (module notes)."""
    with Image.open(io.BytesIO(png)) as image:
        grey = image.convert("L")
    width, height = grey.size
    band = max(1, height // 25)
    top = ImageStat.Stat(grey.crop((0, 0, width, band))).mean[0]
    bottom = ImageStat.Stat(grey.crop((0, height - band, width, height))).mean[0]
    median = ImageStat.Stat(grey).median[0]
    return width, height, round(median / 25), int(top < 128), int(bottom < 128)


def request_key(request: dict[str, Any]) -> str:
    body = {
        "model": request["model"],
        "provider": request["provider"],
        "thinking": request["thinking_level"],
        "resolution": request["media_resolution"],
        "instruction": hashlib.sha256(request["instruction"].encode()).hexdigest(),
        "schema": hashlib.sha256(json.dumps(request["schema"], sort_keys=True).encode()).hexdigest(),
        "look": look(request["png"]),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


class ReplayClient:
    """The model client (``PageClient``) answering from ``replies`` (module notes)."""

    def __init__(self, replies: dict[str, dict[str, Any]]) -> None:
        self.replies = replies
        self.calls: Counter[str] = Counter()
        self._lock = threading.Lock()

    def now(self) -> float:
        return 1000.0

    def read_page(self, **request: Any) -> PageReply:
        key = request_key(request)
        with self._lock:
            self.calls[key] += 1
            if key not in self.replies:
                if not RECORD:
                    raise AssertionError("a request that was never recorded")
                self.replies[key] = respond(request)
            recorded = self.replies[key]
        if "error" in recorded:
            tokens = {"input_tokens": recorded["input_tokens"], "output_tokens": recorded["output_tokens"]}
            if recorded["error"] == "unavailable":
                raise ModelUnavailable(recorded["code"], **tokens)
            raise ModelOutputError(recorded["code"], **tokens)
        return PageReply(recorded["value"], recorded["input_tokens"], recorded["output_tokens"], "STOP")


def respond(request: dict[str, Any]) -> dict[str, Any]:
    """The synthetic replies the fixture was recorded from (RECORD only)."""
    _width, height, grey, top, _bottom = look(request["png"])
    page_no = min(TINTS.items(), key=lambda item: abs(item[0] / 25 - grey))[1]
    model, half = request["model"], height < 700
    fine = {"input_tokens": 1700, "output_tokens": 900}
    if model == LEAD and request["thinking_level"] == "low" and page_no == 3 and not half:
        return {"error": "output", "code": "output_truncated", "input_tokens": 1700, "output_tokens": 8192}
    if half:  # the cut-off page's halves: a table in each, and the total in the bottom one
        cell = {"id": "t1", "row": 0, "col": 0, "header": False}
        if top:
            lines = [
                line("Item", (200, 100, 260, 400), kind="table_cell", table={**cell, "header": True}),
                line("Rice 10 kg", (400, 100, 460, 400), kind="table_cell", table={**cell, "row": 1}),
            ]
        else:
            lines = [
                line("Wheat 5 kg", (300, 100, 360, 400), kind="table_cell", table=cell),
                line("Total: 1,180.00", (700, 100, 760, 650), order=1),
            ]
        return {"value": value(*lines), **fine}
    if model == THIRD and page_no == 1:
        return {"error": "unavailable", "code": "server_error", "input_tokens": 0, "output_tokens": 0}
    total = line("Total: 1,180.00", TOTAL_BOX, unsure=("1,180.00",) if model == LEAD else ())
    if page_no == 1:
        quality = "fair" if model == LEAD else "good"
        return {
            "value": value(line("Invoice No: INV-17", (100, 100, 130, 500)), total, quality=quality),
            **fine,
        }
    if page_no == 2:
        amount = "Amount: 560.00" if model == THIRD else "Amount: 500.00"
        return {
            "value": value(line(amount, TOTAL_BOX), line("Date: 12/10/2026", (600, 100, 630, 500))),
            **fine,
        }
    return {"value": value(line("Paid by: Ramesh", TOTAL_BOX), handwritten=True), **fine}


def _engine(client: ReplayClient) -> GeminiEngine:
    local = FakeRapidOcr()
    return GeminiEngine(SETTINGS, client=client, pool=FakeFinder(FOUND), local=lambda: as_page_engine(local))


def _scan(client: ReplayClient, tints: list[int], reading: Reading | None) -> dict[str, Any]:
    """One document through the pipeline, and what it saved, as text."""

    def job_reading(customer: str, document: str) -> JobReading:
        return JobReading(None, LEAD, VERTEX, reading=reading)

    system = rig(
        _engine(client),
        checker=FakeChecker("Total: 1,180.00"),
        ocr_model=LEAD,
        job_reading=None if reading is None else job_reading,
    )
    job = system.run(make_pdf([picture(tint) for tint in tints]))
    totals = system.totals(job)
    return {
        "status": str(system.status(job)),
        "pages": [repr(page) for page in system.pages(job)],
        "values": [repr(item) for item in system.store.values_for(CUSTOMER, job.document_id)],
        "outputs": [repr(output) for output in system.store.outputs_for(CUSTOMER, job.document_id)],
        "reading": totals.config.get("reading"),
        "counters": [totals.pages_gemini, totals.pages_local, totals.input_tokens, totals.output_tokens],
    }


class ReplayTest(unittest.TestCase):
    """PR0's proof: the saved output and the calls of the three documents are unchanged."""

    recorded: ClassVar[dict[str, Any]] = (
        {"replies": {}, "snapshot": {}} if RECORD else json.loads(FIXTURE.read_text("utf-8"))
    )

    @classmethod
    def tearDownClass(cls) -> None:
        if RECORD:
            FIXTURE.write_text(json.dumps(cls.recorded, sort_keys=True) + "\n", "utf-8", newline="\n")

    def check(self, name: str, tints: list[int], reading: Reading | None) -> None:
        client = ReplayClient(self.recorded["replies"])
        saved = _scan(client, tints, reading)
        saved["calls"] = sorted(client.calls.items())
        snapshot = json.loads(json.dumps(saved, sort_keys=True))  # tuples as lists, as stored
        if RECORD:
            self.recorded["snapshot"][name] = snapshot
        self.assertEqual(snapshot, self.recorded["snapshot"][name])
        self.assertEqual(
            json.dumps(snapshot, sort_keys=True), json.dumps(self.recorded["snapshot"][name], sort_keys=True)
        )

    def test_a_cut_off_page_read_in_halves(self) -> None:
        self.check("cut_off", [200], None)

    def test_an_ensemble_document_with_one_model_failing(self) -> None:
        # Changed on purpose (Ensemble after the scan, 16 Oct 2026): the snapshot was recorded
        # again with the same replies and calls; the values carry no vote now, and the total
        # the vote had made Verified stays Needs review (unsure_text). No scan asks for this
        # reading any more (db.models.resolve_scan).
        reading = Reading(
            "ensemble",
            Agent(LEAD, VERTEX, "Standard"),
            others=(Agent(OTHER, VERTEX, "Pro"), Agent(THIRD, VERTEX, "Sonnet")),
            thinking="low",
        )
        self.check("ensemble", [250, 225], reading)

    def test_a_dynamic_document_with_careful_pages(self) -> None:
        # Careful reads at OCR_RETRY_THINKING_LEVEL (Tushar, 16 Oct 2026) and the old code at
        # the document's level: both are "high" here, so the requests are the same in both.
        # Changed on purpose (Cost saving, 16 Oct 2026; recorded again): the reasons end with
        # the path taken. Page 1 (1 hard line of 2, over HARD_SHARE) and page 3 (handwritten,
        # page wide) are still read whole, so the calls and values are the same.
        # Changed on purpose (Cost saving tuning, 16 Oct 2026; recorded again, same replies and
        # calls): each escalated page carries its first-read and Careful tokens, and kept_quick.
        # Changed on purpose (production QA B1, 5 Nov 2026; recorded again): page 3 is only
        # flagged handwritten, every word sure, so it keeps its first read: one Careful call
        # fewer, the same values.
        reading = Reading(
            "dynamic",
            Agent(LEAD, VERTEX, "Standard"),
            careful=Agent(CAREFUL, VERTEX, "Careful"),
            thinking="high",
        )
        self.check("dynamic", [250, 225, 200], reading)
