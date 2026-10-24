"""The golden runner's own checks (PRD 3 G1). The run itself needs a live stack, so it
is not run here."""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kaagaz.answering.draft import ANSWER_PROMPT_HASH
from scripts.golden_scoring import Hit, NlqScore, Outcome, Question, question_from, score
from scripts.run_golden import (
    GOLDEN,
    RETRIES,
    RETRY_CAP,
    SETS,
    Asked,
    GoldenError,
    Http,
    _parser,
    answer_settings,
    ask_all,
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
        empty: dict[str, Any] = {"status": "not_found", "label": None, "sentences": []}
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


# PRD 6 package G: the NLQ set's runner against a fake API (no stack, no model).
ASKS = {
    "kind": "clarify",
    "status": "answered",
    "opening": "Good evening, Tanvi.",
    "clarify": {
        "question": "Which places?",
        "choices": [{"id": "c1", "label": "Maval, Pune"}, {"id": "c2", "label": "Thane"}],
    },
}
ANSWERS = {
    "status": "answered",
    "label": "verified",
    "route": "search",
    "model_used": True,
    "opening": "Sure.",
}
ANSWERS |= {"sentences": [{"text": "Survey 45/2 at Kusgaon.", "citations": []}], "citations": []}


def nlq_item(pick: str = "Maval") -> Question:
    expect = {
        "kind": "text",
        "value": "45/2",
        "document": "k.pdf",
        "pages": [1],
        "clarify": {"choices": ["Maval"], "pick": pick},
    }
    return question_from(
        {
            "id": "n1",
            "question": "Land between Pune and Mumbai?",
            "split": "dev",
            "tags": [],
            "expect": expect,
        }
    )


class ChatApi(Http):
    """Replies to POST /api/chat in turn (the last one again once they run out), keeping what was sent."""

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        super().__init__("http://127.0.0.1:1")
        self.replies, self.sent = replies, list[Any]()

    def call(self, method: str, path: str, body: Any = None, **kwargs: Any) -> Any:
        self.sent.append(body)
        return {
            "conversation_id": "c1",
            "answer": {"answer": self.replies[min(len(self.sent), len(self.replies)) - 1]},
        }


class NlqRunnerTest(unittest.TestCase):
    def test_a_question_back_is_answered_by_its_choices_label(self) -> None:
        api = ChatApi([ASKS, ANSWERS])
        asked = ask_all(api, [nlq_item()], {"d1": "k.pdf"}, io.StringIO(), nlq=True)
        self.assertEqual(api.sent[1], {"question": "Maval, Pune", "conversation_id": "c1"})
        self.assertEqual((asked.requests, asked.rows[0][1].outcome), (2, Outcome.RIGHT))
        self.assertEqual(asked.nlq[0].passed(), {"clarify_turns": True, "clarify": True})
        self.assertEqual(asked.openings, {"n1": []})

    def test_a_third_question_back_is_a_violation(self) -> None:
        api = ChatApi([ASKS])
        asked = ask_all(api, [nlq_item()], {}, io.StringIO(), nlq=True)
        self.assertEqual((len(api.sent), asked.nlq[0].clarify_turns), (3, 3))
        self.assertFalse(asked.nlq[0].passed()["clarify_turns"])
        self.assertEqual(asked.rows[0][1].outcome, Outcome.CLARIFIED)
        self.assertEqual(asked.openings["n1"], ["reply 2 repeats an opening", "reply 3 repeats an opening"])

    def test_a_pick_not_offered_stops_the_chat(self) -> None:
        api = ChatApi([ASKS, ANSWERS])
        asked = ask_all(api, [nlq_item("Panvel")], {}, io.StringIO(), nlq=True)
        self.assertEqual((len(api.sent), asked.rows[0][1].outcome), (1, Outcome.CLARIFIED))
        self.assertEqual(
            ask_all(ChatApi([ASKS, ANSWERS]), [nlq_item()], {}, io.StringIO()).requests, 1
        )  # not nlq

    def test_a_429_or_503_is_retried_after_its_retry_after(self) -> None:
        slept: list[float] = []

        class Api(Http):
            def __init__(self, replies: list[tuple[int, str, bytes]]) -> None:
                super().__init__("http://127.0.0.1:1", sleep=slept.append)
                self.replies = replies

            def _send(self, *args: Any) -> tuple[int, str, bytes]:
                return self.replies.pop(0)

        self.assertEqual(
            Api([(429, "7", b""), (503, "", b""), (200, "", b'{"ok": 1}')]).call("GET", "/x"), {"ok": 1}
        )
        self.assertEqual(slept, [7.0, RETRY_CAP])
        with self.assertRaisesRegex(GoldenError, "answered HTTP 429"):
            Api([(429, "999", b"")] * (RETRIES + 1)).call("GET", "/x")
        self.assertEqual(slept[2:], [RETRY_CAP] * RETRIES)

    def test_upload_waits_until_no_document_is_being_understood(self) -> None:
        looks = [{"status": "ready", "understanding": True}, {"status": "ready", "understanding": False}]

        class Api(Http):
            def call(self, method: str, path: str, body: Any = None, **kwargs: Any) -> Any:
                return {"document": {"id": "a"}} if method == "POST" else looks.pop(0)

        with mock.patch("scripts.run_golden.time.sleep") as sleep:
            self.assertEqual(
                upload(Api("http://127.0.0.1:1"), {"a.pdf": b"a"}, 1, io.StringIO()), {"a": "a.pdf"}
            )
        self.assertEqual((looks, sleep.call_count), ([], 1))

    def test_each_nlq_run_has_a_customer_of_its_own_and_the_report_carries_the_expectations(self) -> None:
        def asked(api: Any, questions: list[Question], *args: Any) -> Asked:
            return Asked(
                [(q, score(q, {}), None) for q in questions], [NlqScore() for _ in questions], {"n1": []}, 1
            )

        settings = make_settings(model_chat=GEMINI_CHAT, model_embed=GEMINI_EMBED, database_url=LOCAL_DB)
        made: dict[str, Any] = {}
        for question_set, customers in (("nlq", 2), ("golden", 1)):
            with (
                tempfile.TemporaryDirectory() as folder,
                mock.patch("scripts.run_golden.Settings.from_env", return_value=settings),
                mock.patch("scripts.run_golden.sign_in") as sign_in,
                mock.patch("scripts.run_golden.upload", return_value={}),
                mock.patch("scripts.run_golden.ask_all", side_effect=asked),
                mock.patch("scripts.run_golden.write_usage"),
            ):
                path = Path(folder) / "run.json"
                argv = ["--git-sha", "abc", "--set", question_set, "--runs", "2", "--json", str(path)]
                self.assertEqual(main(argv, out=io.StringIO()), 0)
                made[question_set] = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(sign_in.call_count, customers, question_set)
        nlq = made["nlq"]
        self.assertEqual(
            (nlq["settings"]["ANSWER_WHOLE_DOC_TOKENS"], nlq["questions_sha256"]),
            (120000, SETS["nlq"].sha256),
        )
        self.assertEqual(nlq["questions"]["n001"]["expect"], {"clarify_turns": True})
        self.assertEqual(nlq["nlq_metrics"]["openings"]["count"], 1)
        self.assertNotIn("nlq_metrics", made["golden"])
        other = {**nlq, "settings": {**nlq["settings"], "ANSWER_WHOLE_DOC_TOKENS": 0}}
        out = io.StringIO()
        self.assertFalse(check_against(other, nlq, out))
        self.assertIn(
            "GATE FAILED: the baseline ran at ANSWER_WHOLE_DOC_TOKENS=0, this run at 120000", out.getvalue()
        )
