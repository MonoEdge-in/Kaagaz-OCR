"""Tell a file's kind, which reader reads it, after its virus scan (#522).

``sniff.py`` tells the type from the first bytes only, before the scan, so a zip
file (.xlsx, .docx, .pptx or a plain .zip), an old Office file (.doc, .xls, .ppt)
and a text file (CSV, plain text, Markdown, JSON, HTML) are each one type there.
``resolve_kind`` runs after the scan and looks inside, as little as it can:

* A zip file: its central directory only (the names of its parts) and, for a
  Word file, ``[Content_Types].xml`` up to ``CONTENT_TYPES_MAX_BYTES``, read
  with the cap enforced while it is read, never trusting the size it claims.
  ``word/document.xml`` is a Word file (``dotx`` when its content type is a
  template's), ``ppt/presentation.xml`` a PowerPoint, ``xl/workbook.xml`` a
  spreadsheet; any other zip is ``zip``. No part is parsed as XML here.
  A zip of more than ``ZIP_MAX_ENTRIES`` entries, by its end record's count or
  by its central directory entries counted before anything is listed, is
  ``zip`` unlisted, so millions of entries can't stall it.
* An old Office file (OLE2, "compound file"): its directory only, read sector by
  sector with the standard library and a cap on every chain, for the streams at
  its root: ``WordDocument`` is ``doc``, ``PowerPoint Document`` is ``ppt``,
  ``Workbook`` (or Excel 95's ``Book``) is ``xls``. Streams inside embedded
  objects are never looked at, so a Word file holding a spreadsheet stays
  ``doc``. One with none of them (an Outlook message, an installer) is
  ``UNSUPPORTED``.
* A text file: the file name's ending chooses, because every text reader is
  safe for any text: ``.csv``, ``.txt``, ``.md``, ``.json``, ``.html`` or
  ``.htm``; any other ending, or none, is ``txt``. A ``.json`` file must be
  JSON, or it is read as ``txt``. One nested deeper than ``JSON_MAX_DEPTH``
  stays ``json``, so its reader refuses it in plain words.
* A PDF, PNG, JPEG, TIFF or HEIC photo is its sniffed type.
* Anything else (no sniffed type) is ``UNSUPPORTED``.

A zip or OLE2 file whose directory cannot be read raises ``ReadError
("corrupt_file")``, outside any ``except`` block, so no library error (which
can quote the file) is chained on.

Time: a zip's central directory, O(entries); an OLE2 file's FAT and directory
chains, O(sectors) bounded by the file size; a text file, O(n) for a ``.json``
(one scan for the depth, one parse), O(1) otherwise.
"""

import io
import json
import os
import re
import struct
import zipfile
import zlib
from typing import IO

from kaagaz.ingestion import sniff
from kaagaz.reading.spans import ReadError

# The kinds (``documents.detected_type`` once read, migration 0078).
PDF = "pdf"
PNG = "png"
JPEG = "jpeg"
TIFF = "tiff"
HEIC = "heic"  # HEIC and HEIF
XLSX = "xlsx"
XLS = "xls"
CSV = "csv"
DOCX = "docx"
DOTX = "dotx"
DOC = "doc"
PPTX = "pptx"
PPT = "ppt"
TXT = "txt"
MD = "md"
JSON = "json"
HTML = "html"
ZIP = "zip"  # a zip that is no Office file: a folder of files (#522 PR 3)
UNSUPPORTED = "unsupported"  # never stored: the file can't be read

KINDS = frozenset(
    {PDF, PNG, JPEG, TIFF, HEIC, XLSX, XLS, CSV, DOCX, DOTX, DOC, PPTX, PPT, TXT, MD, JSON, HTML, ZIP}
)
# The text kinds, chosen by the file name's ending.
TEXT_KINDS = frozenset({CSV, TXT, MD, JSON, HTML})

# What a text file name's ending (lower case) is read as. Any other ending is TXT.
_TEXT_ENDINGS = {"csv": CSV, "txt": TXT, "md": MD, "json": JSON, "html": HTML, "htm": HTML}
JSON_MAX_DEPTH = 100  # the JSON reader refuses a deeper file (#522)
# A JSON string (to its end, or the text's end when it never closes), or a bracket.
_JSON_TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"?|[\[\]{}]', re.DOTALL)

# Zip: the parts that name an Office file, and the Word template's content type.
_WORD_PART = "word/document.xml"
_POWERPOINT_PART = "ppt/presentation.xml"
_EXCEL_PART = "xl/workbook.xml"
_CONTENT_TYPES = "[Content_Types].xml"
_DOTX_TYPE = b"application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml"
CONTENT_TYPES_MAX_BYTES = 1024 * 1024  # a real one is a few KB
# A zip with more entries than this is never listed: it is no Office file (one has tens of
# parts), so it is a ZIP, which its own limit refuses (#522 PR 3: at most 50,000 entries).
ZIP_MAX_ENTRIES = 50_000
_END_OF_DIRECTORY = b"PK\x05\x06"
_ZIP64_LOCATOR = b"PK\x06\x07"
_END_SEARCH = 22 + 0xFFFF  # the end record and the longest comment
_CENTRAL_ENTRY = b"PK\x01\x02"  # the signature of each central directory entry
_CHUNK = 1024 * 1024
# What zipfile raises for a damaged or unreadable zip (a bad CRC or deflate stream, an
# encrypted part, a method it lacks, a header past the end).
_ZIP_DAMAGED = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    OSError,
    EOFError,
    ValueError,
    NotImplementedError,
    RuntimeError,
    zlib.error,
    struct.error,
)

# OLE2 (MS-CFB): the header, the FAT and the directory.
_HEADER_SIZE = 512
_HEADER_DIFAT = 109  # FAT sector numbers in the header itself
_ENTRY_SIZE = 128
_NO_STREAM = 0xFFFFFFFF
_END_OF_CHAIN = 0xFFFFFFFE
_MAX_SECTOR = 0xFFFFFFFA  # a larger number is a marker, never a sector
_STREAM = 2
_ROOT_STREAMS = (("WordDocument", DOC), ("PowerPoint Document", PPT), ("Workbook", XLS), ("Book", XLS))

Source = bytes | str | os.PathLike[str]


def resolve_kind(source: Source, sniffed: str | None, display_name: str) -> str:
    """The kind of the scanned file in ``source`` (its bytes, or a path to it), one of
    KINDS, or UNSUPPORTED. ``sniffed`` is ``sniff.detect_type`` of its first bytes;
    ``display_name`` is the cleaned file name, used only to choose between text readers.
    Raises ReadError("corrupt_file") for a zip or OLE2 file whose directory can't be read."""
    if sniffed in (sniff.PDF, sniff.PNG, sniff.JPEG, sniff.TIFF, sniff.HEIC):
        return sniffed
    if sniffed == sniff.TEXT:
        return _text_kind(source, display_name)
    if sniffed == sniff.ZIP:
        with _opened(source) as file:
            return _zip_kind(file)
    if sniffed == sniff.OLE2:
        with _opened(source) as file:
            return _ole2_kind(file)
    return UNSUPPORTED


def ending(display_name: str) -> str:
    """The file name's ending after its last dot, lower case; "" with none."""
    _, dot, after = display_name.strip().rpartition(".")
    return after.lower() if dot else ""


def json_depth(text: str) -> int:
    """How deeply the JSON in ``text`` nests its objects and arrays, without parsing it
    (brackets inside strings don't count), so a deep file is refused before ``json.loads``
    could recurse. O(n)."""
    depth = deepest = 0
    for token in _JSON_TOKENS.finditer(text):
        first = token.group()[0]
        if first in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif first in "]}":
            depth -= 1
    return deepest


def _text_kind(source: Source, display_name: str) -> str:
    kind = _TEXT_ENDINGS.get(ending(display_name), TXT)
    if kind != JSON:
        return kind
    data = source if isinstance(source, bytes) else _read_all(source)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return TXT  # its reader says it is not UTF-8
    if json_depth(text) > JSON_MAX_DEPTH:
        return JSON  # too deep: the JSON reader refuses it, never parsed here
    try:
        json.loads(text)
    except ValueError:  # JSONDecodeError is a ValueError
        return TXT
    return JSON


def _read_all(path: str | os.PathLike[str]) -> bytes:
    with open(path, "rb") as file:
        return file.read()


def _opened(source: Source) -> IO[bytes]:
    return io.BytesIO(source) if isinstance(source, bytes) else open(source, "rb")


def _zip_kind(file: IO[bytes]) -> str:
    if _too_many_entries(file):
        return ZIP
    kind = None
    try:
        with zipfile.ZipFile(file) as archive:
            kind = _zip_contents(archive)
    except _ZIP_DAMAGED:
        pass
    if kind is None:
        raise ReadError("corrupt_file")
    return kind


def _zip_contents(archive: zipfile.ZipFile) -> str:
    names = set(archive.namelist())  # the central directory only
    if _WORD_PART in names:
        return DOTX if _is_template(archive, names) else DOCX
    if _POWERPOINT_PART in names:
        return PPTX
    if _EXCEL_PART in names:
        return XLSX
    return ZIP


def _too_many_entries(file: IO[bytes]) -> bool:
    """Whether the zip has more than ZIP_MAX_ENTRIES entries, told before zipfile lists its
    central directory (which builds one object per entry): by the count its end record
    claims, and by its central directory entries' signatures, counted in 1 MB chunks and
    stopped past the cap, since a hostile end record can claim fewer than there are. A
    signature inside a part's data can only count one too many, never one too few.
    O(n) in the file size, in C."""
    entries = _zip_entries(file)
    if entries is not None and entries > ZIP_MAX_ENTRIES:
        return True
    file.seek(0)
    found = 0
    carry = b""  # the last 3 bytes, for a signature cut across two chunks
    while chunk := file.read(_CHUNK):
        window = carry + chunk
        found += window.count(_CENTRAL_ENTRY)
        if found > ZIP_MAX_ENTRIES:
            return True
        carry = window[-3:]
    return False


def _zip_entries(file: IO[bytes]) -> int | None:
    """The entry count the zip's end record gives (its Zip64 one's when it has one), read
    before the central directory is listed; None when there is no readable end record
    (zipfile then refuses the file)."""
    size = file.seek(0, io.SEEK_END)
    file.seek(max(0, size - _END_SEARCH))
    tail = file.read()
    at = tail.rfind(_END_OF_DIRECTORY)
    if at < 0 or len(tail) - at < 22:
        return None
    (entries,) = struct.unpack_from("<H", tail, at + 10)
    if entries != 0xFFFF or at < 20 or tail[at - 20 : at - 16] != _ZIP64_LOCATOR:
        return int(entries)
    (zip64_end,) = struct.unpack_from("<Q", tail, at - 12)
    file.seek(zip64_end)
    record = file.read(56)
    if len(record) < 56:
        return None
    (entries64,) = struct.unpack_from("<Q", record, 32)
    return int(entries64)


def _is_template(archive: zipfile.ZipFile, names: set[str]) -> bool:
    """Whether ``[Content_Types].xml`` says the Word part is a template's. At most
    CONTENT_TYPES_MAX_BYTES are inflated, whatever its header says; a larger one is
    not a template."""
    if _CONTENT_TYPES not in names:
        return False
    with archive.open(_CONTENT_TYPES) as part:
        data = part.read(CONTENT_TYPES_MAX_BYTES + 1)
    return len(data) <= CONTENT_TYPES_MAX_BYTES and _DOTX_TYPE in data


def _ole2_kind(file: IO[bytes]) -> str:
    names = _Compound(file).root_streams()
    for name, kind in _ROOT_STREAMS:
        if name in names:
            return kind
    return UNSUPPORTED


class _Compound:
    """An OLE2 compound file's directory, read with a cap on every chain: each sector is
    visited at most once, so a loop or a chain longer than the file is refused."""

    def __init__(self, file: IO[bytes]) -> None:
        self._file = file
        self._size = file.seek(0, io.SEEK_END)
        header = self._read(0, _HEADER_SIZE)
        major, byte_order, shift = struct.unpack_from("<HHH", header, 0x1A)
        if major not in (3, 4) or byte_order != 0xFFFE or shift not in (9, 12):
            raise ReadError("corrupt_file")
        self._sector = 1 << shift
        self._fat = self._read_fat(header)
        (self._directory_start,) = struct.unpack_from("<I", header, 0x30)

    def root_streams(self) -> set[str]:
        """The names of the streams directly in the root storage."""
        directory = b"".join(self._read_sector(n) for n in self._chain(self._directory_start))
        count = len(directory) // _ENTRY_SIZE
        if count == 0 or directory[0x42] != 5:  # entry 0 must be the root
            raise ReadError("corrupt_file")
        names: set[str] = set()
        (child,) = struct.unpack_from("<I", directory, 0x4C)
        stack, seen = [child], set()
        while stack:  # the root's children are one red-black tree, by sibling links
            entry = stack.pop()
            if entry == _NO_STREAM:
                continue
            if entry >= count or entry in seen:
                raise ReadError("corrupt_file")
            seen.add(entry)
            at = entry * _ENTRY_SIZE
            length, kind = struct.unpack_from("<HB", directory, at + 0x40)
            left, right = struct.unpack_from("<II", directory, at + 0x44)
            if kind == _STREAM and 2 <= length <= 64 and length % 2 == 0:
                names.add(directory[at : at + length - 2].decode("utf-16-le", errors="replace"))
            stack += (left, right)
        return names

    def _read_fat(self, header: bytes) -> list[int]:
        """The FAT: every sector's next sector. Its own sectors are listed in the header's
        DIFAT, then in the DIFAT sector chain."""
        (fat_sectors,) = struct.unpack_from("<I", header, 0x2C)
        difat_start, difat_count = struct.unpack_from("<II", header, 0x44)
        listed = list(struct.unpack_from(f"<{_HEADER_DIFAT}I", header, 0x4C))
        per_sector = self._sector // 4
        seen: set[int] = set()
        sector = difat_start
        for _ in range(difat_count):
            if sector > _MAX_SECTOR or sector in seen:
                raise ReadError("corrupt_file")
            seen.add(sector)
            numbers = struct.unpack(f"<{per_sector}I", self._read_sector(sector))
            listed += numbers[:-1]
            sector = numbers[-1]
        listed = listed[:fat_sectors]
        if len(listed) != fat_sectors or len(set(listed)) != fat_sectors:
            raise ReadError("corrupt_file")
        fat: list[int] = []
        for number in listed:
            fat += struct.unpack(f"<{per_sector}I", self._read_sector(number))
        return fat

    def _chain(self, start: int) -> list[int]:
        chain: list[int] = []
        seen: set[int] = set()
        sector = start
        while sector != _END_OF_CHAIN:
            if sector > _MAX_SECTOR or sector >= len(self._fat) or sector in seen:
                raise ReadError("corrupt_file")
            seen.add(sector)
            chain.append(sector)
            sector = self._fat[sector]
        return chain

    def _read_sector(self, number: int) -> bytes:
        if number > _MAX_SECTOR:
            raise ReadError("corrupt_file")
        return self._read((number + 1) * self._sector, self._sector)

    def _read(self, offset: int, size: int) -> bytes:
        if offset + size > self._size:
            raise ReadError("corrupt_file")  # past the end: never padded
        self._file.seek(offset)
        data = self._file.read(size)
        if len(data) != size:
            raise ReadError("corrupt_file")
        return data
