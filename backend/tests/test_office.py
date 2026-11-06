"""Word, PowerPoint and old Excel files converted by headless LibreOffice (#522, PR 2,
``kaagaz/reading/office.py``), with LibreOffice faked: the command, the fresh profile that
turns macros and link updates off, the clean-up, and every failure in plain words.

The real conversions run where ``soffice`` is (the backend image): test_office_real.py."""

import ast
import os
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import unquote, urlparse

from kaagaz.ingestion import resolve, sniff
from kaagaz.ingestion.ports import BlobNotFound
from kaagaz.reading import office
from kaagaz.reading.office import OfficeConverter
from kaagaz.reading.spans import ReadError
from tests.fakes import MemoryStorage

PDF = b"%PDF-1.7\n% made up\n"
XLSX = b"PK\x03\x04 made up"


class FakeOffice:
    """Stands in for ``office.run_limited``: records each command and the files LibreOffice
    would see, then writes ``output`` where soffice would (or nothing, or times out)."""

    def __init__(self, output: bytes | None = PDF, *, timeout: bool = False, error: bool = False) -> None:
        self.output = output
        self.timeout = timeout
        self.error = error
        self.calls: list[tuple[list[str], Path, float]] = []
        self.inputs: list[tuple[str, bytes]] = []
        self.profiles: list[str] = []
        self.environments: list[dict[str, str]] = []

    def __call__(self, command: Sequence[str], env: dict[str, str], timeout: float) -> None:
        command = list(command)
        out_dir = Path(command[command.index("--outdir") + 1])
        source = Path(command[-1])
        self.calls.append((command, out_dir, timeout))
        self.inputs.append((source.name, source.read_bytes()))
        self.environments.append(env)
        profile = Path(_path_of(_profile_uri(command))) / "user" / "registrymodifications.xcu"
        self.profiles.append(profile.read_text(encoding="utf-8"))
        if self.error:
            raise OSError("soffice could not be started")
        if self.timeout:
            raise subprocess.TimeoutExpired(command, timeout)
        if self.output is not None:
            target = command[command.index("--convert-to") + 1].split(":")[0]
            (out_dir / f"{source.stem}.{target}").write_bytes(self.output)


def _profile_uri(command: Sequence[str]) -> str:
    [option] = [part for part in command if part.startswith("-env:UserInstallation=")]
    return option.split("=", 1)[1]


def _path_of(uri: str) -> str:
    path = unquote(urlparse(uri).path)
    return path[1:] if sys.platform == "win32" else path  # file:///C:/... on Windows


def converter(fake: FakeOffice, **kwargs: int) -> OfficeConverter:
    return OfficeConverter("/usr/bin/soffice", run=fake, **kwargs)


class KindsTest(unittest.TestCase):
    def test_word_and_powerpoint_files_are_read_as_a_pdf_and_an_old_excel_file_as_an_xlsx(self) -> None:
        self.assertEqual(
            {kind: office.read_as(kind) for kind in office.KINDS},
            {
                resolve.DOCX: sniff.PDF,
                resolve.DOTX: sniff.PDF,
                resolve.DOC: sniff.PDF,
                resolve.PPTX: sniff.PDF,
                resolve.PPT: sniff.PDF,
                resolve.XLS: resolve.XLSX,
            },
        )
        self.assertEqual(office.PDF_KINDS, office.KINDS - {resolve.XLS})

    def test_any_other_kind_is_never_converted(self) -> None:
        fake = FakeOffice()
        for kind in (sniff.PDF, resolve.XLSX, resolve.CSV, resolve.ZIP, resolve.HTML):
            with self.subTest(kind), self.assertRaises(ValueError):
                converter(fake).convert(kind, b"data")
        self.assertEqual(fake.calls, [])


class DrawnFromTest(unittest.TestCase):
    """What Ensemble and land facts draw a stored document's pages from again."""

    def test_a_word_or_powerpoint_files_pages_come_from_its_rendition_any_others_from_itself(self) -> None:
        storage = MemoryStorage()
        storage.put_rendition("c", "d", PDF)
        for kind in office.PDF_KINDS:
            with self.subTest(kind):
                self.assertEqual(office.drawn_from(storage, "c", "d", kind, b"original"), (sniff.PDF, PDF))
        for kind in (sniff.PDF, sniff.TIFF, resolve.XLS, resolve.XLSX):
            with self.subTest(kind):
                self.assertEqual(office.drawn_from(storage, "c", "d", kind, b"original"), (kind, b"original"))
        with self.assertRaises(BlobNotFound):
            office.drawn_from(storage, "c", "other", resolve.DOCX, b"original")


class CommandTest(unittest.TestCase):
    def test_a_word_file_is_converted_to_pdf_headless_with_no_restore_and_no_lock_check(self) -> None:
        fake = FakeOffice()
        self.assertEqual(converter(fake).convert(resolve.DOCX, b"the docx"), PDF)
        [(command, out_dir, timeout)] = fake.calls
        self.assertEqual(command[0], "/usr/bin/soffice")
        for flag in ("--headless", "--norestore", "--nolockcheck", "--nologo", "--nodefault"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--convert-to") + 1], "pdf")
        self.assertEqual(timeout, office.TIMEOUT_SECONDS)
        self.assertEqual(office.TIMEOUT_SECONDS, 60)
        # The file LibreOffice opens is named by its kind, never by the user's file name.
        self.assertEqual(fake.inputs, [("document.docx", b"the docx")])
        self.assertTrue(out_dir.name)

    def test_an_old_excel_file_is_converted_to_xlsx(self) -> None:
        fake = FakeOffice(XLSX)
        self.assertEqual(converter(fake).convert(resolve.XLS, b"the xls"), XLSX)
        [(command, _, _)] = fake.calls
        self.assertEqual(command[command.index("--convert-to") + 1], "xlsx:Calc MS Excel 2007 XML")
        self.assertEqual(fake.inputs, [("document.xls", b"the xls")])

    def test_each_conversion_has_a_fresh_profile_of_its_own_removed_after(self) -> None:
        fake = FakeOffice()
        convert = converter(fake)
        convert.convert(resolve.PPTX, b"one")
        convert.convert(resolve.PPT, b"two")
        first, second = (_profile_uri(command) for command, _, _ in fake.calls)
        self.assertNotEqual(first, second)
        for uri in (first, second):
            self.assertTrue(uri.startswith("file:///"))
            self.assertIn("lo-", uri)
            self.assertFalse(Path(_path_of(uri)).exists())  # removed with its files
        for _, out_dir, _ in fake.calls:
            self.assertFalse(out_dir.exists())

    def test_the_profile_never_runs_a_macro_and_never_updates_a_link(self) -> None:
        fake = FakeOffice()
        converter(fake).convert(resolve.DOC, b"doc")
        [profile] = fake.profiles
        for setting in (
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>',
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>',
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop></item>',
            '<item oor:path="/org.openoffice.Office.Writer/Content/Update">'
            '<prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item>',
            '<item oor:path="/org.openoffice.Office.Calc/Content/Update">'
            '<prop oor:name="Link" oor:op="fuse"><value>1</value></prop></item>',
        ):
            self.assertIn(setting, profile)

    def test_libreoffice_gets_none_of_the_workers_environment(self) -> None:
        fake = FakeOffice()
        os.environ["KAAGAZ_TEST_SECRET"] = "never passed on"
        try:
            converter(fake).convert(resolve.DOCX, b"docx")
        finally:
            del os.environ["KAAGAZ_TEST_SECRET"]
        [env] = fake.environments
        self.assertEqual(set(env), {"PATH", "HOME", "TMPDIR", "LANG", "SAL_USE_VCLPLUGIN"})
        self.assertNotIn("never passed on", env.values())


class FailureTest(unittest.TestCase):
    def assert_unreadable(self, fake: FakeOffice, **kwargs: int) -> None:
        with self.assertRaises(ReadError) as caught:
            converter(fake, **kwargs).convert(resolve.DOCX, b"docx")
        self.assertEqual(caught.exception.code, office.UNREADABLE)
        self.assertEqual(office.UNREADABLE, "office_unreadable")
        self.assertIsNone(caught.exception.__cause__)  # nothing that can quote the file
        self.assertIsNone(caught.exception.__context__)
        for _, out_dir, _ in fake.calls:
            self.assertFalse(out_dir.parent.exists())  # cleaned up all the same

    def test_no_output_a_password_or_a_damaged_file_is_unreadable(self) -> None:
        self.assert_unreadable(FakeOffice(None))

    def test_a_timeout_is_unreadable(self) -> None:
        self.assert_unreadable(FakeOffice(timeout=True))

    def test_soffice_that_cannot_start_is_unreadable(self) -> None:
        self.assert_unreadable(FakeOffice(error=True))

    def test_an_empty_output_is_unreadable(self) -> None:
        self.assert_unreadable(FakeOffice(b""))

    def test_an_output_that_is_not_a_pdf_is_unreadable(self) -> None:
        self.assert_unreadable(FakeOffice(b"<html>not a pdf</html>"))

    def test_an_output_over_its_limit_is_unreadable(self) -> None:
        self.assert_unreadable(FakeOffice(PDF + b"0" * 100), max_output_bytes=len(PDF) + 50)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return Path(f"/proc/{pid}").exists() and "Z" not in Path(f"/proc/{pid}/stat").read_text().split()[2]


class ChildSetUpTest(unittest.TestCase):
    def test_the_child_s_set_up_imports_nothing(self) -> None:
        """Security review (6 Nov 2026, nice to have): the function run in the child between fork
        and exec may only call setrlimit: an import there can wait forever on a lock another
        thread of the worker held when it forked. ``resource`` is imported with the module."""
        tree = ast.parse(Path(office.__file__).read_text(encoding="utf-8"))
        [run] = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "run_limited"
        ]
        inner = [node for node in ast.walk(run) if isinstance(node, ast.FunctionDef) and node is not run]
        self.assertEqual([node.name for node in inner], ["limits"])
        self.assertEqual([n for n in ast.walk(inner[0]) if isinstance(n, ast.Import | ast.ImportFrom)], [])
        if sys.platform != "win32":
            self.assertIn("resource", vars(office))


@unittest.skipUnless(sys.platform.startswith("linux"), "process groups and rlimits: Linux only")
class RunLimitedTest(unittest.TestCase):
    """``office.run_limited`` itself, with a shell in place of soffice."""

    def run_shell(self, script: str, timeout: float, home: str) -> None:
        office.run_limited(["/bin/sh", "-c", script], {"PATH": "/usr/bin:/bin", "HOME": home}, timeout)

    def test_a_timeout_kills_the_whole_process_group_children_too(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            pids = Path(home) / "pids"
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_shell(f"sleep 30 & echo $! > {pids}; echo $$ >> {pids}; wait", 1, home)
            self.assertLess(time.monotonic() - started, 10)
            child, shell = (int(line) for line in pids.read_text().split())
            deadline = time.monotonic() + 5
            while (_alive(child) or _alive(shell)) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(_alive(child), "the soffice child was left running")
            self.assertFalse(_alive(shell))

    def test_a_process_that_ends_in_time_returns(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self.run_shell("exit 0", 5, home)

    def test_a_file_over_the_size_limit_cannot_be_written(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            big = Path(home) / "big"
            office.run_limited(
                ["/bin/sh", "-c", f"head -c 4096 /dev/zero > {big}"],
                {"PATH": "/usr/bin:/bin", "HOME": home},
                5,
                max_file_bytes=1024,
            )
            self.assertLessEqual(big.stat().st_size, 1024)

    def test_the_child_gets_only_the_environment_given(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            seen = Path(home) / "env"
            os.environ["KAAGAZ_TEST_SECRET"] = "x"
            try:
                self.run_shell(f"env > {seen}", 5, home)
            finally:
                del os.environ["KAAGAZ_TEST_SECRET"]
            self.assertNotIn("KAAGAZ_TEST_SECRET", seen.read_text())
