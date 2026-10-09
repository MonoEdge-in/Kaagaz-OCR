"""The scan reader an upload chose (PRD 4 #17, kaagaz/reading/ocr.py): ``ocr_choices``,
one reader since PRD 5 RT3, and ``OcrEngines``, which reads a job whose stored choice
names an engine this server has with that engine and falls back to Auto. Engines are
fakes; the timer is a fake clock.
"""The scan reader an upload chose (PRD 4 #17, kaagaz/reading/ocr.py): ``ocr_choices``
from the settings and what is installed, and ``OcrEngines``, which reads one job
with the chosen engine and falls back to Auto. Engines are fakes; the timer is a
fake clock.
"""

import logging
from collections import Counter

import pytest
from PIL import Image

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading import ocr
from kaagaz.reading.ocr import (
    COOL_DOWN_SECONDS,
    MeteredOcrEngine,
    OcrEngine,
    OcrEngines,
    OcrLine,
    OcrOverloaded,
    PaddleVlEngine,
    RapidOcrEngine,
    build_ocr_engines,
    ocr_choices,
)
from kaagaz.reading.spans import ReadError
from kaagaz.reading.wiring import build_reader_socket
from kaagaz.settings import Settings
from tests.settings_factory import make_settings

PAGE_TEXT = "Sunil Patil 27AABCS1234K1Z5"  # never in a log
PADDLE = make_settings(ocr_engine="paddleocr_vl", ocr_url="http://ocr:8080")
RAPID = make_settings(ocr_engine="rapidocr")
NONE = make_settings(ocr_engine="none")
GEMINI = make_settings(ocr_engine="gemini")


@pytest.fixture
def rapidocr_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "_installed_version", lambda package: "3.9.2")


@pytest.fixture
def no_rapidocr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "_installed_version", lambda package: None)


def offered(settings: Settings) -> list[tuple[object, ...]]:
    return [(item["id"], item["available"], item["reason"]) for item in ocr_choices(settings)]


# --- ocr_choices: one reader (PRD 5 RT3) ----------------------------------------------


@pytest.mark.parametrize(
    "settings", [NONE, RAPID, PADDLE, GEMINI], ids=["none", "rapidocr", "paddle", "gemini"]
)
def test_rt3_an_upload_is_offered_one_reader_so_the_select_hides_itself(
    settings: Settings, rapidocr_installed: None
) -> None:
    """RT3: no reader choice at upload. The browser shows "Scan with" only for two or
    more usable readers (frontend/components/documents/readers.ts offersChoice)."""
    assert offered(settings) == [("auto", True, None)]
    assert sum(bool(item["available"]) for item in ocr_choices(settings)) < 2


def test_rt3_the_one_reader_says_what_this_server_reads_with(rapidocr_installed: None) -> None:
    hints = {
        name: ocr_choices(settings)[0]["hint"] for name, settings in (("none", NONE), ("gemini", GEMINI))
    }
    assert hints == {
        "none": "Scans wait until this server has a scan reader",
        "gemini": "The usual reader here: Gemini",
    }
    assert ocr_choices(RAPID)[0]["hint"] == "The usual reader here: RapidOCR"
    assert ocr_choices(PADDLE)[0]["hint"] == (
        "The usual reader here: PaddleOCR-VL, then RapidOCR for a page it can't read"
    )
    assert ocr_choices(RAPID)[0]["title"] == "Auto"


def test_rt3_listing_the_reader_builds_and_calls_nothing(
    rapidocr_installed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pure: the api lists it on every upload page, with no engine and no Gemini call."""

    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("ocr_choices must not build an engine")

    monkeypatch.setattr(ocr, "_new_engine", never)
    assert offered(GEMINI) == [("auto", True, None)]
# --- ocr_choices ---------------------------------------------------------------------


def test_with_no_scan_reader_only_auto_is_offered(rapidocr_installed: None) -> None:
    off = "Scan reading is off on this server"
    assert offered(NONE) == [("auto", True, None), ("paddleocr_vl", False, off), ("rapidocr", False, off)]


def test_a_laptop_with_rapidocr_offers_auto_and_rapidocr(rapidocr_installed: None) -> None:
    assert offered(RAPID) == [
        ("auto", True, None),
        ("paddleocr_vl", False, "Not set up on this server"),
        ("rapidocr", True, None),
    ]
    assert ocr_choices(RAPID)[0]["hint"] == "The usual reader here: RapidOCR"


def test_the_gpu_server_offers_both_and_auto_says_what_it_does(rapidocr_installed: None) -> None:
    assert offered(PADDLE) == [("auto", True, None), ("paddleocr_vl", True, None), ("rapidocr", True, None)]
    assert ocr_choices(PADDLE)[0]["hint"] == (
        "The usual reader here: PaddleOCR-VL, then RapidOCR for a page it can't read"
    )


def test_paddle_without_the_ocr_extra_cannot_offer_rapidocr(no_rapidocr: None) -> None:
    assert offered(PADDLE)[2] == ("rapidocr", False, "Not installed on this server")
    assert ocr_choices(PADDLE)[0]["hint"] == "The usual reader here: PaddleOCR-VL"


def test_each_reader_brings_its_own_title_and_hint(rapidocr_installed: None) -> None:
    """No fixed list: the words come from the engine classes."""
    _, paddle, rapid = ocr_choices(PADDLE)
    assert (paddle["title"], paddle["hint"]) == (PaddleVlEngine.title, PaddleVlEngine.hint)
    assert (rapid["title"], rapid["hint"]) == (RapidOcrEngine.title, RapidOcrEngine.hint)
    assert all(str(item["id"]).isidentifier() and str(item["id"]).islower() for item in ocr_choices(PADDLE))


# --- OcrEngines ------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Engine:
    """Answers each page with the next of ``script``: an error is raised, "" is no line,
    anything else one line."""

    def __init__(self, name: str, script: list[Exception | str]) -> None:
        self.name = name
        self.version = f"{name}-1"
        self.script = script
        self.pages = 0

    def read(self, image: Image.Image) -> list[OcrLine]:
        step = self.script[self.pages % len(self.script)]
        self.pages += 1
        if isinstance(step, Exception):
            raise step
        return [OcrLine(PAGE_TEXT, 1, 1, 9, 9, 0.9, step)] if step else []


class Rig:
    def __init__(
        self, paddle: list[Exception | str], rapid: list[Exception | str], settings: Settings = PADDLE
    ) -> None:
        self.clock = Clock()
        self.paddle = Engine("paddleocr-vl", paddle)
        self.rapid = Engine("rapidocr", rapid)
        self.built: Counter[str] = Counter()
        self.engines = OcrEngines(settings, engine=self.build, timer=self.clock)

    def build(self, engine_id: str) -> OcrEngine:
        self.built[engine_id] += 1
        return {"paddleocr_vl": self.paddle, "rapidocr": self.rapid}[engine_id]

    def read(self) -> list[OcrLine]:
        return self.engines.read(Image.new("RGB", (20, 10), "white"))


def test_auto_is_as_before_and_a_page_with_no_line_stays_empty(rapidocr_installed: None) -> None:
    rig = Rig([""], ["rapid"])
    assert rig.read() == []
    assert rig.built == Counter({"paddleocr_vl": 1})  # RapidOCR never built
    assert rig.engines.name == "paddleocr-vl"


def test_choosing_auto_or_autos_own_first_engine_is_auto_itself(rapidocr_installed: None) -> None:
    rig = Rig([OcrOverloaded("ocr_timeout"), "paddle"], ["rapid"])
    (line,) = rig.read()  # Auto: PaddleOCR-VL overloaded, RapidOCR read it, cool-down starts
    assert line.kind == "rapid"
    for choice in (None, "auto", "paddleocr_vl"):
        with rig.engines.choose(choice) as honoured:
            assert honoured
            assert rig.read()[0].kind == "rapid"  # the same cool-down: PaddleOCR-VL not asked
    assert rig.paddle.pages == 1
    rig.clock.now += COOL_DOWN_SECONDS
    with rig.engines.choose("paddleocr_vl"):
        assert rig.read()[0].kind == "paddle"


@pytest.mark.parametrize("failure", [ReadError("ocr_failed"), ""])
def test_rapidocr_chosen_falls_back_to_auto_when_it_cannot_read(
    failure: Exception | str, rapidocr_installed: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rig = Rig(["paddle"], [failure])
    with rig.engines.choose("rapidocr") as honoured:
        assert honoured
        assert rig.engines.name == "rapidocr"
        (line,) = rig.read()
    assert line.kind == "paddle"
    assert (rig.rapid.pages, rig.paddle.pages) == (1, 1)
    assert rig.engines.take_fallback_pages() == 1
    assert PAGE_TEXT not in caplog.text
    assert [record.__dict__.get("code") for record in caplog.records if record.name == ocr.__name__] == [
        "ocr_failed" if failure else "ocr_no_lines"
    ]


def test_rapidocr_chosen_keeps_its_own_lines_when_it_reads_the_page(rapidocr_installed: None) -> None:
    rig = Rig(["paddle"], ["rapid"])
    with rig.engines.choose("rapidocr"):
        assert rig.read()[0].kind == "rapid"
    assert rig.paddle.pages == 0
    assert rig.engines.take_fallback_pages() == 0


def test_a_page_neither_can_read_stays_as_the_chosen_reader_read_it(rapidocr_installed: None) -> None:
    rig = Rig([BackendUnavailable("ocr")], [""])
    with rig.engines.choose("rapidocr"):
        assert rig.read() == []


def test_auto_and_the_rapidocr_choice_share_one_rapidocr(rapidocr_installed: None) -> None:
    rig = Rig([ReadError("ocr_failed")], ["rapid"])
    rig.read()  # Auto's fallback builds RapidOCR
    with rig.engines.choose("rapidocr"):
        rig.read()
    with rig.engines.choose("rapidocr"):
        rig.read()
    assert rig.built == Counter({"paddleocr_vl": 1, "rapidocr": 1})


def test_the_choice_ends_with_its_job_even_when_the_job_raises(rapidocr_installed: None) -> None:
    rig = Rig(["paddle"], ["rapid"])
    with pytest.raises(RuntimeError), rig.engines.choose("rapidocr"):
        raise RuntimeError("a bug in the job")
    assert rig.engines.name == "paddleocr-vl"
    assert rig.read()[0].kind == "paddle"


@pytest.mark.parametrize("choice", ["rapidocr", "tesseract", "Rapid OCR"])
def test_a_choice_this_server_lacks_is_read_with_auto(choice: str, no_rapidocr: None) -> None:
    rig = Rig(["paddle"], ["rapid"])
    with rig.engines.choose(choice) as honoured:
        assert not honoured
        assert rig.read()[0].kind == "paddle"
    assert "rapidocr" not in rig.built


def test_the_meter_follows_the_chosen_engine_and_sums_fallback_pages(rapidocr_installed: None) -> None:
    rig = Rig([ReadError("ocr_failed"), "paddle"], [""])
    meter = MeteredOcrEngine(rig.engines, timer=rig.clock)
    meter.read(Image.new("RGB", (20, 10)))  # Auto: PaddleOCR-VL failed, RapidOCR read it
    with rig.engines.choose("rapidocr"):
        assert meter.name == "rapidocr"
        meter.read(Image.new("RGB", (20, 10)))  # RapidOCR found nothing, Auto read it
    assert meter.name == "paddleocr-vl"
    assert (meter.take_fallback_pages(), meter.take_fallback_pages()) == (2, 0)


def test_the_image_reader_names_the_engine_in_use(rapidocr_installed: None) -> None:
    rig = Rig(["paddle"], ["rapid"])
    socket = build_reader_socket(PADDLE, MeteredOcrEngine(rig.engines, timer=rig.clock))
    with rig.engines.choose("rapidocr"):
        assert socket.reader_id("png") == ("rapidocr", "rapidocr-1")
    assert socket.reader_id("png") == ("paddleocr-vl", "paddleocr-vl-1")


def test_no_scan_reader_means_no_engines() -> None:
    assert build_ocr_engines(NONE) is None
    with pytest.raises(ValueError):
        OcrEngines(NONE)
