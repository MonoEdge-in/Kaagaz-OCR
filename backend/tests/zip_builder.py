"""Zip files for the unpacking tests (#522 PR 3), made with the standard library, and the byte
patches that make a zip lie: a part with a password, a link, a size its header gets wrong, two
entries over one packed stream. Test-only."""

import io
import stat
import struct
import zipfile
from collections.abc import Iterable

_CENTRAL = b"PK\x01\x02"
_LOCAL = b"PK\x03\x04"


def make_zip(files: Iterable[tuple[str, bytes]], *, folders: Iterable[str] = ()) -> bytes:
    """A deflated zip of ``files`` (path, bytes) and the empty ``folders`` ("a/b/")."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder in folders:
            archive.writestr(zipfile.ZipInfo(folder), b"")
        for path, data in files:
            archive.writestr(path, data)
    return out.getvalue()


def with_link(path: str, target: bytes = b"/etc/passwd") -> tuple[zipfile.ZipInfo, bytes]:
    """A symbolic link entry as a Unix zip tool writes it: mode S_IFLNK in the external attributes."""
    info = zipfile.ZipInfo(path)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info, target


def make_zip_with(entries: Iterable[tuple[str | zipfile.ZipInfo, bytes]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry, data in entries:
            archive.writestr(entry, data)
    return out.getvalue()


def _central_entries(data: bytes) -> list[tuple[int, str]]:
    """(offset, name) of every central directory entry."""
    found, at = [], data.find(_CENTRAL)
    while at >= 0:
        name_length = struct.unpack_from("<H", data, at + 28)[0]
        found.append((at, data[at + 46 : at + 46 + name_length].decode()))
        at = data.find(_CENTRAL, at + 46 + name_length)
    return found


def _local_at(data: bytes, name: str) -> int:
    for at, entry in _central_entries(data):
        if entry == name:
            return int(struct.unpack_from("<I", data, at + 42)[0])
    raise KeyError(name)


def _central_at(data: bytes, name: str) -> int:
    return next(at for at, entry in _central_entries(data) if entry == name)


def mark_encrypted(data: bytes, name: str) -> bytes:
    """``name`` says it has a password (flag bit 0), in its local header and the central directory."""
    out = bytearray(data)
    for flags_at in (_local_at(data, name) + 6, _central_at(data, name) + 8):
        (flags,) = struct.unpack_from("<H", out, flags_at)
        struct.pack_into("<H", out, flags_at, flags | 0x1)
    return bytes(out)


def set_size(data: bytes, name: str, size: int) -> bytes:
    """``name`` says it unpacks to ``size`` bytes, whatever it really does (both headers)."""
    out = bytearray(data)
    struct.pack_into("<I", out, _local_at(data, name) + 22, size)
    struct.pack_into("<I", out, _central_at(data, name) + 24, size)
    return bytes(out)


def point_at(data: bytes, name: str, other: str) -> bytes:
    """``name``'s central directory entry points at ``other``'s packed bytes: they overlap."""
    out = bytearray(data)
    struct.pack_into("<I", out, _central_at(data, name) + 42, _local_at(data, other))
    return bytes(out)


def claim_entries(data: bytes, count: int) -> bytes:
    """The end record says the zip has ``count`` entries, whatever it has."""
    out = bytearray(data)
    at = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", out, at + 8, count, count)
    return bytes(out)
