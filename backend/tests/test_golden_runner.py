"""The golden runner's own checks (PRD 3 G1). The run itself needs a live stack, so it
is not run here."""

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.golden_scoring import Hit
from scripts.run_golden import GOLDEN, GoldenError, check_local, hits, load_golden, main

LOCAL_DB = "postgresql://kaagaz@127.0.0.1:5432/kaagaz"


class RunnerTest(unittest.TestCase):
    def test_a_target_off_this_machine_is_refused_without_the_flag(self) -> None:
        check_local("http://127.0.0.1:8000", LOCAL_DB, allow_remote=False)
        check_local("http://localhost:8000", "postgresql://kaagaz@localhost/kaagaz", allow_remote=False)
        for api, db in (
            ("https://kaagaz.example", LOCAL_DB),
            ("http://127.0.0.1:8000", "postgresql://db.example/k"),
        ):
            with self.assertRaises(GoldenError):
                check_local(api, db, allow_remote=False)
            check_local(api, db, allow_remote=True)

    def test_the_committed_documents_load_and_a_changed_one_is_refused(self) -> None:
        questions, documents = load_golden(GOLDEN / "docs")
        self.assertEqual((len(questions), len(documents)), (30, 7))
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / "docs"
            shutil.copytree(GOLDEN / "docs", copy)
            (copy / "hindi-bill.csv").write_bytes(b"changed\n")
            with self.assertRaisesRegex(GoldenError, "hindi-bill.csv is missing from --docs or differs"):
                load_golden(copy)

    def test_a_citation_is_a_hit_spanning_its_regions_pages(self) -> None:
        answer = {
            "citations": [
                {"document_id": "d1", "page_no": 15, "regions": [{"page": 15}, {"page": 16}]},
                {"document_id": "d2", "page_no": 3, "regions": []},
            ]
        }
        self.assertEqual(
            hits(answer, {"d1": "monthly-report.pdf"}), [Hit("monthly-report.pdf", 15, 16), Hit("?", 3, 3)]
        )

    def test_no_wait_or_no_run_stops_before_anything_is_made(self) -> None:
        for argv in (["--wait-minutes", "0"], ["--runs", "0"]):
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                self.assertEqual(main(["--git-sha", "abc", *argv]), 2)
            self.assertIn("--wait-minutes above 0", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
