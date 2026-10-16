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
  encrypted PDF is ``unscannable``.
* **Office files (zip):** no macros (vbaProject), no ActiveX, no embedded OLE
  objects, no external templates or links in the package relationships, no
  executables or nested archives inside, and no path traversal (``../``,
  absolute paths).
* **Plain text (CSV):** it must be data, not a script or markup: no HTML,
  XML, SVG, RTF, shell or batch scripts, and no spreadsheet formulas that
  run commands (DDE, ``=cmd|...``, ``=WEBSERVICE``).
* **Every file:** no hidden program inside (a Windows or Linux executable
  header), and no zip archive glued onto a PDF or an image (a polyglot file).

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

from pdfminer import pdftypes
from pdfminer.lzw import LZWDecoder
from pdfminer.pdfdocument import LITERAL_OBJSTM, PDFDocument, PDFXRefFallback
from pdfminer.pdfparser import PDFParser, PDFStreamParser
from pdfminer.pdftypes import PDFObjRef, PDFStream
from pdfminer.psexceptions import PSException
from pdfminer.psparser import PSKeyword, PSLiteral

from kaagaz.ingestion.sniff import JPEG, PDF, PNG, TEXT, TIFF, ZIP, detect_type


class Finding(Enum):
    """Why a file is refused. The value is the short code the document keeps."""

    BLOCKED = "blocked_content"  # active content or a hidden program
    UNSCANNABLE = "unscannable"  # could not be inspected, so not called safe


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

# An object's header, "12 0 obj", anywhere in the file (a viewer's index rebuild).
_OBJECT_HEADER = re.compile(rb"(?<![0-9])\d+\s+\d+\s+obj\b")
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
#   _READ_PER_OBJECT (two of its 4 KiB buffers) for each object it is asked for, those
#   grants adding up to at most _MAX_GRANT_BYTES;
# * everything pdfminer decompresses during the check (Flate, LZW, run length) adds up
#   to at most _MAX_DECODED_BYTES; a CCITT fax stream is never decoded here (no index or
#   object stream uses one);
# * the whole check ends after _DEADLINE_SECONDS.
# Past any bound the PDF is unscannable.
_BYTES_PER_OBJECT = 8
_SPARE_OBJECT_IDS = 1000
_READ_FACTOR = 4
_READ_PER_OBJECT = 8192
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
        out = self._inner.decompress(data, room if max_length <= 0 else min(max_length, room))
        self._bounds.decode(len(out))
        return out

    def flush(self, *args: Any) -> bytes:
        out = self._inner.flush(*args)
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


_PLAIN_LZW = pdftypes.lzwdecode
_PLAIN_RL = pdftypes.rldecode
_PLAIN_CCITT = pdftypes.ccittfaxdecode


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
pdftypes.zlib = _GuardedZlib()  # type: ignore[assignment]
pdftypes.lzwdecode = _guarded_lzw
pdftypes.rldecode = _guarded_rl
pdftypes.ccittfaxdecode = _guarded_ccitt


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
                    break  # more ids than the file can hold objects: the repair walk looks
                bounds.grant(_READ_PER_OBJECT)
                try:
                    obj = document.getobj(object_id)
                except _DAMAGED:
                    damaged = True  # one unreadable object: the repair walk below looks again
                    continue
                if _is_active(obj, 0):
                    return Finding.BLOCKED
    except _DAMAGED:
        damaged = True  # the index itself broke part way
    if document.encryption is not None:
        # Objects outside the index cannot be decrypted here; a whole index was read.
        return Finding.UNSCANNABLE if damaged else None
    # Always, not only when damaged (security review, 17 Oct 2026): a sound index can
    # leave out an object a viewer still finds and runs (one written after the end, say).
    return _repair_walk(data, document, bounds)


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
    for header in _OBJECT_HEADER.finditer(data):
        bounds.grant(_READ_PER_OBJECT)
        parser.seek(header.end())
        try:
            found = [parser.nextobject()[1], *_leftovers(parser)]
        except _DAMAGED:
            found = None
        if found is None:
            active = _loose_names_active(parser, header.end())
        else:
            active = any(_holds_active(value, document) for value in found)
        if active:
            return Finding.BLOCKED
    return None


def _leftovers(parser: PDFParser) -> list[object]:
    """What a parse left on the parser's stacks besides the object it gave: an object
    with no "endobj" before the next one, or a dictionary never closed."""
    values = [value for _, value in (*parser.results, *parser.curstack)]
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
        except _DAMAGED:
            return False
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
        except _DAMAGED:  # PSEOF at the end
            return any(_holds_active(value, document) for value in _leftovers(parser))
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


# --- Office packages (zip) ---------------------------------------------------------

_RISKY_PARTS = re.compile(
    r"(^|/)(vbaProject\.bin|vbaData\.xml|activeX/|embeddings/|oleObject|macrosheets/)",
    re.IGNORECASE,
)
_RISKY_EXTENSIONS = re.compile(
    r"\.(exe|dll|com|scr|msi|msp|bat|cmd|ps1|psm1|vbs|vbe|js|jse|wsf|wsh|hta|lnk|jar|"
    r"iso|img|vhd|zip|rar|7z|gz|tar|cab|docm|xlsm|pptm|dotm|xltm|xlam|ppam)$",
    re.IGNORECASE,
)
# A relationship that loads something from outside the file (remote templates,
# external links): TargetMode="External" on anything that is not a hyperlink.
_EXTERNAL_TARGET = re.compile(rb'<Relationship\b[^>]*TargetMode="External"[^>]*>', re.IGNORECASE)
_HYPERLINK_TYPE = re.compile(rb'Type="[^"]*/hyperlink"', re.IGNORECASE)
_MAX_RELS_BYTES = 1_000_000


def _zip_finding(data: bytes) -> Finding | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            for info in infos:
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/") or re.match(r"^[A-Za-z]:", name):
                    return Finding.BLOCKED  # path traversal (Zip Slip)
                if info.flag_bits & 0x1:
                    return Finding.UNSCANNABLE  # a password-protected part cannot be checked
                if _RISKY_PARTS.search(name) or _RISKY_EXTENSIONS.search(name):
                    return Finding.BLOCKED
            for info in infos:
                if info.filename.endswith(".rels") and info.file_size <= _MAX_RELS_BYTES:
                    for match in _EXTERNAL_TARGET.finditer(archive.read(info)):
                        if not _HYPERLINK_TYPE.search(match.group(0)):
                            return Finding.BLOCKED  # a remote template or external link
    except (
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


def _text_finding(data: bytes) -> Finding | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Finding.UNSCANNABLE
    text = text.removeprefix(_BOM)
    if _MARKUP_OR_SCRIPT_START.match(text) or _SCRIPT_ANYWHERE.search(text):
        return Finding.BLOCKED
    if _DANGEROUS_FORMULA.search(text) or _DDE_CALL.search(text):
        return Finding.BLOCKED
    return None


# --- the check ----------------------------------------------------------------------


def inspect(data: bytes) -> Finding | None:
    """Why this file must be refused, or None if nothing risky was found.

    The type is worked out again from the bytes (never from a name), so the
    check cannot be fooled by an extension.
    """
    kind = detect_type(data[:4096])
    if kind is None:
        return Finding.BLOCKED  # not a type we accept at all
    if _hides_a_program(data):
        return Finding.BLOCKED
    if kind in (PDF, PNG, JPEG, TIFF) and _has_zip_inside(data):
        return Finding.BLOCKED  # a polyglot: an archive glued onto a document or image
    if kind == PDF:
        return _pdf_finding(data)
    if kind == ZIP:
        return _zip_finding(data)
    if kind == TEXT:
        return _text_finding(data)
    return None
