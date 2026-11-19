"""Which way up a page's writing is (#709: kaagaz/reading/orientation.py). The real RapidOCR on
pages drawn with Pillow (Latin) and on the Hindi and Marathi fixture letters (Devanagari), each
turned every way; a fake pool for the rules that decide between the two ways a page may be."""

import pathlib
from typing import Any, cast

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw, ImageFont

from kaagaz.reading import orientation
from kaagaz.reading.rapid_pool import PixelBox, RapidOcrPool

# rapidocr is the optional ocr extra (pyproject.toml), as tests/test_ocr.py.
pytest.importorskip("rapidocr", reason='needs the ocr extra: pip install -e ".[dev,ocr]"')

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "office"
LINES = (
    "Tax Invoice No 4521 dated 12 March 2026",
    "Sharma Traders, Station Road, Pune 411001",
    "Item: Cotton fabric 40 metres at Rs 220",
    "Total amount payable Rs 8,800 only",
    "GSTIN 27ABCDE1234F1Z5 Place of supply",
    "Thank you for your business with us",
    "Bank: State Bank of India, Account 1234567890",
)


@pytest.fixture(scope="module")
def pool() -> RapidOcrPool:
    return RapidOcrPool(1)


def _latin_page() -> Image.Image:
    page = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=30)
    for row, line in enumerate(LINES):
        draw.text((90, 140 + row * 80), line, fill="black", font=font)
    return page


def _letter(name: str) -> Image.Image:
    document = pdfium.PdfDocument(FIXTURES / f"{name}.pdf")
    try:
        bitmap = document[0].render(scale=150 / 72)
        picture: Image.Image = bitmap.to_pil().convert("RGB")
        return picture
    finally:
        document.close()


# A page turned left by ``turn`` (PIL turns anticlockwise) needs ``turn`` to the right to be upright.
@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_a_latin_page_turned_any_way_gets_the_turn_that_puts_it_upright(
    pool: RapidOcrPool, turn: int
) -> None:
    assert orientation.upright_turn(pool, _latin_page().rotate(turn, expand=True)) == turn


@pytest.mark.parametrize("name", ["hindi-letter", "marathi-letter"])
@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_a_devanagari_page_turned_any_way_gets_the_turn_that_puts_it_upright(
    pool: RapidOcrPool, name: str, turn: int
) -> None:
    assert orientation.upright_turn(pool, _letter(name).rotate(turn, expand=True)) == turn


def test_a_blank_page_and_a_page_of_two_lines_are_unsure(pool: RapidOcrPool) -> None:
    assert orientation.upright_turn(pool, Image.new("RGB", (1240, 1754), "white")) is None
    page = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(page)
    for row, line in enumerate(LINES[:2]):
        draw.text((90, 140 + row * 80), line, fill="black", font=ImageFont.load_default(size=30))
    assert orientation.upright_turn(pool, page) is None


class _Fake:
    """``count`` short lines across a page, each dark at its start, so a read can tell the way a
    piece was turned: the right way up it reads ``reads[script][0]``, upside down
    ``reads[script][1]`` (script False: Latin, True: Devanagari). The lines sit evenly about the
    page's middle, so the page turned upside down has its lines in the same boxes."""

    def __init__(self, count: int, reads: dict[bool, tuple[str, str]]) -> None:
        top = 515 - 30 * count
        self.boxes: list[PixelBox] = [
            (400.0, top + 60.0 * n, 600.0, top + 60.0 * n + 30) for n in range(count)
        ]
        self.reads = reads
        self.read = 0
        self.page = Image.new("RGB", (1000, 1000), "white")
        draw = ImageDraw.Draw(self.page)
        for x0, y0, _, y1 in self.boxes:
            draw.rectangle((x0, y0, x0 + 20, y1), fill="black")

    def find_boxes(self, image: Image.Image) -> list[PixelBox]:
        return self.boxes

    def read_crop(self, image: Image.Image, *, devanagari: bool) -> str:
        self.read += 1
        gray = image.convert("L")
        middle = gray.height // 2
        upright = cast(int, gray.getpixel((2, middle))) < cast(int, gray.getpixel((gray.width - 3, middle)))
        return self.reads[devanagari][0 if upright else 1]


def _turn(fake: _Fake, picture: Image.Image | None = None) -> Any:
    return orientation.upright_turn(cast(RapidOcrPool, fake), picture or fake.page)


def test_the_way_that_reads_clearly_more_letters_wins() -> None:
    fake = _Fake(6, {False: ("Invoice 4521", "lo"), True: ("", "")})
    assert _turn(fake) == 0
    assert _turn(fake, fake.page.rotate(180)) == 180
    assert fake.read == 2 * 2 * orientation.LINES_READ  # Latin was clear: Devanagari never read


def test_too_few_lines_is_unsure_and_nothing_is_read() -> None:
    fake = _Fake(4, {False: ("Invoice 4521", ""), True: ("", "")})  # MIN_LINES is 5
    assert _turn(fake) is None
    assert fake.read == 0


def test_two_ways_that_read_alike_are_unsure() -> None:
    # Twice as many letters is the bar: 6 against 4 is not clear (each line: 2 against 1.33).
    fake = _Fake(6, {False: ("abc123", "abcd"), True: ("abc123", "abcd")})
    assert _turn(fake) is None


def test_too_few_letters_either_way_is_unsure() -> None:
    fake = _Fake(6, {False: ("a", ""), True: ("b", "")})  # 3 letters over the lines read
    assert _turn(fake) is None


def test_devanagari_decides_when_latin_cannot_tell() -> None:
    fake = _Fake(6, {False: ("", ""), True: ("कर बीजक ४५२१", "ा")})
    assert _turn(fake, fake.page.rotate(180)) == 180
