"""Ask the golden questions against a live stack and score them (PRD 3 G1).

    cd backend
    python -m scripts.run_golden --git-sha "$(git rev-parse HEAD)" [--api http://127.0.0.1:8000]
        [--set golden|xlang] [--docs DIR] [--runs 3]

Run it with the stack's settings loaded in the shell, as for ``kaagaz.cli``
(docs/running-locally.md), and the model on for a real baseline. Never in CI.

It refuses an API or database off this machine without ``--allow-remote``, and
any document in ``--docs`` or question that differs from what is committed (a
changed file means a new baseline). Then it creates a customer "Golden Test Co"
with ``kaagaz.cli create-customer``, signs in as its admin (the password from
``KAAGAZ_ADMIN_PASSWORD``, or made with ``secrets.token_urlsafe``; never
printed), uploads the documents and waits until they are ready. It asks every
question ``--runs`` times through POST /api/chat, a follow-up in its
question's conversation, and prints a row per question, each run's totals and
slices, and the worst run with Wilson intervals. The header shows this shell's
settings, ANSWER_WHOLE_DOC_TOKENS among them (PRD 5 M5 compares runs with it at 0, so
search alone finds the evidence), which may not be the server's, so each run also counts the search
answers the model wrote, and warns when none were.

recall@5 uses the answer's citations, in order, as the hits, each spanning its
regions' pages; a not-found answer cites nothing, so it is a miss. Part 3.2
adds GET /api/search, and recall then comes from its top 5: a new baseline,
not comparable with this one.

``--set`` picks the questions, each file frozen by its own sha256, with its own
documents: ``golden`` (the default) is questions.json over docs/; ``xlang`` is
questions_xlang.json over the synthetic scans in xlang/ (council 4 Oct 2026). An
xlang document that is not ready is reported and its questions are still asked:
an unread page is part of what that set measures.

A command line report, so it writes to standard output, not to the log.
"""

import argparse
import io
import json
import os
import secrets
import sys
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

from kaagaz import cli
from kaagaz.api.auth import CSRF_HEADER, CSRF_VALUE, SESSION_COOKIE
from kaagaz.settings import Settings, SettingsError, is_gemini_model
from scripts import golden_scoring as scoring

GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "golden"
CUSTOMER = "Golden Test Co"
LOCAL = {"localhost", "127.0.0.1", "::1"}
DONE = {"ready", "rejected", "needs_reader", "failed"}  # statuses a document stays in
CHAT_TIMEOUT = 600.0  # seconds: one answer may wait on the model
CALL_TIMEOUT = 30.0  # seconds, for every other call; an upload gets 4 times this

Row = tuple[scoring.Question, scoring.Score, bool | None]


@dataclass(frozen=True, slots=True)
class QuestionSet:
    questions: str  # a file in tests/golden
    sha256: str
    docs: Path  # the default --docs
    manifest: Path  # the committed sha256 of every document
    check: Callable[[Any, Collection[str]], list[str]]
    all_ready: bool  # every document must be ready before a question is asked


SETS = {
    "golden": QuestionSet(
        "questions.json",
        scoring.QUESTIONS_SHA256,
        GOLDEN / "docs",
        GOLDEN / "documents.sha256",
        scoring.problems,
        all_ready=True,
    ),
    "xlang": QuestionSet(
        "questions_xlang.json",
        scoring.XLANG_SHA256,
        GOLDEN / "xlang",
        GOLDEN / "xlang" / "documents.sha256",
        scoring.xlang_problems,
        all_ready=False,
    ),
}


class GoldenError(Exception):
    """Stops the run with a short message and exit code 2."""


class Http:
    """JSON over one connection per call; it keeps the session cookie it is given."""

    def __init__(self, base: str) -> None:
        parts = urlsplit(base)
        self._https, self._host, self._port = parts.scheme == "https", parts.hostname or "", parts.port
        self._cookie = ""

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        files: tuple[str, bytes] | None = None,
        timeout: float = CALL_TIMEOUT,
    ) -> Any:
        headers = {CSRF_HEADER: CSRF_VALUE, "Cookie": self._cookie}
        data = None if body is None else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
        if files is not None:
            boundary = secrets.token_hex(16)
            name, content = files
            data = (
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                    "Content-Type: application/octet-stream\r\n\r\n"
                ).encode()
                + content
                + f"\r\n--{boundary}--\r\n".encode()
            )
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        connect = HTTPSConnection if self._https else HTTPConnection
        connection = connect(self._host, self._port, timeout=timeout)
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            reply = response.read()
        finally:
            connection.close()
        for cookie in response.headers.get_all("Set-Cookie") or []:
            if cookie.startswith(f"{SESSION_COOKIE}="):
                self._cookie = cookie.split(";", 1)[0]
        if response.status >= 400:
            raise GoldenError(f"{method} {path} answered HTTP {response.status}")
        return json.loads(reply) if reply else {}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.run_golden", description=__doc__.split("\n")[0])
    parser.add_argument("--git-sha", required=True, help='the commit under test: "$(git rev-parse HEAD)"')
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--set", choices=sorted(SETS), default="golden", dest="question_set")
    parser.add_argument("--docs", type=Path, help="default: the set's own folder")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--wait-minutes", type=float, default=30.0)
    parser.add_argument(
        "--allow-remote", action="store_true", help="allow an API or database not on this machine"
    )
    return parser


def check_local(api: str, database_url: str, allow_remote: bool) -> None:
    hosts = {urlsplit(api).hostname, urlsplit(database_url).hostname}
    if not allow_remote and not hosts <= LOCAL:
        raise GoldenError("the API and database must be on this machine (or pass --allow-remote)")


def load_golden(
    docs: Path | None = None, chosen: QuestionSet = SETS["golden"]
) -> tuple[list[scoring.Question], dict[str, bytes]]:
    """The questions and the documents, refused unless both match what is committed."""
    docs = chosen.docs if docs is None else docs
    raw = json.loads((GOLDEN / chosen.questions).read_text(encoding="utf-8"))
    manifest = scoring.sha256_manifest(chosen.manifest.read_text(encoding="utf-8"))
    found = chosen.check(raw, manifest)
    if scoring.file_sha256(GOLDEN / chosen.questions) != chosen.sha256:
        found.append(f"{chosen.questions} differs from its sha256 in golden_scoring")
    listed = chosen.manifest.relative_to(GOLDEN.parent).as_posix()
    for name, digest in manifest.items():
        path = docs / name
        if not path.is_file() or scoring.file_sha256(path) != digest:
            found.append(f"{name} is missing from --docs or differs from tests/{listed}")
    if found:
        raise GoldenError("; ".join(found))
    documents = {name: (docs / name).read_bytes() for name in manifest}
    return [scoring.question_from(item) for item in raw], documents


def model_digests(settings: Settings) -> str:
    """The chat and embedding models' digests from Ollama's /api/tags, if it answers. A
    google/gemini- model runs on OpenRouter (PRD 5 M1, M5), so it has no Ollama digest and
    Ollama is not asked when neither model is on it."""
    names = (settings.model_chat, settings.model_embed)
    digests: dict[Any, str] = {}
    if not all(map(is_gemini_model, names)):
        try:
            models = Http(settings.model_base_url).call("GET", "/api/tags", timeout=5).get("models", [])
        except (OSError, GoldenError, HTTPException, ValueError):
            return "digests unknown (Ollama did not answer)"
        digests = {m.get("name"): str(m.get("digest", ""))[:12] for m in models if isinstance(m, dict)}
    return " ".join(
        f"{name}@{'openrouter' if is_gemini_model(name) else digests.get(name, 'not-pulled')}"
        for name in names
    )


def header(out: TextIO, settings: Settings, api: str, sha: str, questions: str = "questions.json") -> None:
    out.write(f"Golden run {datetime.now(UTC).isoformat(timespec='seconds')}  git {sha}  api {api}\n")
    out.write(f"{questions} sha256 {scoring.file_sha256(GOLDEN / questions)}\n")
    out.write(
        f"MODEL_ENABLED={settings.model_enabled} MODEL_CHAT={settings.model_chat}"
        f" MODEL_EMBED={settings.model_embed} OCR_ENGINE={settings.ocr_engine}"
        f" ANSWER_MAX_EVIDENCE={settings.answer_max_evidence}"
        f" ANSWER_MIN_EVIDENCE_SCORE={settings.answer_min_evidence_score}"
        f" ANSWER_WHOLE_DOC_TOKENS={settings.answer_whole_doc_tokens} TIMEZONE={settings.timezone}\n\n"
    )


def sign_in(api: Http, password: str, out: TextIO) -> None:
    """A new "Golden Test Co" and its admin, made by ``kaagaz.cli``, then signed in."""
    email = f"golden-{datetime.now(UTC):%Y%m%d%H%M%S}@golden-test.example"
    made = io.StringIO()
    argv = ["create-customer", "--name", CUSTOMER, "--admin-email", email, "--admin-name", "Golden admin"]
    if cli.main(argv, environ={**os.environ, cli.ADMIN_ENV_VAR: password}, out=made, ask=None) != 0:
        raise GoldenError("kaagaz.cli create-customer failed (its message is above)")
    out.write(f"{CUSTOMER}: {made.getvalue().strip()}, admin {email}\n")
    api.call("POST", "/api/auth/login", {"email": email, "password": password})


def upload(
    api: Http, documents: dict[str, bytes], wait_minutes: float, out: TextIO, all_ready: bool = True
) -> dict[str, str]:
    """Upload every document and wait until none is still being read. {document id: file name}.
    Without ``all_ready``, only none ready stops the run; the others are listed."""
    names = {}
    for name, data in documents.items():
        reply = api.call("POST", "/api/documents", files=(name, data), timeout=4 * CALL_TIMEOUT)
        names[reply["document"]["id"]] = name
    deadline = time.monotonic() + wait_minutes * 60
    while True:  # at least one look, however short the wait
        status = {i: api.call("GET", f"/api/documents/{i}")["status"] for i in names}
        if set(status.values()) <= DONE or time.monotonic() > deadline:
            break
        time.sleep(5)
    not_ready = {names[i]: s for i, s in status.items() if s != "ready"}
    if not_ready and (all_ready or len(not_ready) == len(names)):
        raise GoldenError(f"documents not ready after {wait_minutes} minutes: {not_ready}")
    if not_ready:
        out.write(f"NOT READY, their questions are still asked: {not_ready}\n")
    out.write(f"{len(names) - len(not_ready)} documents ready\n\n")
    return names


def hits(answer: dict[str, Any], names: dict[str, str]) -> list[scoring.Hit]:
    """The answer's citations as hits, each spanning its regions' pages.
    ponytail: citations are the evidence the answer kept, not the search's top 5, so
    this recall stands in for search recall until GET /api/search (Part 3.2)."""
    found = []
    for citation in answer.get("citations") or []:
        regions = citation.get("regions") or []
        pages = [int(r["page"]) for r in regions if "page" in r] or [int(citation["page_no"])]
        found.append(scoring.Hit(names.get(citation["document_id"], "?"), min(pages), max(pages)))
    return found


def ask_all(api: Http, questions: list[scoring.Question], names: dict[str, str], out: TextIO) -> list[Row]:
    conversations: dict[str, str] = {}
    rows: list[Row] = []
    searched = modelled = 0
    out.write(f"{'id':<5} {'outcome':<21} {'label':<13} {'route':<12} {'recall':<6} answer\n")
    for question in questions:
        body: dict[str, Any] = {"question": question.question}
        if question.follow_up_of is not None:
            body["conversation_id"] = conversations[question.follow_up_of]
        reply = api.call("POST", "/api/chat", body, timeout=CHAT_TIMEOUT)
        conversations[question.id] = reply["conversation_id"]
        answer = reply["answer"]["answer"]
        if answer["route"] == "search":
            searched += 1
            modelled += answer.get("model_used") is True
        marked = scoring.score(question, answer)
        counted = scoring.counts_for_recall(question, answer["route"])
        recall = scoring.recall_hit(question, hits(answer, names)) if counted else None
        rows.append((question, marked, recall))
        flag = " WRONG-VERIFIED" if marked.wrong_verified else ""
        out.write(
            f"{question.id:<5} {marked.outcome.value + flag:<21} {answer.get('label') or '-':<13}"
            f" {answer['route']:<12} {'-' if recall is None else 'hit' if recall else 'miss':<6}"
            f" {scoring.answer_text(answer)[:70]}\n"
        )
    out.write(f"The model wrote {modelled} of {searched} search-route answers.\n")
    if searched and not modelled:
        out.write("WARNING: the model was off or down, so this is not a model-on baseline.\n")
    return rows


def write_totals(out: TextIO, label: str, total: scoring.Totals) -> None:
    """One totals line. "right or not-found" is the count G3's "24 of 30 right" reads."""
    counts = total.outcomes
    right = counts[scoring.Outcome.RIGHT] + counts[scoring.Outcome.CORRECT_NOT_FOUND]
    low, high = scoring.wilson(right, total.questions)
    recall_low, recall_high = scoring.wilson(total.recall_hits, total.recall_of)
    out.write(
        f"{label:<16} right or not-found {right}/{total.questions} [{low:.2f}, {high:.2f}]"
        + "".join(f"  {outcome.value} {n}" for outcome, n in counts.items())
        + f"  wrong labelled verified {total.wrong_verified}"
        f"  recall@5 {total.recall_hits}/{total.recall_of} [{recall_low:.2f}, {recall_high:.2f}]\n"
    )


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.runs < 1 or args.wait_minutes <= 0:
            raise GoldenError("--runs must be at least 1 and --wait-minutes above 0")
        settings = Settings.from_env(os.environ)
        check_local(args.api, settings.database_url, args.allow_remote)
        chosen = SETS[args.question_set]
        questions, documents = load_golden(args.docs, chosen)
        header(out, settings, args.api, args.git_sha, chosen.questions)
        api = Http(args.api)
        sign_in(api, os.environ.get(cli.ADMIN_ENV_VAR) or secrets.token_urlsafe(24), out)
        names = upload(api, documents, args.wait_minutes, out, chosen.all_ready)
        runs: list[scoring.Totals] = []
        for run in range(1, args.runs + 1):
            out.write(f"Run {run} of {args.runs}\n")
            rows = ask_all(api, questions, names, out)
            runs.append(scoring.totals(rows))
            write_totals(out, f"run {run}", runs[-1])
            for name, total in scoring.by_slice(rows).items():
                write_totals(out, f"  {name}", total)
            out.write("\n")
        write_totals(out, "WORST RUN", scoring.worst(runs))
    except (GoldenError, SettingsError, OSError, HTTPException, ValueError, KeyError) as error:
        # Short codes and names only: no message here carries the password.
        sys.stderr.write(f"error: {type(error).__name__}: {error}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
