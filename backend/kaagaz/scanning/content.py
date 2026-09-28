"""Our own content check, run before the virus scanner on every file.

A virus scanner knows signatures. This check knows what a business document
may contain, and refuses the rest, whether or not any signature matches:

* **PDF:** no active content. JavaScript, launch actions, embedded files,
  rich media, XFA forms, and form submit or import actions are all refused.
  Every object is walked, including those inside compressed object streams,
  so an obfuscated name (``/J#61vaScript``) is caught too.
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

Time and memory are O(n) in the file size, plus O(objects) for a PDF.
"""

import io
import re
import zipfile
from enum import Enum

from pdfminer.pdfdocument import PDFDocument, PDFEncryptionError, PDFPasswordIncorrect
from pdfminer.pdfparser import PDFParser, PDFSyntaxError
from pdfminer.pdftypes import PDFObjRef, PDFStream
from pdfminer.psparser import PSLiteral

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
_PDF_ACTIVE_KEYS = frozenset({"JavaScript", "JS", "Launch", "EmbeddedFile", "EmbeddedFiles", "EF", "RichMedia", "XFA"})
_PDF_ACTIVE_ACTIONS = frozenset({"JavaScript", "Launch", "GoToE", "SubmitForm", "ImportData", "RichMediaExecute"})
_MAX_PDF_DEPTH = 64


def _pdf_finding(data: bytes) -> Finding | None:
    try:
        document = PDFDocument(PDFParser(io.BytesIO(data)))
    except (PDFSyntaxError, PDFEncryptionError, PDFPasswordIncorrect):
        return Finding.UNSCANNABLE
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        # pdfminer raises plain errors on badly broken files.
        return Finding.UNSCANNABLE
    for xref in document.xrefs:
        for object_id in xref.get_objids():
            try:
                obj = document.getobj(object_id)
            except (PDFSyntaxError, ValueError, TypeError, KeyError, IndexError, AttributeError):
                continue  # one unreadable object; the reader will judge the file
            if _is_active(obj, 0):
                return Finding.BLOCKED
    return None


def _is_active(obj: object, depth: int) -> bool:
    """True if this object, or anything directly inside it, is active content.

    References to other objects are not followed: every object is visited on
    its own by the caller, so each is checked once. O(size of the object).
    """
    if depth > _MAX_PDF_DEPTH:
        return False
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
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, ValueError, OSError):
        return Finding.UNSCANNABLE
    return None


# --- plain text --------------------------------------------------------------------

_BOM = "﻿"
_MARKUP_OR_SCRIPT_START = re.compile(r"^\s*(<|\{\\rtf|#!|@echo\b|::|rem\s)", re.IGNORECASE)
_SCRIPT_ANYWHERE = re.compile(r"<\s*script\b|javascript\s*:|vbscript\s*:|<\s*iframe\b|<\s*object\b", re.IGNORECASE)
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
