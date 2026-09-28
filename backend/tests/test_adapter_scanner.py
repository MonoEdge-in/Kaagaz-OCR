"""ClamdScanner against a fake clamd on 127.0.0.1, and EicarDevScanner.

The fake speaks clamd's INSTREAM protocol (standard library socketserver, in
a thread): it reads length-prefixed chunks up to a zero length and answers
like clamd does. It is told how to behave per test: answer normally, refuse
a stream over a size limit, never answer (timeout), or answer nonsense.
"""

import socket
import socketserver
import struct
import threading
import unittest

from kaagaz.adapters.scanner import EICAR, ClamdScanner, EicarDevScanner, LayeredScanner, build_scanner
from kaagaz.scanning.gate import ScannerError, Verdict
from kaagaz.settings import SettingsError
from tests.settings_factory import make_settings


def _read_exact(connection: socket.socket, size: int) -> bytes | None:
    data = bytearray()
    while len(data) < size:
        part = connection.recv(size - len(data))
        if not part:
            return None
        data.extend(part)
    return bytes(data)


class FakeClamdHandler(socketserver.BaseRequestHandler):
    server: "FakeClamd"

    def handle(self) -> None:
        connection: socket.socket = self.request
        server = self.server
        if _read_exact(connection, len(b"zINSTREAM\0")) != b"zINSTREAM\0":
            connection.sendall(b"UNKNOWN COMMAND\0")
            return
        received = bytearray()
        while True:
            header = _read_exact(connection, 4)
            if header is None:
                return
            (length,) = struct.unpack("!I", header)
            if length == 0:
                break
            if server.size_limit is not None and len(received) + length > server.size_limit:
                connection.sendall(b"INSTREAM size limit exceeded. ERROR\0")
                # Stop writing, then drain what the client still sends, so the
                # close is clean and the reply is not lost to a reset.
                connection.shutdown(socket.SHUT_WR)
                while connection.recv(65536):
                    pass
                return
            chunk = _read_exact(connection, length)
            if chunk is None:
                return
            received.extend(chunk)
        server.received.append(bytes(received))
        if server.never_answer:
            server.release.wait(10)
            return
        if server.reply is not None:
            connection.sendall(server.reply)
        elif EICAR in received:
            connection.sendall(b"stream: Win.Test.EICAR_HDB-1 FOUND\0")
        else:
            connection.sendall(b"stream: OK\0")


class FakeClamd(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False
    size_limit: int | None = None
    never_answer = False
    reply: bytes | None = None

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), FakeClamdHandler)
        self.received: list[bytes] = []
        self.release = threading.Event()


class ClamdScannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeClamd()
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()

        def stop() -> None:
            self.server.release.set()
            self.server.shutdown()
            self.server.server_close()
            thread.join(5)

        self.addCleanup(stop)
        host, port = self.server.server_address[:2]
        self.scanner = ClamdScanner(str(host), int(port), timeout=2.0)

    def test_clean_file(self) -> None:
        data = b"%PDF-1.7 an ordinary invoice" * 10_000  # several chunks
        self.assertIs(self.scanner.scan(data), Verdict.CLEAN)
        self.assertEqual(self.server.received, [data])  # every byte arrived, in order

    def test_empty_file_is_still_asked_about(self) -> None:
        self.assertIs(self.scanner.scan(b""), Verdict.CLEAN)
        self.assertEqual(self.server.received, [b""])

    def test_eicar_is_infected(self) -> None:
        self.assertIs(self.scanner.scan(b"prefix " + EICAR + b" suffix"), Verdict.INFECTED)

    def test_size_limit_is_unscannable(self) -> None:
        self.server.size_limit = 100_000
        self.assertIs(self.scanner.scan(b"x" * 300_000), Verdict.UNSCANNABLE)
        self.assertEqual(self.server.received, [])

    def test_limits_and_encryption_alerts_are_unscannable_not_infected(self) -> None:
        # What clamd says with AlertExceedsMax / AlertEncrypted on: it could not look inside.
        for reply in (
            b"stream: Heuristics.Limits.Exceeded.MaxFileSize FOUND\0",
            b"stream: Heuristics.Encrypted.PDF FOUND\0",
        ):
            with self.subTest(reply=reply):
                self.server.reply = reply
                self.assertIs(self.scanner.scan(b"hello"), Verdict.UNSCANNABLE)

    def test_no_answer_is_a_scanner_error(self) -> None:
        self.server.never_answer = True
        scanner = ClamdScanner(*self._address(), timeout=0.3)
        with self.assertRaises(ScannerError):
            scanner.scan(b"hello")

    def test_other_clamd_errors_are_scanner_errors(self) -> None:
        for reply in (b"INSTREAM: Can't write to temporary file ERROR\0", b"\0", b"banana\0", b""):
            with self.subTest(reply=reply):
                self.server.reply = reply
                with self.assertRaises(ScannerError):
                    self.scanner.scan(b"hello")

    def test_connection_refused_is_a_scanner_error(self) -> None:
        with socket.socket() as probe:  # find a port nobody listens on
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with self.assertRaises(ScannerError):
            ClamdScanner("127.0.0.1", port, timeout=5.0).scan(b"hello")

    def test_the_timeout_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            ClamdScanner("127.0.0.1", 3310, timeout=0)

    def test_error_carries_a_code_only(self) -> None:
        self.server.reply = b"stream: something odd /tmp/secret-name.pdf\0"
        with self.assertRaises(ScannerError) as caught:
            self.scanner.scan(b"hello")
        self.assertEqual(str(caught.exception), "scanner_error")
        self.assertIsNone(caught.exception.__cause__)

    def _address(self) -> tuple[str, int]:
        host, port = self.server.server_address[:2]
        return str(host), int(port)


class EicarDevScannerTest(unittest.TestCase):
    def test_finds_eicar_and_nothing_else(self) -> None:
        scanner = EicarDevScanner()
        self.assertIs(scanner.scan(EICAR), Verdict.INFECTED)
        self.assertIs(scanner.scan(b"data " + EICAR), Verdict.INFECTED)
        self.assertIs(scanner.scan(b"%PDF-1.7 ordinary"), Verdict.CLEAN)
        self.assertIs(scanner.scan(EICAR[:-1]), Verdict.CLEAN)

    def test_eicar_constant_is_the_standard_test_string(self) -> None:
        self.assertEqual(len(EICAR), 68)
        self.assertTrue(EICAR.startswith(b"X5O!P%@AP"))
        self.assertTrue(EICAR.endswith(b"$H+H*"))

    def test_docstring_says_laptop_only(self) -> None:
        self.assertIn("LAPTOP ONLY", EicarDevScanner.__doc__ or "")


class BuildScannerTest(unittest.TestCase):
    def test_clamav(self) -> None:
        scanner = build_scanner(make_settings(scanner="clamav", clamav_host="clamav", clamav_port=3310))
        # Always behind the content check: there is no way to build clamd alone.
        self.assertIsInstance(scanner, LayeredScanner)
        assert isinstance(scanner, LayeredScanner)
        self.assertIsInstance(scanner.scanner, ClamdScanner)

    def test_clamav_needs_host_and_port(self) -> None:
        for overrides in (
            {"clamav_host": None, "clamav_port": 3310},
            {"clamav_host": "clamav", "clamav_port": None},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(SettingsError):
                build_scanner(make_settings(scanner="clamav", **overrides))

    def test_eicar_dev_warns(self) -> None:
        with self.assertLogs("kaagaz.adapters.scanner", "WARNING"):
            scanner = build_scanner(make_settings(scanner="eicar_dev"))
        self.assertIsInstance(scanner, LayeredScanner)
        assert isinstance(scanner, LayeredScanner)
        self.assertIsInstance(scanner.scanner, EicarDevScanner)

    def test_unknown_scanner_is_refused(self) -> None:
        with self.assertRaises(SettingsError):
            build_scanner(make_settings(scanner="none"))


if __name__ == "__main__":
    unittest.main()
