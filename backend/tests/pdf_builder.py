"""Build small PDF files in memory for tests, written out by hand.

Deliberately independent of the readers: the PDF syntax is written here byte
by byte (like ``tests/xlsx_builder.py``), never produced by pdfplumber or
pypdfium2. pypdfium2 5.13 can create text objects only through raw ctypes
calls, and a hand-written file also lets tests build what no library writes on
purpose: a password-protected file and a page with no text layer.

Coordinates here are PDF-native: points, origin at the bottom left, and a
text's ``y`` is its baseline. Text uses the standard Helvetica font, or the
page's ``font_name`` (a font that is named but not embedded, with the same
WinAnsi codes), so text must be ASCII.
"""

import re
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
    # Drawn with text render mode 3 (neither filled nor stroked), as the hidden
    # OCR layer of a searchable scan is: nothing shows, but readers still
    # extract it (PRD 2, the council's ML 4).
    invisible: bool = False


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
    # The page's font by name instead of Helvetica, for example "KrutiDev010"
    # (an old Hindi font that stores Devanagari on Latin codes): the name goes
    # in /BaseFont and in its descriptor's /FontName, and no font file is
    # embedded, so a viewer draws it with a stand-in font (PRD 2, ML 3).
    font_name: str | None = None


# Letters, digits, "+", "-" and "_": a PDF name with nothing to escape.
_FONT_NAME = re.compile(r"[A-Za-z0-9+_-]{1,64}")


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
        font = 3 if page.font_name is None else _named_font(objects, page.font_name)
        resources = b"/Font << /F1 %d 0 R >>" % font
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
        if item.invisible:
            # The render mode is part of the graphics state and outlives ET,
            # so q ... Q keeps it to this text only.
            parts.append(f"q BT /F1 {item.size} Tf 3 Tr {matrix} {item.x} {item.y} Tm ({escaped}) Tj ET Q\n")
        else:
            parts.append(f"BT /F1 {item.size} Tf {matrix} {item.x} {item.y} Tm ({escaped}) Tj ET\n")
    return "".join(parts).encode("ascii")


def _named_font(objects: list[bytes], name: str) -> int:
    """Add a named, not embedded TrueType font with WinAnsi codes; return its object number."""
    if not _FONT_NAME.fullmatch(name):
        raise ValueError("a font name here is 1 to 64 letters, digits, '+', '-' or '_'")
    objects.append(
        b"<< /Type /FontDescriptor /FontName /%s /Flags 32 /FontBBox [0 -200 1000 900] "
        b"/ItalicAngle 0 /Ascent 900 /Descent -200 /CapHeight 700 /StemV 80 >>" % name.encode()
    )
    widths = b" ".join([b"500"] * 95)  # every code from 32 to 126, half an em wide
    objects.append(
        b"<< /Type /Font /Subtype /TrueType /BaseFont /%s /FirstChar 32 /LastChar 126 /Widths [%s] "
        b"/FontDescriptor %d 0 R /Encoding /WinAnsiEncoding >>" % (name.encode(), widths, len(objects))
    )
    return len(objects)


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
