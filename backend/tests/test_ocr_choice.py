"""No reader choice at upload (PRD 5 RT3, kaagaz/reading/ocr.py): ``ocr_choices`` offers
one reader, Auto, and every scan is read with OCR_ENGINE's engine.
"""The scan reader an upload chose (PRD 4 #17, kaagaz/reading/ocr.py): ``ocr_choices``,
one reader since PRD 5 RT3, and ``OcrEngines``, which reads a job whose stored choice
names an engine this server has with that engine and falls back to Auto. Engines are
fakes; the timer is a fake clock.
"""The scan reader an upload chose (PRD 4 #17, kaagaz/reading/ocr.py): ``ocr_choices``
from the settings and what is installed, and ``OcrEngines``, which reads one job
with the chosen engine and falls back to Auto. Engines are fakes; the timer is a
fake clock.
"""

import pytest

from kaagaz.reading import ocr
from kaagaz.reading.ocr import build_ocr_engine, ocr_choices
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

RAPID = make_settings(ocr_engine="rapidocr")
NONE = make_settings(ocr_engine="none")
GEMINI = make_settings(ocr_engine="gemini")


def offered(settings: Settings) -> list[tuple[object, ...]]:
    return [(item["id"], item["available"], item["reason"]) for item in ocr_choices(settings)]


@pytest.mark.parametrize("settings", [NONE, RAPID, GEMINI], ids=["none", "rapidocr", "gemini"])
def test_rt3_an_upload_is_offered_one_reader_so_the_select_hides_itself(settings: Settings) -> None:
    """RT3: no reader choice at upload. The browser shows "Scan with" only for two or
    more usable readers (frontend/components/documents/readers.ts offersChoice)."""
    assert offered(settings) == [("auto", True, None)]
    assert ocr_choices(settings)[0]["title"] == "Auto"


def test_rt3_the_one_reader_says_what_this_server_reads_with() -> None:
    hints = {
        name: ocr_choices(settings)[0]["hint"] for name, settings in (("none", NONE), ("gemini", GEMINI))
    }
    assert hints == {
        "none": "Scans wait until this server has a scan reader",
        "gemini": "The usual reader here: the main reader",  # U8: no engine named
    }
    assert ocr_choices(RAPID)[0]["hint"] == "The usual reader here: the backup reader"


@pytest.mark.parametrize("settings", [NONE, RAPID, GEMINI], ids=["none", "rapidocr", "gemini"])
def test_u8_no_server_sent_title_or_hint_names_an_engine(settings: Settings) -> None:
    """PRD 5 U8: words people know, never an engine's name."""
    words = [str(item[key]).lower() for item in ocr_choices(settings) for key in ("title", "hint")]
    assert [word for word in words if "ocr" in word or "gemini" in word] == []


def test_rt3_listing_the_reader_builds_and_calls_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pure: the api lists it on every upload page, with no engine and no Gemini call."""

    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("ocr_choices must not build an engine")

    monkeypatch.setattr(ocr, "RapidOcrEngine", never)
    monkeypatch.setattr(ocr, "shared_pool", never)
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


def test_no_scan_reader_means_no_engine() -> None:
    assert build_ocr_engine(NONE) is None
