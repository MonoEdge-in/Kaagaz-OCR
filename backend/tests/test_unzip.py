"""What a zip upload holds and each file's bytes (#522 PR 3, kaagaz/ingestion/unzip.py): every
entry sorted from the central directory alone, every unsafe one refused with its code, and each
file unpacked with its limits checked as its bytes come, never trusting the zip's own sizes."""

import io
import zipfile
from typing import Any

import pytest

from kaagaz.ingestion import unzip
from kaagaz.ingestion.unzip import FileRefused, ZipRefused, list_zip, open_zip, read_file
from tests.zip_builder import (
    claim_entries,
    make_zip,
    make_zip_with,
    mark_encrypted,
    point_at,
    set_size,
    with_link,
)

MB = 1024 * 1024
DEEP = 99  # folder levels the tests let under the zip's own folder


def _listing(data: bytes, depth_left: int = DEEP) -> unzip.Listing:
    with open_zip(data) as archive:
        return list_zip(archive, depth_left=depth_left)


def _refused(listing: unzip.Listing) -> dict[str, str]:
    return {refused.name: refused.code for refused in listing.refused}


def _rows(size: int) -> bytes:
    """CSV rows of about ``size`` bytes that pack only a few times smaller."""
    rows, total, n = [], 0, 0
    while total < size:
        row = f"{n},{n * 7919 % 104729},{n * 31337 % 9973}\n".encode()
        rows.append(row)
        total, n = total + len(row), n + 1
    return b"".join(rows)


def _any_limit(_sniffed: str | None) -> int:
    return 10 * MB


def test_a_three_level_folder_keeps_its_structure_and_its_empty_folders() -> None:
    data = make_zip(
        [("Top/a.csv", b"a,b\n1,2\n"), ("Top/Mid/b.csv", b"c\n3\n"), ("Top/Mid/Low/c.txt", b"hello")],
        folders=["Top/Empty/"],
    )
    listing = _listing(data)
    assert listing.folders == [("Top",), ("Top", "Empty"), ("Top", "Mid"), ("Top", "Mid", "Low")]  # zip order
    assert [(entry.folder, entry.name) for entry in listing.files] == [
        (("Top",), "a.csv"),
        (("Top", "Mid"), "b.csv"),
        (("Top", "Mid", "Low"), "c.txt"),
    ]
    assert listing.refused == [] and listing.junk == 0
    assert listing.declared_bytes == len(b"a,b\n1,2\n") + len(b"c\n3\n") + len(b"hello")
    assert len({entry.key for entry in listing.files}) == 3


@pytest.mark.parametrize(
    "path",
    [
        "../evil.csv",
        "a/../../evil.csv",
        "/etc/evil.csv",
        "C:/evil.csv",
        "c:evil.csv",
        "a\\..\\evil.csv",
        "a//b.csv",
    ],
)
def test_an_unsafe_path_is_refused_and_the_rest_unpacked(path: str) -> None:
    listing = _listing(make_zip([(path, b"bad"), ("good.csv", b"a\n1\n")]))
    assert [entry.name for entry in listing.files] == ["good.csv"]
    assert [refused.code for refused in listing.refused] == [unzip.UNSAFE_PATH]


def test_a_symbolic_link_is_refused_by_its_mode_whatever_its_name() -> None:
    link, target = with_link("statement.pdf")
    listing = _listing(make_zip_with([(link, target), ("good.csv", b"a\n1\n")]))
    assert _refused(listing) == {"statement.pdf": unzip.LINK}
    assert [entry.name for entry in listing.files] == ["good.csv"]


def test_a_windows_reparse_point_is_a_link_too() -> None:
    info = zipfile.ZipInfo("shortcut.csv")
    info.external_attr = 0x400
    assert _refused(_listing(make_zip_with([(info, b"x")]))) == {"shortcut.csv": unzip.LINK}


def test_a_file_with_a_password_is_refused_in_plain_words() -> None:
    data = mark_encrypted(make_zip([("secret.csv", b"a\n1\n"), ("open.csv", b"b\n2\n")]), "secret.csv")
    listing = _listing(data)
    assert _refused(listing) == {"secret.csv": unzip.ENCRYPTED}
    assert unzip.REASONS[unzip.ENCRYPTED] == "It has a password. Remove the password, then upload it again."
    assert [entry.name for entry in listing.files] == ["open.csv"]


def test_a_mac_s_own_files_are_left_out_unnoticed() -> None:
    data = make_zip([("__MACOSX/._a.csv", b"x"), ("Top/.DS_Store", b"x"), ("Top/a.csv", b"a\n1\n")])
    listing = _listing(data)
    assert ([entry.name for entry in listing.files], listing.refused, listing.junk) == (["a.csv"], [], 2)
    assert ("__MACOSX",) not in listing.folders


def test_a_name_the_folder_upload_refuses_is_refused_with_every_file_under_it() -> None:
    data = make_zip([("bad\u202efdp.csv", b"x"), ("ok\u200fdir/a.csv", b"y"), ("ok.csv", b"z")])
    listing = _listing(data)
    assert sorted(code for code in _refused(listing).values()) == [unzip.BAD_NAME, unzip.BAD_NAME]
    assert [entry.name for entry in listing.files] == ["ok.csv"]


def test_names_are_nfc_so_a_mac_s_folder_is_one_folder() -> None:
    listing = _listing(make_zip([("Cafe\u0301/a.csv", b"x"), ("Caf\u00e9/b.csv", b"y")]))
    assert listing.folders == [("Caf\u00e9",)]


def test_a_file_deeper_than_the_levels_left_is_refused_too_deep() -> None:
    listing = _listing(make_zip([("a/b/c.csv", b"x"), ("a/d.csv", b"y")]), depth_left=1)
    assert _refused(listing) == {"c.csv": unzip.TOO_DEEP}
    assert listing.folders == [("a",)]


def test_a_nested_zip_is_listed_as_an_ordinary_file() -> None:
    inner = make_zip([("x.csv", b"a\n1\n")])
    listing = _listing(make_zip([("inner.zip", inner)]))
    assert [entry.name for entry in listing.files] == ["inner.zip"]
    with open_zip(make_zip([("inner.zip", inner)])) as archive:
        assert read_file(archive, listing.files[0], limit_for=_any_limit, budget=10 * MB) == inner


def test_a_zip_bomb_entry_is_refused_from_its_header_before_a_byte_is_unpacked() -> None:
    bomb = b"\0" * (20 * MB)  # packs to about 20 KB: about 1,000 to 1
    listing = _listing(make_zip([("bomb.csv", bomb), ("ok.csv", b"a\n1\n")]))
    assert _refused(listing) == {"bomb.csv": unzip.TOO_COMPRESSED}


def test_a_small_file_is_never_too_compressed() -> None:
    blank = b" " * (unzip.RATIO_FLOOR // 2)  # packs far better than 100 to 1
    assert _refused(_listing(make_zip([("blank.txt", blank)]))) == {}


def test_a_header_that_lies_about_the_size_is_damaged_and_never_trusted() -> None:
    data = set_size(make_zip([("liar.csv", b"a,b\n" * 50_000)]), "liar.csv", 1000)
    with open_zip(data) as archive:
        (entry,) = list_zip(archive, depth_left=DEEP).files
        assert entry.info.file_size == 1000
        with pytest.raises(FileRefused) as refused:
            read_file(archive, entry, limit_for=_any_limit, budget=10 * MB)
    assert refused.value.code == unzip.DAMAGED


def test_unpacking_stops_at_the_kind_s_limit_whatever_the_header_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The header says 1 TB; reading stops one chunk past the 1 MB limit, never later."""
    data = make_zip([("big.csv", _rows(3 * MB))])
    read: list[int] = []
    with open_zip(data) as archive:
        (entry,) = list_zip(archive, depth_left=DEEP).files
        entry.info.file_size = 10**12
        opened = archive.open

        def counting(info: Any, *args: Any, **kwargs: Any) -> Any:
            stream = opened(info, *args, **kwargs)
            real = stream.read

            def read_some(n: int = -1) -> bytes:
                chunk = real(n)
                read.append(len(chunk))
                return chunk

            monkeypatch.setattr(stream, "read", read_some)
            return stream

        monkeypatch.setattr(archive, "open", counting)
        with pytest.raises(FileRefused) as refused:
            read_file(archive, entry, limit_for=lambda _sniffed: 1 * MB, budget=10**12)
    assert refused.value.code == unzip.TOO_LARGE
    assert sum(read) <= 1 * MB + unzip.CHUNK


def test_the_zip_s_budget_left_stops_a_file_as_too_compressed() -> None:
    data = make_zip([("a.csv", b"a,b\n" * 10_000)])
    with open_zip(data) as archive:
        (entry,) = list_zip(archive, depth_left=DEEP).files
        with pytest.raises(FileRefused) as refused:
            read_file(archive, entry, limit_for=_any_limit, budget=5000)
    assert refused.value.code == unzip.TOO_COMPRESSED


def test_a_type_with_no_limit_is_refused_before_the_rest_is_unpacked() -> None:
    data = make_zip([("program.csv", b"MZ\x90\x00" + b"\0" * 5000)])
    with open_zip(data) as archive:
        (entry,) = list_zip(archive, depth_left=DEEP).files
        with pytest.raises(FileRefused) as refused:
            read_file(archive, entry, limit_for=lambda sniffed: None, budget=10 * MB)
    assert refused.value.code == unzip.UNSUPPORTED_TYPE


def test_a_damaged_packed_stream_is_damaged() -> None:
    data = bytearray(make_zip([("a.csv", _rows(20_000))]))
    start = data.find(b"a.csv") + len(b"a.csv")  # the packed bytes follow the local header's name
    data[start + 50 : start + 70] = b"\xff" * 20
    with open_zip(bytes(data)) as archive:
        (entry,) = list_zip(archive, depth_left=DEEP).files
        with pytest.raises(FileRefused) as refused:
            read_file(archive, entry, limit_for=_any_limit, budget=10 * MB)
    assert refused.value.code == unzip.DAMAGED


def test_entries_over_one_packed_stream_refuse_the_whole_zip() -> None:
    data = point_at(make_zip([("a.csv", b"a\n1\n" * 100), ("b.csv", b"b\n2\n" * 100)]), "b.csv", "a.csv")
    with pytest.raises(ZipRefused) as refused:
        open_zip(data)
    assert refused.value.code == "damaged"


def test_what_is_not_a_zip_is_damaged() -> None:
    with pytest.raises(ZipRefused) as refused:
        open_zip(b"PK\x03\x04 not really")
    assert refused.value.code == "damaged"


def _many(count: int) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
        for n in range(count):
            archive.writestr(f"{n}.txt", b"")
    return out.getvalue()


def test_fifty_thousand_entries_are_listed_and_one_more_refuses_the_zip_whatever_it_claims() -> None:
    assert unzip.MAX_ENTRIES == 50_000
    with open_zip(_many(50_000)) as archive:
        assert len(archive.infolist()) == 50_000
    too_many = _many(50_001)
    for data in (too_many, claim_entries(too_many, 5)):
        with pytest.raises(ZipRefused) as refused:
            open_zip(data)
        assert refused.value.code == "too_many_files"
