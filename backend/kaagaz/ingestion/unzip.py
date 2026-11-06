"""What a zip upload holds and each of its files' bytes, never trusting what the zip says about
itself (#522 PR 3, Tushar, 6 Nov 2026). Only the standard library's ``zipfile``. Nothing is
written to disk and no name from the zip is ever used as a path: each becomes a folder or
document name by the folder upload's own rule (``names.upload_name``). The worker
(``kaagaz/jobs/unpack.py``) runs this after the zip's own virus check, and checks every file
again on its own before it is stored.

* ``open_zip`` refuses the whole zip (``ZipRefused``) with more than MAX_ENTRIES entries,
  counted by the central directory's own signatures before ``zipfile`` lists a single one (a
  count in the end record can lie), or whose entries overlap one another (a "zip bomb" that
  reuses one packed stream for many files) or run past the zip's end, or that can't be listed.
* ``list_zip`` sorts every entry, from the central directory alone:
  - left out unnoticed (``junk``): ``__MACOSX/`` and ``.DS_Store``, a Mac's own files;
  - a folder: an entry ending in "/", and every folder a file is in, made even when empty;
  - refused, with why (``Refused.code``, ``folder_upload_items.failure_code``, 0080):
    ``unsafe_path`` (an absolute path, ``..``, an empty part, a drive letter, a backslash or a
    NUL), ``link`` (a symbolic link or any other special file, by its Unix mode, or a Windows
    reparse point), ``encrypted``, ``bad_name`` (a folder or file name ``upload_name``
    refuses), ``too_deep`` (a folder past ``depth_left`` levels under the zip's own folder) and
    ``too_compressed`` (it says it unpacks to more than MAX_RATIO times its packed size);
  - else a file to unpack (``Entry``).
* ``read_file`` unpacks one file, counting the bytes as they come and stopping one byte past
  the least of: its kind's limit (``limit_for`` of the type its first bytes sniff as; a type
  with no limit stops at once, ``unsupported_type``), MAX_RATIO times its packed size and the
  zip's ``budget`` left; and it reads to the end, so ``zipfile`` checks its CRC. A file that
  unpacks to other than the size it says, or can't be unpacked, is ``damaged``.

The ratio (Tushar, 6 Nov 2026: 100:1 at most, a file and the whole zip): a file of at most
RATIO_FLOOR (1 MB) bytes is never too compressed (a blank page or a small text file packs far
better than 100:1, and no bomb is that small), and neither is a zip that unpacks to at most
RATIO_FLOOR. This exemption is Tushar's, approved on 6 Nov 2026.

Time: ``open_zip`` O(n) for an n-byte zip plus O(e log e) for its e entries; ``list_zip``
O(e) and O(total name length); ``read_file`` O(its bytes), memory O(its limit).
"""

import contextlib
import hashlib
import io
import lzma
import re
import stat
import struct
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field

from kaagaz.api.names import DOCUMENT_NAME_MAX, FOLDER_NAME_MAX, shown_upload_name, upload_name
from kaagaz.ingestion.sniff import detect_type
from kaagaz.ingestion.spool import HEAD_SIZE

MAX_ENTRIES = 50_000  # as resolve.ZIP_MAX_ENTRIES
MAX_RATIO = 100
RATIO_FLOOR = 1024 * 1024
CHUNK = 64 * 1024

# Why a file inside a zip is not unpacked: folder_upload_items.failure_code (0061c, 0080).
UNSAFE_PATH = "unsafe_path"
LINK = "link"
ENCRYPTED = "encrypted"
BAD_NAME = "bad_name"
TOO_DEEP = "too_deep"
TOO_COMPRESSED = "too_compressed"
TOO_LARGE = "too_large"
UNSUPPORTED_TYPE = "unsupported_type"
EMPTY_FILE = "empty_file"
DAMAGED = "damaged"

# Why a file of a zip was not unpacked, in plain words (the zip's document lists them): every
# code an item of an unpacked zip can fail with (0061c's and 0080's).
REASONS = {
    UNSAFE_PATH: "Its path in the zip is unsafe: it points outside the zip.",
    LINK: "It is a link to another file, not a file.",
    ENCRYPTED: "It has a password. Remove the password, then upload it again.",
    BAD_NAME: "Its name holds a character Kaagaz can't keep, or another file in its folder has that name.",
    TOO_DEEP: "It is more than 100 folders deep.",
    TOO_COMPRESSED: "It unpacks to more than 100 times its packed size.",
    TOO_LARGE: "It is too large for its kind of file.",
    UNSUPPORTED_TYPE: "Kaagaz doesn't take this kind of file.",
    EMPTY_FILE: "It is empty.",
    DAMAGED: "It is damaged and can't be unpacked.",
    "virus_found": "It failed the virus check.",
    "blocked_content": "It contains scripts, macros or a hidden program.",
    "unscannable": "It could not be checked for viruses.",
    "storage_full": "Your organisation's storage is full.",
    "folder_gone": "Its folder was deleted while the zip was unpacked.",
}
# The same, in a few words, for the zip's one line ("3 skipped (2 password-protected, ...)").
SHORT_REASONS = {
    UNSAFE_PATH: "unsafe path",
    LINK: "link",
    ENCRYPTED: "password-protected",
    BAD_NAME: "name not allowed",
    TOO_DEEP: "too deep",
    TOO_COMPRESSED: "too compressed",
    TOO_LARGE: "too large",
    UNSUPPORTED_TYPE: "kind not taken",
    EMPTY_FILE: "empty",
    DAMAGED: "damaged",
    "virus_found": "failed the virus check",
    "blocked_content": "scripts or macros",
    "unscannable": "couldn't be checked",
    "storage_full": "storage full",
    "folder_gone": "folder deleted",
}
# Why a whole zip was not unpacked: its document's failure code (kaagaz/jobs/unpack.py).
ZIP_FAILURES = {
    "zip_too_many_files": "This zip holds more files than a folder upload may: 50,000 at most, or your"
    " organisation's lower limit. Split it into smaller zips and upload each.",
    "zip_too_many_folders": "This zip holds more folders than your organisation's folder upload limit."
    " Split it into smaller zips and upload each.",
    "zip_over_limit": "Zip files can be up to 1 GB. Unzip it on your computer and upload the folder instead.",
    "zip_too_large": "This zip unpacks to more than your organisation's folder upload limit. Split it into"
    " smaller zips and upload each.",
    "zip_too_compressed": "This zip unpacks to more than 100 times its own size, as a zip bomb does, so it"
    " was not unpacked.",
    "zip_damaged": "This zip is damaged and can't be unpacked. Make it again and upload that copy.",
    "storage_full": "Unpacking this zip would take your organisation past its storage limit. Delete files"
    " forever from the Trash to free space, or ask Kaagaz to raise the limit.",
    "folder_too_deep": "This zip's folder would be more than 100 levels deep here. Upload it into a folder"
    " higher up.",
    "folder_gone": "The folder this zip was uploaded to was deleted before it was unpacked.",
    "folder_name_taken": "There are already 100 folders named like this zip here. Rename the zip and upload"
    " it again.",
}

_CENTRAL_ENTRY = b"PK\x01\x02"  # every central directory entry starts with it
_LOCAL_HEADER = 30  # a local file header's fixed part, before its name
_ENCRYPTED = 0x1  # general purpose flag bit 0
_UTF8 = 0x800  # general purpose flag bit 11: the name is UTF-8
_REPARSE_POINT = 0x400  # a Windows link (FILE_ATTRIBUTE_REPARSE_POINT)
_DRIVE = re.compile(r"^[A-Za-z]:")
_MAC_FOLDER, _MAC_FILE = "__MACOSX", ".DS_Store"
# What zipfile and its decompressors raise for a part they can't unpack.
_DAMAGED = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    OSError,
    EOFError,
    ValueError,
    NotImplementedError,
    RuntimeError,
    zlib.error,
    lzma.LZMAError,
    struct.error,
)


class ZipRefused(Exception):
    """The whole zip is refused before anything is made. ``code``: too_many_files or damaged."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FileRefused(Exception):
    """One file is not unpacked; ``code`` says why (module notes)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Entry:
    """A file to unpack."""

    info: zipfile.ZipInfo
    folder: tuple[str, ...]  # its folder's names under the zip's own folder; () is that folder
    name: str  # cleaned by upload_name
    key: str  # SHA-256 of its place in the zip: unique, the same every time


@dataclass(frozen=True, slots=True)
class Refused:
    """A file refused from the listing alone."""

    name: str  # as it can be shown (shown_upload_name)
    size: int  # as the zip says
    code: str
    key: str


@dataclass(slots=True)
class Listing:
    files: list[Entry] = field(default_factory=list)
    refused: list[Refused] = field(default_factory=list)
    folders: list[tuple[str, ...]] = field(default_factory=list)  # under the zip's own, parents first
    junk: int = 0  # __MACOSX/ and .DS_Store files, left out

    @property
    def declared_bytes(self) -> int:
        """What the files to unpack say they unpack to."""
        return sum(entry.info.file_size for entry in self.files)


def open_zip(data: bytes) -> zipfile.ZipFile:
    """The zip in ``data``, listed; ZipRefused as the module notes say. The caller closes it."""
    if data.count(_CENTRAL_ENTRY) > MAX_ENTRIES:  # at least one per entry, whatever it claims
        raise ZipRefused("too_many_files")
    archive = None
    with contextlib.suppress(*_DAMAGED):
        archive = zipfile.ZipFile(io.BytesIO(data))
    if archive is None:
        raise ZipRefused("damaged")  # outside the except block: zipfile's words are never chained
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES or _overlaps(infos, len(data)):
        archive.close()
        raise ZipRefused("too_many_files" if len(infos) > MAX_ENTRIES else "damaged")
    return archive


def _overlaps(infos: list[zipfile.ZipInfo], size: int) -> bool:
    """Whether two entries' local header and packed bytes share a byte, or one runs past the
    zip's end. Each span is a lower bound (a local extra field or a data descriptor only makes
    it longer), so a real zip always passes."""
    spans = sorted(
        (info.header_offset, info.header_offset + _LOCAL_HEADER + len(_raw_name(info)) + info.compress_size)
        for info in infos
    )
    end = 0
    for start, stop in spans:
        if start < end or stop > size:
            return True
        end = stop
    return False


def _raw_name(info: zipfile.ZipInfo) -> bytes:
    return info.orig_filename.encode("utf-8" if info.flag_bits & _UTF8 else "cp437", errors="replace")


def list_zip(archive: zipfile.ZipFile, *, depth_left: int) -> Listing:
    """Every entry of ``archive`` sorted (module notes). ``depth_left``: how many folder levels
    may go under the zip's own folder (MAX_DEPTH less that folder's depth)."""
    listing = Listing()
    folders: dict[tuple[str, ...], None] = {}  # in order, parents first

    def add_folder(names: tuple[str, ...]) -> None:
        for end in range(1, len(names) + 1):
            folders.setdefault(names[:end])

    for index, info in enumerate(archive.infolist()):
        raw = info.orig_filename
        is_folder = raw.endswith("/")
        parts = _parts(raw)
        if parts is not None and (parts[0] == _MAC_FOLDER or (not is_folder and parts[-1] == _MAC_FILE)):
            listing.junk += 0 if is_folder else 1
            continue
        if is_folder:
            names = None if parts is None or _is_link(info) else _folder_names(parts)
            if names is not None and len(names) <= depth_left:
                add_folder(names)
            continue  # a folder Kaagaz can't make holds no file it unpacks: each is refused itself
        key = hashlib.sha256(f"{index}/{raw}".encode(errors="replace")).hexdigest()
        folder = None if parts is None else _folder_names(parts[:-1])
        name = None if parts is None else upload_name(parts[-1], max_length=DOCUMENT_NAME_MAX)
        code = _refusal(info, parts, folder, name, depth_left)
        if code is not None or folder is None or name is None:
            shown = shown_upload_name(re.split(r"[/\\]", raw.rstrip("/\\"))[-1], max_length=DOCUMENT_NAME_MAX)
            listing.refused.append(Refused(shown, info.file_size, code or BAD_NAME, key))
            continue
        add_folder(folder)
        listing.files.append(Entry(info, folder, name, key))
    listing.folders = list(folders)
    return listing


def _refusal(
    info: zipfile.ZipInfo,
    parts: list[str] | None,
    folder: tuple[str, ...] | None,
    name: str | None,
    depth_left: int,
) -> str | None:
    """Why the file ``info`` is refused from the listing, the first reason in this order."""
    if parts is None:
        return UNSAFE_PATH
    if _is_link(info):
        return LINK
    if info.flag_bits & _ENCRYPTED:
        return ENCRYPTED
    if folder is None or name is None:
        return BAD_NAME
    if len(folder) > depth_left:
        return TOO_DEEP
    if info.file_size > max(RATIO_FLOOR, MAX_RATIO * info.compress_size):
        return TOO_COMPRESSED
    return None


def _parts(raw: str) -> list[str] | None:
    """The names along the entry's path ("." parts dropped), or None when it is unsafe: empty,
    absolute, a drive letter, a backslash or NUL anywhere, an empty or ".." part."""
    if not raw or "\\" in raw or "\x00" in raw or raw.startswith("/") or _DRIVE.match(raw):
        return None
    body = raw[:-1] if raw.endswith("/") else raw
    parts = [part for part in body.split("/") if part != "."]
    if not parts or any(part in ("", "..") for part in parts):
        return None
    return parts


def _folder_names(parts: list[str]) -> tuple[str, ...] | None:
    """Each folder name cleaned by upload_name; None when one is refused."""
    names = tuple(upload_name(part, max_length=FOLDER_NAME_MAX) for part in parts)
    return None if any(name is None for name in names) else tuple(name for name in names if name)


def _is_link(info: zipfile.ZipInfo) -> bool:
    """A symbolic link or another special file (a device, a pipe, a socket) by the Unix mode in
    the external attributes, or a Windows reparse point. A plain file or folder is not."""
    kind = stat.S_IFMT(info.external_attr >> 16)
    return bool(kind and kind not in (stat.S_IFREG, stat.S_IFDIR)) or bool(
        info.external_attr & _REPARSE_POINT
    )


def read_file(
    archive: zipfile.ZipFile, entry: Entry, *, limit_for: Callable[[str | None], int | None], budget: int
) -> bytes:
    """The file's bytes, unpacked with every limit checked as they come (module notes); raises
    FileRefused (too_large, too_compressed, unsupported_type, empty_file or damaged)."""
    info = entry.info
    most = min(max(RATIO_FLOOR, MAX_RATIO * info.compress_size), budget)  # past it: too compressed
    limit: int | None = None  # the kind's, once the first bytes are in
    data = bytearray()
    failed = False
    try:
        with archive.open(info) as stream:
            while True:
                cap = most if limit is None else min(most, limit)
                want = HEAD_SIZE - len(data) if limit is None else min(CHUNK, cap + 1 - len(data))
                chunk = stream.read(want)
                data += chunk
                if limit is None and (len(data) >= HEAD_SIZE or not chunk):
                    limit = limit_for(detect_type(bytes(data[:HEAD_SIZE])))
                    if limit is None:
                        raise FileRefused(UNSUPPORTED_TYPE if data else EMPTY_FILE)
                if limit is not None and len(data) > limit:
                    raise FileRefused(TOO_LARGE)
                if len(data) > most:
                    raise FileRefused(TOO_COMPRESSED)
                if not chunk:
                    break
    except _DAMAGED:
        failed = True
    if failed or len(data) != info.file_size:
        raise FileRefused(DAMAGED)  # outside the except block: zipfile's words are never chained
    if not data:
        raise FileRefused(EMPTY_FILE)
    return bytes(data)
