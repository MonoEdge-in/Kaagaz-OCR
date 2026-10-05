"""The OCR DPI rule (council 5 Oct 2026): the line height estimator, the DPI it
gives, and the PDF and image readers with OCR_DPI_RULE. Pictures are drawn with
rectangles only (tests/picture_support.py), so they are the same on every machine."""

import io
import itertools
import math
import unittest
from pathlib import Path

from PIL import Image

from kaagaz.preparing.pages import open_pdf, render_pdf_page
from kaagaz.reading import ocr_dpi
from kaagaz.reading.image_reader import ImageReader
from kaagaz.reading.ocr import OcrLine
from kaagaz.reading.pdf_reader import PdfReader
from kaagaz.reading.spans import Span
from kaagaz.reading.text_quality import TextLayerRules
from tests.pdf_builder import Page, Picture, Text, make_pdf
from tests.picture_support import Bar, noise_picture, picture_with_bars

XLANG = Path(__file__).resolve().parent / "golden" / "xlang"
RULES = TextLayerRules(min_chars=40, min_good_ratio=0.9, scan_image_coverage=0.5)
# The widths of the letters, in turn, so no gap lines up from one line to the next (as in print).
_LETTERS = (5, 8, 6, 9, 7, 4, 8, 6)


def text_lines(width: int, height: int, line_px: int, *, headline: bool = False) -> list[Bar]:
    """Lines of letter-like bars ``line_px`` high, 1.8 lines apart, inside a 40-pixel
    margin. With ``headline``, each word is Devanagari-like: a 2-pixel bar over the
    whole word, with letters (a stem and a body) hanging from it."""
    bars: list[Bar] = []
    for index, top in enumerate(range(40, height - 40 - line_px, max(line_px + 2, line_px * 9 // 5))):
        left, letter = 40 + index * 7 % 23, index
        while left < width - 60:
            word_left = left
            for _ in range(5):
                letter_px = _LETTERS[letter % len(_LETTERS)]
                letter += 1
                if headline:
                    bars.append((left, top, 2, line_px))  # the stem
                    bars.append((left + 2, top + line_px // 3, letter_px, line_px // 3))  # the body
                else:
                    bars.append((left, top, letter_px, line_px))
                left += letter_px + 3
            if headline:
                bars.append((word_left, top, left - word_left - 3, 2))
            left += 12
    return bars


def page_of(line_px: int, *, size: tuple[int, int] = (850, 1200), headline: bool = False) -> Image.Image:
    return picture_with_bars(*size, text_lines(*size, line_px, headline=headline))


def measure(picture: Image.Image, dpi: float | None = None) -> int | None:
    """The line height, the picture taken as an A4 page across its short side unless ``dpi``."""
    return ocr_dpi.line_height_px(picture, dpi=min(picture.size) / 8.27 if dpi is None else dpi)


class LineHeightTest(unittest.TestCase):
    def test_lines_of_a_known_height_are_measured_within_one_pixel(self) -> None:
        for line_px in (5, 8, 13, 21, 34):
            with self.subTest(line_px=line_px):
                measured = measure(page_of(line_px))
                assert measured is not None
                self.assertLessEqual(abs(measured - line_px), 1)

    def test_devanagari_like_words_are_measured_from_the_headline_down(self) -> None:
        for line_px in (9, 15, 24):
            with self.subTest(line_px=line_px):
                measured = measure(page_of(line_px, headline=True))
                assert measured is not None
                self.assertLessEqual(abs(measured - line_px), 1)

    def test_a_table_is_measured_by_its_text_not_its_ruled_lines(self) -> None:
        rules = [(30, top, 790, 2) for top in range(30, 1170, 90)]
        rules += [(left, 30, 2, 1140) for left in range(30, 830, 130)]
        measured = measure(picture_with_bars(850, 1200, text_lines(850, 1200, 12) + rules))
        assert measured is not None
        self.assertLessEqual(abs(measured - 12), 1)

    def test_real_scans_in_devanagari_with_a_ruled_table_are_measured(self) -> None:
        """Two xlang scans (tilted, with noise and JPEG marks), on the probe the reader draws."""
        for name in ("hi-rent-agreement.pdf", "mr-land-record.pdf"):
            document = open_pdf((XLANG / name).read_bytes())
            page = document[0]
            try:
                probe = ocr_dpi.probe_dpi(page.get_size(), 400)
                assert probe is not None
                picture = render_pdf_page(page, dpi=probe, max_image_pixels=21_000_000)
            finally:
                page.close()
                document.close()
            with self.subTest(name):
                measured = measure(picture, dpi=probe)
                assert measured is not None
                self.assertTrue(12 <= measured <= 22, measured)  # about 13 pt of ink a line

    def test_with_two_sizes_the_smaller_decides(self) -> None:
        small = text_lines(850, 600, 8)
        large = [(left, top + 600, width, height) for left, top, width, height in text_lines(850, 600, 14)]
        measured = measure(picture_with_bars(850, 1200, small + large))
        assert measured is not None
        self.assertLessEqual(abs(measured - 8), 1)

    def test_a_picture_it_cannot_trust_gives_none(self) -> None:
        grid = [(40, top, 770, 2) for top in range(40, 1160, 40)]
        grid += [(left, 40, 2, 1120) for left in range(40, 810, 100)]
        spread = text_lines(850, 200, 6) + [(x, y + 200, w, h) for x, y, w, h in text_lines(850, 1000, 30)]
        cases = {
            "blank": Image.new("RGB", (850, 1200), "white"),
            "one word": picture_with_bars(850, 1200, [(40, 40, 30, 10)]),
            "noise": noise_picture(400, 300, mode="L"),
            "photo": Image.linear_gradient("L").resize((600, 800)),
            "a table grid with no text": picture_with_bars(850, 1200, grid),
            "turned a quarter": page_of(12).transpose(Image.Transpose.ROTATE_90),
            "sizes too far apart": picture_with_bars(850, 1200, spread),
        }
        for name, picture in cases.items():
            with self.subTest(name):
                self.assertIsNone(measure(picture))

    def test_the_same_picture_gives_the_same_answer_in_grey_and_rgb(self) -> None:
        picture = page_of(11)
        answers = {measure(picture) for _ in range(2)}
        answers.add(measure(picture.convert("L")))
        self.assertEqual(len(answers), 1)
        self.assertIsNotNone(answers.pop())

    def test_otsu_splits_ink_from_paper(self) -> None:
        histogram = [0] * 256
        histogram[24], histogram[255] = 100, 900
        self.assertEqual(ocr_dpi.otsu_threshold(histogram), 24)  # the lowest of the best: ink is <= 24
        self.assertIsNone(ocr_dpi.otsu_threshold([0] * 255 + [1000]))  # one grey: no split


class ChosenDpiTest(unittest.TestCase):
    def test_the_dpi_is_never_above_the_ceiling_nor_below_72(self) -> None:
        for fit, line_px, probe, scan in itertools.product(
            (72, 100, 300, 400), (1, 2, 6, 24, 48, 200, 2000), (36, 109, 400), (None, 1, 50, 150, 600)
        ):
            dpi = ocr_dpi.chosen_dpi(fit, line_px, probe, scan)
            self.assertLessEqual(dpi, fit)
            self.assertGreaterEqual(dpi, 72)

    def test_text_sets_the_dpi_and_the_size_fit_and_scan_cap_lower_it(self) -> None:
        # 24 px lines on a 109 dpi probe are 15.9 pt: 48 px lines need 218 dpi.
        self.assertEqual(ocr_dpi.chosen_dpi(400, 24, 109, None), 218)
        self.assertEqual(ocr_dpi.chosen_dpi(164, 24, 109, None), 164)  # the size fit still wins
        self.assertEqual(ocr_dpi.chosen_dpi(400, 24, 109, 150), 150)  # a 150 dpi scan has no more detail
        self.assertEqual(ocr_dpi.chosen_dpi(400, 6, 109, None), 400)  # small print stays at the ceiling
        self.assertEqual(ocr_dpi.chosen_dpi(400, 200, 109, None), 72)
        self.assertEqual(ocr_dpi.chosen_dpi(400, None, 109, 150), 400)  # not measured: as today

    def test_the_probe_is_about_1200_pixels_long_and_never_above_the_ceiling(self) -> None:
        self.assertEqual(ocr_dpi.probe_dpi((612.0, 792.0), 400), 109)
        self.assertEqual(ocr_dpi.probe_dpi((100.0, 150.0), 400), 400)
        self.assertEqual(ocr_dpi.probe_dpi((10.0**9, 10.0), 400), 1)
        for size in ((0.0, 0.0), (math.nan, 10.0), (math.inf, 10.0)):
            self.assertIsNone(ocr_dpi.probe_dpi(size, 400))

    def test_a_photo_is_never_enlarged_nor_shrunk_under_72_dpi_on_a4(self) -> None:
        self.assertEqual(ocr_dpi.photo_scale(96, 2000), 0.5)
        self.assertEqual(ocr_dpi.photo_scale(20, 2000), 1.0)
        self.assertEqual(ocr_dpi.photo_scale(None, 2000), 1.0)
        self.assertAlmostEqual(ocr_dpi.photo_scale(480, 2000), 72 * 8.27 / 2000)


class FakeOcr:
    """Records the size of each picture and answers with ``answers`` in turn (the last one after)."""

    name = "fake-ocr"
    version = "1"

    def __init__(self, *answers: list[OcrLine]) -> None:
        self.answers = list(answers) or [[OcrLine("TOTAL 500", 10, 10, 120, 30, 0.9)]]
        self.seen: list[tuple[int, int]] = []

    def read(self, image: Image.Image) -> list[OcrLine]:
        self.seen.append(image.size)
        return self.answers[min(len(self.seen), len(self.answers)) - 1]


def pdf_dpis(
    data: bytes, rule: str, ocr: FakeOcr | None = None, *, width_pt: float = 612.0
) -> tuple[list[int], list[Span], str]:
    """The DPI of each picture a page ``width_pt`` wide was sent at (from its width), the
    spans and the reader's version, with OCR_DPI 300."""
    engine = ocr or FakeOcr()
    reader = PdfReader(
        engine,
        max_pages=2,
        dpi=300,
        max_image_pixels=40_000_000,
        rules=RULES,
        blank_stddev=3.0,
        dpi_rule=rule,
    )
    spans = reader.read(data)
    return [round(width * 72 / width_pt) for width, _height in engine.seen], spans, reader.version


# A scan at 144 dpi (1224 x 1584 pixels on a letter page) with 40-pixel lines, 20 pt: the
# text rule wants 72 x 48 / 20 = 173 dpi, measured on the 109 dpi probe within a pixel.
SCAN_144 = picture_with_bars(1224, 1584, text_lines(1224, 1584, 40))


class PdfReaderRuleTest(unittest.TestCase):
    def test_fixed_reads_every_page_as_before(self) -> None:
        data = make_pdf([Page(picture=Picture(SCAN_144, 0, 0, 612, 792))])
        before, after = FakeOcr(), FakeOcr()
        today = PdfReader(
            before, max_pages=2, dpi=300, max_image_pixels=40_000_000, rules=RULES, blank_stddev=3.0
        )
        self.assertEqual(pdf_dpis(data, ocr_dpi.FIXED, after)[1], today.read(data))
        self.assertEqual(after.seen, before.seen)
        self.assertEqual(before.seen, [(2550, 3301)])  # 300 dpi, as render_pdf_page rounds up

    def test_a_full_page_scan_is_drawn_at_its_text_size_but_never_above_its_own_dpi(self) -> None:
        data = make_pdf([Page(picture=Picture(SCAN_144, 0, 0, 612, 792))])
        dpis, spans, version = pdf_dpis(data, ocr_dpi.TEXT_HEIGHT)
        self.assertEqual(dpis, [144])  # the scan cap
        self.assertTrue(version.endswith(";ocr_dpi=144"))  # recorded as the lowest DPI used
        self.assertEqual([span.text for span in spans], ["TOTAL 500"])
        (no_cap,), _spans, _version = pdf_dpis(data, ocr_dpi.TEXT_HEIGHT_NO_SCAN_CAP)
        self.assertTrue(165 <= no_cap <= 181, no_cap)

    def test_an_image_under_the_scan_coverage_does_not_cap_the_page(self) -> None:
        # The same text on 43% of the page: the image's 144 dpi is not the page's.
        part = SCAN_144.crop((0, 0, 1224, 680))
        data = make_pdf([Page(picture=Picture(part, 0, 452, 612, 340))])
        ((dpi,), _spans, _version) = pdf_dpis(data, ocr_dpi.TEXT_HEIGHT)
        self.assertTrue(165 <= dpi <= 181, dpi)

    def test_a_vector_page_with_a_logo_is_not_capped_by_the_logo(self) -> None:
        # An old Hindi font sends the page to OCR (legacy_font); the 18 dpi logo is the only image.
        words = ("Ramesh", "Kumar", "paid", "4,520", "for", "12", "bags", "of", "cement", "to", "Sharma")
        texts = [Text(40, 740 - 34 * row, " ".join(words[row % 5 :][:6]), size=24) for row in range(20)]
        logo = Image.new("RGB", (20, 20), (30, 30, 30))
        data = make_pdf([Page(texts=texts, picture=Picture(logo, 500, 20, 80, 80), font_name="KrutiDev010")])
        ((dpi,), _spans, _version) = pdf_dpis(data, ocr_dpi.TEXT_HEIGHT)
        self.assertTrue(100 < dpi < 300, dpi)

    def test_vector_text_over_a_full_page_tint_is_not_capped_at_the_tints_dpi(self) -> None:
        # Review fix 1: 8 pt old-font text (legacy_font, over 200 characters) on a 72 dpi tint.
        texts = [Text(40, 760 - 12 * row, "Ramesh Kumar paid 4,520 for 12 bags", size=8) for row in range(60)]
        tint = Image.new("RGB", (612, 792), (225, 225, 225))
        data = make_pdf([Page(texts=texts, picture=Picture(tint, 0, 0, 612, 792), font_name="KrutiDev010")])
        self.assertEqual(pdf_dpis(data, ocr_dpi.TEXT_HEIGHT)[0], [300])

    def test_small_print_stays_at_the_ceiling(self) -> None:
        small = picture_with_bars(1224, 1584, text_lines(1224, 1584, 8))  # 4 pt lines
        data = make_pdf([Page(picture=Picture(small, 0, 0, 612, 792))])
        self.assertEqual(pdf_dpis(data, ocr_dpi.TEXT_HEIGHT_NO_SCAN_CAP)[0], [300])

    def test_small_print_on_an_a3_page_stays_at_the_ceiling(self) -> None:
        """The A3 probe is 72 dpi, so 4 pt print is 4 pixels on it, and still counts."""
        bars = text_lines(1684, 500, 60) + [(x, y + 500, w, h) for x, y, w, h in text_lines(1684, 1882, 8)]
        a3 = picture_with_bars(1684, 2382, bars)  # 144 dpi: 30 pt headings over 4 pt print
        data = make_pdf([Page(width=842, height=1191, picture=Picture(a3, 0, 0, 842, 1191))])
        dpis, _spans, _version = pdf_dpis(data, ocr_dpi.TEXT_HEIGHT_NO_SCAN_CAP, width_pt=842)
        self.assertEqual(dpis, [300])

    def test_no_lines_at_a_lowered_dpi_reads_the_page_again_at_the_ceiling(self) -> None:
        data = make_pdf([Page(picture=Picture(SCAN_144, 0, 0, 612, 792))])
        ocr = FakeOcr([], [OcrLine("TOTAL 500", 10, 10, 120, 30, 0.9)])
        dpis, spans, _version = pdf_dpis(data, ocr_dpi.TEXT_HEIGHT, ocr)
        self.assertEqual(dpis, [144, 300])
        self.assertEqual([span.text for span in spans], ["TOTAL 500"])

    def test_an_unknown_rule_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            PdfReader(
                None, max_pages=1, dpi=1, max_image_pixels=1, rules=RULES, blank_stddev=0, dpi_rule="auto"
            )
        with self.assertRaises(ValueError):
            ImageReader(
                FakeOcr(), max_pages=1, max_image_pixels=1, ocr_dpi=1, blank_stddev=0, dpi_rule="auto"
            )


def photo_sizes(
    picture: Image.Image, rule: str, ocr: FakeOcr | None = None, *, dpi: int = 300
) -> list[tuple[int, int]]:
    engine = ocr or FakeOcr()
    reader = ImageReader(
        engine, max_pages=1, max_image_pixels=20_000_000, ocr_dpi=dpi, blank_stddev=3.0, dpi_rule=rule
    )
    reader.read(_png(picture))
    return engine.seen


def _png(picture: Image.Image) -> bytes:
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


class ImageReaderRuleTest(unittest.TestCase):
    def test_a_photo_with_tall_lines_is_shrunk_to_its_text(self) -> None:
        photo = page_of(96, size=(1600, 2200))
        self.assertEqual(photo_sizes(photo, ocr_dpi.FIXED), [(1600, 2200)])
        self.assertEqual(photo_sizes(photo, ocr_dpi.TEXT_HEIGHT), [(800, 1100)])  # 48 / 96

    def test_a_photo_with_small_lines_is_never_enlarged(self) -> None:
        photo = page_of(20, size=(1600, 2200))
        self.assertEqual(photo_sizes(photo, ocr_dpi.TEXT_HEIGHT), [(1600, 2200)])

    def test_fine_print_on_a_large_photo_keeps_it_from_being_shrunk(self) -> None:
        """The probe is the photo reduced 4 times, so the 16 px print is 4 pixels on it."""
        bars = text_lines(3000, 2000, 80) + [(x, y + 2000, w, h) for x, y, w, h in text_lines(3000, 2000, 16)]
        photo = picture_with_bars(3000, 4000, bars)
        self.assertEqual(photo_sizes(photo, ocr_dpi.TEXT_HEIGHT, dpi=400), [(3000, 4000)])

    def test_no_lines_on_the_shrunk_photo_reads_it_again_as_it_was(self) -> None:
        ocr = FakeOcr([], [OcrLine("TOTAL 500", 10, 10, 120, 30, 0.9)])
        photo = page_of(96, size=(1600, 2200))
        self.assertEqual(photo_sizes(photo, ocr_dpi.TEXT_HEIGHT, ocr), [(800, 1100), (1600, 2200)])


if __name__ == "__main__":
    unittest.main()
