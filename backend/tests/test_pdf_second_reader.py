import unittest

import pypdfium2 as pdfium

from kaagaz.reading.pdf_reader import PdfReader
from kaagaz.reading.pdf_second_reader import PdfiumReader
from kaagaz.reading.spans import BoxLocation, ReadError, Span
from kaagaz.reading.text_quality import TextLayerRules
from tests.pdf_builder import Page, Text, make_pdf


def second(data: bytes, max_pages: int = 10) -> list[Span]:
    return PdfiumReader(max_pages=max_pages).read(data)


def first(data: bytes) -> list[Span]:
    rules = TextLayerRules(min_chars=40, min_good_ratio=0.9, scan_image_coverage=0.5)
    return PdfReader(
        None, max_pages=10, dpi=72, max_image_pixels=10_000_000, rules=rules, blank_stddev=3.0
    ).read(data)


def box_of(span: Span) -> BoxLocation:
    assert isinstance(span.location, BoxLocation)
    return span.location


def iou(a: BoxLocation, b: BoxLocation) -> float:
    """Intersection over union, the measure the value labels use (>= 0.5 is "the same place")."""
    width = min(a.x1, b.x1) - max(a.x0, b.x0)
    height = min(a.y1, b.y1) - max(a.y0, b.y0)
    shared = max(0.0, width) * max(0.0, height)
    union = (a.x1 - a.x0) * (a.y1 - a.y0) + (b.x1 - b.x0) * (b.y1 - b.y0) - shared
    return shared / union


INVOICE = [
    Page(
        texts=[
            Text(72, 740, "Sharma Traders", size=18),
            Text(72, 700, "Invoice No: INV-1042"),
            Text(400, 700, "Date: 12/08/2026"),
            Text(72, 660, "Grand Total (Rs) 1,23,456.78"),
        ]
    ),
    Page(width=300, height=400, texts=[Text(20, 300, "Page two, small", size=9)]),
]


class AgreementTest(unittest.TestCase):
    """The second reader must agree with the first on a clean file, or every
    value on a good PDF would be sent for review."""

    def assert_agree(self, data: bytes) -> None:
        ones, twos = first(data), second(data)
        self.assertEqual([s.text for s in twos], [s.text for s in ones])
        for one, two in zip(ones, twos, strict=True):
            with self.subTest(text=one.text):
                self.assertEqual(box_of(one).page, box_of(two).page)
                self.assertGreaterEqual(iou(box_of(one), box_of(two)), 0.7)

    def test_same_lines_and_nearly_the_same_boxes(self) -> None:
        self.assert_agree(make_pdf(INVOICE))

    def test_they_agree_on_a_rotated_page_too(self) -> None:
        self.assert_agree(make_pdf([Page(rotate=90, texts=[Text(540, 72, "Landscape total 500", turn=90)])]))

    def test_they_agree_on_a_page_cropped_past_its_media_box(self) -> None:
        """#445 follow-up: the first reader's window was cut to the media box and the
        second's was not, so their boxes did not overlap at all (IoU 0)."""
        for rotate in (0, 90, 180, 270):
            page = Page(
                rotate=rotate,
                crop=(-30.0, 60.0, 560.0, 900.0),
                texts=[Text(300, 400, "Hg Total 500", turn=rotate)],
            )
            with self.subTest(rotate=rotate):
                self.assert_agree(make_pdf([page]))

    def test_second_reading_carries_no_confidence_and_keeps_raw(self) -> None:
        spans = second(make_pdf(INVOICE))
        self.assertTrue(all(s.confidence is None for s in spans))
        self.assertTrue(all(s.raw == s.text and box_of(s).is_valid() for s in spans))


class SecondReaderTest(unittest.TestCase):
    def test_line_text_and_box(self) -> None:
        (span,) = second(make_pdf([Page(texts=[Text(72, 700, "Invoice No: INV-1042")])]))
        box = box_of(span)
        self.assertEqual((span.text, box.page), ("Invoice No: INV-1042", 1))
        self.assertAlmostEqual(box.x0, 72 / 612, places=3)
        self.assertLess(box.y0, (792 - 700) / 792)

    def test_pages_without_a_text_layer_give_nothing(self) -> None:
        self.assertEqual(second(make_pdf([Page()])), [])

    def test_two_columns_split_the_same_way_as_the_first_reader(self) -> None:
        data = make_pdf([Page(texts=[Text(72, 700, "Invoice No: 7"), Text(400, 700, "Date: 1/2/26")])])
        self.assertEqual([s.text for s in second(data)], ["Invoice No: 7", "Date: 1/2/26"])

    def test_refusals_are_short_codes_with_nothing_chained(self) -> None:
        cases = {
            "encrypted": (make_pdf([Page()], password_protected=True), 10),
            "corrupt_file": (b"%PDF-1.7\nnot really a pdf", 10),
            "too_many_pages": (make_pdf([Page(), Page()]), 1),
        }
        for code, (data, max_pages) in cases.items():
            with self.subTest(code), self.assertRaises(ReadError) as ctx:
                second(data, max_pages)
            self.assertEqual(ctx.exception.code, code)
            self.assertIsNone(ctx.exception.__context__)

    def test_hidden_text_is_not_read(self) -> None:
        """Council PR 3 review, high 1: render mode 3, white fill and a size under 1 pt
        are all left out, so hidden text never agrees with itself."""
        texts = [
            Text(72, 700, "Seen line one"),
            Text(72, 650, "Total: 1.00", invisible=True),
            Text(72, 600, "White words", color=(1, 1, 1)),
            Text(72, 550, "Tiny words", size=0.5),
        ]
        spans = PdfiumReader(max_pages=10).read(make_pdf([Page(texts=texts)]))
        self.assertEqual([span.text for span in spans], ["Seen line one"])

    def test_name_and_version(self) -> None:
        reader = PdfiumReader(max_pages=1)
        self.assertEqual(reader.name, "pdfium")
        self.assertEqual(reader.version, f"{pdfium.PYPDFIUM_INFO}+pdfium-{pdfium.PDFIUM_INFO.build}")

    def test_max_pages_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            PdfiumReader(max_pages=0)


if __name__ == "__main__":
    unittest.main()
