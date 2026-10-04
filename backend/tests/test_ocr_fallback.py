"""FallbackOcrEngine (kaagaz/reading/ocr.py): a scan page PaddleOCR-VL cannot read is
read by RapidOCR instead. Both engines are fakes here, and the timer is a fake clock.
"""

import io
import logging
from collections.abc import Callable

import pytest
from PIL import Image

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading import ocr
from kaagaz.reading.image_reader import ImageReader
from kaagaz.reading.ocr import (
    COOL_DOWN_SECONDS,
    FallbackOcrEngine,
    MeteredOcrEngine,
    OcrLine,
    OcrOverloaded,
    build_ocr_engine,
)
from kaagaz.reading.socket import ReaderSocket
from kaagaz.reading.spans import BoxLocation, ReadError
from kaagaz.settings import SettingsError
from tests.picture_support import text_like_picture
from tests.settings_factory import make_settings

PAGE_TEXT = "Sunil Patil 27AABCS1234K1Z5"  # never in a log


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Engine:
    """Answers each page with the next of ``script``: an error is raised, a kind is a
    one-block page (PaddleOCR-VL), None is a RapidOCR line with a confidence."""

    def __init__(self, name: str, script: list[Exception | str | None]) -> None:
        self.name = name
        self.version = f"{name}-1"
        self.script = script
        self.pages = 0

    def read(self, image: Image.Image) -> list[OcrLine]:
        step = self.script[self.pages % len(self.script)]
        self.pages += 1
        if isinstance(step, Exception):
            raise step
        w, h = image.width, image.height
        return [OcrLine(PAGE_TEXT, 0.1 * w, 0.1 * h, 0.9 * w, 0.2 * h, None if step else 0.62, step)]


class Rig:
    def __init__(self, primary: list[Exception | str | None], fallback: list[Exception | str | None]) -> None:
        self.clock = Clock()
        self.primary = Engine("paddleocr-vl", primary)
        self.fallback = Engine("rapidocr", fallback)
        self.built = 0
        self.engine = FallbackOcrEngine(self.primary, self.build, timer=self.clock)

    def build(self) -> Engine:
        self.built += 1
        return self.fallback

    def read(self) -> list[OcrLine]:
        return self.engine.read(Image.new("RGB", (200, 100), "white"))


def test_a_page_the_model_failed_on_is_read_by_the_fallback(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    rig = Rig([ReadError("ocr_failed")], [None])
    (line,) = rig.read()
    assert (line.kind, line.confidence) == (None, 0.62)  # RapidOCR's own line
    assert (rig.engine.take_fallback_pages(), rig.engine.take_fallback_pages()) == (1, 0)
    assert [record.__dict__.get("code") for record in caplog.records] == ["ocr_failed"]
    assert PAGE_TEXT not in caplog.text
    rig.read()  # no cool-down after a page's own failure: the primary is asked again
    assert rig.primary.pages == 2


@pytest.mark.parametrize("code", ["ocr_timeout", "ocr_out_of_memory"])
def test_an_overloaded_reader_cools_down_then_is_tried_again(code: str) -> None:
    rig = Rig([OcrOverloaded(code), "paragraph"], [None])
    rig.read()
    assert (rig.primary.pages, rig.fallback.pages) == (1, 1)
    rig.clock.now += COOL_DOWN_SECONDS - 1
    rig.read()  # still cooling down: straight to the fallback
    assert (rig.primary.pages, rig.fallback.pages) == (1, 2)
    rig.clock.now += 1
    (line,) = rig.read()  # the cool-down is over
    assert (rig.primary.pages, rig.fallback.pages, line.kind) == (2, 2, "paragraph")
    assert rig.engine.take_fallback_pages() == 2


@pytest.mark.parametrize(
    "failure",
    [
        ReadError("corrupt_file"),
        ReadError("image_too_large"),
        ReadError("invalid_reader_output"),
        BackendUnavailable("ocr"),  # down, not_ready, internal_error or 421: a set-up fault
    ],
)
def test_other_failures_are_not_hidden_behind_the_fallback(failure: Exception) -> None:
    rig = Rig([failure], [None])
    with pytest.raises(type(failure)) as caught:
        rig.read()
    assert caught.value is failure
    assert (rig.built, rig.engine.take_fallback_pages()) == (0, 0)


@pytest.mark.parametrize(
    "failure", [ReadError("ocr_failed"), OcrOverloaded("ocr_timeout")], ids=["read_failed", "overloaded"]
)
def test_when_the_fallback_fails_too_the_primarys_error_is_raised(
    failure: ReadError | OcrOverloaded, caplog: pytest.LogCaptureFixture
) -> None:
    rig = Rig([failure, "paragraph"], [RuntimeError(PAGE_TEXT)])
    with pytest.raises(type(failure)) as caught:
        rig.read()
    assert caught.value is failure
    assert "ocr fallback failed: RuntimeError" in caplog.text
    assert PAGE_TEXT not in caplog.text
    rig.read()  # no cool-down was started: the next page goes to the primary
    assert rig.primary.pages == 2
    assert rig.engine.take_fallback_pages() == 0


def test_a_fallback_that_cannot_be_built_leaves_the_error_as_it_was() -> None:
    """Without the ocr extra, RapidOcrEngine() raises SettingsError: as before this PR."""

    def missing() -> Engine:
        raise SettingsError("OCR_ENGINE=rapidocr needs the ocr extra installed")

    failure = ReadError("ocr_failed")
    engine = FallbackOcrEngine(Engine("paddleocr-vl", [failure]), missing, timer=Clock())
    with pytest.raises(ReadError) as caught:
        engine.read(Image.new("RGB", (20, 20), "white"))
    assert caught.value is failure


def test_a_fallback_failing_in_the_cool_down_ends_it() -> None:
    rig = Rig([OcrOverloaded("ocr_timeout"), "paragraph"], [None, RuntimeError("x")])
    rig.read()
    with pytest.raises(OcrOverloaded) as caught:
        rig.read()  # cooling down, and the fallback fails: retried later, as a timeout would be
    assert caught.value.code == "ocr_cooling_down"
    rig.read()
    assert rig.primary.pages == 2  # the primary is tried again at once


def test_the_fallback_is_built_once_and_named_nowhere() -> None:
    rig = Rig([ReadError("ocr_failed")], [None])
    for _ in range(3):
        rig.read()
    assert (rig.built, rig.fallback.pages) == (1, 3)
    assert (rig.engine.name, rig.engine.version) == ("paddleocr-vl", "paddleocr-vl-1")  # the ETA keys on it


def test_the_meter_counts_fallback_pages_per_run() -> None:
    """Taken at the start and end of each run, so one run's fallback pages never reach the next."""
    rig = Rig([ReadError("ocr_failed"), "paragraph"], [None])
    meter = MeteredOcrEngine(rig.engine, timer=rig.clock)
    for _ in range(4):
        meter.read(Image.new("RGB", (20, 20), "white"))
    assert (meter.take(), meter.take_fallback_pages()) == ((4, 0.0), 2)
    meter.read(Image.new("RGB", (20, 20), "white"))
    assert meter.take_fallback_pages() == 1
    assert MeteredOcrEngine(rig.primary, timer=rig.clock).take_fallback_pages() == 0  # no fallback


def test_build_ocr_engine_wraps_paddleocr_vl_when_rapidocr_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(ocr_engine="paddleocr_vl", ocr_url="http://ocr:8080")
    engine = build_ocr_engine(settings)
    assert isinstance(engine, FallbackOcrEngine)
    assert (engine.name, engine.version) == ("paddleocr-vl", "unknown")
    monkeypatch.setattr(ocr, "_installed_version", lambda package: None)
    assert isinstance(build_ocr_engine(settings), ocr.PaddleVlEngine)  # no ocr extra: as before


def tiff(pages: int) -> bytes:
    buffer = io.BytesIO()
    first, *rest = [text_like_picture(153, 198) for _ in range(pages)]
    first.save(buffer, format="TIFF", save_all=True, append_images=rest)
    return buffer.getvalue()


def test_a_document_with_pages_from_both_engines_gives_valid_spans() -> None:
    """ocr_spans decides per page: a PaddleOCR-VL page stays one span per block, a RapidOCR
    page goes through the line rule with its real confidence (so a value from it is
    labelled by it), and the socket's checks pass for both."""
    rig = Rig(["paragraph", ReadError("ocr_failed")], [None])
    reader = ImageReader(rig.engine, max_pages=5, max_image_pixels=10_000_000, ocr_dpi=72, blank_stddev=3.0)
    spans = ReaderSocket({"tiff": reader}).read("tiff", tiff(2))
    first, second = spans
    assert (first.kind, first.confidence, first.origin) == ("paragraph", None, "ocr")
    assert (second.kind, second.confidence, second.origin) == (None, 0.62, "ocr")
    assert [span.location.page for span in spans if isinstance(span.location, BoxLocation)] == [1, 2]
    assert rig.engine.take_fallback_pages() == 1


def _never_built() -> Callable[[], Engine]:
    def build() -> Engine:
        raise AssertionError("built")

    return build


def test_a_page_the_primary_reads_never_builds_the_fallback() -> None:
    engine = FallbackOcrEngine(Engine("paddleocr-vl", ["paragraph"]), _never_built(), timer=Clock())
    assert len(engine.read(Image.new("RGB", (20, 20), "white"))) == 1
    assert engine.take_fallback_pages() == 0
