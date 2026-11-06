"""Virus scanners behind the scan gate's ``Scanner`` port.

``ClamdScanner`` talks to a clamd daemon (ClamAV) over TCP with its INSTREAM
command, using only the standard library ``socket``:

1. send ``zINSTREAM\\0``;
2. send the file in chunks, each prefixed with its length as 4 bytes, big
   endian, then a zero length to say "done";
3. read one reply, ending in a zero byte:

   * ``stream: OK`` is CLEAN;
   * ``stream: <signature> FOUND`` is INFECTED;
   * ``INSTREAM size limit exceeded. ERROR`` is UNSCANNABLE: clamd refused to
     read the whole file, so it cannot be called clean;
   * ``stream: Heuristics.Limits.Exceeded.MaxScanTime FOUND`` is ``ScannerError``: clamd ran
     out of time, which depends on how busy it was, not only on the file (Vrushit UAT,
     25 Oct 2026), so the job is retried like a timeout; after the last try the document
     fails, still unscanned, and is never read.
   * any other ``stream: Heuristics.Limits.Exceeded... FOUND`` and
     ``stream: Heuristics.Encrypted... FOUND`` are UNSCANNABLE too: clamd could
     not look inside. So is ``stream: Heuristics.Broken... FOUND``
     (``AlertBrokenMedia``): a damaged file, not a virus; still refused.
     clamd only says so with ``AlertExceedsMax yes`` and
     ``AlertEncrypted yes`` in clamd.conf; without them an over-limit file comes
     back ``OK``. PROVISIONAL: clamd's configuration is Vrushit's.
   * cannot connect, a timeout, a closed connection, any other ERROR or a
     reply we do not know is ``ScannerError``. The gate then leaves the file
     unscanned and the job is retried. Nothing unclear ever counts as clean.

The signature name and clamd's wording are never passed on: the gate turns a
verdict into a short code only.

``EicarDevScanner`` is for the laptop without ClamAV. It finds the EICAR test
file and nothing else.

Time O(n) and memory O(chunk) beyond the bytes already held, for an n-byte
file.
"""

import logging
import socket
import struct

from kaagaz.scanning.content import Finding
from kaagaz.scanning.gate import Scanner, ScannerError, Verdict
from kaagaz.scanning.isolated import inspect_isolated
from kaagaz.settings import Settings, SettingsError

logger = logging.getLogger(__name__)

# Large enough to keep system calls few; clamd accepts any chunk up to its
# StreamMaxLength.
_CHUNK_BYTES = 64 * 1024

# clamd's replies are one short line. A longer one is not clamd.
_MAX_REPLY_BYTES = 4096

# clamd's MaxScanTime alert: retried, not a verdict on the file (see _verdict).
_TIME_LIMIT = "stream: Heuristics.Limits.Exceeded.MaxScanTime"
# clamd could not look inside, by alert: the reason logged for each.
_UNSCANNABLE = (
    ("stream: Heuristics.Limits.Exceeded", "limits_exceeded"),
    ("stream: Heuristics.Encrypted", "encrypted"),
    ("stream: Heuristics.Broken.", "broken"),
)

# The EICAR anti-virus test string, split in two so this source file is not
# itself flagged by a virus scanner on a developer's machine.
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class ClamdScanner:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._address = (host, port)
        self._timeout = timeout  # applies to connecting and to each send and read

    def scan(self, data: bytes, *, name: str | None = None) -> Verdict:
        try:
            with socket.create_connection(self._address, timeout=self._timeout) as connection:
                sent_all = _send_stream(connection, data)
                reply = _read_reply(connection)
        except OSError:  # refused, reset, unreachable and timeouts are all OSError
            raise ScannerError("scanner_unavailable") from None
        return _verdict(reply, sent_all=sent_all)


def _send_stream(connection: socket.socket, data: bytes) -> bool:
    """Send the INSTREAM command and the file. False if clamd closed early.

    clamd stops reading and closes the connection once the stream passes its
    size limit, and says why in its reply, so a send that fails part way is
    not an error by itself: the reply is read next.
    """
    view = memoryview(data)
    try:
        connection.sendall(b"zINSTREAM\0")
        for start in range(0, len(view), _CHUNK_BYTES):
            chunk = view[start : start + _CHUNK_BYTES]
            connection.sendall(struct.pack("!I", len(chunk)))
            connection.sendall(chunk)
        connection.sendall(struct.pack("!I", 0))
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        return False
    return True


def _read_reply(connection: socket.socket) -> bytes:
    """clamd's reply up to its closing zero byte (or until it closes)."""
    reply = bytearray()
    while b"\0" not in reply and len(reply) < _MAX_REPLY_BYTES:
        try:
            part = connection.recv(_MAX_REPLY_BYTES)
        except (ConnectionResetError, ConnectionAbortedError):
            break  # keep what arrived before the reset
        if not part:
            break
        reply.extend(part)
    return bytes(reply).split(b"\0", 1)[0]


def _verdict(reply: bytes, *, sent_all: bool) -> Verdict:
    text = reply.decode("ascii", errors="replace").strip()
    if text.startswith(_TIME_LIMIT):
        logger.warning("virus scan unfinished: time_limit")
        raise ScannerError("scanner_timeout")
    if text.endswith("size limit exceeded. ERROR"):
        logger.warning("virus scan unscannable: size_limit")
        return Verdict.UNSCANNABLE
    for prefix, reason in _UNSCANNABLE:
        if text.startswith(prefix):
            # A fixed reason only: which check could not look inside, never clamd's words.
            logger.warning("virus scan unscannable: %s", reason)
            return Verdict.UNSCANNABLE
    if sent_all and text == "stream: OK":
        return Verdict.CLEAN
    if text.startswith("stream: ") and text.endswith(" FOUND"):
        return Verdict.INFECTED
    raise ScannerError("scanner_error")


class EicarDevScanner:
    """LAPTOP ONLY. NOT A VIRUS SCANNER. NEVER USE IT WITH REAL FILES.

    It marks a file INFECTED if it contains the EICAR test string and CLEAN
    otherwise, so the reject and quarantine path can be tried on a laptop
    without ClamAV.
    """

    def scan(self, data: bytes, *, name: str | None = None) -> Verdict:
        return Verdict.INFECTED if EICAR in data else Verdict.CLEAN


class LayeredScanner:
    """Two layers, both deny by default: our content check, then the virus scanner.

    The content check (kaagaz/scanning/content.py) refuses scripts, macros,
    hidden programs and other active content that no signature may cover yet.
    Only a file both layers pass is CLEAN. The content check runs first: it is
    local and cheap, and a file it refuses never needs the scanner at all. It runs in
    a child process with memory and CPU limits (kaagaz/scanning/isolated.py).
    """

    def __init__(self, scanner: Scanner) -> None:
        self.scanner = scanner

    def scan(self, data: bytes, *, name: str | None = None) -> Verdict:
        """``name``: the file's name, which only chooses between text kinds (#522)."""
        finding = inspect_isolated(data, name or "")  # in a child process with OS limits
        if finding is not None:
            # Which layer refused the file, so a support case can tell (Vrushit UAT, 25 Oct 2026).
            logger.warning("content check refused: %s", finding.value)
        if finding is Finding.BLOCKED:
            return Verdict.BLOCKED
        if finding is Finding.UNSCANNABLE:
            return Verdict.UNSCANNABLE
        return self.scanner.scan(data)


def build_scanner(settings: Settings) -> Scanner:
    """The scanner the settings name, always behind the content check.

    There is no default and no "off".
    """
    if settings.scanner == "clamav":
        if settings.clamav_host is None:
            raise SettingsError("CLAMAV_HOST is not set")
        if settings.clamav_port is None:
            raise SettingsError("CLAMAV_PORT is not set")
        return LayeredScanner(
            ClamdScanner(settings.clamav_host, settings.clamav_port, settings.scanner_timeout_seconds)
        )
    if settings.scanner == "eicar_dev":
        logger.warning("SCANNER=eicar_dev: laptop only, files are not virus checked")
        return LayeredScanner(EicarDevScanner())
    raise SettingsError("SCANNER must be one of ['clamav', 'eicar_dev']")
