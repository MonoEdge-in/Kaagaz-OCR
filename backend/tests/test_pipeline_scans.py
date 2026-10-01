"""The pipeline on documents read as lines on pages (PDF text layers and scans).

Readers, page preparation and models are fakes (tests/fakes.py), so these
tests prove the pipeline's own steps: page pictures, pieces from lines,
labelled values, second readings, the vision reader and the vectors step.
The lease tests at the end use the real PDF reader with a slow fake OCR engine,
and a slow fake embedder.
"""

import io
import unittest

from PIL import Image

from kaagaz.ingestion import sniff
from kaagaz.jobs.ports import Delivery
from kaagaz.reading.ocr import OcrLine
from kaagaz.reading.pdf_reader import PdfReader
from kaagaz.reading.socket import ReaderSocket
from kaagaz.reading.spans import BoxLocation, PageInfo, ReadError, Span
from kaagaz.reading.text_quality import TextLayerRules
from kaagaz.scanning.gate import DocumentStatus
from tests.fakes import FakeEmbedder, FakePrepared, FakePreparer, FakeVision, PresetSocket
from tests.lease_queue import FakeClock
from tests.pdf_builder import Page, Picture, make_pdf
from tests.picture_support import text_like_picture
from tests.support import PDF_BYTES
from tests.test_pipeline import CUSTOMER, LEASE_SECONDS, MAX_ATTEMPTS, build, make_worker, processor_with


def png_of(width: int, height: int, color: str = "white") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


PNG_1 = png_of(612, 792)
PNG_2 = png_of(300, 400, "gray")


def webp_size(data: bytes) -> tuple[int, int]:
    """The size of a stored page picture, which must be WebP (RIFF ... WEBP)."""
    assert (data[:4], data[8:12]) == (b"RIFF", b"WEBP"), "stored page pictures are WebP"
    with Image.open(io.BytesIO(data)) as picture:
        return picture.size


def line(text: str, top: float, *, confidence: float | None = None, page: int = 1) -> Span:
    origin = "text_layer" if confidence is None else "ocr"  # as the readers set it
    return Span(text, text, BoxLocation(page, 0.1, top, 0.9, top + 0.02), confidence, origin=origin)


INVOICE = [
    line("Sharma Traders", 0.05),
    line("Invoice No: INV-17", 0.08),
    line("Date: 12/08/2026", 0.11),
    line("Grand Total: Rs. 1,180.00", 0.30),
]


def two_pages() -> FakePreparer:
    pages = [PageInfo(1, 612.0, 792.0, "pt"), PageInfo(2, 612.0, 792.0, "pt")]
    return FakePreparer({sniff.PDF: FakePrepared(pages, {1: PNG_1, 2: PNG_2})})


def page_picture() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (400, 500), "white").save(out, format="PNG")
    return out.getvalue()


def scan_preparer() -> FakePreparer:
    return FakePreparer({sniff.PDF: FakePrepared([PageInfo(1, 400.0, 500.0, "px")], {1: page_picture()})})


class TextLayerTest(unittest.TestCase):
    def test_pages_pictures_pieces_and_labelled_values_are_saved(self) -> None:
        system = build(socket=PresetSocket({sniff.PDF: INVOICE}, {sniff.PDF: INVOICE}), preparer=two_pages())
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()

        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.READY)
        self.assertEqual(webp_size(system.page_images.images[(CUSTOMER, doc, 1)]), (612, 792))
        self.assertEqual(webp_size(system.page_images.images[(CUSTOMER, doc, 2)]), (300, 400))
        pages = system.store.pages_for(CUSTOMER, doc)
        self.assertEqual(
            [(p.info.page, p.info.unit, p.has_image) for p in pages], [(1, "pt", True), (2, "pt", True)]
        )
        self.assertEqual(system.repo.page_counts[(CUSTOMER, doc)], 2)

        # One blank-line gap (0.13 -> 0.30) splits the page into two pieces.
        pieces = system.store.for_document(CUSTOMER, doc)
        self.assertEqual(
            [p.text for p in pieces],
            ["Sharma Traders\nInvoice No: INV-17\nDate: 12/08/2026", "Grand Total: Rs. 1,180.00"],
        )
        self.assertEqual([p.label for p in pieces], ["verified", "verified"])
        self.assertEqual(pieces[1].regions(), [{"kind": "box", "page": 1, "box": [0.1, 0.3, 0.9, 0.32]}])

        values = {v.field: v for v in system.store.values_for(CUSTOMER, doc)}
        self.assertEqual(sorted(values), ["invoice_date", "invoice_number", "total_amount"])
        total = values["total_amount"]
        self.assertEqual((total.value_text, str(total.value_number)), ("Rs. 1,180.00", "1180.00"))
        self.assertEqual(total.location, INVOICE[3].location)
        self.assertEqual(str(values["invoice_date"].value_date), "2026-08-12")
        self.assertTrue(all(v.record_key == "document" for v in values.values()))
        self.assertTrue(
            all((v.label, v.label_reason) == ("verified", "readers_agree") for v in values.values())
        )

        outputs = system.store.outputs_for(CUSTOMER, doc)
        self.assertEqual(
            [(o.role, o.reader) for o in outputs], [("first", "preset-first"), ("second", "preset-second")]
        )

    def test_a_second_reading_that_differs_needs_review_and_is_kept(self) -> None:
        second = [line("Grand Total: Rs. 1,130.00", 0.301)]
        system = build(socket=PresetSocket({sniff.PDF: INVOICE}, {sniff.PDF: second}), preparer=two_pages())
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()

        values = {v.field: v for v in system.store.values_for(CUSTOMER, doc)}
        total = values["total_amount"]
        self.assertEqual((total.label, total.label_reason), ("needs_review", "readers_disagree"))
        self.assertEqual(total.second_reading, "Rs. 1,130.00")
        # Nothing on the second reader's page where the invoice number is.
        self.assertEqual(values["invoice_number"].label_reason, "single_reader")

    def test_no_second_reader_means_single_reader(self) -> None:
        system = build(socket=PresetSocket({sniff.PDF: INVOICE}), preparer=two_pages())
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()
        values = system.store.values_for(CUSTOMER, doc)
        self.assertTrue(values)
        self.assertTrue(all((v.label, v.label_reason) == ("needs_review", "single_reader") for v in values))
        self.assertEqual([o.role for o in system.store.outputs_for(CUSTOMER, doc)], ["first"])

    def test_a_second_reader_that_fails_leaves_the_first_reading_standing(self) -> None:
        socket = PresetSocket({sniff.PDF: INVOICE}, {sniff.PDF: ReadError("corrupt_file")})
        system = build(socket=socket, preparer=two_pages())
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        with self.assertLogs("kaagaz.pipeline", level="WARNING") as logs:
            system.worker.run_once()
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.READY)
        self.assertEqual(system.queue.failed, {})
        values = system.store.values_for(CUSTOMER, doc)
        self.assertTrue(values)
        self.assertTrue(all(v.label_reason == "single_reader" for v in values))
        self.assertEqual([o.role for o in system.store.outputs_for(CUSTOMER, doc)], ["first"])
        (record,) = logs.records
        self.assertEqual(
            (getattr(record, "code", None), getattr(record, "document_id", None)), ("corrupt_file", doc)
        )

    def test_a_line_without_a_valid_box_gives_no_piece_and_no_value(self) -> None:
        broken = Span("Total: 5", "Total: 5", BoxLocation(1, 0.9, 0.1, 0.1, 0.2))  # x0 > x1
        system = build(socket=PresetSocket({sniff.PDF: [broken, INVOICE[1]]}), preparer=two_pages())
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()
        self.assertEqual([v.field for v in system.store.values_for(CUSTOMER, doc)], ["invoice_number"])
        self.assertEqual([p.text for p in system.store.for_document(CUSTOMER, doc)], ["Invoice No: INV-17"])

    def test_identity_numbers_in_lines_are_kept_as_read(self) -> None:
        lines = [line("GSTIN: 27ABCDE1234F1Z5", 0.1), line("Customer: Asha 2345 6789 0123", 0.13)]  # FAKE
        system = build(socket=PresetSocket({sniff.PDF: lines}, {sniff.PDF: lines}))
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()

        content = system.store.content(CUSTOMER, doc)
        assert content is not None
        stored = [s.text + s.raw for o in content.outputs for s in o.spans] + [p.text for p in content.pieces]
        stored += [f"{v.value_text} {v.value_raw} {v.second_reading}" for v in content.values]
        joined = " ".join(stored)
        self.assertIn("ABCDE1234F", joined)
        self.assertIn("2345 6789 0123", joined)
        self.assertEqual(
            {v.field: v.value_text for v in content.values},
            {"gstin": "27ABCDE1234F1Z5", "buyer_name": "Asha 2345 6789 0123"},
        )


SCANNED = [line("Invoice No: INV-17", 0.1, confidence=0.97), line("Total: 1,180.00", 0.4, confidence=0.6)]


class ScanTest(unittest.TestCase):
    def test_scanned_values_without_a_second_reading_need_review(self) -> None:
        system = build(socket=PresetSocket({sniff.PDF: SCANNED}), preparer=scan_preparer())
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()

        reasons = {v.field: v.label_reason for v in system.store.values_for(CUSTOMER, doc)}
        self.assertEqual(reasons, {"invoice_number": "single_reader", "total_amount": "low_confidence"})
        self.assertTrue(all(p.label == "needs_review" for p in system.store.for_document(CUSTOMER, doc)))

    def test_a_vision_reading_that_agrees_verifies_a_scanned_value(self) -> None:
        vision = FakeVision("Total: 1,180.00")
        system = build(socket=PresetSocket({sniff.PDF: SCANNED[1:]}), preparer=scan_preparer(), vision=vision)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()

        (value,) = system.store.values_for(CUSTOMER, doc)
        self.assertEqual(
            (value.label, value.label_reason, value.second_reading), ("verified", "readers_agree", "1,180.00")
        )
        (picture,) = vision.pictures
        with Image.open(io.BytesIO(picture)) as crop:  # the value's box, not the whole page
            self.assertEqual(crop.format, "PNG")
            self.assertLess(crop.height, 50)
            self.assertGreater(crop.width, 300)

    def test_a_vision_reading_that_differs_needs_review(self) -> None:
        vision = FakeVision("Total: 1,130.00")
        system = build(socket=PresetSocket({sniff.PDF: SCANNED[1:]}), preparer=scan_preparer(), vision=vision)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()
        (value,) = system.store.values_for(CUSTOMER, doc)
        self.assertEqual((value.label_reason, value.second_reading), ("readers_disagree", "1,130.00"))

    def test_a_vision_model_that_is_down_leaves_single_reader_and_the_document_ready(self) -> None:
        vision = FakeVision(down=True)
        system = build(socket=PresetSocket({sniff.PDF: SCANNED}), preparer=scan_preparer(), vision=vision)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        with self.assertLogs("kaagaz.pipeline", level="WARNING") as logs:
            system.worker.run_once()

        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.READY)
        reasons = {v.field: v.label_reason for v in system.store.values_for(CUSTOMER, doc)}
        self.assertEqual(reasons, {"invoice_number": "single_reader", "total_amount": "low_confidence"})
        self.assertEqual(len(vision.pictures), 1)  # stops asking once it is down
        self.assertEqual([getattr(r, "code", None) for r in logs.records], ["model_down"])

    def test_a_line_with_an_identity_number_goes_to_the_vision_model_like_any_other(self) -> None:
        lines = [line("GSTIN: 27ABCDE1234F1Z5", 0.1, confidence=0.95)]  # FAKE
        vision = FakeVision("GSTIN: 27ABCDE1234F1Z5")
        socket = PresetSocket({sniff.PDF: lines})
        system = build(socket=socket, preparer=scan_preparer(), vision=vision)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()
        self.assertEqual(len(vision.pictures), 1)
        (value,) = system.store.values_for(CUSTOMER, doc)
        self.assertEqual(value.value_text, "27ABCDE1234F1Z5")

    def test_the_vision_reading_is_kept_as_read(self) -> None:
        lines = [line("Seller: Sharma Traders", 0.1, confidence=0.95)]
        vision = FakeVision("Seller: Sharma Traders ABCDE1234F")  # FAKE number the model "saw"
        system = build(
            socket=PresetSocket({sniff.PDF: lines}),
            preparer=scan_preparer(),
            vision=vision,
        )
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()
        (value,) = system.store.values_for(CUSTOMER, doc)
        self.assertEqual(value.second_reading, "Sharma Traders ABCDE1234F")
        self.assertEqual(value.label_reason, "readers_disagree")


class PaddleOcrVlScanTest(unittest.TestCase):
    """PaddleOCR-VL gives no confidence, so its spans are known as a scan by origin "ocr"."""

    def test_a_paddle_piece_needs_review_and_its_value_gets_a_vision_reading(self) -> None:
        typed = BoxLocation(1, 0.1, 0.1, 0.9, 0.12)
        spans = [
            Span(
                "Total: 1,180.00",
                "Total: 1,180.00",
                BoxLocation(1, 0.1, 0.4, 0.9, 0.42),
                None,
                "paragraph",
                "ocr",
            ),
            Span("Invoice No: INV-17", "Invoice No: INV-17", typed, origin="text_layer"),
        ]
        vision = FakeVision("Total: 1,180.00")
        system = build(socket=PresetSocket({sniff.PDF: spans}), preparer=scan_preparer(), vision=vision)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()

        pieces = system.store.for_document(CUSTOMER, doc)
        self.assertEqual({p.label for p in pieces}, {"needs_review"})  # the scanned line is in every piece
        values = {v.field: v for v in system.store.values_for(CUSTOMER, doc)}
        total = values["total_amount"]
        self.assertEqual((total.second_reading, total.label_reason), ("1,180.00", "readers_agree"))
        self.assertEqual(len(vision.pictures), 1)  # the text-layer line is a direct read: no crop
        self.assertEqual(values["invoice_number"].label_reason, "single_reader")

    def test_a_value_that_does_not_say_how_it_was_read_gets_a_vision_reading(self) -> None:
        # Fail closed (council): no origin and no confidence is not taken as a direct read.
        spans = [Span("Total: 1,180.00", "Total: 1,180.00", BoxLocation(1, 0.1, 0.4, 0.9, 0.42))]
        vision = FakeVision("Total: 1,180.00")
        system = build(socket=PresetSocket({sniff.PDF: spans}), preparer=scan_preparer(), vision=vision)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()

        self.assertEqual(len(vision.pictures), 1)
        self.assertEqual({p.label for p in system.store.for_document(CUSTOMER, doc)}, {"needs_review"})


def checkerboard(width: int, height: int) -> Image.Image:
    """One-pixel black and white squares: any lossy step blurs them into greys."""
    black_first = bytes([0, 0, 0, 255, 255, 255] * (width // 2))
    white_first = bytes([255, 255, 255, 0, 0, 0] * (width // 2))
    return Image.frombytes("RGB", (width, height), (black_first + white_first) * (height // 2))


def colours(data: bytes) -> set[tuple[int, ...] | float]:
    """Every colour in a picture (Pillow gives RGB colours as tuples)."""
    with Image.open(io.BytesIO(data)) as picture:
        found = picture.convert("RGB").getcolors(maxcolors=1 << 24) or []
    return {colour for _, colour in found}


class PagePictureTest(unittest.TestCase):
    def test_a_page_longer_than_webp_allows_is_stored_smaller_for_people_only(self) -> None:
        # WebP holds at most 16383 pixels a side (PROVISIONAL: shrink for storage, never fail).
        for width, height, stored in ((20_000, 10, (16_383, 8)), (10, 20_000, (8, 16_383))):
            with self.subTest(size=(width, height)):
                page = PageInfo(1, float(width), float(height), "px")
                preparer = FakePreparer({sniff.PDF: FakePrepared([page], {1: png_of(width, height)})})
                system = build(socket=PresetSocket({sniff.PDF: INVOICE}), preparer=preparer)
                doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
                system.worker.run_once()

                self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.READY)
                self.assertEqual(webp_size(system.page_images.images[(CUSTOMER, doc, 1)]), stored)
                # The page keeps its real size; boxes are fractions of it.
                (saved,) = system.store.pages_for(CUSTOMER, doc)
                self.assertEqual((saved.info, saved.has_image), (page, True))

    def test_vision_crops_come_from_the_lossless_page_not_the_stored_webp(self) -> None:
        png = io.BytesIO()
        checkerboard(400, 500).save(png, format="PNG")
        page = PageInfo(1, 400.0, 500.0, "px")
        preparer = FakePreparer({sniff.PDF: FakePrepared([page], {1: png.getvalue()})})
        vision = FakeVision("Total: 1,180.00")
        system = build(socket=PresetSocket({sniff.PDF: SCANNED[1:]}), preparer=preparer, vision=vision)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()

        (crop,) = vision.pictures
        self.assertEqual(colours(crop), {(0, 0, 0), (255, 255, 255)})  # exactly the page's pixels
        stored = system.page_images.images[(CUSTOMER, doc, 1)]
        self.assertGreater(len(colours(stored)), 2)  # the stored WebP is lossy, so it was not used
        (value,) = system.store.values_for(CUSTOMER, doc)
        self.assertEqual(value.label_reason, "readers_agree")


class VectorsTest(unittest.TestCase):
    def test_vectors_are_made_once_after_saving(self) -> None:
        embed = FakeEmbedder()
        system = build(socket=PresetSocket({sniff.PDF: INVOICE}), preparer=two_pages(), embed=embed)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()
        self.assertEqual(embed.calls, [(CUSTOMER, doc)])
        self.assertEqual(system.store.saves, 1)

    def test_a_model_that_is_down_is_logged_with_ids_only_and_the_document_is_ready(self) -> None:
        embed = FakeEmbedder(down=True)
        system = build(socket=PresetSocket({sniff.PDF: INVOICE}), preparer=two_pages(), embed=embed)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        with self.assertLogs("kaagaz.pipeline", level="WARNING") as logs:
            system.worker.run_once()

        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.READY)
        self.assertEqual(len(system.store.for_document(CUSTOMER, doc)), 2)
        (record,) = logs.records
        self.assertEqual(
            (getattr(record, "customer_id", None), getattr(record, "document_id", None)), (CUSTOMER, doc)
        )
        self.assertNotIn("Sharma", record.getMessage())

    def test_a_failed_document_is_never_embedded(self) -> None:
        embed = FakeEmbedder()
        system = build(
            socket=PresetSocket({sniff.PDF: ReadError("encrypted")}), preparer=two_pages(), embed=embed
        )
        system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES))
        system.worker.run_once()
        self.assertEqual(embed.calls, [])
        self.assertEqual([f.code for f in system.queue.failed.values()], ["encrypted"])


class SlowOcr:
    """An OCR engine that takes ``seconds`` of the test clock to read each page."""

    name = "slow-ocr"
    version = "1"

    def __init__(self, clock: FakeClock, seconds: float) -> None:
        self._clock = clock
        self._seconds = seconds
        self.pages = 0

    def read(self, image: Image.Image) -> list[OcrLine]:
        self._clock.advance(self._seconds)
        self.pages += 1
        return [OcrLine(f"Scanned page {self.pages}", 60, 60, 300, 90, 0.9)]


class LongReadingTest(unittest.TestCase):
    def test_a_long_scan_keeps_its_lease_between_pages(self) -> None:
        system = build()
        ocr = SlowOcr(system.clock, seconds=20)
        self.assertEqual(LEASE_SECONDS, 30)  # one page fits in a lease; three pages (60 s) do not
        rules = TextLayerRules(min_chars=40, min_good_ratio=0.9, scan_image_coverage=0.5)
        reader = PdfReader(
            ocr, max_pages=10, dpi=72, max_image_pixels=10_000_000, rules=rules, blank_stddev=3.0
        )
        processor = processor_with(system, ReaderSocket({sniff.PDF: reader}))
        worker = make_worker(system.queue, processor, system.repo)
        # Three scanned pages (pictures of text): blank pages would never reach the engine (R3).
        scan = Page(picture=Picture(text_like_picture(153, 198), 0, 0, 612, 792))
        doc = system.upload.accept(CUSTOMER, io.BytesIO(make_pdf([scan, scan, scan]))).document_id

        self.assertTrue(worker.run_once())
        self.assertEqual(ocr.pages, 3)
        self.assertEqual(system.queue.pending(), 0)  # acked: the lease was renewed after each page
        self.assertEqual(system.queue.failed, {})
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.READY)

    def test_a_long_indexing_keeps_its_lease_between_batches(self) -> None:
        """R2 1: a 20k-row sheet is about 626 embedding batches, far longer than one lease.
        Each batch here takes 0.6 of a lease: the heartbeat after each renews it (on the
        worker's clock, at most every 15 s), so a second worker asking meanwhile gets nothing."""
        handed_out: list[Delivery | None] = []

        def slow_batch(done: int) -> None:
            system.clock.advance(0.6 * LEASE_SECONDS)
            handed_out.append(system.queue.receive())  # a second worker asks for work

        embed = FakeEmbedder(batches=3, during_batch=slow_batch)
        system = build(socket=PresetSocket({sniff.PDF: INVOICE}), preparer=two_pages(), embed=embed)
        worker = make_worker(system.queue, system.processor, system.repo, clock=system.clock)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id

        self.assertTrue(worker.run_once())
        self.assertEqual(handed_out, [None, None, None])
        self.assertEqual(system.queue.pending(), 0)  # acked, once
        self.assertEqual(system.repo.attempts[(CUSTOMER, doc)], (1, MAX_ATTEMPTS))  # on attempt 1
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.READY)


if __name__ == "__main__":
    unittest.main()
