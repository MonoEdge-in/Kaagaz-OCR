"""Build small PDF files in memory for tests, written out by hand.

Deliberately independent of the readers: the PDF syntax is written here byte
by byte (like ``tests/xlsx_builder.py``), never produced by pdfplumber or
pypdfium2. pypdfium2 5.13 can create text objects only through raw ctypes
calls, and a hand-written file also lets tests build what no library writes on
purpose: a password-protected file and a page with no text layer.

Coordinates here are PDF-native: points, origin at the bottom left, and a
text's ``y`` is its baseline. Only the standard Helvetica font is used, so
text must be ASCII.
"""

import zlib
from dataclasses import dataclass, field

from PIL import Image


@dataclass(frozen=True)
class Text:
    x: float
    y: float  # the baseline, from the bottom of the page
    text: str
    size: float = 12.0
    # Drawn a quarter turn anticlockwise, so it reads upright on a page with
    # /Rotate 90 (how a landscape page is often stored).
    turned: bool = False


@dataclass(frozen=True)
class Picture:
    """An RGB image drawn at (x, y), bottom left, ``width`` x ``height`` points."""

    image: Image.Image
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Page:
    width: float = 612.0
    height: float = 792.0
    texts: list[Text] = field(default_factory=list)
    picture: Picture | None = None
    rotate: int = 0


def make_pdf(pages: list[Page], *, password_protected: bool = False) -> bytes:
    """A complete PDF with a correct cross-reference table.

    ``password_protected`` adds a standard security handler whose check value
    matches no password, so the file cannot be opened without one.
    """
    objects: list[bytes] = [
        b"",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    kids = []
    for page in pages:
        resources = b"/Font << /F1 3 0 R >>"
        content = _text_content(page.texts)
        if page.picture is not None:
            objects.append(_image_object(page.picture.image))
            resources += b" /XObject << /Im1 %d 0 R >>" % len(objects)
            picture = page.picture
            content = (
                f"q {picture.width} 0 0 {picture.height} {picture.x} {picture.y} cm /Im1 Do Q\n".encode()
                + content
            )
        objects.append(_stream(b"", content))
        content_number = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page.width} {page.height}] "
            f"/Rotate {page.rotate} /Contents {content_number} 0 R ".encode()
            + b"/Resources << "
            + resources
            + b" >> >>"
        )
        kids.append(f"{len(objects)} 0 R")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()

    trailer_extra = b""
    if password_protected:
        objects.append(
            b"<< /Filter /Standard /V 1 /R 2 /Length 40 /P -4 "
            b"/O <" + b"11" * 32 + b"> /U <" + b"22" * 32 + b"> >>"
        )
        trailer_extra = b" /Encrypt %d 0 R /ID [<%s> <%s>]" % (len(objects), b"33" * 16, b"33" * 16)
    return _assemble(objects, trailer_extra)


def _text_content(texts: list[Text]) -> bytes:
    parts = []
    for item in texts:
        escaped = item.text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        matrix = "0 1 -1 0" if item.turned else "1 0 0 1"
        parts.append(f"BT /F1 {item.size} Tf {matrix} {item.x} {item.y} Tm ({escaped}) Tj ET\n")
    return "".join(parts).encode("ascii")


def _image_object(image: Image.Image) -> bytes:
    rgb = image.convert("RGB")
    header = (
        f"/Type /XObject /Subtype /Image /Width {rgb.width} /Height {rgb.height} "
        "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
    ).encode()
    return _stream(header, zlib.compress(rgb.tobytes()))


def _stream(header: bytes, data: bytes) -> bytes:
    return b"<< " + header + b"/Length %d >>\nstream\n" % len(data) + data + b"\nendstream"


def _assemble(objects: list[bytes], trailer_extra: bytes) -> bytes:
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R" % (len(objects) + 1) + trailer_extra + b" >>\n"
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)
