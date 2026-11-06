"""Tell a file's type from its first bytes.

The type is never taken from the file name or from what the browser says,
because both are easy to fake. Only the first bytes ("magic numbers") are
compared. Nothing inside the file is parsed here: the virus scan (#15) must
run before any step opens a file, so telling an .xlsx from a .docx (both are
zip files), or a CSV from other text, waits until after the scan
(``kaagaz/ingestion/resolve.py``, #522).

Time is O(len(head)): a fixed number of prefix comparisons plus one scan and
one decode of the head for the text check. Callers pass at most HEAD_SIZE
(4 KiB) bytes, so in practice it is a small constant.

Every UTF-8 text file is tagged TEXT here; after the scan, resolve.py lets the
file name choose between the text readers (CSV, plain text, Markdown, JSON,
HTML), all safe for any text, so a .txt, JSON or HTML file is no longer read as
CSV (#522).
"""

import codecs
import re

PDF = "pdf"
PNG = "png"
JPEG = "jpeg"
TIFF = "tiff"
HEIC = "heic"  # HEIC and HEIF photos (ISO-BMFF), the iPhone's own format
ZIP = "zip"  # .xlsx, .docx and other zip containers; resolve.py tells them apart after the scan
OLE2 = "ole2"  # old .xls, .doc and .ppt; resolve.py tells them apart after the scan
TEXT = "text"  # CSV and other plain UTF-8 text

KNOWN_TYPES = frozenset({PDF, PNG, JPEG, TIFF, HEIC, ZIP, OLE2, TEXT})

# (prefix, type). Each prefix must appear at offset 0.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", PDF),
    (b"\x89PNG\r\n\x1a\n", PNG),
    (b"\xff\xd8\xff", JPEG),
    (b"II*\x00", TIFF),  # little-endian TIFF
    (b"MM\x00*", TIFF),  # big-endian TIFF
    (b"PK\x03\x04", ZIP),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", OLE2),
)

# HEIC and HEIF (#522): an ISO-BMFF file starts with its ``ftyp`` box, the box's size in its
# first 4 bytes, then "ftyp" at offset 4 and the major brand at offset 8. Only these brands
# are HEIC or HEIF photos (an AVIF or an MP4 has another brand).
_FTYP = b"ftyp"
HEIC_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"heif", b"mif1", b"msf1"})


def detect_type(head: bytes) -> str | None:
    """Return one of KNOWN_TYPES, or None when the type is not recognised."""
    for prefix, kind in _MAGIC:
        if head.startswith(prefix):
            return kind
    if head[4:8] == _FTYP and head[8:12] in HEIC_BRANDS:  # at offset 4, not 0: its own check
        return HEIC
    if _looks_like_utf8_text(head):
        return TEXT
    return None


# Control bytes other than tab, line feed, form feed and carriage return, and
# DEL, mean "not plain text". The CSV reader checks the whole file with this
# too: in valid UTF-8 these bytes only ever stand for these characters.
FORBIDDEN_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0e-\x1f\x7f]")


def _looks_like_utf8_text(head: bytes) -> bool:
    """True if the head is valid UTF-8 so far and has no control bytes.

    The head may end in the middle of a multi-byte character, so an
    incremental decoder with ``final=False`` is used: an unfinished last
    character is not an error. HTML or SVG also pass this check; they are
    only ever treated as plain text, never rendered.
    """
    if not head or FORBIDDEN_CONTROL.search(head):
        return False
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        decoder.decode(head, final=False)
    except UnicodeDecodeError:
        return False
    return True
