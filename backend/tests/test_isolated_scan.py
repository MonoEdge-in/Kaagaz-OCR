"""The content check in a child process: whatever goes wrong there is ``unscannable``."""

import os
import subprocess
import sys
import time
import unittest
from typing import Any
from unittest import mock

from kaagaz.adapters.scanner import LayeredScanner
from kaagaz.scanning import isolated
from kaagaz.scanning.content import Finding
from kaagaz.scanning.gate import Verdict
from tests.test_content_inspection import CATALOG, JS, PAGE, PAGES, RecordingScanner, pdf_with, png_bytes


def child(code: str) -> Any:
    """The child command replaced by ``python -c code``."""
    return mock.patch.object(isolated, "_COMMAND", [sys.executable, "-c", code])


class TheChild(unittest.TestCase):
    def test_an_ordinary_pdf_passes_through_the_child(self) -> None:
        self.assertIsNone(isolated.inspect_isolated(pdf_with([CATALOG, PAGES, PAGE])))

    def test_the_childs_verdict_comes_back(self) -> None:
        catalog = b"<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>"
        self.assertIs(isolated.inspect_isolated(pdf_with([catalog, PAGES, PAGE, JS])), Finding.BLOCKED)

    def test_a_child_past_its_wall_clock_is_unscannable(self) -> None:
        started = time.monotonic()
        with (
            child("import time; time.sleep(30)"),
            mock.patch.object(isolated, "WALL_SECONDS", 1.0),
            self.assertLogs(isolated.logger, "WARNING") as logs,
        ):
            self.assertIs(isolated.inspect_isolated(b"x"), Finding.UNSCANNABLE)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(logs.output, ["WARNING:kaagaz.scanning.isolated:content check failed: timeout"])

    def test_a_child_that_fails_or_says_something_else_is_unscannable(self) -> None:
        for code in (
            "import sys; sys.exit(3)",
            "print('CLEAN')",
            "import sys; sys.stdout.write('ok'); sys.exit(1)",  # a right word, a wrong exit
            "",  # nothing said
        ):
            with self.subTest(code=code), child(code), self.assertLogs(isolated.logger, "WARNING"):
                self.assertIs(isolated.inspect_isolated(b"x"), Finding.UNSCANNABLE)

    def test_a_child_that_cannot_start_is_unscannable(self) -> None:
        with (
            mock.patch.object(isolated, "_COMMAND", ["/no/such/python"]),
            self.assertLogs(isolated.logger, "WARNING"),
        ):
            self.assertIs(isolated.inspect_isolated(b"x"), Finding.UNSCANNABLE)

    def test_the_child_gets_none_of_the_apps_secrets(self) -> None:
        secrets = {"OPENROUTER_API_KEY": "sk-test", "DATABASE_URL": "postgresql://u:p@h/d"}
        with (
            mock.patch.dict(os.environ, secrets),
            mock.patch.object(subprocess, "run", wraps=subprocess.run) as run,
        ):
            self.assertIsNone(isolated.inspect_isolated(png_bytes()))
        env = run.call_args.kwargs["env"]
        self.assertFalse(set(secrets) & set(env))
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))


class TheLayersUseTheChild(unittest.TestCase):
    def test_layered_scanner_asks_the_isolated_check(self) -> None:
        inner = RecordingScanner()
        with mock.patch("kaagaz.adapters.scanner.inspect_isolated", return_value=Finding.UNSCANNABLE):
            self.assertIs(LayeredScanner(inner).scan(png_bytes()), Verdict.UNSCANNABLE)
        self.assertEqual(inner.calls, 0)


if __name__ == "__main__":
    unittest.main()
