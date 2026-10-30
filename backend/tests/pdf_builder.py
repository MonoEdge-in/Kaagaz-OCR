"""Build small PDF files in memory for tests, written out by hand.

Deliberately independent of the readers: the PDF syntax is written here byte
by byte (like ``tests/xlsx_builder.py``), never produced by pdfplumber or
pypdfium2. pypdfium2 5.13 can create text objects only through raw ctypes
calls, and a hand-written file also lets tests build what no library writes on
purpose: a password-protected file and a page with no text layer.

Coordinates here are PDF-native: points, origin at the bottom left, and a
text's ``y`` is its baseline. Text uses the standard Helvetica font, or the
page's ``font_name`` (a font that is named but not embedded, with the same
WinAnsi codes), so text is written in cp1252: ASCII, plus the Latin-1
symbols a garbled text layer holds (PRD 2 R2).
"""

import re
import struct
import zlib
from dataclasses import dataclass, field

from PIL import Image


@dataclass(frozen=True)
class Text:
    x: float
    y: float  # the baseline, from the bottom of the page
    text: str
    size: float = 12.0
    # Drawn turned anticlockwise by this many degrees (0, 90, 180 or 270), so it
    # reads upright on a page with that /Rotate (90: how a landscape page is
    # often stored).
    turn: int = 0
    # Drawn with text render mode 3 (neither filled nor stroked), as the hidden
    # OCR layer of a searchable scan is: nothing shows, but readers still
    # extract it (PRD 2, the council's ML 4).
    invisible: bool = False
    # The fill colour (r, g, b from 0 to 1), for example white text that shows
    # nothing on white paper; None is the default black.
    color: tuple[float, float, float] | None = None
    # Written with a Type0 font (Identity-H, a ToUnicode map for ASCII and
    # U+0900 to U+097F, no font file) as hex code points, so the readers get
    # Devanagari back exactly. It draws almost blank, so never use it where
    # OCR must read the page (council test-engineer 9).
    devanagari: bool = False
    # Written with the standard Helvetica-Bold font, as a bold heading is.
    bold: bool = False


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
    # More pictures after ``picture``, for example the strips of a tiled scan.
    pictures: list[Picture] = field(default_factory=list)
    rotate: int = 0
    # The page's font by name instead of Helvetica, for example "KrutiDev010"
    # (an old Hindi font that stores Devanagari on Latin codes): the name goes
    # in /BaseFont and in its descriptor's /FontName, and no font file is
    # embedded, so a viewer draws it with a stand-in font (PRD 2, ML 3).
    font_name: str | None = None
    # The /CropBox (x0, y0, x1, y1), the part of the page a viewer shows; None
    # writes none, so the whole MediaBox shows.
    crop: tuple[float, float, float, float] | None = None


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
        resources = b"/Font << /F1 %d 0 R" % font
        if any(item.devanagari for item in page.texts):
            resources += b" /F2 %d 0 R" % _devanagari_font(objects)
        if any(item.bold for item in page.texts):
            objects.append(
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
            )
            resources += b" /F3 %d 0 R" % len(objects)
        resources += b" >>"
        content = _text_content(page.texts)
        drawn = ([] if page.picture is None else [page.picture]) + page.pictures
        images = b""
        pictures = b""
        for number, picture in enumerate(drawn, start=1):
            objects.append(_image_object(picture.image))
            images += b" /Im%d %d 0 R" % (number, len(objects))
            placed = f"{picture.width} 0 0 {picture.height} {picture.x} {picture.y}"
            pictures += f"q {placed} cm /Im{number} Do Q\n".encode()
        if drawn:
            resources += b" /XObject <<" + images + b" >>"
            content = pictures + content
        objects.append(_stream(b"", content))
        content_number = len(objects)
        crop = "" if page.crop is None else f"/CropBox [{' '.join(map(str, page.crop))}] "
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page.width} {page.height}] {crop}"
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
        font, shown = ("F2", _hex_code_points(item.text)) if item.devanagari else ("F1", f"({escaped})")
        font = "F3" if item.bold else font
        matrix = _TURNS[item.turn]
        if item.color is not None:
            red, green, blue = item.color
            drawn = f"BT /{font} {item.size} Tf {matrix} {item.x} {item.y} Tm {shown} Tj ET"
            parts.append(f"q {red} {green} {blue} rg {drawn} Q\n")
        elif item.invisible:
            # The render mode is part of the graphics state and outlives ET,
            # so q ... Q keeps it to this text only.
            parts.append(f"q BT /{font} {item.size} Tf 3 Tr {matrix} {item.x} {item.y} Tm {shown} Tj ET Q\n")
        else:
            parts.append(f"BT /{font} {item.size} Tf {matrix} {item.x} {item.y} Tm {shown} Tj ET\n")
    return "".join(parts).encode("cp1252")


_TURNS = {0: "1 0 0 1", 90: "0 1 -1 0", 180: "-1 0 0 -1", 270: "0 -1 1 0"}


def _hex_code_points(text: str) -> str:
    """``text`` as 2-byte codes for the Identity-H font: each code is the code point."""
    if not all(" " <= ch <= "~" or "\u0900" <= ch <= "\u097f" for ch in text):
        raise ValueError("Devanagari text here is ASCII and U+0900 to U+097F only")
    return "<" + "".join(f"{ord(ch):04X}" for ch in text) + ">"


def _devanagari_font(objects: list[bytes]) -> int:
    """Add the Type0 font for ``Text(devanagari=True)``; return its object number."""
    cmap = (
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        b"/CMapName /Adobe-Identity-UCS def /CMapType 2 def\n"
        b"1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
        b"2 beginbfrange <0020> <007E> <0020> <0900> <097F> <0900> endbfrange\n"
        b"endcmap CMapName currentdict /CMap defineresource pop end end"
    )
    objects.append(_stream(b"", cmap))
    objects.append(
        b"<< /Type /FontDescriptor /FontName /KaagazDevanagari /Flags 4 /FontBBox [0 -200 1000 900] "
        b"/ItalicAngle 0 /Ascent 900 /Descent -200 /CapHeight 700 /StemV 80 >>"
    )
    objects.append(
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /KaagazDevanagari "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        b"/FontDescriptor %d 0 R /CIDToGIDMap /Identity /DW 600 >>" % len(objects)
    )
    objects.append(
        b"<< /Type /Font /Subtype /Type0 /BaseFont /KaagazDevanagari /Encoding /Identity-H "
        b"/DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>" % (len(objects), len(objects) - 2)
    )
    return len(objects)


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


def make_objstm_pdf(plain: list[bytes], packed: list[bytes], *, objstm_offset: int | None = None) -> bytes:
    """A PDF 1.5 file as most writers save one today: the ``packed`` object bodies in one
    compressed object stream, indexed by a cross-reference stream. Objects are numbered
    ``plain`` first, from 1 (object 1 must be the catalog), then ``packed``, then the
    object stream and the index. ``objstm_offset`` writes a wrong offset in the object
    stream's index entry (a broken index)."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    entries = [struct.pack(">BIH", 0, 0, 0xFFFF)]
    for number, body in enumerate(plain, start=1):
        entries.append(struct.pack(">BIH", 1, len(out), 0))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    stream_number = len(plain) + len(packed) + 1
    header, bodies = [], b""
    for index, body in enumerate(packed):
        header.append(b"%d %d" % (len(plain) + 1 + index, len(bodies)))
        bodies += body + b"\n"
        entries.append(struct.pack(">BIH", 2, stream_number, index))
    first = b" ".join(header) + b"\n"
    data = zlib.compress(first + bodies)
    entries.append(struct.pack(">BIH", 1, len(out) if objstm_offset is None else objstm_offset, 0))
    out += b"%d 0 obj\n<< /Type /ObjStm /N %d /First %d /Filter /FlateDecode /Length %d >>\nstream\n" % (
        stream_number,
        len(packed),
        len(first),
        len(data),
    )
    out += data + b"\nendstream\nendobj\n"
    xref = len(out)
    entries.append(struct.pack(">BIH", 1, xref, 0))
    table = b"".join(entries)
    out += b"%d 0 obj\n<< /Type /XRef /Size %d /W [1 4 2] /Root 1 0 R /Length %d >>\nstream\n" % (
        stream_number + 1,
        stream_number + 2,
        len(table),
    )
    out += table + b"\nendstream\nendobj\n"
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)
