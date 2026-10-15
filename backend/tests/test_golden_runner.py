"""The golden runner's own checks (PRD 3 G1). The run itself needs a live stack, so it
is not run here."""

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kaagaz.answering.draft import ANSWER_PROMPT_HASH
from scripts.golden_scoring import Hit, score
from scripts.run_golden import (
    GOLDEN,
    SETS,
    GoldenError,
    Http,
    _parser,
    answer_settings,
    check_against,
    check_local,
    header,
    hits,
    load_golden,
    main,
    report,
    upload,
)
from tests.settings_factory import make_settings

LOCAL_DB = "postgresql://kaagaz@127.0.0.1:5432/kaagaz"
GEMINI_CHAT, GEMINI_EMBED = "google/gemini-3.1-pro-preview", "google/gemini-embedding-2"


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

    def test_the_xlang_set_loads_its_own_questions_and_documents(self) -> None:
        self.assertEqual(_parser().parse_args(["--git-sha", "abc"]).question_set, "golden")
        questions, documents = load_golden(chosen=SETS["xlang"])
        self.assertEqual((len(questions), len(documents)), (60, 6))
        self.assertIn("dev", questions[0].slices)
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / "xlang"
            shutil.copytree(GOLDEN / "xlang", copy)
            (copy / "ta-bill.pdf").write_bytes(b"changed")
            with self.assertRaisesRegex(GoldenError, "ta-bill.pdf is missing from --docs or differs"):
                load_golden(copy, SETS["xlang"])

    def test_an_unread_xlang_document_is_listed_not_fatal(self) -> None:
        class Api(Http):
            """Every upload is ready at once, but b.pdf, which no reader could read."""

            def call(self, method: str, path: str, body: Any = None, **kwargs: Any) -> Any:
                if method == "POST":
                    return {"document": {"id": kwargs["files"][0]}}
                return {"status": "needs_reader" if path.endswith("b.pdf") else "ready"}

        out = io.StringIO()
        files = {"a.pdf": b"a", "b.pdf": b"b"}
        api = Api("http://127.0.0.1:1")
        self.assertEqual(upload(api, files, 1, out, all_ready=False), {"a.pdf": "a.pdf", "b.pdf": "b.pdf"})
        self.assertIn("NOT READY, their questions are still asked: {'b.pdf': 'needs_reader'}", out.getvalue())
        self.assertIn("1 documents ready", out.getvalue())
        with self.assertRaisesRegex(GoldenError, "not ready"):
            upload(api, files, 1, io.StringIO(), all_ready=True)

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


class HeaderTest(unittest.TestCase):
    """PRD 5 M5: the before and after runs are compared with ANSWER_WHOLE_DOC_TOKENS=0."""

    def test_m5_the_header_prints_answer_whole_doc_tokens(self) -> None:
        out = io.StringIO()
        settings = make_settings(model_chat=GEMINI_CHAT, model_embed=GEMINI_EMBED, answer_whole_doc_tokens=0)
        header(out, settings, "http://127.0.0.1:8000", "abc123")
        self.assertIn(" ANSWER_WHOLE_DOC_TOKENS=0 ", out.getvalue())


class AnswerSettingsTest(unittest.TestCase):
    """Features 25-27 (C14): the golden run can ask with chosen answer models and a level."""

    def test_agents_and_thinking_become_the_chat_fields(self) -> None:
        argv = ["--git-sha", "abc", "--agents", "google/a, anthropic/b", "--thinking", "medium"]
        args = _parser().parse_args(argv)
        self.assertEqual(
            answer_settings(args.agents, args.thinking),
            {"agents": ["google/a", "anthropic/b"], "thinking": "medium"},
        )
        self.assertEqual(answer_settings(None, None), {})
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            _parser().parse_args(["--git-sha", "abc", "--thinking", "max"])


class PrecisionReportTest(unittest.TestCase):
    """Ask precision PR 1a: the Romanised set, the --json report and the --compare gate."""

    def test_the_romanised_set_asks_each_spelling_over_the_golden_documents(self) -> None:
        self.assertEqual(
            _parser().parse_args(["--git-sha", "a", "--set", "romanised"]).question_set, "romanised"
        )
        questions, documents = load_golden(chosen=SETS["romanised"])
        self.assertEqual((len(questions), len(documents)), (97, 7))
        self.assertEqual([q.id for q in questions[:3]], ["r01#1", "r01#2", "r01#3"])
        self.assertEqual(len({q.id.split("#")[0] for q in questions if q.kind == "not_found"}), 15)

    def test_the_report_keeps_each_questions_worst_run_and_the_gate_reads_it(self) -> None:
        questions, _ = load_golden()
        profit, salary = questions[0], questions[2]  # 12,000 in September; not found
        right = {"status": "answered", "label": "verified", "sentences": [{"text": "It was Rs. 12,000."}]}
        empty = {"status": "not_found", "label": None, "sentences": []}
        runs = [
            [(profit, score(profit, right), None), (salary, score(salary, empty), None)],
            [(profit, score(profit, empty), None), (salary, score(salary, empty), None)],
        ]
        settings = make_settings(model_chat=GEMINI_CHAT, model_embed=GEMINI_EMBED)
        made = report("golden", settings, "abc", runs)
        self.assertEqual(
            (made["set"], made["MODEL_CHAT"], made["ANSWER_PROMPT_HASH"], made["runs"]),
            ("golden", GEMINI_CHAT, ANSWER_PROMPT_HASH, 2),
        )
        self.assertEqual(
            made["questions"],
            {
                profit.id: {"outcome": "abstained", "verified": False},  # its worse run
                salary.id: {"outcome": "right", "verified": False},
            },
        )
        self.assertEqual((made["metrics"]["coverage"]["count"], made["metrics"]["coverage"]["of"]), (0, 1))
        self.assertEqual(made["totals"]["abstained"], 1)
        self.assertIn("cross_page", made["slices"])
        base = {**made, "questions": {profit.id: {"outcome": "right", "verified": True}}}
        self.assertTrue(check_against(base, made, io.StringIO()))  # one right -> abstained is allowed
        base["questions"][profit.id]["outcome"] = "abstained"
        worse = {**made, "questions": {profit.id: {"outcome": "wrong", "verified": False}}}
        out = io.StringIO()
        self.assertFalse(check_against(base, worse, out))
        self.assertIn(f"  {profit.id}: abstained -> wrong\n", out.getvalue())
        self.assertIn(f"GATE FAILED: {profit.id} moved into wrong\n", out.getvalue())
