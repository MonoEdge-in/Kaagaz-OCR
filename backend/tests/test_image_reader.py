import io
import unittest
from unittest import mock

from PIL import Image, ImageDraw

from kaagaz.reading import image_reader
from kaagaz.reading.image_reader import ImageReader
from kaagaz.reading.ocr import OcrLine
from kaagaz.reading.spans import BoxLocation, ReadError, Span
from tests.picture_support import INK, text_like_bars, text_like_picture

BLANK_PAGE_STDDEV = 3.0  # the PRD's value


class FakeOcr:
    """Records each picture it is given and answers with fixed lines."""

    name = "fake-ocr"
    version = "3"

    def __init__(self, lines: list[OcrLine] | None = None) -> None:
        self.lines = [OcrLine("TOTAL 500", 20, 10, 120, 30, 0.93)] if lines is None else lines
        self.seen: list[Image.Image] = []

    def read(self, image: Image.Image) -> list[OcrLine]:
        self.seen.append(image.copy())
        return self.lines


def encode(image: Image.Image, fmt: str, **options: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def read(
    data: bytes, ocr: FakeOcr | None = None, *, max_pages: int = 10, max_image_pixels: int = 1_000_000
) -> list[Span]:
    engine = ocr if ocr is not None else FakeOcr()
    return reader_of(engine, max_pages=max_pages, max_image_pixels=max_image_pixels).read(data)


def reader_of(
    ocr: FakeOcr, *, max_pages: int = 10, max_image_pixels: int = 1_000_000, ocr_dpi: int = 300
) -> ImageReader:
    return ImageReader(
        ocr,
        max_pages=max_pages,
        max_image_pixels=max_image_pixels,
        ocr_dpi=ocr_dpi,
        blank_stddev=BLANK_PAGE_STDDEV,
    )


def refused_with(test: unittest.TestCase, data: bytes, code: str, **kwargs: int) -> None:
    ocr = FakeOcr()
    with test.assertRaises(ReadError) as ctx:
        read(data, ocr, **kwargs)
    test.assertEqual(ctx.exception.code, code)
    test.assertIsNone(ctx.exception.__context__)  # nothing chained
    if code != "corrupt_file":
        test.assertEqual(ocr.seen, [])  # refused before any page was read


class ImageReaderTest(unittest.TestCase):
    def test_each_line_is_a_span_with_a_page_fraction_box_and_confidence(self) -> None:
        (span,) = read(encode(text_like_picture(200, 100), "PNG"))
        self.assertEqual((span.text, span.raw, span.confidence), ("TOTAL 500", "TOTAL 500", 0.93))
        self.assertEqual(span.location, BoxLocation(1, 0.1, 0.1, 0.6, 0.3))

    def test_png_jpeg_and_tiff_are_read_as_rgb(self) -> None:
        for fmt, mode in (("PNG", "L"), ("JPEG", "RGB"), ("TIFF", "CMYK"), ("PNG", "P")):
            ocr = FakeOcr()
            with self.subTest(fmt=fmt, mode=mode):
                read(encode(text_like_picture(50, 40, mode=mode), fmt), ocr)
                self.assertEqual([(i.mode, i.size) for i in ocr.seen], [("RGB", (50, 40))])

    def test_exif_orientation_is_applied_before_reading(self) -> None:
        exif = Image.Exif()
        exif[0x0112] = 6  # "rotate 90 degrees clockwise to show"
        ocr = FakeOcr()
        read(encode(text_like_picture(200, 100), "JPEG", exif=exif), ocr)
        self.assertEqual(ocr.seen[0].size, (100, 200))

    def test_every_tiff_frame_is_a_page(self) -> None:
        frames = [text_like_picture(30 + i, 20) for i in range(3)]
        ocr = FakeOcr([OcrLine("x", 0, 0, 10, 10, 0.5)])
        spans = read(encode(frames[0], "TIFF", save_all=True, append_images=frames[1:]), ocr)
        self.assertEqual([s.location.page for s in spans], [1, 2, 3])
        self.assertEqual([i.width for i in ocr.seen], [30, 31, 32])

    def test_on_page_is_called_after_each_page_in_order(self) -> None:
        frames = [text_like_picture(30 + i, 20) for i in range(3)]
        ocr = FakeOcr([OcrLine("x", 0, 0, 10, 10, 0.5)])
        widths_read_at_each_call: list[list[int]] = []
        reader = reader_of(ocr)

        reader.read(
            encode(frames[0], "TIFF", save_all=True, append_images=frames[1:]),
            on_page=lambda: widths_read_at_each_call.append([i.width for i in ocr.seen]),
        )
        self.assertEqual(widths_read_at_each_call, [[30], [30, 31], [30, 31, 32]])

    def test_transparent_parts_are_read_on_white(self) -> None:
        clear = Image.new("RGBA", (200, 100), (0, 0, 0, 0))  # transparent black, with lines of ink on it
        draw = ImageDraw.Draw(clear)
        for left, top, width, height in text_like_bars(200, 100):
            draw.rectangle((left, top, left + width - 1, top + height - 1), fill=(*INK, 255))
        ocr = FakeOcr()
        read(encode(clear, "PNG"), ocr)
        self.assertEqual(ocr.seen[0].getpixel((0, 0)), (255, 255, 255))

    def test_an_image_with_no_text_is_unreadable(self) -> None:
        blind = FakeOcr([])
        with self.assertRaises(ReadError) as ctx:
            read(encode(text_like_picture(20, 20), "PNG"), blind)
        self.assertEqual(ctx.exception.code, "no_text")

    def test_name_and_version_are_the_engines(self) -> None:
        ocr = FakeOcr()
        reader = reader_of(ocr, max_pages=1, max_image_pixels=1)
        self.assertEqual((reader.name, reader.version), ("fake-ocr", "3"))
        ocr.version = "4"  # PaddleOCR-VL's version comes from its reply, so it is read late
        self.assertEqual(reader.version, "4")

    def test_limits_must_be_positive(self) -> None:
        for max_pages, max_image_pixels, ocr_dpi, blank in (
            (0, 1, 1, 3.0),
            (1, 0, 1, 3.0),
            (1, 1, 0, 3.0),
            (1, 1, 1, -1.0),
        ):
            with self.subTest(max_pages=max_pages, blank=blank), self.assertRaises(ValueError):
                ImageReader(
                    FakeOcr(),
                    max_pages=max_pages,
                    max_image_pixels=max_image_pixels,
                    ocr_dpi=ocr_dpi,
                    blank_stddev=blank,
                )

    def test_a_photo_above_ocr_dpi_is_shrunk_for_ocr_and_never_enlarged(self) -> None:
        # OCR_DPI 10 allows a short side of 83 px: 10 x 8.27 in, A4's short side (R5).
        for size, seen in (((400, 200), (166, 83)), ((200, 400), (83, 166)), ((100, 50), (100, 50))):
            ocr = FakeOcr()
            reader = reader_of(ocr, max_pages=1, ocr_dpi=10)
            with self.subTest(size=size):
                (span,) = reader.read(encode(text_like_picture(*size), "PNG"))
                self.assertEqual([image.size for image in ocr.seen], [seen])
                self.assertEqual(span.origin, "ocr")

    def test_a_photo_over_the_ocr_pixel_budget_is_shrunk_to_fit_it(self) -> None:
        # The budget keeps the PNG under the ocr server's body cap; 5000 here, 21M in the product.
        ocr = FakeOcr()
        with mock.patch.object(image_reader, "MAX_OCR_PIXELS", 5000):
            reader = reader_of(ocr, max_pages=1)
            reader.read(
                encode(text_like_picture(400, 100), "PNG")
            )  # not blank, so it reaches the engine (R3)
        ((width, height),) = [image.size for image in ocr.seen]
        self.assertLessEqual(width * height, 5000)
        self.assertEqual((width, height), (141, 35))  # 400 x 100 scaled by sqrt(5000 / 40000), rounded down

    def test_a_blank_photo_is_no_text(self) -> None:
        """R3: a blank photo is never sent to the engine, and a page is still counted."""
        ocr = FakeOcr()
        calls: list[int] = []
        with self.assertRaises(ReadError) as ctx:
            reader_of(ocr).read(
                encode(Image.new("RGB", (200, 100), "white"), "PNG"), on_page=lambda: calls.append(1)
            )
        self.assertEqual(ctx.exception.code, "no_text")
        self.assertEqual(ocr.seen, [])
        self.assertEqual(calls, [1])

    def test_only_the_blank_frame_of_a_tiff_is_skipped(self) -> None:
        frames = [text_like_picture(40, 30), Image.new("RGB", (40, 30), "white"), text_like_picture(40, 30)]
        ocr = FakeOcr([OcrLine("x", 0, 0, 10, 10, 0.5)])
        spans = read(encode(frames[0], "TIFF", save_all=True, append_images=frames[1:]), ocr)
        self.assertEqual([s.location.page for s in spans], [1, 3])
        self.assertEqual(len(ocr.seen), 2)


class RefusalsTest(unittest.TestCase):
    def test_too_many_pixels_is_refused_from_the_header(self) -> None:
        refused_with(
            self, encode(Image.new("RGB", (100, 100)), "PNG"), "image_too_large", max_image_pixels=9999
        )

    def test_one_large_tiff_frame_is_enough_to_refuse(self) -> None:
        frames = [Image.new("RGB", (10, 10)), Image.new("RGB", (200, 200))]
        data = encode(frames[0], "TIFF", save_all=True, append_images=frames[1:])
        with self.assertRaises(ReadError) as ctx:
            read(data, max_image_pixels=1000)
        self.assertEqual(ctx.exception.code, "image_too_large")

    def test_too_many_frames(self) -> None:
        frames = [Image.new("RGB", (10, 10)) for _ in range(3)]
        data = encode(frames[0], "TIFF", save_all=True, append_images=frames[1:])
        refused_with(self, data, "too_many_pages", max_pages=2)

    def test_damaged_images(self) -> None:
        png = encode(Image.new("RGB", (64, 64), "white"), "PNG")
        for name, data in {
            "not an image": b"\x89PNG\r\n\x1a\nnot really",
            "truncated": png[: len(png) // 2],
            "a gif": encode(Image.new("RGB", (4, 4)), "GIF"),
        }.items():
            with self.subTest(name):
                refused_with(self, data, "corrupt_file")


if __name__ == "__main__":
    unittest.main()
