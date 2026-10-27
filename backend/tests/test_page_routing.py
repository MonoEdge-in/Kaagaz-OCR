"""Routing each scanned page by its content (#384) and reading only its hard regions again
(#385). No network, no database: the Gemini reader with scripted clients and a fake local
reader (tests/test_gemini_ocr.py, tests/test_cost_saving.py), the resolver with its fake
connection (tests/test_ai_resolve.py), and the pipeline's rig (tests/test_pipeline_gemini.py).

* ``route_page`` on made-up local reads: clean print stays local, a few unsure lines go as
  regions, anything else (handwriting, low confidence, a table, small text, Devanagari, few
  words, nothing found) goes to the model.
* The account's settings: only the reader choice Save cost (#398) routes; None never reaches
  the model, nothing saved, Low, High, Max and single read every page with it.
* The engine: a local page makes no call, a hard page one, a regions page one crops call whose
  lines alone change, bounded per document; a read again is never routed.
* Read again by region (F5): the cached read with only the lines in the region read again,
  harder, zoomed, and kept in the cache; else the whole page as before.
"""

import dataclasses
import io
import unittest
from typing import Any, cast

from PIL import Image

from kaagaz.ai.replies import ModelOutputError
from kaagaz.db.models import Agent, Reading
from kaagaz.pipeline import JobReading
from kaagaz.reading.gemini_ocr import (
    CROP_GAP,
    PAGE_SCHEMA,
    GeminiEngine,
    crop_boxes,
    crop_strip,
    region_zoom,
)
from kaagaz.reading.ocr import MAX_OCR_PIXELS, DocumentPages, OcrLine, PageRead, as_page_engine
from kaagaz.reading.page_route import LOCAL, REGIONS, RouteRules, route_page, table_share
from kaagaz.reading.page_route import MODEL as BY_MODEL
from kaagaz.settings import Settings
from tests.pdf_builder import make_pdf
from tests.settings_factory import make_settings
from tests.test_ai_resolve import FakeConn, row, scan
from tests.test_cost_saving import Client, crops_reply
from tests.test_gemini_ocr import (
    CUSTOMER,
    DOCUMENT,
    MODEL,
    FakeCache,
    FakeClient,
    FakeFinder,
    FakeRapidOcr,
    line,
    page,
    reply,
    value,
)
from tests.test_pipeline_gemini import CUSTOMER as JOB_CUSTOMER
from tests.test_pipeline_gemini import SCANNED, TOTAL, ScriptedReader, beat, page_read, rig

RULES = RouteRules()
GEMINI = make_settings(ocr_engine="gemini", ocr_thinking_level="high")
FLASH, PRO = "google/gemini-3.8-flash", "google/gemini-3.1-pro-preview"


def local_line(n: int, *, text: str = "", confidence: float = 0.98, height: float = 40.0) -> OcrLine:
    """Line n of a local read: 100..900 across, ``height`` high, its top 90 pixels below the last."""
    top = 100.0 + 90 * n
    return OcrLine(text or f"Clean printed line number {n}", 100.0, top, 900.0, top + height, confidence)


def local_read(*lines: OcrLine) -> PageRead:
    return PageRead(
        page_no=1, lines=lines, quality=None, handwritten=False, scripts=(), issues=(), read_by="rapidocr"
    )


def clean(count: int = 20) -> list[OcrLine]:
    return [local_line(n) for n in range(count)]


# --- the decision ---------------------------------------------------------------------------


class RouteTest(unittest.TestCase):
    def test_clean_print_keeps_the_local_read(self) -> None:
        self.assertEqual(route_page(local_read(*clean()), RULES).route, LOCAL)

    def test_a_few_unsure_lines_are_read_as_regions(self) -> None:
        lines = clean()
        lines[3] = local_line(3, confidence=0.61)
        lines[10] = local_line(10, confidence=0.80)
        decision = route_page(local_read(*lines), RULES)
        self.assertEqual((decision.route, decision.reason, decision.lines), (REGIONS, "few_unsure", (3, 10)))

    def test_low_confidence_over_the_count_or_the_share_goes_to_the_model(self) -> None:
        many = [local_line(n, confidence=0.7 if n < 7 else 0.99) for n in range(20)]  # 7 > 6 lines
        self.assertEqual(route_page(local_read(*many), RULES).reason, "unsure")
        share = [local_line(n, confidence=0.7 if n < 3 else 0.99) for n in range(10)]  # 30% > 15%
        self.assertEqual(route_page(local_read(*share), RULES).route, BY_MODEL)
        none = RouteRules(region_lines_max=0)  # regions switched off: any unsure line is the model's
        one = [local_line(n, confidence=0.7 if n == 0 else 0.99) for n in range(20)]
        self.assertEqual(route_page(local_read(*one), none).route, BY_MODEL)

    def test_handwriting_signs_go_to_the_model(self) -> None:
        heights = [20, 70, 35, 90, 25, 60, 40, 110, 30, 80]  # sure of every word, all sizes
        lines = [local_line(n, height=h) for n, h in enumerate(heights)]
        self.assertEqual(route_page(local_read(*lines), RULES).reason, "handwriting")

    def test_a_heading_among_printed_lines_is_not_handwriting(self) -> None:
        lines = [local_line(0, height=80), *(local_line(n) for n in range(1, 20))]
        self.assertEqual(route_page(local_read(*lines), RULES).route, LOCAL)

    def test_a_table_goes_to_the_model(self) -> None:
        cells = [
            OcrLine(
                f"cell {r}-{c} amount", 100.0 + 250 * c, 100.0 + 60 * r, 300.0 + 250 * c, 140.0 + 60 * r, 0.99
            )
            for r in range(6)
            for c in range(3)
        ]
        self.assertEqual(table_share(tuple(cells)), 1.0)
        self.assertEqual(route_page(local_read(*cells), RULES).reason, "table")

    def test_small_text_devanagari_few_words_and_no_text_go_to_the_model(self) -> None:
        small = [local_line(n, height=12) for n in range(20)]
        self.assertEqual(route_page(local_read(*small), RULES).reason, "small_text")
        hindi = [*clean(19), local_line(19, text="कुल राशि 1,180")]
        self.assertEqual(route_page(local_read(*hindi), RULES).reason, "script")
        self.assertEqual(route_page(local_read(local_line(0, text="Signed")), RULES).reason, "few_words")
        self.assertEqual(route_page(local_read(), RULES).reason, "no_text")

    def test_a_line_with_no_confidence_is_unsure(self) -> None:
        lines = clean()
        lines[5] = dataclasses.replace(lines[5], confidence=None)
        self.assertEqual(route_page(local_read(*lines), RULES).lines, (5,))


# --- the organisation's settings (``db.models.resolve_scan``) ---------------------------------


def _document(*saved: tuple[bool, str, bool], mode: str = "dynamic") -> dict[str, Any]:
    return {"read_model": None, "read_mode": mode, "scan_settings": [list(item) for item in saved]}


DYNAMIC_ON = {"scan_thinking": "high", "dynamic": True}
TIERS = [row(FLASH, default=True), row(PRO, price=(10, 40))]


def _reading(
    document: dict[str, Any], *, settings: dict[str, Any] = DYNAMIC_ON, routing: bool = True
) -> Reading:
    configured = dataclasses.replace(GEMINI, page_routing=routing)
    return scan(FakeConn(scan=TIERS, settings=settings, document=document), configured)


class SettingsTest(unittest.TestCase):
    def test_only_the_reader_choice_save_routes_and_only_with_page_routing(self) -> None:
        # Changed on purpose (#398): routing is the account's choice (Save cost), PAGE_ROUTING
        # only permits it; nothing saved and Low (Automatic) read every page with the model.
        self.assertTrue(_reading(_document((True, "save", False))).routes)
        self.assertTrue(_reading(_document((True, "save", False), mode="single")).routes)
        self.assertFalse(_reading(_document()).routes)  # nothing saved: as before
        self.assertFalse(_reading(_document((True, "low", False))).routes)
        self.assertFalse(_reading(_document((True, "save", False)), routing=False).routes)

    def test_ai_off_never_routes_to_the_model(self) -> None:
        reading = _reading(_document((True, "none", False)))
        self.assertEqual((reading.mode, reading.routes), ("local", False))

    def test_every_page_by_the_model_stays_so(self) -> None:
        for level in ("high", "max"):
            self.assertFalse(_reading(_document((True, level, False))).routes, level)
        revoked = _reading(_document((True, "save", False)), settings={**DYNAMIC_ON, "dynamic": False})
        self.assertEqual((revoked.mode, revoked.routes), ("single", False))  # Cost saving off
        self.assertFalse(_reading(_document(mode="single")).routes)


# --- the engine -----------------------------------------------------------------------------


def _engine(
    client: Any, local: FakeRapidOcr, *, finder: FakeFinder | None = None, **settings: Any
) -> GeminiEngine:
    return GeminiEngine(
        make_settings(**settings),
        client=cast(Any, client),
        pool=finder or FakeFinder(),
        local=lambda: as_page_engine(local),
    )


def _local(lines: list[OcrLine]) -> FakeRapidOcr:
    local = FakeRapidOcr()
    local.lines = lines
    return local


def _routed_pages(**fields: Any) -> DocumentPages:
    return DocumentPages(
        customer_id=CUSTOMER, document_id=DOCUMENT, cache=FakeCache(), route_pages=True, routed={}, **fields
    )


WHOLE = reply(value(line("Read by the model", (100, 100, 130, 900))))


def _unsure_page() -> list[OcrLine]:
    lines = clean()
    lines[3] = local_line(3, text="Total amount 1,18O.00", confidence=0.6)
    lines[10] = local_line(10, text="Paid by Sharrna Traders", confidence=0.7)
    return lines


class EngineTest(unittest.TestCase):
    def test_a_clean_page_is_kept_local_with_no_model_call(self) -> None:
        client, local = FakeClient(), _local(clean())
        pages = _routed_pages(memo={})
        engine = _engine(client, local)
        with engine.document(pages):
            read = engine.read_page(page(), page_no=1)
        self.assertEqual((read.read_by, len(read.lines), client.calls), ("rapidocr", 20, []))
        self.assertEqual(pages.routed, {1: (LOCAL, "clean")})
        self.assertEqual(list(cast(dict[Any, PageRead], pages.memo).values()), [read])  # Dynamic's memo

    def test_a_hard_page_is_read_whole_by_the_model(self) -> None:
        heights = [20, 70, 35, 90, 25, 60, 40, 110, 30, 80]
        client = FakeClient(WHOLE)
        pages = _routed_pages()
        engine = _engine(client, _local([local_line(n, height=h) for n, h in enumerate(heights)]))
        with engine.document(pages):
            read = engine.read_page(page(), page_no=1)
        self.assertEqual((read.read_by, [c.schema for c in client.calls]), (MODEL, [PAGE_SCHEMA]))
        self.assertEqual(pages.routed, {1: (BY_MODEL, "handwriting")})

    def test_ai_off_never_calls_the_model_even_for_a_hard_page(self) -> None:
        client, local = FakeClient(), _local([local_line(0, text="Signed", confidence=0.3)])
        engine = _engine(client, local)
        with engine.document(_routed_pages(local=True)):
            read = engine.read_page(page(), page_no=1)
        self.assertEqual((read.read_by, client.calls, local.pages), ("rapidocr", [], 1))

    def test_without_routing_every_page_is_the_models_and_the_local_reader_is_not_asked(self) -> None:
        client, local = FakeClient(WHOLE), _local(clean())
        engine = _engine(client, local)
        with engine.document(DocumentPages(customer_id=CUSTOMER, document_id=DOCUMENT, cache=FakeCache())):
            read = engine.read_page(page(), page_no=1)
        self.assertEqual((read.read_by, len(client.calls), local.pages), (MODEL, 1, 0))

    def test_a_page_asked_to_be_read_again_is_never_routed(self) -> None:
        client, local = FakeClient(WHOLE), _local(clean())
        pages = _routed_pages(read_again=frozenset({1}))
        engine = _engine(client, local)
        with engine.document(pages):
            read = engine.read_page(page(), page_no=1)
        self.assertEqual((read.read_by, local.pages, pages.routed), (MODEL, 0, {}))
        self.assertEqual(client.calls[0].thinking_level, "high")  # harder, as F5 reads

    def test_a_few_unsure_lines_are_read_as_zoomed_regions_and_only_they_change(self) -> None:
        client = Client(
            {MODEL: WHOLE},
            crops=crops_reply([("Total amount 1,180.00", ())], [("Paid by Sharma Traders", ("Sharma",))]),
        )
        local = _local(_unsure_page())
        pages = _routed_pages()
        engine = _engine(client, local)
        with engine.document(pages):
            read = engine.read_page(page(), page_no=1)
        self.assertEqual(client.calls(), [(MODEL, "low", "crops")])  # the document's level
        self.assertEqual(pages.routed, {1: (REGIONS, "few_unsure")})
        self.assertEqual(read.read_by, "rapidocr")  # never checked against itself
        self.assertEqual((read.lines[3].text, read.lines[3].confidence), ("Total amount 1,180.00", None))
        self.assertEqual(
            (read.lines[10].text, read.lines[10].unsure), ("Paid by Sharma Traders", ("Sharma",))
        )
        untouched = [n for n in range(20) if n not in (3, 10)]
        self.assertEqual([read.lines[n] for n in untouched], [local.lines[n] for n in untouched])
        self.assertEqual(
            [(ln.x0, ln.y0, ln.x1, ln.y1) for ln in read.lines],
            [(ln.x0, ln.y0, ln.x1, ln.y1) for ln in local.lines],
        )
        self.assertEqual((read.input_tokens, read.output_tokens, read.unsure_words), (300, 120, 1))
        # Two crops of 840 x 80 pixels, zoomed 64 / 40 = 1.6 times, a gap between them.
        strip = Image.open(io.BytesIO(client.requests[0]["png"]))
        self.assertEqual(strip.size, (1344, 2 * 128 + CROP_GAP))

    def test_regions_are_bounded_per_document_and_the_rest_read_whole(self) -> None:
        client = Client(
            {MODEL: WHOLE},
            crops=crops_reply([("Total amount 1,180.00", ())], [("Paid by Sharma Traders", ())]),
        )
        pages = _routed_pages()
        engine = _engine(client, _local(_unsure_page()), page_route_region_pages_max=1)
        with engine.document(pages):
            engine.read_page(page(), page_no=1)
            second = engine.read_page(page(), page_no=2)
        self.assertEqual([kind for _m, _t, kind in client.calls()], ["crops", "page"])
        self.assertEqual(pages.routed, {1: (REGIONS, "few_unsure"), 2: (BY_MODEL, "region_cap")})
        self.assertEqual(second.read_by, MODEL)

    def test_a_regions_call_that_fails_reads_the_whole_page_and_bills_both(self) -> None:
        client = Client(
            {MODEL: WHOLE}, crops=ModelOutputError("invalid_output", input_tokens=5, output_tokens=7)
        )
        pages = _routed_pages()
        engine = _engine(client, _local(_unsure_page()))
        with engine.document(pages):
            read = engine.read_page(page(), page_no=1)
        self.assertEqual([kind for _m, _t, kind in client.calls()], ["crops", "page"])
        self.assertEqual(pages.routed, {1: (BY_MODEL, "region_failed")})
        self.assertEqual((read.read_by, read.input_tokens, read.output_tokens), (MODEL, 1705, 4007))


# --- crops, zoomed (#385) --------------------------------------------------------------------


class CropTest(unittest.TestCase):
    def test_the_crop_has_a_margin_stays_on_the_page_and_merges_overlaps(self) -> None:
        lines = [
            OcrLine("a", 10, 5, 300, 45, 0.5),  # its margin would leave the page at the top and left
            OcrLine("b", 100, 60, 400, 100, 0.5),  # its margin meets a's: one crop
            OcrLine("c", 100, 600, 990, 640, 0.5),  # clamped at the right edge
        ]
        crops = crop_boxes(lines, [0, 1, 2], 1000, 1000, pad=0.5)
        self.assertEqual(crops, [((0.0, 0.0, 420.0, 120.0), (0, 1)), ((80.0, 580.0, 1000.0, 660.0), (2,))])

    def test_the_zoom_aims_at_readable_lines_and_never_shrinks_or_grows_past_its_limits(self) -> None:
        lines = [OcrLine("x", 0, 0, 100, h, 0.5) for h in (16.0, 40.0, 200.0)]
        self.assertEqual(region_zoom(lines, [0], [(0, 0, 100, 32)]), 3.0)  # 64 / 16 = 4, at most 3
        self.assertEqual(region_zoom(lines, [1], [(0, 0, 100, 80)]), 1.6)
        self.assertEqual(region_zoom(lines, [2], [(0, 0, 100, 400)]), 1.0)  # never smaller
        huge = region_zoom(lines, [0], [(0, 0, 5000, 2000)])
        self.assertLessEqual(5000 * 2000 * huge * huge, MAX_OCR_PIXELS)

    def test_the_strip_is_zoomed_and_unzoomed_as_before(self) -> None:
        image = page(400, 400)
        self.assertEqual(crop_strip(image, [(0, 0, 100, 50)]).size, (100, 50))
        self.assertEqual(
            crop_strip(image, [(0, 0, 100, 50), (0, 100, 50, 120)], zoom=2.0).size, (200, 100 + 40 + CROP_GAP)
        )


# --- read again by region (F5, #385) ---------------------------------------------------------

# The page of tests/test_gemini_ocr.py, 1000 x 2000 pixels: line n's box_2d is
# [100 + 100n, 100, 130 + 100n, 900], so (100, 200 + 200n, 900, 260 + 200n) in pixels.
TEXTS = ["Invoice INV-17", "Sharma Traders", "Total 1,18O.00", "Pune", "Thank you"]
FIRST = reply(
    value(*(line(t, (100 + 100 * n, 100, 130 + 100 * n, 900), order=n) for n, t in enumerate(TEXTS)))
)
FOUND = FakeFinder(*((100.0, 200.0 + 200 * n, 900.0, 260.0 + 200 * n) for n in range(5)))
TOTAL_REGION = (0.2, 0.31, 0.5, 0.32)  # inside line 2


def _f5(cache: FakeCache, regions: dict[int, tuple[tuple[float, float, float, float], ...]]) -> DocumentPages:
    again = frozenset(regions) or frozenset({1})
    return DocumentPages(
        customer_id=CUSTOMER, document_id=DOCUMENT, cache=cache, read_again=again, read_again_regions=regions
    )


def _first_read(client: Client, cache: FakeCache, **settings: Any) -> GeminiEngine:
    engine = _engine(client, FakeRapidOcr(), finder=FOUND, **settings)
    with engine.document(DocumentPages(customer_id=CUSTOMER, document_id=DOCUMENT, cache=cache)):
        engine.read_page(page(), page_no=1)
    return engine


class ReadAgainRegionTest(unittest.TestCase):
    def test_only_the_lines_in_the_region_are_read_again_harder_and_kept_in_the_cache(self) -> None:
        cache = FakeCache()
        client = Client({MODEL: FIRST}, crops=crops_reply([("Total 1,180.00", ())]))
        engine = _first_read(client, cache)
        with engine.document(_f5(cache, {1: (TOTAL_REGION,)})):
            read = engine.read_page(page(), page_no=1)
        self.assertEqual(client.calls(), [(MODEL, "low", "page"), (MODEL, "high", "crops")])
        self.assertEqual(client.requests[1]["media_resolution"], "high")  # OCR_RETRY_MEDIA_RESOLUTION
        self.assertEqual([ln.text for ln in read.lines], [*TEXTS[:2], "Total 1,180.00", *TEXTS[3:]])
        self.assertEqual((read.read_by, read.cached, read.input_tokens), (MODEL, False, 300))
        self.assertTrue(all(ln.box_source == "found" for ln in read.lines))
        # The cache keeps the merged read: the next reading makes no call and keeps the fix.
        with engine.document(DocumentPages(customer_id=CUSTOMER, document_id=DOCUMENT, cache=cache)):
            again = engine.read_page(page(), page_no=1)
        self.assertEqual(
            (len(client.requests), again.lines[2].text, again.cached), (2, "Total 1,180.00", True)
        )

    def test_with_nothing_cached_or_no_line_in_the_region_the_whole_page_is_read_harder(self) -> None:
        client = Client({MODEL: FIRST})
        engine = _engine(client, FakeRapidOcr(), finder=FOUND)
        with engine.document(_f5(FakeCache(), {1: (TOTAL_REGION,)})):
            engine.read_page(page(), page_no=1)
        self.assertEqual(client.calls(), [(MODEL, "high", "page")])

        cache = FakeCache()
        client = Client({MODEL: FIRST})
        engine = _first_read(client, cache)
        with engine.document(_f5(cache, {1: ((0.0, 0.95, 0.05, 1.0),)})):  # a corner with no line
            engine.read_page(page(), page_no=1)
        self.assertEqual([kind for _m, _t, kind in client.calls()], ["page", "page"])

    def test_more_lines_than_the_limit_read_the_whole_page(self) -> None:
        cache = FakeCache()
        client = Client({MODEL: FIRST})
        engine = _first_read(client, cache, cost_saving_hard_lines_max=1)
        with engine.document(_f5(cache, {1: ((0.0, 0.0, 1.0, 1.0),)})):  # every line
            engine.read_page(page(), page_no=1)
        self.assertEqual([kind for _m, _t, kind in client.calls()], ["page", "page"])


# --- through the pipeline --------------------------------------------------------------------


def _dynamic(routes: bool) -> JobReading:
    reading = Reading("dynamic", Agent(MODEL, "google-vertex", "Lead"), thinking="low", routes=routes)
    return JobReading(None, MODEL, "google-vertex", reading=reading)


class PipelineTest(unittest.TestCase):
    def test_a_clean_scan_is_read_locally_noted_on_the_run_and_its_values_need_review(self) -> None:
        texts = [f"Clean printed line number {n} here" for n in range(8)]
        texts[3] = "Total: 1,180.00"
        lines = [OcrLine(text, 20, 40 + 40 * n, 590, 60 + 40 * n, 0.99) for n, text in enumerate(texts)]
        client = FakeClient()
        settings: Settings = dataclasses.replace(GEMINI, ocr_thinking_level="low")
        engine = GeminiEngine(
            settings, client=cast(Any, client), pool=FakeFinder(), local=lambda: as_page_engine(_local(lines))
        )
        system = rig(engine, ocr_model=MODEL, settings=settings, job_reading=lambda _c, _d: _dynamic(True))
        job = system.run(make_pdf([SCANNED]))
        self.assertEqual(client.calls, [])
        (record,) = system.pages(job)
        self.assertEqual(record.read_by, "rapidocr")
        self.assertEqual(
            system.totals(job).config["reading"]["routed"], [{"page": 1, "route": LOCAL, "reason": "clean"}]
        )
        values = system.store.values_for(JOB_CUSTOMER, job.document_id)
        # No second reader checks a local page: its values need review, never verified.
        self.assertIn(("1,180.00", "needs_review"), [(item.value_text, item.label) for item in values])
        self.assertNotIn("verified", {item.label for item in values})

    def test_the_routing_and_the_regions_asked_for_reach_the_reader(self) -> None:
        reader = ScriptedReader({1: page_read(TOTAL), 2: page_read(TOTAL)})
        system = rig(reader, job_reading=lambda _c, _d: _dynamic(True))
        job = system.job(make_pdf([SCANNED, SCANNED]))
        system.read_again.marks[(JOB_CUSTOMER, job.document_id)] = {2}
        system.read_again.regions[(JOB_CUSTOMER, job.document_id)] = {2: (TOTAL_REGION,)}
        system.processor(job, beat)
        opened = reader.opened[0]
        self.assertEqual((opened.route_pages, opened.read_again_regions), (True, {2: (TOTAL_REGION,)}))
        self.assertEqual(opened.routed, {})

    def test_no_routing_without_the_readings_flag(self) -> None:
        reader = ScriptedReader({1: page_read(TOTAL)})
        system = rig(reader, job_reading=lambda _c, _d: _dynamic(False))
        system.run(make_pdf([SCANNED]))
        self.assertEqual((reader.opened[0].route_pages, reader.opened[0].routed), (False, None))
