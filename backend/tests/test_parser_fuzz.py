"""Untrusted files never crash a parser (council 17 Oct 2026).

Two scanned PDFs crashed every worker that took them: pdfminer raised an error the
content check did not list. This test mutates small, valid files of every type we
accept, with fixed seeds, and feeds every mutant to every entry point that parses an
upload. Each must return its result or raise only its own domain error (ReadError,
NoReader); the content check, the scanner and the page count must never raise at all.
A call that runs past HANG_SECONDS is a hang.

Every failure is listed, one line each, with what reproduces it: ``case(seed, i)``
gives the mutant's bytes. ``KAAGAZ_FUZZ_ITERATIONS`` (default 60 per seed) runs it
deeper. Standard library and the test builders only.
"""

import io
import os
import random
import re
import struct
import threading
import unittest
import warnings
import zlib
from collections.abc import Callable
from functools import cache
from unittest import mock

from PIL import Image, ImageDraw

from kaagaz.adapters.scanner import LayeredScanner
from kaagaz.ingestion import sniff
from kaagaz.ingestion.pagecount import count_pages
from kaagaz.preparing.pages import page_picture, prepare_pages
from kaagaz.reading.csv_reader import CsvReader
from kaagaz.reading.image_reader import ImageReader
from kaagaz.reading.ocr import OcrLine
from kaagaz.reading.pdf_reader import PdfReader
from kaagaz.reading.pdf_second_reader import PdfiumReader
from kaagaz.reading.socket import NoReader
from kaagaz.reading.spans import ReadError
from kaagaz.reading.text_quality import TextLayerRules
from kaagaz.reading.xlsx_reader import XlsxLimits, XlsxReader
from kaagaz.scanning.content import inspect
from kaagaz.scanning.gate import Verdict
from kaagaz.worker_main import crash_site
from tests.pdf_builder import Page, Picture, Text, make_objstm_pdf, make_pdf
from tests.picture_support import text_like_picture
from tests.xlsx_builder import make_xlsx, sheet_xml

ITERATIONS = int(os.environ.get("KAAGAZ_FUZZ_ITERATIONS", "60"))
HANG_SECONDS = 5.0

CATALOG = b"<< /Type /Catalog /Pages 2 0 R >>"
CATALOG_JS = b"<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>"
PAGES = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
PAGE = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"
JS = b"<< /S /JavaScript /JS (app.alert(1)) >>"


def pdf_with(objects: list[bytes]) -> bytes:
    """A small PDF with a classic cross-reference table; object 1 is the catalog."""
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def encoded(image: Image.Image, fmt: str, **options: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def picture() -> Image.Image:
    image = Image.new("RGB", (60, 40), "white")
    ImageDraw.Draw(image).rectangle((5, 10, 50, 20), fill="black")
    return image


def two_frame_tiff(compression: str) -> bytes:
    first, second = picture(), picture().rotate(180)
    return encoded(first, "TIFF", save_all=True, append_images=[second], compression=compression)


@cache
def seeds() -> dict[str, bytes]:
    text = Page(texts=[Text(72, 700, "Invoice INV-1042 from Sharma Traders, total 500")])
    scan = Page(picture=Picture(text_like_picture(60, 80), 0, 0, 612, 792))
    return {
        "pdf_text": make_pdf([text]),
        "pdf_scan": make_pdf([scan]),
        "pdf_devanagari": make_pdf(
            [Page(texts=[Text(72, 700, "नमस्ते", devanagari=True)]), Page(texts=[Text(72, 700, "Page two")])]
        ),
        "pdf_password": make_pdf([text], password_protected=True),
        "pdf_js": pdf_with([CATALOG_JS, PAGES, PAGE, JS]),
        "pdf_objstm": make_objstm_pdf([CATALOG_JS], [PAGES, PAGE, JS]),
        "png": encoded(picture(), "PNG"),
        "jpeg": encoded(picture(), "JPEG"),
        "tiff_raw": two_frame_tiff("raw"),
        "tiff_lzw": two_frame_tiff("tiff_lzw"),
        "xlsx": make_xlsx(
            [("Sheet1", sheet_xml('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>500</v></c></row>'))],
            ["Sharma Traders"],
        ),
        "csv": b"name,amount\nSharma Traders,500\nPatel & Sons,1200\n",
    }


# --- mutation operators: (file, rng) -> mutant. One that does not apply flips bytes. -----


def flip(data: bytes, rng: random.Random) -> bytes:
    out = bytearray(data)
    for _ in range(rng.randint(1, 8)):
        if out:
            out[rng.randrange(len(out))] = rng.randrange(256)
    return bytes(out)


def truncate(data: bytes, rng: random.Random) -> bytes:
    return data[: rng.randrange(max(len(data), 1))]


def truncate_short(data: bytes, rng: random.Random) -> bytes:
    return data[: rng.choice((0, 1, 8, 9))]


def _replace_one(
    data: bytes, rng: random.Random, pattern: bytes, make: Callable[[re.Match[bytes]], bytes]
) -> bytes:
    matches = list(re.finditer(pattern, data, re.DOTALL))
    if not matches:
        return flip(data, rng)
    match = rng.choice(matches)
    return data[: match.start()] + make(match) + data[match.end() :]


def xref_offset(data: bytes, rng: random.Random) -> bytes:
    value = rng.choice((0, 9, len(data) - 1, len(data) + 100, 9_999_999_999))
    return _replace_one(data, rng, rb"\d{10} (\d{5} n)", lambda m: b"%010d " % value + m.group(1))


def blank_object(data: bytes, rng: random.Random) -> bytes:
    return _replace_one(data, rng, rb"\d+ \d+ obj.*?endobj", lambda m: b" " * len(m.group(0)))


def bad_trailer(data: bytes, rng: random.Random) -> bytes:
    at = data.rfind(b"trailer")
    if at < 0:
        at = data.rfind(b"startxref")
    if at < 0:
        return flip(data, rng)
    prev, start = rng.randrange(-5, len(data) + 50), rng.randrange(-5, len(data) + 50)
    garbage = bytes(rng.randrange(256) for _ in range(rng.randint(0, 40)))
    tail = (
        b"trailer\n<< /Size 9 /Prev %d /Root 1 0 R " % prev
        + garbage
        + b">>\nstartxref\n%d\n%%%%EOF\n" % start
    )
    return data[:at] + tail


def huge_number(data: bytes, rng: random.Random) -> bytes:
    if data.startswith(b"\x89PNG"):
        ihdr = struct.pack(">II", 2**31 - 1, 2**31 - 1) + data[24:29]
        return data[:12] + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)) + data[33:]
    if data.startswith(b"\xff\xd8"):
        at = data.find(b"\xff\xc0")
        return flip(data, rng) if at < 0 else data[: at + 5] + b"\xff\xff\xff\xff" + data[at + 9 :]
    if rng.random() < 0.2 and b"/W [" in data:
        return re.sub(rb"/W \[[^\]]*\]", b"/W [1 400000000 2]", data, count=1)
    value = rng.choice((99_999_999_999, 2_147_483_647, 999_999_999))
    return _replace_one(
        data, rng, rb"/(Length|Count|Size|N|First) \d+", lambda m: b"/%s %d" % (m.group(1), value)
    )


def kids_to_catalog(data: bytes, rng: random.Random) -> bytes:
    return _replace_one(data, rng, rb"/Kids \[[^\]]*\]", lambda m: b"/Kids [1 0 R]")


def duplicate_chunk(data: bytes, rng: random.Random) -> bytes:
    if len(data) < 2:
        return data * 2
    start = rng.randrange(len(data))
    chunk = data[start : start + rng.randint(1, 200)]
    at = rng.randrange(len(data))
    return data[:at] + chunk + data[at:]


def insert_random(data: bytes, rng: random.Random) -> bytes:
    at = rng.randrange(len(data) + 1)
    return data[:at] + bytes(rng.randrange(256) for _ in range(rng.randint(1, 64))) + data[at:]


def break_stream(data: bytes, rng: random.Random) -> bytes:
    return _replace_one(
        data, rng, rb"endstream|stream\r?\n", lambda m: rng.choice((b"", b"endstrem", b"stream"))
    )


def damaged_member(data: bytes, rng: random.Random) -> bytes:
    """A zip member's compressed bytes damaged, its header kept; or its declared sizes
    inflated in the central directory."""
    headers = [m.start() for m in re.finditer(rb"PK\x03\x04", data)]
    if not headers:
        return flip(data, rng)
    at = rng.choice(headers)
    if rng.random() < 0.3:
        central = [m.start() for m in re.finditer(rb"PK\x01\x02", data)]
        if central:
            entry = rng.choice(central)
            return data[: entry + 20] + struct.pack("<II", 0x7FFFFFFF, 0x7FFFFFFF) + data[entry + 28 :]
    try:
        size, name_len, extra_len = (
            struct.unpack("<I", data[at + 18 : at + 22])[0],
            *struct.unpack("<HH", data[at + 26 : at + 30]),
        )
    except struct.error:
        return flip(data, rng)
    begin = at + 30 + name_len + extra_len
    out = bytearray(data)
    for _ in range(rng.randint(1, 6)):
        position = begin + rng.randrange(max(size, 1))
        if position < len(out):
            out[position] = rng.randrange(256)
    return bytes(out)


OPERATORS: tuple[Callable[[bytes, random.Random], bytes], ...] = (
    flip,
    truncate,
    truncate_short,
    xref_offset,
    blank_object,
    bad_trailer,
    huge_number,
    kids_to_catalog,
    duplicate_chunk,
    insert_random,
    break_stream,
    damaged_member,
)

FIXED_CASES = {
    "empty": b"",
    "percent": b"%",
    "pdf_magic": b"%PDF-",
    "png_magic": b"\x89PNG\r\n\x1a\n",
    "tiff_magic": b"II*\x00",
    "zip_magic": b"PK\x03\x04",
}


def case(seed: str, i: int) -> bytes:
    """Mutant ``i`` of ``seed`` (or the fixed case named ``seed``): for replaying a failure."""
    if seed in FIXED_CASES:
        return FIXED_CASES[seed]
    rng = random.Random(f"{seed}:{i}")  # noqa: S311 - fixed seeds for replay, not secrets
    return OPERATORS[i % len(OPERATORS)](seeds()[seed], rng)


def operator_name(seed: str, i: int) -> str:
    return "fixed" if seed in FIXED_CASES else OPERATORS[i % len(OPERATORS)].__name__


# --- entry points -----------------------------------------------------------------------


class FakeOcr:
    name = "fake-ocr"
    version = "1"

    def read(self, image: Image.Image) -> list[OcrLine]:
        return [OcrLine("TOTAL 500", 2, 2, 20, 10, 0.9)]


class CleanScanner:
    """A virus scanner that finds nothing: only our content check is under test."""

    def scan(self, data: bytes) -> Verdict:
        return Verdict.CLEAN


RULES = TextLayerRules(min_chars=40, min_good_ratio=0.9, scan_image_coverage=0.5)
LIMITS = XlsxLimits(
    max_entries=50,
    max_member_bytes=200_000,
    max_total_bytes=1_000_000,
    max_cells=10_000,
    max_text_chars=1_000_000,
)
PIXELS = 4_000_000
PDF_TYPES = {sniff.PDF}
IMAGE_TYPES = {sniff.PNG, sniff.JPEG, sniff.TIFF}
NOTHING: tuple[type[BaseException], ...] = ()
READ_ERRORS = (ReadError,)
READ_OR_NO_READER = (ReadError, NoReader)


def pdf_reader(ocr: FakeOcr | None) -> PdfReader:
    return PdfReader(ocr, max_pages=10, dpi=72, max_image_pixels=PIXELS, rules=RULES, blank_stddev=3.0)


def prepare(kind: str, data: bytes) -> object:
    prepared = prepare_pages(
        kind,
        data,
        lambda number, count, image: None,
        dpi=72,
        max_pages=10,
        max_image_pixels=PIXELS,
        blank_stddev=3.0,
        low_res_dpi=150,
    )
    if prepared.pages:
        prepared.picture(1)  # the checker's crop path (PreparedPages.picture)
    return prepared


def layered_in_process(data: bytes) -> object:
    """LayeredScanner with its content check in this process: a child per mutant would take
    minutes, and the child's own failures are tested in test_isolated_scan."""
    with mock.patch("kaagaz.adapters.scanner.inspect_isolated", inspect):
        return LayeredScanner(CleanScanner()).scan(data)


# (name, the types it is called for (None: every file), what it may raise, the call)
EntryPoint = tuple[str, set[str] | None, tuple[type[BaseException], ...], Callable[[str, bytes], object]]
ENTRY_POINTS: tuple[EntryPoint, ...] = (
    ("detect_type", None, NOTHING, lambda kind, data: sniff.detect_type(data[:4096])),
    ("content.inspect", None, NOTHING, lambda kind, data: inspect(data)),
    ("LayeredScanner.scan", None, NOTHING, lambda kind, data: layered_in_process(data)),
    ("count_pages", None, NOTHING, lambda kind, data: count_pages(io.BytesIO(data), kind)),
    ("prepare_pages", PDF_TYPES | IMAGE_TYPES, READ_ERRORS, prepare),
    (
        "page_picture",
        PDF_TYPES | IMAGE_TYPES,
        READ_ERRORS,
        lambda kind, data: page_picture(kind, data, 1, dpi=72, max_image_pixels=PIXELS),
    ),
    ("PdfReader(no OCR)", PDF_TYPES, READ_OR_NO_READER, lambda kind, data: pdf_reader(None).read(data)),
    ("PdfReader(OCR)", PDF_TYPES, READ_OR_NO_READER, lambda kind, data: pdf_reader(FakeOcr()).read(data)),
    ("PdfiumReader", PDF_TYPES, READ_ERRORS, lambda kind, data: PdfiumReader(max_pages=10).read(data)),
    (
        "ImageReader",
        IMAGE_TYPES,
        READ_ERRORS,
        lambda kind, data: ImageReader(
            FakeOcr(), max_pages=10, max_image_pixels=PIXELS, ocr_dpi=150, blank_stddev=3.0
        ).read(data),
    ),
    ("XlsxReader", {sniff.ZIP}, READ_OR_NO_READER, lambda kind, data: list(XlsxReader(LIMITS).read(data))),
    ("CsvReader", {sniff.TEXT}, READ_ERRORS, lambda kind, data: list(CsvReader(max_cells=10_000).read(data))),
)


class Hang(Exception):
    pass


def call_with_limit(call: Callable[[], object]) -> BaseException | None:
    """Run ``call`` on a daemon thread; its exception, Hang past HANG_SECONDS, or None."""
    outcome: list[BaseException | None] = []

    def run() -> None:
        try:
            call()
        except BaseException as error:  # noqa: BLE001 - the test reports whatever escapes
            outcome.append(error)
        else:
            outcome.append(None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(HANG_SECONDS)
    return outcome[0] if outcome else Hang()


def failures_for(seed: str, i: int) -> tuple[list[str], bool]:
    """Each entry point's escape for one mutant, one line each, and whether one hung."""
    data = case(seed, i)
    kind = sniff.detect_type(data[:4096]) or ""
    lines = []
    for name, kinds, allowed, entry in ENTRY_POINTS:
        if kinds is not None and kind not in kinds:
            continue
        error = call_with_limit(lambda entry=entry: entry(kind, data))
        if error is None or isinstance(error, allowed):
            continue
        where = "a hang" if isinstance(error, Hang) else crash_site(error)
        lines.append(
            f"entry={name} seed={seed}:{i} op={operator_name(seed, i)} "
            f"error={type(error).__name__} at {where}"
        )
        if isinstance(error, Hang):
            return lines, True  # pdfium is not safe to call beside the thread still running
    return lines, False


class UntrustedFilesNeverCrash(unittest.TestCase):
    def test_every_mutant_gives_a_result_or_a_domain_error(self) -> None:
        cases = [(seed, 0) for seed in FIXED_CASES]
        cases += [(seed, i) for seed in seeds() for i in range(ITERATIONS)]
        failures: list[str] = []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # production logs warnings; only exceptions count
            for seed, i in cases:
                lines, hung = failures_for(seed, i)
                failures += lines
                if hung:
                    break
        if failures:
            self.fail(f"{len(failures)} escapes:\n" + "\n".join(failures))

    def test_a_cut_two_frame_tiff_is_a_read_error_not_a_crash(self) -> None:
        """The test engineer's find: a 2-frame TIFF cut to 300 or 2000 bytes raised
        TypeError from Pillow's n_frames, past the page count and the readers."""
        for length in (300, 2000):
            data = seeds()["tiff_raw"][:length]
            with self.subTest(length=length), warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.assertIsNone(count_pages(io.BytesIO(data), sniff.TIFF))
                with self.assertRaises(ReadError):
                    prepare(sniff.TIFF, data)
                with self.assertRaises(ReadError):
                    ImageReader(
                        FakeOcr(), max_pages=10, max_image_pixels=PIXELS, ocr_dpi=150, blank_stddev=3.0
                    ).read(data)

    def test_a_declared_index_of_a_billion_objects_is_checked_in_under_a_second(self) -> None:
        """The test engineer's find: /Size 999999999 in an xref stream walked ~1e9 ids
        (about 3 hours) in a 400-byte file. Now bounded by what the file can hold."""
        import time

        for packed, expected in (([PAGES, PAGE], None), ([PAGES, PAGE, JS], "blocked_content")):
            data = make_objstm_pdf([CATALOG_JS if expected else CATALOG], packed)
            data = re.sub(rb"/Size \d+", b"/Size 999999999", data)
            started = time.perf_counter()
            finding = inspect(data)
            with self.subTest(expected=expected):
                self.assertLess(time.perf_counter() - started, 1.0)
                self.assertEqual(None if finding is None else finding.value, expected)


if __name__ == "__main__":
    unittest.main()
