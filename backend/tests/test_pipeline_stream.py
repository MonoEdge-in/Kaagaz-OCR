"""Preparing streams the page pictures (council A5b; R2 findings 2 and 3).

Each page is drawn, made WebP straight from its picture, stored, then the
heartbeat beats, and the picture is dropped before the next page is drawn: no
PNG of every page is kept. A vision crop draws its page again, at the same
PAGE_IMAGE_DPI and through the same helpers, so its pixels are the ones
preparing drew. These use the real preparer (``prepare_pages``) with fakes for
everything else.
"""

import functools
import io
import math
import unittest
import weakref
from collections.abc import Callable
from typing import Any
from unittest import mock

from PIL import Image, ImageChops, ImageStat

from kaagaz.ingestion import sniff
from kaagaz.ingestion.policy import UploadPolicy, limits_by_type
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.jobs.worker import JobFailed
from kaagaz.pipeline import DocumentProcessor, _crop
from kaagaz.preparing import pages as preparing
from kaagaz.preparing.pages import open_pdf, prepare_pages, render_pdf_page
from kaagaz.reading.spans import BoxLocation, Span
from kaagaz.scanning.gate import ScanGate, Verdict
from tests.fakes import (
    FakeScanner,
    FakeVision,
    MemoryDocuments,
    MemoryPageImages,
    MemoryPipelineStore,
    MemoryQueue,
    MemoryStatuses,
    MemoryStorage,
    ModelDown,
    PresetSocket,
    fake_sheet_pages,
)
from tests.pdf_builder import Page, Picture, make_pdf
from tests.picture_support import text_like_picture
from tests.support import SeqIds

CUSTOMER = "cust-a"
DPI = 72  # PAGE_IMAGE_DPI for these tests: a 612 x 792 pt page is 612 x 792 px
PREPARE = functools.partial(
    prepare_pages, dpi=DPI, max_pages=10, max_image_pixels=10_000_000, blank_stddev=3.0, low_res_dpi=150
)
POLICY = UploadPolicy(
    max_bytes=1_000_000,
    allowed_types=frozenset({"pdf", "jpeg", "tiff"}),
    type_limits=limits_by_type(pdf=1_000_000, image=1_000_000, sheet=1_000_000),
)
SCAN = Page(picture=Picture(text_like_picture(306, 396), 0, 0, 612, 792))
TOTAL = Span("Total: 1,180.00", "Total: 1,180.00", BoxLocation(1, 0.1, 0.4, 0.9, 0.42), 0.6, origin="ocr")


class RecordingPictures(MemoryPageImages):
    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self.log = log

    def put_page_image(self, customer_id: str, document_id: str, page_no: int, png: bytes) -> None:
        self.log.append(f"put {page_no}")
        super().put_page_image(customer_id, document_id, page_no, png)


class Rig:
    def __init__(self, data: bytes, *, spans: list[Span] | None = None, vision: FakeVision | None = None):
        self.log: list[str] = []
        self.statuses, storage = MemoryStatuses(), MemoryStorage()
        self.repo = MemoryDocuments(self.statuses)
        self.store = MemoryPipelineStore()
        self.pictures = RecordingPictures(self.log)
        detected = sniff.PDF if data.startswith(b"%PDF") else sniff.JPEG
        self.processor = DocumentProcessor(
            gate=ScanGate(storage, FakeScanner(Verdict.CLEAN), storage, self.statuses),
            statuses=self.statuses,
            documents=self.repo,
            socket=PresetSocket({detected: spans or []}),
            prepare=PREPARE,
            sheet_pages=fake_sheet_pages,
            page_images=self.pictures,
            store=self.store,
            vision=vision,
            model_errors=(ModelDown,),
        )
        upload = UploadService(POLICY, self.repo, storage, MemoryQueue(), new_id=SeqIds())
        self.job = Job(CUSTOMER, upload.accept(CUSTOMER, io.BytesIO(data)).document_id)

    def run(self) -> None:
        self.processor(self.job, lambda: self.log.append("beat"))


def watching_draws(name: str, log: list[str], drawn: list[Image.Image] | None = None) -> Any:
    """Patch the preparer's page drawing helper ``name``: log each draw, with how many
    pictures drawn before it are still alive (``weakref.finalize``), and keep a copy."""
    real: Callable[..., Image.Image] = getattr(preparing, name)
    alive = [0]

    def draw(*args: Any, **kwargs: Any) -> Image.Image:
        log.append(f"draw alive={alive[0]}")
        picture = real(*args, **kwargs)
        alive[0] += 1
        weakref.finalize(picture, lambda: alive.__setitem__(0, alive[0] - 1))
        if drawn is not None:
            drawn.append(picture.copy())
        return picture

    return mock.patch.object(preparing, name, draw)


def psnr(a: Image.Image, b: Image.Image) -> float:
    rms = ImageStat.Stat(ImageChops.difference(a.convert("RGB"), b.convert("RGB"))).rms
    mse = sum(value * value for value in rms) / len(rms)
    return math.inf if mse == 0 else 10 * math.log10(255 * 255 / mse)


def webp(picture: Image.Image, method: int) -> bytes:
    out = io.BytesIO()
    picture.save(out, format="WEBP", quality=80, method=method)
    return out.getvalue()


def jpeg_photo() -> bytes:
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 degrees clockwise to show"
    out = io.BytesIO()
    text_like_picture(400, 300).save(out, format="JPEG", exif=exif)
    return out.getvalue()


class StreamingTest(unittest.TestCase):
    def test_each_page_is_drawn_stored_and_beaten_before_the_next_is_drawn(self) -> None:
        """Draw -> put -> beat, page by page, and no picture drawn earlier is still alive
        when the next page is drawn: at most one picture at a time (PDF and TIFF)."""
        frames = [text_like_picture(200, 260) for _ in range(3)]
        tiff = io.BytesIO()
        frames[0].save(tiff, format="TIFF", save_all=True, append_images=frames[1:])
        for data, helper in (
            (make_pdf([SCAN, SCAN, SCAN]), "render_pdf_page"),
            (tiff.getvalue(), "_decode_frame"),
        ):
            with self.subTest(helper=helper):
                rig = Rig(data)
                with watching_draws(helper, rig.log):
                    rig.run()
                first = rig.log.index("draw alive=0")
                self.assertEqual(
                    rig.log[first : first + 9],
                    [event for page in (1, 2, 3) for event in ("draw alive=0", f"put {page}", "beat")],
                )

    def test_preparing_encodes_no_png(self) -> None:
        real = Image.SAVE["PNG"]
        encodes: list[str] = []

        def counted(*args: Any) -> None:
            encodes.append("png")
            real(*args)

        rig = Rig(make_pdf([SCAN, SCAN]))
        with mock.patch.dict(Image.SAVE, {"PNG": counted}):
            rig.run()
        self.assertEqual(sorted(rig.pictures.images), [(CUSTOMER, rig.job.document_id, n) for n in (1, 2)])
        self.assertEqual(encodes, [])

    def test_the_stored_picture_is_webp_method_2_within_its_error_bound(self) -> None:
        """WebP method 2 is viewer-only (OCR and vision draw their own pages). Against
        method 4 on this page: at most 1 dB less PSNR and at most 10% more bytes (the
        probe measured -0.3 dB and +4%)."""
        drawn: list[Image.Image] = []
        rig = Rig(make_pdf([SCAN]))
        with watching_draws("render_pdf_page", rig.log, drawn):
            rig.run()
        (page,) = drawn
        stored = rig.pictures.images[(CUSTOMER, rig.job.document_id, 1)]
        self.assertEqual(stored, webp(page, 2))
        with Image.open(io.BytesIO(stored)) as back:
            quality = psnr(page, back)
        with Image.open(io.BytesIO(webp(page, 4))) as back:
            quality_at_4 = psnr(page, back)
        self.assertGreater(quality, 40.0)
        self.assertGreater(quality, quality_at_4 - 1.0)
        self.assertLessEqual(len(stored), 1.10 * len(webp(page, 4)))


class VisionCropTest(unittest.TestCase):
    def test_a_vision_crop_has_the_pixels_preparing_drew(self) -> None:
        """Page 2 of a PDF, and a photo turned by its EXIF orientation: the crop sent to
        the vision model is pixel-equal to the same box of the page as preparing draws
        it (same DPI, pixel check and EXIF handling)."""
        pdf = make_pdf([SCAN, SCAN])
        document = open_pdf(pdf)
        try:
            pdf_page = render_pdf_page(document[1], dpi=DPI, max_image_pixels=10_000_000)
        finally:
            document.close()
        photo = jpeg_photo()
        photo_page = next(preparing.image_pages(photo, max_pages=1, max_image_pixels=10_000_000))
        self.assertEqual(photo_page.size, (300, 400))  # turned upright
        box_2 = BoxLocation(2, 0.1, 0.4, 0.9, 0.42)
        for data, page, box in ((pdf, pdf_page, box_2), (photo, photo_page, TOTAL.location)):
            with self.subTest(pages=page.size):
                vision = FakeVision("Total: 1,180.00")
                assert isinstance(box, BoxLocation)
                rig = Rig(data, spans=[Span(TOTAL.text, TOTAL.raw, box, 0.6, origin="ocr")], vision=vision)
                rig.run()
                (sent,) = vision.pictures
                with Image.open(io.BytesIO(sent)) as crop, Image.open(io.BytesIO(_crop(page, box))) as want:
                    self.assertEqual((crop.size, crop.tobytes()), (want.size, want.tobytes()))


class MidStreamFailureTest(unittest.TestCase):
    def test_a_page_that_fails_after_others_were_stored_leaves_them_in_the_customers_prefix(self) -> None:
        """Page 2 is too large to draw. Page 1's picture is already stored, under this
        customer's and document's key, and stays; the document fails and nothing else
        is saved. Delete and purge remove every key under the document's prefix
        (``test_adapter_storage``: pages/N.webp included), so nothing is left behind."""
        rig = Rig(make_pdf([SCAN, Page(width=6000, height=6000)]))
        with self.assertRaises(JobFailed) as failed:
            rig.run()
        self.assertEqual(failed.exception.code, "image_too_large")
        self.assertEqual(list(rig.pictures.images), [(CUSTOMER, rig.job.document_id, 1)])
        self.assertEqual(rig.store.saves, 0)
        self.assertEqual(rig.repo.failure_codes[(CUSTOMER, rig.job.document_id)], "image_too_large")


if __name__ == "__main__":
    unittest.main()
