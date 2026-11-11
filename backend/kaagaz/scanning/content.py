"""Our own content check, run before the virus scanner on every file.

A virus scanner knows signatures. This check knows what a business document
may contain, and refuses the rest, whether or not any signature matches:

* **PDF:** no active content. JavaScript, launch actions, embedded files,
  rich media, XFA forms, and form submit or import actions are all refused.
  Every object is walked, including those inside compressed object streams,
  so an obfuscated name (``/J#61vaScript``) is caught too. When the file's
  index (cross-reference) is damaged, so some object could not be loaded
  through it, every object header in the whole file is parsed as well, the way
  a viewer rebuilds a broken index, so damage cannot hide active content from
  the check while a viewer still runs it (council 17 Oct 2026). A damaged
  encrypted PDF is ``unscannable``; one that needs a password to open is
  ``password_protected`` (#552). pdfium (Chrome's engine) opens the file too, and
  document JavaScript, an attached file or an XFA form it finds is refused as well:
  either reader finding active content blocks the file.
* **Office files (zip):** no macros (vbaProject), no ActiveX, no embedded OLE
  objects, no external templates or links in the package relationships, no
  executables or nested archives inside, and no path traversal (``../``,
  absolute paths). Embedded parts (``embeddings/``) only as a chart's or another
  Office package (.xlsx, .docx, .pptx, which ClamAV looks inside too) or a picture.
* **A plain zip** (none of ``[Content_Types].xml`` and the parts that make a Word,
  PowerPoint or Excel file, as ``kaagaz/ingestion/resolve.py`` tells them: a folder
  of files, #522 PR 3): the
  same, except that a zip inside it, a part with a password and an unsafe path are
  each refused on their own when it is unpacked (``kaagaz/ingestion/unzip.py``),
  where every file is checked again by itself, so they don't refuse the whole zip.
* **Old Office files (OLE2: .doc, .xls, .ppt, #522):** no macros (a ``Macros``,
  ``_VBA_PROJECT...`` or ``VBA`` storage or stream, anywhere), no embedded OLE objects
  (``ObjectPool``) and no password (``EncryptedPackage``: an encrypted .docx, .xlsx or
  .pptx, which can't be checked; ``password_protected``, #552, unless it holds macros or an
  object too). Read with ``resolve.py``'s bounded directory reader. A
  PowerPoint 97 file keeps its macros and objects inside its own stream, out of this
  check's sight: ClamAV's OLE2 parser looks there.
* **Plain text:** it must be data, not a script or markup: no HTML,
  XML, SVG, RTF, shell or batch scripts, and no spreadsheet formulas that
  run commands (DDE, ``=cmd|...``, ``=WEBSERVICE``). The markup rule is left out
  for a file the name makes HTML, Markdown or JSON (#522, ``resolve.py``'s text
  kinds): the text reader never renders one, and Download sends it as plain
  text, an attachment, with nosniff. A .txt or .csv file keeps it (a spreadsheet
  may open a .csv), and so does a file with no name.
* **Every file:** no hidden program inside (a Windows or Linux executable
  header), no zip archive glued onto a PDF or an image, and no PDF header in the
  first 1 KiB of anything but a PDF (polyglot files). A zip with one there (#553: a
  PDF stored first) is read as a PDF by a viewer, so it gets the whole PDF check, then
  the zip's own.

It never opens anything that could run: it only reads bytes and names.
A file it cannot inspect is not called safe: that is ``unscannable``.

PROVISIONAL: which content is refused is Vrushit's security decision. These
rules follow the upload threat list Tushar raised on 28 Sep 2026.

Time and memory are bounded for a PDF (security review, 17 Oct 2026):
* before pdfminer touches the file, every structural stream (an object stream or
  an index stream, the only streams pdfminer decompresses here) must use a filter
  this check understands and decompress to at most _MAX_STRUCTURE_BYTES, or the
  file is ``unscannable`` (no zlib bomb, no filter only a viewer can decode);
* one read budget for the whole check: _READ_FACTOR times the file, plus at most
  _MAX_GRANT_BYTES granted per object in all, never reset;
* one deadline for the whole check, _DEADLINE_SECONDS.
Past any bound a PDF is ``unscannable``.

Production runs this check in a child process with OS memory and CPU limits
(kaagaz/scanning/isolated.py): a crash, a hang or a blown limit there is ``unscannable``.
"""

import io
import re
import struct
import threading
import time
import zipfile
import zlib
from collections.abc import Callable
from enum import Enum
from typing import Any
from xml.etree import ElementTree

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pdfminer import pdftypes
from pdfminer.lzw import LZWDecoder
from pdfminer.pdfdocument import LITERAL_OBJSTM, PDFDocument, PDFPasswordIncorrect, PDFXRefFallback
from pdfminer.pdfparser import PDFParser, PDFStreamParser
from pdfminer.pdftypes import PDFObjRef, PDFStream
from pdfminer.psexceptions import PSEOF, PSException
from pdfminer.psparser import KWD, PSBaseParser, PSKeyword, PSLiteral

from kaagaz import pdfminer_fix  # noqa: F401  (for its effect: a /P 0 PDF opens, #551)
from kaagaz.ingestion import resolve
from kaagaz.ingestion.sniff import HEIC, JPEG, OLE2, PDF, PNG, TEXT, TIFF, ZIP, detect_type
from kaagaz.reading.spans import ReadError


class Finding(Enum):
    """Why a file is refused. The value is the short code the document keeps."""

    BLOCKED = "blocked_content"  # active content or a hidden program
    UNSCANNABLE = "unscannable"  # could not be inspected, so not called safe
    ENCRYPTED = "password_protected"  # needs a password to open, so not called safe (#552)


# --- every file -----------------------------------------------------------------

# A Windows program: "MZ", then the DOS stub text every linker writes. Two
# fixed markers together cannot turn up by chance inside compressed data.
_WINDOWS_PROGRAM = re.compile(rb"MZ.{60,400}?This program cannot be run in DOS mode", re.DOTALL)
# A Linux program: the ELF magic, then a valid class, byte order and version.
_LINUX_PROGRAM = re.compile(rb"\x7fELF[\x01\x02][\x01\x02]\x01")
# A complete zip archive: a local file header, a central directory entry and
# the end record. All three together mean an archive, not random bytes.
_ZIP_PARTS = (b"PK\x03\x04", b"PK\x01\x02", b"PK\x05\x06")


def _hides_a_program(data: bytes) -> bool:
    return bool(_WINDOWS_PROGRAM.search(data) or _LINUX_PROGRAM.search(data))


def _has_zip_inside(data: bytes) -> bool:
    return all(part in data for part in _ZIP_PARTS)


# --- PDF ---------------------------------------------------------------------------

# Dictionary keys and action types that make a PDF do something by itself.
_PDF_ACTIVE_KEYS = frozenset(
    {"JavaScript", "JS", "Launch", "EmbeddedFile", "EmbeddedFiles", "EF", "RichMedia", "XFA"}
)
_PDF_ACTIVE_ACTIONS = frozenset(
    {"JavaScript", "Launch", "GoToE", "SubmitForm", "ImportData", "RichMediaExecute"}
)
_ACTIVE_NAMES = _PDF_ACTIVE_KEYS | _PDF_ACTIVE_ACTIONS
_MAX_PDF_DEPTH = 64

# What pdfminer raises on a damaged file. PSException is the base of every pdfminer
# error (syntax, encryption, an object the cross-reference points to but that is not
# there: PDFObjectNotFound, which scanners write and which crashed workers, 16 Oct 2026).
# pdfminer also fails with plain errors deep inside its parser: asserts (load_trailer),
# a bad key, type or index, a truncated or corrupt compressed stream, a deep structure.
_DAMAGED = (
    PSException,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AttributeError,
    AssertionError,
    EOFError,
    RecursionError,
    struct.error,
    zlib.error,
)

# An object's header, "12 0 obj", anywhere in the file (a viewer's index rebuild). PDF
# syntax lets a comment stand for white space ("12 0 %x\nobj"), so the repair walk also
# looks for it in a copy of the file with every comment blanked out (_COMMENT).
_OBJECT_HEADER = re.compile(rb"(?<![0-9])\d+\s+\d+\s+obj\b")
_COMMENT = re.compile(rb"%[^\r\n]*")
# The usual header at an index offset; anything else is read with pdfminer's own tokens.
_HEADER_AT = re.compile(rb"\s*(\d+)\s+\d+\s+obj\b")
# Bytes after an index offset read for its header's three tokens (_tokens_say_header).
_HEADER_PEEK = 4096
_OBJ = KWD(b"obj")
_ENDS_A_DICTIONARY_PART = frozenset({b"stream", b"endobj", b"obj"})

# Work bounds, so no PDF can make the check hang or fill memory (council 17 Oct 2026: an
# xref stream declaring /Size 999999999 in a 404-byte file walked ~1e9 ids, ~3 hours;
# security review 17 Oct 2026: per-object read grants let a file of tiny "1 0 obj"
# headers grant itself ~1000x its size, and pdfminer's decompression had no limit).
# One _Bounds per check, shared by every walk, never reset. PROVISIONAL, Vrushit may tune:
# * an object needs at least ~8 bytes ("1 0 obj\n"), so an index listing more ids than
#   len // _BYTES_PER_OBJECT + _SPARE_OBJECT_IDS is not walked past that: the repair walk
#   (O(file size)) looks at every object the file can actually hold instead;
# * pdfminer may read the file's bytes at most _READ_FACTOR times over, plus
#   _READ_PER_OBJECT for each object it is asked for, those grants adding up to at most
#   _MAX_GRANT_BYTES. A grant is what pdfminer re-reads around one object whatever its
#   size: every seek drops its buffer and fills a whole one again (PSBaseParser.BUFSIZ), and
#   a stream object takes four (its header, the "stream" line, the "endstream" line, then
#   the token after), its data aside. Two buffers were granted until 4 Nov 2026, so a 10 KB
#   golden file of 20 one-line pages read ~74x its size and was refused as unscannable (the
#   likely cause of a sale deed refused on the test server). The ceiling is unchanged, so a hostile
#   file can still read at most _READ_FACTOR x its size + _MAX_GRANT_BYTES in all;
# * everything pdfminer decompresses during the check (Flate, LZW, run length) adds up
#   to at most _MAX_DECODED_BYTES; a CCITT fax stream is never decoded here (no index or
#   object stream uses one);
# * the whole check ends after _DEADLINE_SECONDS.
# Past any bound the PDF is unscannable.
_BYTES_PER_OBJECT = 8
_SPARE_OBJECT_IDS = 1000
_READ_FACTOR = 4
_READ_PER_OBJECT = 4 * PSBaseParser.BUFSIZ  # 16 KiB
_MAX_GRANT_BYTES = 16 * 1024 * 1024
_MAX_DECODED_BYTES = 64 * 1024 * 1024
_DEADLINE_SECONDS = 20.0


class _ReadBudgetSpent(Exception):
    """The check went past one of its work bounds (reads, decoded bytes or time)."""


class _Bounds:
    """One check's work bounds: bytes read, bytes decoded and a deadline. O(1) a call."""

    def __init__(self, size: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.reads = _READ_FACTOR * size
        self.grants = _MAX_GRANT_BYTES
        self.decoded = _MAX_DECODED_BYTES
        self._clock = clock
        self._deadline = clock() + _DEADLINE_SECONDS

    def grant(self, size: int) -> None:
        given = min(size, self.grants)
        self.grants -= given
        self.reads += given

    def read(self, size: int) -> None:
        self.reads -= size
        self._check(self.reads)

    def decode(self, size: int) -> None:
        self.decoded -= size
        self._check(self.decoded)

    def _check(self, left: int) -> None:
        if left < 0 or self._clock() > self._deadline:
            raise _ReadBudgetSpent


# The check in progress on this thread (pdfminer's decoders below look here).
_current = threading.local()


def _bounds() -> "_Bounds | None":
    return getattr(_current, "bounds", None)


class _BoundedDecompressor:
    """A zlib decompressor whose output counts against the check's decoded bytes."""

    def __init__(self, inner: Any, bounds: _Bounds) -> None:
        self._inner = inner
        self._bounds = bounds

    def decompress(self, data: bytes, max_length: int = 0) -> bytes:
        room = self._bounds.decoded + 1
        out: bytes = self._inner.decompress(data, room if max_length <= 0 else min(max_length, room))
        self._bounds.decode(len(out))
        return out

    def flush(self, *args: Any) -> bytes:
        out: bytes = self._inner.flush(*args)
        self._bounds.decode(len(out))
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _GuardedZlib:
    """Stands in for ``zlib`` inside pdfminer.pdftypes: the same calls, bounded while a
    PDF is being checked on this thread, untouched everywhere else (the readers)."""

    error = zlib.error

    def __getattr__(self, name: str) -> Any:
        return getattr(zlib, name)

    def decompress(self, data: bytes, *args: Any, **kwargs: Any) -> bytes:
        bounds = _bounds()
        if bounds is None:
            return zlib.decompress(data, *args, **kwargs)
        inner = zlib.decompressobj(*args, **kwargs)
        out = _BoundedDecompressor(inner, bounds).decompress(data)
        if not inner.eof:
            raise zlib.error("incomplete or truncated stream")  # as zlib.decompress says
        return out

    def decompressobj(self, *args: Any, **kwargs: Any) -> Any:
        inner = zlib.decompressobj(*args, **kwargs)
        bounds = _bounds()
        return inner if bounds is None else _BoundedDecompressor(inner, bounds)


# The ignores here and below: pdftypes imports these names without re-exporting them, and they
# are read and replaced in pdftypes on purpose, as that is where its decoders look them up.
_PLAIN_LZW = pdftypes.lzwdecode  # type: ignore[attr-defined]  # pdftypes' own name, see above
_PLAIN_RL = pdftypes.rldecode  # type: ignore[attr-defined]  # pdftypes' own name, see above
_PLAIN_CCITT = pdftypes.ccittfaxdecode  # type: ignore[attr-defined]  # pdftypes' own name, see above


def _guarded_lzw(data: bytes) -> bytes:
    bounds = _bounds()
    if bounds is None:
        return _PLAIN_LZW(data)
    parts = []
    for chunk in LZWDecoder(io.BytesIO(data)).run():
        bounds.decode(len(chunk))
        parts.append(chunk)
    return b"".join(parts)


def _run_length_size(data: bytes) -> int:
    """How many bytes a run-length stream decodes to, without decoding it. O(n)."""
    size, at = 0, 0
    while at < len(data) and data[at] != 128:
        length = data[at]
        if length < 128:
            size, at = size + length + 1, at + length + 2
        else:
            size, at = size + 257 - length, at + 2
    return size


def _guarded_rl(data: bytes) -> bytes:
    bounds = _bounds()
    if bounds is not None:
        bounds.decode(_run_length_size(data))
    return _PLAIN_RL(data)


def _guarded_ccitt(data: bytes, params: Any) -> bytes:
    if _bounds() is not None:
        raise _ReadBudgetSpent  # never an index or object stream's filter
    return _PLAIN_CCITT(data, params)


# ponytail: pdfminer's own decoders, swapped for bounded ones (they delegate unchanged
# outside a check); if pdfminer ever renames them, the import-time names below fail loudly.
pdftypes.zlib = _GuardedZlib()  # type: ignore[assignment,attr-defined]  # a stand-in; see _PLAIN_LZW
pdftypes.lzwdecode = _guarded_lzw  # type: ignore[attr-defined]  # pdftypes' own name, see _PLAIN_LZW
pdftypes.rldecode = _guarded_rl  # type: ignore[attr-defined]  # pdftypes' own name, see _PLAIN_LZW
pdftypes.ccittfaxdecode = _guarded_ccitt  # type: ignore[attr-defined]  # pdftypes' own name, see _PLAIN_LZW


class _MeteredFile(io.BytesIO):
    """The file, counting what pdfminer reads from it against the check's bounds."""

    def __init__(self, data: bytes, bounds: _Bounds) -> None:
        super().__init__(data)
        self._bounds = bounds

    def read(self, size: int | None = -1, /) -> bytes:
        chunk = super().read(size)
        self._bounds.read(len(chunk))
        return chunk


def _pdf_finding(data: bytes) -> Finding | None:
    bounds = _Bounds(len(data))
    _current.bounds = bounds
    try:
        return _bounded_pdf_finding(data, bounds)
    except _ReadBudgetSpent:
        return Finding.UNSCANNABLE
    finally:
        _current.bounds = None


def _bounded_pdf_finding(data: bytes, bounds: _Bounds) -> Finding | None:
    file = _MeteredFile(data, bounds)
    try:
        document = PDFDocument(PDFParser(file))
    except PDFPasswordIncorrect:  # no empty user password opens it (#552)
        return Finding.ENCRYPTED
    except _DAMAGED:
        return Finding.UNSCANNABLE
    # pdfminer's own fallback index stops at the first trailer, so it is no proof of
    # having seen every object.
    damaged = any(isinstance(xref, PDFXRefFallback) for xref in document.xrefs)
    most_ids = len(data) // _BYTES_PER_OBJECT + _SPARE_OBJECT_IDS
    try:
        for xref in document.xrefs:
            for object_id in xref.get_objids():
                most_ids -= 1
                if most_ids < 0:
                    # More ids than the file can hold objects: the repair walk looks, and
                    # an encrypted file (no repair walk) was not wholly read: unscannable.
                    damaged = True
                    break
                bounds.grant(_READ_PER_OBJECT)
                if not _header_where_indexed(data, document, object_id):
                    # Vrushit UAT, 25 Oct 2026: from a wrong offset pdfminer reads on to the next
                    # "obj", through a scanned page's image bytes, quadratic in them (a 2.5 MB scan
                    # with offsets 7 bytes off ran out the deadline: "unscannable"). A viewer
                    # rebuilds such an index; the repair walk below finds the object by its header.
                    damaged = True
                    continue
                try:
                    obj = document.getobj(object_id)
                except _DAMAGED:
                    damaged = True  # one unreadable object: the repair walk below looks again
                    continue
                # The same check as the repair walk's (incl. inside an object stream): an
                # encrypted file gets only this walk.
                if _holds_active(obj, document):
                    return Finding.BLOCKED
    except _DAMAGED:
        damaged = True  # the index itself broke part way
    if document.encryption is not None:
        # Objects outside the index cannot be decrypted here; a whole index was read.
        return Finding.UNSCANNABLE if damaged else None
    # Always, not only when damaged (security review, 17 Oct 2026): a sound index can
    # leave out an object a viewer still finds and runs (one written after the end, say).
    return _repair_walk(data, document, bounds)


def _header_where_indexed(data: bytes, document: PDFDocument, object_id: int) -> bool:
    """False if the index entry pdfminer would use (the first index listing the object, as
    ``getobj`` picks it) gives a file offset where this object's header is not. An object
    inside an object stream has no offset of its own: True. O(1)."""
    for xref in document.xrefs:
        try:
            stream_id, position, _ = xref.get_pos(object_id)
        except KeyError:
            continue
        if stream_id is not None:
            return True
        header = _HEADER_AT.match(data, position)
        if header is not None and int(header.group(1)) == object_id:
            return True
        return _tokens_say_header(data, position, object_id)
    return True  # listed nowhere: getobj says so itself


def _tokens_say_header(data: bytes, position: int, object_id: int) -> bool:
    """Whether pdfminer's tokens at ``position`` (comments and white space skipped, as a
    viewer skips them) are this object's header, "id gen obj". Read from at most
    _HEADER_PEEK bytes, so a wrong offset into image bytes costs O(1); if those run out
    before three tokens, it is not called a non-header: getobj reads it, as it always did
    (security review, 25 Oct 2026: "4 0 %c\\nobj" is a header a stricter test missed)."""
    parser = PSBaseParser(io.BytesIO(data[max(0, position) : position + _HEADER_PEEK]))
    tokens: list[object] = []
    try:
        for _ in range(3):
            tokens.append(parser.nexttoken()[1])
    except PSEOF:
        return True  # undecided
    except _DAMAGED:
        return False  # no header there: getobj would fail the same way
    first, generation, keyword = tokens
    return first == object_id and isinstance(generation, int) and keyword is _OBJ


def _repair_walk(data: bytes, document: PDFDocument, bounds: _Bounds) -> Finding | None:
    """BLOCKED if any object in the whole file, found by its header as a viewer rebuilds
    an index (pdfium, pdf.js and Acrobat all do), or inside an object stream, is active;
    else None. Raises _ReadBudgetSpent past the check's bounds. O(file size) reads.

    Each object is parsed on its own from its header. A parse that fails (an odd or
    unclosed dictionary) has its dictionary part scanned for active names token by
    token, and what an unfinished parse left on the parser's stacks (an object with no
    "endobj") is checked too, so damage cannot hide a name a viewer would still read."""
    file = _MeteredFile(data, bounds)
    parser = PDFParser(file)
    parser.set_document(document)
    parser.fallback = True  # a stream runs to its "endstream", whatever its /Length says
    for end in _header_ends(data):
        bounds.grant(_READ_PER_OBJECT)
        parser.seek(end)
        try:
            found = [parser.nextobject()[1], *_leftovers(parser)]
        except _DAMAGED:
            found = None
        if found is None:
            active = _loose_names_active(parser, end)
        else:
            active = any(_holds_active(value, document) for value in found)
        if active:
            return Finding.BLOCKED
    return None


def _header_ends(data: bytes) -> list[int]:
    """Where each object header in the file ends, in file order: headers as written, and
    headers with a comment between their parts, found in a copy with each comment blanked
    to spaces (same offsets). Both, since a "%" inside a string is not a comment. O(n)."""
    blanked = _COMMENT.sub(lambda comment: b" " * len(comment.group()), data)
    ends = {header.end() for header in _OBJECT_HEADER.finditer(data)}
    ends.update(header.end() for header in _OBJECT_HEADER.finditer(blanked))
    return sorted(ends)


def _leftovers(parser: PDFParser) -> list[object]:
    """What a parse left on the parser's stacks besides the object it gave: an object
    with no "endobj" before the next one, or a dictionary never closed."""
    values: list[object] = [value for _, value in (*parser.results, *parser.curstack)]
    for _, _, stack in parser.context:
        values.extend(value for _, value in stack)
    return values


def _loose_names_active(parser: PDFParser, start: int) -> bool:
    """Any active name among the tokens from ``start`` to the object's stream, its end,
    or the next object's header: the dictionary part of an object that would not parse.
    O(tokens), never past the next header."""
    parser.seek(start)
    while True:
        try:
            _, token = parser.nexttoken()
        except PSEOF:
            return False  # the end of the file
        except _DAMAGED as error:
            raise _ReadBudgetSpent from error  # the rest was not read: not called safe
        if isinstance(token, PSKeyword) and token.name in _ENDS_A_DICTIONARY_PART:
            return False
        if isinstance(token, PSLiteral) and token.name in _ACTIVE_NAMES:
            return True


def _holds_active(obj: object, document: PDFDocument) -> bool:
    """``_is_active``, plus a loose active name (a dictionary that never closed), plus
    the objects inside an object stream."""
    if isinstance(obj, PSLiteral):
        return obj.name in _ACTIVE_NAMES
    if _is_active(obj, 0):
        return True
    return (
        isinstance(obj, PDFStream)
        and obj.get("Type") is LITERAL_OBJSTM
        and _object_stream_active(obj, document)
    )


def _object_stream_active(stream: PDFStream, document: PDFDocument) -> bool:
    """Any active object inside an object stream, read to its end or its first damage."""
    try:
        parser = PDFStreamParser(stream.get_data())
    except _DAMAGED as error:
        # It cannot be decoded here, but a viewer may decode it (security review, 17 Oct
        # 2026: "no viewer reads it either" was not safe to assume): not called safe.
        raise _ReadBudgetSpent from error
    parser.set_document(document)
    while True:
        try:
            _, obj = parser.nextobject()
        except PSEOF:  # the end of the stream
            return any(_holds_active(value, document) for value in _leftovers(parser))
        except _DAMAGED as error:
            raise _ReadBudgetSpent from error  # the rest was not read: not called safe
        if _holds_active(obj, document):
            return True


def _is_active(obj: object, depth: int) -> bool:
    """True if this object, or anything directly inside it, is active content.

    References to other objects are not followed: every object is visited on
    its own by the caller, so each is checked once. O(size of the object).
    Deeper than _MAX_PDF_DEPTH is called active: an object nested that deep is
    not a business document, and what lies below it was never checked.
    """
    if depth > _MAX_PDF_DEPTH:
        return True
    if isinstance(obj, PDFStream):
        obj = obj.attrs
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _PDF_ACTIVE_KEYS:
                return True
            if key == "S" and isinstance(value, PSLiteral) and value.name in _PDF_ACTIVE_ACTIONS:
                return True
            if not isinstance(value, PDFObjRef) and _is_active(value, depth + 1):
                return True
    elif isinstance(obj, list):
        return any(not isinstance(item, PDFObjRef) and _is_active(item, depth + 1) for item in obj)
    return False


_XFA_FORMS = frozenset({pdfium_c.FORMTYPE_XFA_FULL, pdfium_c.FORMTYPE_XFA_FOREGROUND})


def _pdfium_sees_active(data: bytes) -> bool:
    """Whether pdfium (Chrome's engine, which repairs a damaged file as a viewer does)
    finds document JavaScript, an attached file or an XFA form: a second reader, so a
    file read one way by pdfminer and another by a viewer is still caught.

    A file pdfium cannot open (damaged past its repair, or a password needed) is left to
    pdfminer's verdict: Chrome runs nothing in it, and every object pdfminer could find
    was already walked; the readers (pdfium again) then judge whether it can be read.
    An owner-password-only file opens here with no password, so its catalog is seen
    even though pdfminer's repair walk cannot run on it.

    ponytail: pdfium is not thread-safe; production runs this check in its own process
    (isolated.py), one at a time. The document's OpenAction and pages' /AA are not
    exposed by pdfium's raw API without loading every page; pdfminer's walk checks them.
    """
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError:
        return False
    try:
        raw = document.raw
        return (
            pdfium_c.FPDFDoc_GetJavaScriptActionCount(raw) != 0  # -1 (an error): not called safe
            or pdfium_c.FPDFDoc_GetAttachmentCount(raw) != 0
            or pdfium_c.FPDF_GetFormType(raw) in _XFA_FORMS
        )
    finally:
        document.close()


# --- Office packages (zip) ---------------------------------------------------------

_RISKY_PARTS = re.compile(
    r"(^|/)(vbaProject\.bin|vbaData\.xml|activeX/|oleObject|macrosheets/)",
    re.IGNORECASE,
)
# What a package may embed (#522, security review 6 Nov 2026): a chart's workbook, another
# Office package (ClamAV looks inside each too) or a picture. Anything else under embeddings/
# (an OLE object, any .bin package, a program, a macro-enabled file) is refused.
_EMBEDDINGS = re.compile(r"(^|/)embeddings/", re.IGNORECASE)
_EMBEDDABLE = re.compile(r"\.(xlsx|docx|pptx|png|jpe?g|gif|bmp|tiff?|emf|wmf)$", re.IGNORECASE)
_RISKY_EXTENSIONS = re.compile(
    r"\.(exe|dll|com|scr|msi|msp|bat|cmd|ps1|psm1|vbs|vbe|js|jse|wsf|wsh|hta|lnk|jar|"
    r"iso|img|vhd|zip|rar|7z|gz|tar|cab|docm|xlsm|pptm|dotm|xltm|xlam|ppam)$",
    re.IGNORECASE,
)
_MAX_RELS_BYTES = 1_000_000
# Any of these makes a zip an Office package (resolve.py reads it as one by the last three).
_PACKAGE_PARTS = frozenset(
    {"[Content_Types].xml", "word/document.xml", "ppt/presentation.xml", "xl/workbook.xml"}
)
_NESTED_ZIP = re.compile(r"\.zip$", re.IGNORECASE)  # kept unread in a plain zip (#522 PR 3)


def _loads_from_outside(rels: bytes) -> bool:
    """A relationship that loads something from outside the file (remote templates,
    external links): TargetMode External on anything that is not a hyperlink. Parsed as
    XML, as Office reads it, so quotes, spacing and character references cannot hide it
    (the caller refuses a DTD first). Raises ParseError."""
    for element in ElementTree.fromstring(rels).iter():  # noqa: S314 (no DTD, as xlsx_reader)
        if not element.tag.endswith("Relationship"):
            continue
        mode, kind = element.get("TargetMode", ""), element.get("Type", "")
        if mode.strip().lower() == "external" and not kind.strip().lower().endswith("/hyperlink"):
            return True
    return False


def _zip_finding(data: bytes) -> Finding | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            package = any(info.filename in _PACKAGE_PARTS for info in infos)  # else a folder of files
            for info in infos:
                name = info.filename.replace("\\", "/")
                unsafe = name.startswith("/") or ".." in name.split("/") or re.match(r"^[A-Za-z]:", name)
                if package and unsafe:
                    return Finding.BLOCKED  # path traversal (Zip Slip)
                if package and info.flag_bits & 0x1:
                    return Finding.UNSCANNABLE  # a password-protected part cannot be checked
                embedded = bool(_EMBEDDINGS.search(name)) and not _EMBEDDABLE.search(name)
                risky = bool(_RISKY_PARTS.search(name) or _RISKY_EXTENSIONS.search(name)) or embedded
                if risky and (package or not _NESTED_ZIP.search(name)):
                    return Finding.BLOCKED
            for info in infos:
                if not info.filename.lower().endswith(".rels"):
                    continue
                # Read to the bound, never trusting the declared size: a part can
                # decompress past what its header says.
                with archive.open(info) as part:
                    rels = part.read(_MAX_RELS_BYTES + 1)
                if len(rels) > _MAX_RELS_BYTES or b"<!doctype" in rels.lower():
                    return Finding.UNSCANNABLE  # no Office part has a DTD (no entity tricks)
                if _loads_from_outside(rels):
                    return Finding.BLOCKED  # a remote template or external link
    except (
        ElementTree.ParseError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        NotImplementedError,
        ValueError,
        OSError,
        EOFError,
        zlib.error,  # a damaged compressed part (xlsx_reader._DAMAGED lists the same)
    ):
        return Finding.UNSCANNABLE
    return None


# --- plain text --------------------------------------------------------------------

_BOM = "﻿"
_MARKUP_OR_SCRIPT_START = re.compile(r"^\s*(<|\{\\rtf|#!|@echo\b|::|rem\s)", re.IGNORECASE)
_SCRIPT_ANYWHERE = re.compile(
    r"<\s*script\b|javascript\s*:|vbscript\s*:|<\s*iframe\b|<\s*object\b", re.IGNORECASE
)
# A cell that starts a formula which runs a command or calls the internet.
_DANGEROUS_FORMULA = re.compile(
    r"(^|[,;\t\"'])\s*[=+\-@]\s*(cmd\s*\||msexcel\s*\||dde\s*\(|ddeauto|webservice\s*\(|"
    r"hyperlink\s*\(|importxml\s*\(|importdata\s*\(|call\s*\(|register\s*\(|exec\s*\()",
    re.IGNORECASE | re.MULTILINE,
)


# DDE, the old way a spreadsheet cell starts another program: app|'topic'!item,
# for example cmd|' /C calc'!A0. Checked anywhere, as it can hide mid-formula.
_DDE_CALL = re.compile(r"[A-Za-z0-9_.]+\s*\|\s*'[^'\n\r]*'\s*!", re.IGNORECASE)


# The text kinds whose reader never renders their markup (#522): HTML, Markdown and JSON.
_MARKUP_IS_TEXT = frozenset({resolve.HTML, resolve.MD, resolve.JSON})


def _text_finding(data: bytes, name: str) -> Finding | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Finding.UNSCANNABLE
    text = text.removeprefix(_BOM)
    markup_is_text = resolve.resolve_kind(data, TEXT, name) in _MARKUP_IS_TEXT
    if not markup_is_text and (_MARKUP_OR_SCRIPT_START.match(text) or _SCRIPT_ANYWHERE.search(text)):
        return Finding.BLOCKED
    if _DANGEROUS_FORMULA.search(text) or _DDE_CALL.search(text):
        return Finding.BLOCKED
    return None


# --- the check ----------------------------------------------------------------------


# OLE2 entries a business document must not carry (#522): macros, embedded OLE objects, and a
# password (an encrypted .docx, .xlsx or .pptx, which nothing can look inside).
_OLE2_MACROS = re.compile(r"^(Macros|_VBA_PROJECT.*|VBA)$", re.IGNORECASE)
_OLE2_REFUSED = frozenset({"objectpool"})


def _ole2_finding(data: bytes) -> Finding | None:
    try:
        names = resolve.ole2_entry_names(data)
    except ReadError:
        return Finding.UNSCANNABLE
    if any(_OLE2_MACROS.match(name) or name.lower() in _OLE2_REFUSED for name in names):
        return Finding.BLOCKED
    # #552 (UAT FF-14): told it has a password, not macros. After the check above, so a file
    # refused for macros or an object keeps that verdict, whatever else it holds.
    if any(name.lower() == "encryptedpackage" for name in names):
        return Finding.ENCRYPTED
    return None


def inspect(data: bytes, name: str = "") -> Finding | None:
    """Why this file must be refused, or None if nothing risky was found.

    The type is worked out again from the bytes (never from a name), so the
    check cannot be fooled by an extension. ``name``, the file's name, only chooses
    between text kinds that are all read as text (#522: the markup rule).
    """
    kind = detect_type(data[:4096])
    if kind is None:
        return Finding.BLOCKED  # not a type we accept at all
    if _hides_a_program(data):
        return Finding.BLOCKED
    if kind != ZIP and _has_zip_inside(data):
        return Finding.BLOCKED  # a polyglot: an archive glued onto a document, image or text
    if kind not in (PDF, ZIP) and b"%PDF-" in data[:1024]:
        return Finding.BLOCKED  # a polyglot: viewers find a PDF header anywhere in 1 KiB
    if kind == PDF or (kind == ZIP and b"%PDF-" in data[:1024]):
        # #553 (UAT ZP-04/05): a zip whose first file is a PDF stored, not compressed, is read
        # by a viewer as a PDF, all of it (comments and names too), so that view gets this whole
        # check, as a PDF upload does (the repair walk reads every object header in the file).
        finding = _pdf_finding(data)
        # Either reader finding active content blocks the file.
        finding = Finding.BLOCKED if finding is not Finding.BLOCKED and _pdfium_sees_active(data) else finding
        if kind == PDF or finding is not None:
            return finding
    if kind == ZIP:
        return _zip_finding(data)
    if kind == TEXT:
        return _text_finding(data, name)
    if kind == OLE2:
        return _ole2_finding(data)
    return None if kind in (PNG, JPEG, TIFF, HEIC) else Finding.BLOCKED  # deny by default
