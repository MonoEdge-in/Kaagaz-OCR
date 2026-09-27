import io
import unittest

from PIL import Image

from kaagaz.reading.image_reader import ImageReader
from kaagaz.reading.ocr import OcrLine
from kaagaz.reading.spans import BoxLocation, ReadError, Span


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
    return ImageReader(engine, max_pages=max_pages, max_image_pixels=max_image_pixels).read(data)


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
        (span,) = read(encode(Image.new("RGB", (200, 100), "white"), "PNG"))
        self.assertEqual((span.text, span.raw, span.confidence), ("TOTAL 500", "TOTAL 500", 0.93))
        self.assertEqual(span.location, BoxLocation(1, 0.1, 0.1, 0.6, 0.3))

    def test_png_jpeg_and_tiff_are_read_as_rgb(self) -> None:
        for fmt, mode in (("PNG", "L"), ("JPEG", "RGB"), ("TIFF", "CMYK"), ("PNG", "P")):
            ocr = FakeOcr()
            with self.subTest(fmt=fmt, mode=mode):
                read(encode(Image.new(mode, (50, 40)), fmt), ocr)
                self.assertEqual([(i.mode, i.size) for i in ocr.seen], [("RGB", (50, 40))])

    def test_exif_orientation_is_applied_before_reading(self) -> None:
        exif = Image.Exif()
        exif[0x0112] = 6  # "rotate 90 degrees clockwise to show"
        ocr = FakeOcr()
        read(encode(Image.new("RGB", (200, 100), "white"), "JPEG", exif=exif), ocr)
        self.assertEqual(ocr.seen[0].size, (100, 200))

    def test_every_tiff_frame_is_a_page(self) -> None:
        frames = [Image.new("RGB", (30 + i, 20), "white") for i in range(3)]
        ocr = FakeOcr([OcrLine("x", 0, 0, 10, 10, 0.5)])
        spans = read(encode(frames[0], "TIFF", save_all=True, append_images=frames[1:]), ocr)
        self.assertEqual([s.location.page for s in spans], [1, 2, 3])
        self.assertEqual([i.width for i in ocr.seen], [30, 31, 32])

    def test_transparent_parts_are_read_on_white(self) -> None:
        clear = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
        ocr = FakeOcr()
        read(encode(clear, "PNG"), ocr)
        self.assertEqual(ocr.seen[0].getpixel((0, 0)), (255, 255, 255))

    def test_an_image_with_no_text_is_unreadable(self) -> None:
        blind = FakeOcr([])
        with self.assertRaises(ReadError) as ctx:
            read(encode(Image.new("RGB", (20, 20), "white"), "PNG"), blind)
        self.assertEqual(ctx.exception.code, "no_text")

    def test_name_and_version_are_the_engines(self) -> None:
        reader = ImageReader(FakeOcr(), max_pages=1, max_image_pixels=1)
        self.assertEqual((reader.name, reader.version), ("fake-ocr", "3"))

    def test_limits_must_be_positive(self) -> None:
        for max_pages, max_image_pixels in ((0, 1), (1, 0)):
            with self.subTest(max_pages=max_pages), self.assertRaises(ValueError):
                ImageReader(FakeOcr(), max_pages=max_pages, max_image_pixels=max_image_pixels)


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
