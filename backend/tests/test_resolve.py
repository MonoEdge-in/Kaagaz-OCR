"""The file kind after the virus scan (#522, ``kaagaz/ingestion/resolve.py``): a zip's parts, an
old Office file's root streams, and a text file's name, each read as little as it can be."""

import struct
from collections.abc import Callable
from pathlib import Path

import pytest

from kaagaz.ingestion import resolve, sniff
from kaagaz.ingestion.resolve import UNSUPPORTED, json_depth, resolve_kind
from kaagaz.reading.spans import ReadError
from tests.xlsx_builder import make_xlsx, make_zip, sheet_xml

_DOCX_TYPES = b'<Types><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'  # noqa: E501
_DOTX_TYPES = _DOCX_TYPES.replace(b".document.main+xml", b".template.main+xml")
_FREE, _END, _FAT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD


def _kind(data: bytes, name: str = "file") -> str:
    return resolve_kind(data, sniff.detect_type(data[:4096]), name)


# --- old Office files (OLE2) -----------------------------------------------------------------------


def _entry(name: str, kind: int, *, right: int = _FREE, child: int = _FREE) -> bytes:
    encoded = name.encode("utf-16-le") + b"\x00\x00"
    entry = bytearray(128)
    entry[: len(encoded)] = encoded
    struct.pack_into("<HBBIII", entry, 0x40, len(encoded), kind, 1, _FREE, right, child)
    struct.pack_into("<I", entry, 0x74, _END)
    return bytes(entry)


def compound(root: list[str], storages: dict[str, list[str]] | None = None, *, shift: int = 9) -> bytes:
    """A compound file whose root holds the streams ``root`` and the storages ``storages`` (each
    with its own streams), its directory over as many sectors as it needs, one FAT sector."""
    size = 1 << shift
    entries = [b""]  # the root, written last
    tops: list[int] = []
    for name in root:
        tops.append(len(entries))
        entries.append(_entry(name, 2))
    for name, streams in (storages or {}).items():
        first = len(entries) + 1
        inner = [
            _entry(stream, 2, right=first + i + 1 if i + 1 < len(streams) else _FREE)
            for i, stream in enumerate(streams)
        ]
        tops.append(len(entries))
        entries.append(_entry(name, 1, child=first if streams else _FREE))
        entries += inner
    # The root's children as one chain of right siblings.
    for position, at in enumerate(tops):
        right = tops[position + 1] if position + 1 < len(tops) else _FREE
        raw = bytearray(entries[at])
        struct.pack_into("<I", raw, 0x48, right)
        entries[at] = bytes(raw)
    entries[0] = _entry("Root Entry", 5, child=tops[0] if tops else _FREE)
    per_sector = size // 128
    directory = b"".join(entries)
    directory += bytes(-len(directory) % size)
    sectors = len(directory) // size
    fat = [_FAT] + [n + 2 for n in range(sectors - 1)] + [_END]
    fat_sector = struct.pack(f"<{size // 4}I", *(fat + [_FREE] * (size // 4 - len(fat))))
    header = bytearray(size if shift == 12 else 512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", header, 0x18, 0x3E, 3 if shift == 9 else 4, 0xFFFE, shift, 6)
    struct.pack_into("<III", header, 0x2C, 1, 1, 0)  # one FAT sector; the directory at sector 1
    struct.pack_into("<IIIII", header, 0x38, 4096, _END, 0, _END, 0)
    struct.pack_into("<109I", header, 0x4C, 0, *([_FREE] * 108))
    assert per_sector >= 4
    return bytes(header) + fat_sector + directory


@pytest.mark.parametrize(
    ("streams", "kind"),
    [
        (["WordDocument", "1Table", "\x05SummaryInformation"], resolve.DOC),
        (["Workbook", "\x05SummaryInformation"], resolve.XLS),
        (["Book"], resolve.XLS),  # Excel 5 and 95
        (["PowerPoint Document", "Current User"], resolve.PPT),
    ],
)
def test_an_old_office_file_is_told_by_its_root_streams(streams: list[str], kind: str) -> None:
    assert _kind(compound(streams), "x.bin") == kind
    assert _kind(compound(streams, shift=12)) == kind  # version 4: 4096-byte sectors


def test_a_word_file_holding_a_spreadsheet_is_word_and_a_spreadsheet_holding_one_is_a_spreadsheet() -> None:
    # An embedded object is a storage of its own: only the root's streams count.
    assert _kind(compound(["1Table", "WordDocument"], {"ObjectPool": ["Workbook"]})) == resolve.DOC
    assert _kind(compound(["Workbook"], {"MBD0001": ["WordDocument"]})) == resolve.XLS


def test_an_old_office_file_with_none_of_the_streams_is_unsupported() -> None:
    assert _kind(compound(["__substg1.0_0037001F"])) == UNSUPPORTED  # an Outlook message
    assert _kind(compound([], {"WordDocument": []})) == UNSUPPORTED  # a storage, not a stream
    assert _kind(compound([])) == UNSUPPORTED


def test_many_directory_sectors_are_followed() -> None:
    filler = [f"Stream{n}" for n in range(30)]  # 31 entries with the root: 8 sectors
    assert _kind(compound([*filler, "WordDocument"])) == resolve.DOC


@pytest.mark.parametrize(
    "damage",
    [
        lambda data: data[:300],  # the header cut short
        lambda data: data[:600],  # the directory past the end
        lambda data: data[:0x1A] + b"\x07\x00" + data[0x1C:],  # an unknown version
        lambda data: data[:512] + struct.pack("<II", _FAT, 1) + data[520:],  # the directory loops
        lambda data: data[:512] + struct.pack("<II", _FAT, 99) + data[520:],  # past the FAT
        lambda data: data[:0x2C] + struct.pack("<I", 2) + data[0x30:],  # FAT sectors not listed
    ],
)
def test_a_damaged_old_office_file_is_corrupt(damage: Callable[[bytes], bytes]) -> None:
    data = damage(compound(["WordDocument"]))
    with pytest.raises(ReadError) as refused:
        resolve_kind(data, sniff.OLE2, "a.doc")
    assert refused.value.code == "corrupt_file"


def test_a_root_whose_children_loop_is_corrupt() -> None:
    data = bytearray(compound(["Stream", "WordDocument"]))
    struct.pack_into("<I", data, 1024 + 128 * 2 + 0x48, 1)  # the second child points back
    with pytest.raises(ReadError):
        resolve_kind(bytes(data), sniff.OLE2, "a.doc")


# --- zip files --------------------------------------------------------------------------------------


def test_zip_parts_tell_word_powerpoint_excel_or_a_plain_zip() -> None:
    assert _kind(make_zip({"[Content_Types].xml": _DOCX_TYPES, "word/document.xml": b"<w/>"})) == resolve.DOCX
    assert _kind(make_zip({"[Content_Types].xml": _DOTX_TYPES, "word/document.xml": b"<w/>"})) == resolve.DOTX
    assert _kind(make_zip({"ppt/presentation.xml": b"<p/>"})) == resolve.PPTX
    assert _kind(make_xlsx([("Sheet1", sheet_xml(""))]), "a.docx") == resolve.XLSX
    assert _kind(make_zip({"a.pdf": b"%PDF-1.4", "folder/b.txt": b"hi"}), "a.xlsx") == resolve.ZIP
    assert _kind(make_zip({"Word/Document.xml": b"<w/>"})) == resolve.ZIP  # names are exact


def test_a_word_file_with_no_content_types_or_a_huge_one_is_not_a_template() -> None:
    assert _kind(make_zip({"word/document.xml": b"<w/>"})) == resolve.DOCX
    huge = _DOTX_TYPES + b" " * resolve.CONTENT_TYPES_MAX_BYTES  # inflated past the cap: not read
    assert _kind(make_zip({"[Content_Types].xml": huge, "word/document.xml": b"<w/>"})) == resolve.DOCX


def _zip64(parts: dict[str, bytes], entries: int) -> bytes:
    """``parts`` as a zip whose end records are Zip64's, claiming ``entries`` entries."""
    data = make_zip(parts)
    at = data.rfind(b"PK\x05\x06")
    end = data[at:]
    directory_size, directory_at = struct.unpack_from("<II", end, 12)
    record = struct.pack(
        "<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, entries, entries, directory_size, directory_at
    )
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, at, 1)
    return data[:at] + record + locator + end[:8] + b"\xff\xff\xff\xff" + end[12:]


def test_a_zip_claiming_more_entries_than_the_cap_is_a_zip_never_listed() -> None:
    word = {"word/document.xml": b"<w/>"}
    data = make_zip(word)
    at = data.rfind(b"PK\x05\x06")
    claims = data[: at + 8] + struct.pack("<HH", 60_000, 60_000) + data[at + 12 :]
    assert _kind(claims) == resolve.ZIP
    assert _kind(_zip64(word, 1)) == resolve.DOCX  # Zip64's own count read, then listed
    assert _kind(_zip64(word, resolve.ZIP_MAX_ENTRIES + 1)) == resolve.ZIP


@pytest.mark.parametrize(
    "data",
    [
        b"PK\x03\x04" + b"\x00" * 200,  # no central directory
        make_zip({"word/document.xml": b"<w/>"})[:-10],  # the end record cut short
    ],
)
def test_a_damaged_zip_is_corrupt(data: bytes) -> None:
    with pytest.raises(ReadError) as refused:
        resolve_kind(data, sniff.ZIP, "a.docx")
    assert refused.value.code == "corrupt_file"


def test_a_damaged_content_types_part_is_corrupt() -> None:
    data = bytearray(make_zip({"[Content_Types].xml": _DOTX_TYPES * 50, "word/document.xml": b"<w/>"}))
    start = data.find(b"[Content_Types].xml") + len("[Content_Types].xml")
    data[start + 5 : start + 25] = b"\xff" * 20  # inside its deflate stream
    with pytest.raises(ReadError):
        resolve_kind(bytes(data), sniff.ZIP, "a.docx")


# --- text files ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("register.csv", resolve.CSV),
        ("REGISTER.CSV", resolve.CSV),
        ("notes.txt", resolve.TXT),
        ("readme.md", resolve.MD),
        ("page.html", resolve.HTML),
        ("page.HTM", resolve.HTML),
        ("server.log", resolve.TXT),
        ("no ending", resolve.TXT),
        ("trailing dot.", resolve.TXT),
        ("a.csv.txt", resolve.TXT),  # the last ending
    ],
)
def test_a_text_files_name_chooses_its_reader(name: str, kind: str) -> None:
    assert _kind(b"some text\n", name) == kind


def test_the_name_wins_over_what_the_text_looks_like() -> None:
    assert _kind(b"invoice,amount\nINV-1,100\n", "register.txt") == resolve.TXT
    assert _kind(b"<html><body><script>x()</script>Hi</body></html>", "export.csv") == resolve.CSV
    assert _kind(b'{"a": 1}', "data.txt") == resolve.TXT


def test_a_json_file_must_be_json_or_it_is_read_as_text() -> None:
    assert _kind(b'{"invoice": "INV-1", "amount": [100, 250]}', "a.json") == resolve.JSON
    assert _kind(b"\xef\xbb\xbf[1, 2]", "a.json") == resolve.JSON  # a byte order mark is allowed
    assert _kind(b'{"invoice": "INV-1",', "a.json") == resolve.TXT
    assert _kind(b"just words", "a.JSON") == resolve.TXT
    assert resolve_kind(b"caf\xe9", sniff.TEXT, "a.json") == resolve.TXT  # not UTF-8: its reader says so


def test_json_nested_past_the_cap_stays_json_for_its_reader_to_refuse() -> None:
    at_cap = b"[" * resolve.JSON_MAX_DEPTH + b"]" * resolve.JSON_MAX_DEPTH
    assert _kind(at_cap, "a.json") == resolve.JSON
    too_deep = b"[" * 100_000 + b"]" * 100_000  # deeper than json.loads could recurse: never parsed
    assert _kind(too_deep, "a.json") == resolve.JSON
    assert _kind(b"[" * 101, "a.json") == resolve.JSON  # deep and broken: refused as too deep too


def test_json_depth_counts_brackets_outside_strings_only() -> None:
    assert json_depth('{"a": "[[[{{{", "b": [[1]], "c": "\\"]]"}') == 3
    assert json_depth("[]") == 1
    assert json_depth("3") == 0
    assert json_depth('"never closed [[[[') == 0


def test_a_path_is_read_like_bytes(tmp_path: Path) -> None:
    sheet = tmp_path / "upload"
    sheet.write_bytes(make_zip({"xl/workbook.xml": b"<w/>"}))
    assert resolve_kind(sheet, sniff.ZIP, "x") == resolve.XLSX
    old = tmp_path / "old"
    old.write_bytes(compound(["PowerPoint Document"]))
    assert resolve_kind(str(old), sniff.OLE2, "x") == resolve.PPT
    data = tmp_path / "data"
    data.write_bytes(b'{"a": 1}')
    assert resolve_kind(data, sniff.TEXT, "data.json") == resolve.JSON


# --- the rest ------------------------------------------------------------------------------------------


def test_a_pdf_photo_or_scan_is_its_sniffed_type_whatever_its_name() -> None:
    for sniffed in (sniff.PDF, sniff.PNG, sniff.JPEG, sniff.TIFF, sniff.HEIC):
        assert resolve_kind(b"anything", sniffed, "a.csv") == sniffed
        assert sniffed in resolve.KINDS


def test_no_sniffed_type_is_unsupported_and_never_a_kind() -> None:
    assert resolve_kind(b"\x7fELF", None, "a.txt") == UNSUPPORTED
    assert resolve_kind(b"x", "exe", "a.txt") == UNSUPPORTED
    assert UNSUPPORTED not in resolve.KINDS


def test_every_kind_resolve_can_tell_is_named_in_kinds() -> None:
    assert resolve.TEXT_KINDS <= resolve.KINDS
    assert len(resolve.KINDS) == 18
