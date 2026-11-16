"""Ask the golden questions against a live stack and score them (PRD 3 G1).

    cd backend
    python -m scripts.run_golden --git-sha "$(git rev-parse HEAD)" [--api http://127.0.0.1:8000]
        [--set golden|xlang|romanised|nlq] [--docs DIR] [--runs 3] [--agents ID,ID]
        [--thinking low|medium|high] [--json RUN.json] [--compare BASE.json]

Run it with the stack's settings loaded in the shell, as for ``kaagaz.cli``
(docs/running-locally.md), and the model on for a real baseline. Never in CI.

It refuses an API or database off this machine without ``--allow-remote``, and
any document in ``--docs`` or question that differs from what is committed (a
changed file means a new baseline). Then it creates a customer "Golden Test Co"
with ``kaagaz.cli create-customer``, signs in as its admin (the password from
``KAAGAZ_ADMIN_PASSWORD``, or made with ``secrets.token_urlsafe``; never
printed), uploads the documents and waits until they are ready and no longer being
understood (``understanding`` false: their card and map are done). It asks every
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
an unread page is part of what that set measures. ``romanised`` (ask precision PR 1a)
is questions_romanised.json over docs/, each spelling asked as its own question.

After the runs it prints the precision numbers (golden_scoring's ``metrics``, with
Wilson intervals, reported and never a gate) over each question's worst run.
``--json`` writes them, per slice too, with the worst-run totals, each question's worst
outcome, the git sha, MODEL_CHAT, MODEL_EMBED and ANSWER_PROMPT_HASH. ``--compare``
reads such a file of the same set (the baseline) and applies the paired gate
(golden_scoring's ``compare``): it lists every changed question and exits 1 when the gate
fails.

``nlq`` (PRD 6 package G) is questions_nlq.json over the made-up documents in nlq/, each
run with a customer of its own (admin ASKER), so no run learns from another. A reply that
asks back gets the expected pick, by its choice's label, as the next question (a stub until
package B names a reply field), at most MAX_CLARIFY_TURNS times. The NLQ expectations
(golden_scoring's ``nlq_score``) and each chat's openings go in the report and ``--compare``.
The set fits whole-document mode, so run it with the server at ANSWER_WHOLE_DOC_TOKENS=0
(search alone) and at its default; ``--compare`` refuses a baseline of the other mode.

A 429 or 503 is retried after its Retry-After (at most RETRY_CAP seconds), RETRIES times.
Each run prints its chat requests and the customer's AI use (GET /api/admin/usage). The
header and report record the settings and their sha256, ANSWER_PROMPT_HASH and the set's
and the scorer's sha256.

``--agents`` and ``--thinking`` (features 25-27, council 15 Oct 2026, C14) send every
question with those answer models (comma-separated ids, the first leads) and that thinking
level, as the composer's Answer settings do; the organisation must be allowed them (the
operator console), or each question is refused. Left out, the organisation's defaults.

A command line report, so it writes to standard output, not to the log.
"""

import argparse
import hashlib
import io
import json
import os
import secrets
import sys
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

from kaagaz import cli
from kaagaz.answering.draft import ANSWER_PROMPT_HASH
from kaagaz.api.auth import CSRF_HEADER, CSRF_VALUE, SESSION_COOKIE
from kaagaz.settings import Settings, SettingsError
from scripts import golden_scoring as scoring

GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "golden"
CUSTOMER = "Golden Test Co"
LOCAL = {"localhost", "127.0.0.1", "::1"}
DONE = {"ready", "rejected", "needs_reader", "failed"}  # statuses a document stays in
CHAT_TIMEOUT = 600.0  # seconds: one answer may wait on the model
CALL_TIMEOUT = 30.0  # seconds, for every other call; an upload gets 4 times this
RETRIES, RETRY_CAP = 5, 120.0  # a 429 or 503: tries again, and the longest Retry-After waited (s)
ASKER = "Tanvi"  # the NLQ customer's admin's first name (made up)

Row = tuple[scoring.Question, scoring.Score, bool | None]


@dataclass(frozen=True, slots=True)
class QuestionSet:
    questions: str  # a file in tests/golden
    sha256: str
    docs: Path  # the default --docs
    manifest: Path  # the committed sha256 of every document
    check: Callable[[Any, Collection[str]], list[str]]
    all_ready: bool  # every document must be ready before a question is asked
    spellings: bool = False  # items with spellings, each asked as its own question
    nlq: bool = False  # PRD 6: a customer per run, clarify replies, the NLQ expectations


@dataclass(slots=True)
class Asked:
    """One run: its rows, and for the NLQ set each question's marks (in the rows' order)
    and each chat's opening problems (by its first question's id)."""

    rows: list[Row] = field(default_factory=list)
    nlq: list[scoring.NlqScore] = field(default_factory=list)
    openings: dict[str, list[str]] = field(default_factory=dict)
    requests: int = 0  # POST /api/chat calls, the replies to questions back included


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
    "romanised": QuestionSet(
        "questions_romanised.json",
        scoring.ROMANISED_SHA256,
        GOLDEN / "docs",
        GOLDEN / "documents.sha256",
        scoring.romanised_problems,
        all_ready=True,
        spellings=True,
    ),
    "nlq": QuestionSet(
        "questions_nlq.json",
        scoring.NLQ_SHA256,
        GOLDEN / "nlq",
        GOLDEN / "nlq" / "documents.sha256",
        scoring.nlq_problems,
        all_ready=True,
        nlq=True,
    ),
}


class GoldenError(Exception):
    """Stops the run with a short message and exit code 2."""


class Http:
    """JSON over one connection per call; it keeps the session cookie it is given, and
    waits out a 429 or 503 (``Retry-After``, at most RETRY_CAP seconds) up to RETRIES times."""

    def __init__(self, base: str, sleep: Callable[[float], None] = time.sleep) -> None:
        parts = urlsplit(base)
        self._https, self._host, self._port = parts.scheme == "https", parts.hostname or "", parts.port
        self._cookie = ""
        self._sleep = sleep

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        files: tuple[str, bytes] | None = None,
        timeout: float = CALL_TIMEOUT,
    ) -> Any:
        headers = {CSRF_HEADER: CSRF_VALUE}
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
        for attempt in range(RETRIES + 1):
            status, wait, reply = self._send(method, path, data, headers, timeout)
            if status not in (429, 503) or attempt == RETRIES:
                break
            self._sleep(min(RETRY_CAP, float(wait) if wait.isdigit() else RETRY_CAP))
        if status >= 400:
            raise GoldenError(f"{method} {path} answered HTTP {status}")
        return json.loads(reply) if reply else {}

    def _send(
        self, method: str, path: str, data: bytes | None, headers: dict[str, str], timeout: float
    ) -> tuple[int, str, bytes]:
        """One request: its status, Retry-After ("" when none) and body."""
        connect = HTTPSConnection if self._https else HTTPConnection
        connection = connect(self._host, self._port, timeout=timeout)
        try:
            connection.request(method, path, body=data, headers={**headers, "Cookie": self._cookie})
            response = connection.getresponse()
            reply = response.read()
        finally:
            connection.close()
        for cookie in response.headers.get_all("Set-Cookie") or []:
            if cookie.startswith(f"{SESSION_COOKIE}="):
                self._cookie = cookie.split(";", 1)[0]
        return response.status, (response.headers.get("Retry-After") or "").strip(), reply


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.run_golden", description=__doc__.split("\n")[0])
    parser.add_argument("--git-sha", required=True, help='the commit under test: "$(git rev-parse HEAD)"')
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--set", choices=sorted(SETS), default="golden", dest="question_set")
    parser.add_argument("--docs", type=Path, help="default: the set's own folder")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--wait-minutes", type=float, default=30.0)
    parser.add_argument("--agents", help="answer models, comma-separated ids (default: the organisation's)")
    parser.add_argument("--thinking", choices=["low", "medium", "high"], help="the answer's thinking level")
    parser.add_argument("--json", type=Path, dest="json_path", help="write the precision report here")
    parser.add_argument("--compare", type=Path, help="a --json report of the same set: the baseline")
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
    items = scoring.spelled_out(raw) if chosen.spellings else raw
    return [scoring.question_from(item) for item in items], documents


SETTINGS_USED = "model_enabled model_chat model_embed ocr_engine answer_max_evidence"
SETTINGS_USED += " answer_min_evidence_score answer_whole_doc_tokens timezone"


def settings_used(settings: Settings) -> dict[str, Any]:
    """The settings a run depends on: the header's, for the report and its sha256."""
    return {name.upper(): getattr(settings, name) for name in SETTINGS_USED.split()}


def fingerprint(settings: Settings) -> str:
    return hashlib.sha256(
        json.dumps(settings_used(settings), sort_keys=True, default=str).encode()
    ).hexdigest()


def header(out: TextIO, settings: Settings, api: str, sha: str, questions: str = "questions.json") -> None:
    out.write(f"Golden run {datetime.now(UTC).isoformat(timespec='seconds')}  git {sha}  api {api}\n")
    out.write(f"{questions} sha256 {scoring.file_sha256(GOLDEN / questions)}\n")
    out.write(f"scorer sha256 {scoring.file_sha256(Path(scoring.__file__))}  ANSWER_PROMPT_HASH")
    out.write(f" {ANSWER_PROMPT_HASH}  settings sha256 {fingerprint(settings)}\n")
    out.write(
        f"MODEL_ENABLED={settings.model_enabled} MODEL_CHAT={settings.model_chat}"
        f" MODEL_EMBED={settings.model_embed} OCR_ENGINE={settings.ocr_engine}"
        f" ANSWER_MAX_EVIDENCE={settings.answer_max_evidence}"
        f" ANSWER_MIN_EVIDENCE_SCORE={settings.answer_min_evidence_score}"
        f" ANSWER_WHOLE_DOC_TOKENS={settings.answer_whole_doc_tokens} TIMEZONE={settings.timezone}\n\n"
    )


def sign_in(api: Http, password: str, out: TextIO, admin_name: str = "Golden admin") -> None:
    """A new "Golden Test Co" and its admin, made by ``kaagaz.cli``, then signed in."""
    email = f"golden-{datetime.now(UTC):%Y%m%d%H%M%S}-{secrets.token_hex(3)}@golden-test.example"
    made = io.StringIO()
    argv = ["create-customer", "--name", CUSTOMER, "--admin-email", email, "--admin-name", admin_name]
    if cli.main(argv, environ={**os.environ, cli.ADMIN_ENV_VAR: password}, out=made, ask=None) != 0:
        raise GoldenError("kaagaz.cli create-customer failed (its message is above)")
    out.write(f"{CUSTOMER}: {made.getvalue().strip()}, admin {email}\n")
    api.call("POST", "/api/auth/login", {"email": email, "password": password})


def upload(
    api: Http, documents: dict[str, bytes], wait_minutes: float, out: TextIO, all_ready: bool = True
) -> dict[str, str]:
    """Upload every document and wait until none is still being read or understood (its card
    and map). {document id: file name}. Without ``all_ready``, only none ready stops the run;
    the others are listed."""
    names = {}
    for name, data in documents.items():
        reply = api.call("POST", "/api/documents", files=(name, data), timeout=4 * CALL_TIMEOUT)
        names[reply["document"]["id"]] = name
    deadline = time.monotonic() + wait_minutes * 60
    while True:  # at least one look, however short the wait
        seen = {i: api.call("GET", f"/api/documents/{i}") for i in names}
        status = {i: document["status"] for i, document in seen.items()}
        understanding = sorted(names[i] for i, document in seen.items() if document.get("understanding"))
        if (set(status.values()) <= DONE and not understanding) or time.monotonic() > deadline:
            break
        time.sleep(5)
    not_ready = {names[i]: s for i, s in status.items() if s != "ready"}
    if not_ready and (all_ready or len(not_ready) == len(names)):
        raise GoldenError(f"documents not ready after {wait_minutes} minutes: {not_ready}")
    if not_ready:
        out.write(f"NOT READY, their questions are still asked: {not_ready}\n")
    if understanding:
        out.write(f"WARNING: still being understood after {wait_minutes} minutes: {understanding}\n")
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


def ask_all(
    api: Http,
    questions: list[scoring.Question],
    names: dict[str, str],
    out: TextIO,
    choice: dict[str, Any] | None = None,
    nlq: bool = False,
) -> Asked:
    """``choice``: the answer settings sent with every question (``agents``, ``thinking``).
    ``nlq``: answer questions back (``pick_choice``), ask in a question's ``scope`` and mark
    the NLQ expectations and each chat's openings."""
    conversations: dict[str, str] = {}
    chats: dict[str, str] = {}  # conversation id: its first question's id
    openings: dict[str, list[str | None]] = {}
    ids = {name: i for i, name in names.items()}
    asked = Asked()
    searched = modelled = 0
    out.write(f"{'id':<5} {'outcome':<21} {'label':<13} {'route':<12} {'recall':<6} answer\n")
    for question in questions:
        body: dict[str, Any] = {"question": question.question, **(choice or {})}
        if question.follow_up_of is not None:
            body["conversation_id"] = conversations[question.follow_up_of]
        scope = question.nlq.get("scope")
        if nlq and scope is not None:
            # Package K (#644): {"all": true} switches the scope to All documents mid-conversation.
            body["scope"] = {} if scope.get("all") else {"document_ids": [ids[scope["document"]]]}
        replies = [api.call("POST", "/api/chat", body, timeout=CHAT_TIMEOUT)]
        conversation = conversations[question.id] = replies[0]["conversation_id"]
        while nlq and len(replies) <= scoring.MAX_CLARIFY_TURNS:
            label = pick_choice(replies[-1]["answer"]["answer"], question.nlq.get("clarify"))
            if label is None:
                break
            again = {"question": label, "conversation_id": conversation, **(choice or {})}
            replies.append(api.call("POST", "/api/chat", again, timeout=CHAT_TIMEOUT))
        asked.requests += len(replies)
        first, answer = replies[0]["answer"]["answer"], replies[-1]["answer"]["answer"]
        route = answer.get("route")
        if route == "search":
            searched += 1
            modelled += answer.get("model_used") is True
        cited = hits(answer, names)
        marked = scoring.score(question, answer, cited)
        recall = scoring.recall_hit(question, cited) if scoring.counts_for_recall(question, route) else None
        asked.rows.append((question, marked, recall))
        flag, failed = " WRONG-VERIFIED" if marked.wrong_verified else "", ""
        if nlq:
            backs = sum(scoring.clarify_of(reply["answer"]["answer"]) is not None for reply in replies)
            asked.nlq.append(scoring.nlq_score(question, first, answer, names, ASKER, backs))
            failed = " ".join(f"FAILS {name}" for name, ok in asked.nlq[-1].passed().items() if not ok)
            chat = chats.setdefault(conversation, question.id)
            openings.setdefault(chat, []).extend(
                reply["answer"]["answer"].get("opening") for reply in replies
            )
        out.write(
            f"{question.id:<5} {marked.outcome.value + flag:<21} {answer.get('label') or '-':<13}"
            f" {route or '-':<12} {'-' if recall is None else 'hit' if recall else 'miss':<6}"
            f" {scoring.answer_text(answer)[:70]}{' ' if failed else ''}{failed}\n"
        )
    out.write(f"The model wrote {modelled} of {searched} search-route answers.\n")
    if searched and not modelled:
        out.write("WARNING: the model was off or down, so this is not a model-on baseline.\n")
    asked.openings = {chat: scoring.opening_problems(said, ASKER) for chat, said in openings.items()}
    return asked


def pick_choice(answer: Mapping[str, Any], expected: Any) -> str | None:
    """The label to send back when the reply asks back: the offered choice that holds the
    expected pick, or that the pick holds; None when it does not ask, no pick is expected
    or none fits. ponytail: sent as the next question's text until package B names the
    reply field (a choice id)."""
    clarify = scoring.clarify_of(answer)
    if clarify is None or not isinstance(expected, Mapping):
        return None
    pick = str(expected["pick"])
    fits = [
        c for c in scoring.choice_labels(clarify) if scoring.phrase_in(pick, c) or scoring.phrase_in(c, pick)
    ]
    return fits[0] if fits else None


def answer_settings(agents: str | None, thinking: str | None) -> dict[str, Any]:
    """The --agents and --thinking choice as POST /api/chat's fields; {} for neither."""
    choice: dict[str, Any] = {}
    if agents:
        choice["agents"] = [agent.strip() for agent in agents.split(",") if agent.strip()]
    if thinking:
        choice["thinking"] = thinking
    return choice


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


def write_metrics(out: TextIO, rates: dict[str, Any]) -> None:
    """A report's ``metrics``, one line each, with the Wilson interval."""
    for name, rate in rates.items():
        low, high = rate["wilson"]
        out.write(f"  {name:<24} {rate['count']}/{rate['of']} [{low:.2f}, {high:.2f}]\n")


def write_usage(api: Http, out: TextIO, asked: Asked, questions: int) -> None:
    """The run's chat requests, and the customer's AI use this month (GET /api/admin/usage):
    model calls and tokens, in all and by kind. With a customer per run, that is the run's."""
    usage = api.call("GET", "/api/admin/usage")

    def use(group: Mapping[str, Any]) -> str:
        calls, tokens_in, tokens_out = (
            group.get(key, 0) for key in ("calls", "input_tokens", "output_tokens")
        )
        return f"{calls} model calls, {tokens_in} tokens in, {tokens_out} out"

    out.write(f"{questions} questions, {asked.requests} chat requests; the customer's AI use: ")
    out.write(f"{use(usage.get('totals') or {})}\n")
    out.writelines(f"  {group.get('kind')}: {use(group)}\n" for group in usage.get("by_kind") or [])


def report(
    set_name: str, settings: Settings, sha: str, runs: Sequence[Sequence[Row]], nlq: Sequence[Asked] = ()
) -> dict[str, Any]:
    """The --json report: each question's worst run over ``runs`` (each run's rows, in
    the same order), its precision numbers overall and per slice, and the worst-run totals;
    for the NLQ set (``nlq``, each run's marks) each expectation's numbers, the openings and
    each question's expectations passed at its worst run."""
    worst = [
        (question, scoring.worst_score([run[i][1] for run in runs]))
        for i, (question, _, _) in enumerate(runs[0])
    ]
    slices = sorted({name for question, _ in worst for name in question.slices})
    total = scoring.worst([scoring.totals(run) for run in runs])
    marks = [scoring.worst_nlq([one.nlq[i] for one in nlq]) for i in range(len(worst))] if nlq else []
    chats: dict[str, list[str]] = {}
    for one in nlq:
        for chat, problems in one.openings.items():
            known = chats.setdefault(chat, [])
            known += [problem for problem in problems if problem not in known]

    def rates(numbers: dict[str, tuple[int, int]]) -> dict[str, Any]:
        return {
            name: {"count": n, "of": of, "wilson": scoring.wilson(n, of)} for name, (n, of) in numbers.items()
        }

    expect = {"openings": (sum(not p for p in chats.values()), len(chats))} if nlq else {}
    return {
        "set": set_name,
        "git_sha": sha,
        "MODEL_CHAT": settings.model_chat,
        "MODEL_EMBED": settings.model_embed,
        "ANSWER_PROMPT_HASH": ANSWER_PROMPT_HASH,
        "settings": settings_used(settings),
        "settings_sha256": fingerprint(settings),
        "questions_sha256": SETS[set_name].sha256,
        "scorer_sha256": scoring.file_sha256(Path(scoring.__file__)),
        "runs": len(runs),
        "totals": {
            **{outcome.value: n for outcome, n in total.outcomes.items()},
            "wrong_verified": total.wrong_verified,
            "recall_hits": total.recall_hits,
            "recall_of": total.recall_of,
        },
        "metrics": rates(scoring.metrics(worst)),
        "slices": {
            name: rates(scoring.metrics([r for r in worst if name in r[0].slices])) for name in slices
        },
        "questions": {
            question.id: {
                "outcome": scoring.category(marked),
                "verified": marked.verified,
                **({"expect": marks[i].passed()} if marks else {}),
            }
            for i, (question, marked) in enumerate(worst)
        },
        **(
            {"nlq_metrics": rates({**scoring.nlq_metrics(marks), **expect}), "openings": chats} if nlq else {}
        ),
    }


def check_against(base: dict[str, Any], now: dict[str, Any], out: TextIO) -> bool:
    """--compare: the paired gate against the baseline report; True when it holds. A
    baseline in the other ANSWER_WHOLE_DOC_TOKENS mode fails it; other settings that differ
    are listed."""
    changed, failures, p = scoring.compare(base["questions"], now["questions"])
    was, made = base.get("settings") or {}, now.get("settings") or {}
    mode = "ANSWER_WHOLE_DOC_TOKENS"
    if mode in was and was[mode] != made.get(mode):
        failures.insert(0, f"the baseline ran at {mode}={was[mode]}, this run at {made.get(mode)}")
    differ = sorted(name for name in was if name != mode and was[name] != made.get(name))
    out.writelines(f"WARNING: {name} was {was[name]}, now {made.get(name)}\n" for name in differ)
    out.write(f"\nCompared with the baseline (git {base.get('git_sha')}): {len(changed)} changed\n")
    out.writelines(f"  {line}\n" for line in changed)
    out.write(f"McNemar exact p {p:.4f} (reported, not a gate)\n")
    out.writelines(f"GATE FAILED: {failure}\n" for failure in failures)
    return not failures


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.runs < 1 or args.wait_minutes <= 0:
            raise GoldenError("--runs must be at least 1 and --wait-minutes above 0")
        settings = Settings.from_env(os.environ)
        check_local(args.api, settings.database_url, args.allow_remote)
        chosen = SETS[args.question_set]
        questions, documents = load_golden(args.docs, chosen)
        base = None if args.compare is None else json.loads(args.compare.read_text(encoding="utf-8"))
        if base is not None and base.get("set") != args.question_set:
            raise GoldenError(f"--compare is a report of {base.get('set')!r}, not {args.question_set!r}")
        header(out, settings, args.api, args.git_sha, chosen.questions)
        password = os.environ.get(cli.ADMIN_ENV_VAR) or secrets.token_urlsafe(24)
        choice = answer_settings(args.agents, args.thinking)
        if choice:
            out.write(f"Answer settings: {json.dumps(choice)}\n\n")
        api, names, asked = Http(args.api), {}, []
        for run in range(1, args.runs + 1):
            if run == 1 or chosen.nlq:  # the NLQ set: a customer per run, so no run learns from another
                api = Http(args.api)
                sign_in(api, password, out, f"{ASKER} Karandikar" if chosen.nlq else "Golden admin")
                names = upload(api, documents, args.wait_minutes, out, chosen.all_ready)
            out.write(f"Run {run} of {args.runs}\n")
            asked.append(ask_all(api, questions, names, out, choice, chosen.nlq))
            write_totals(out, f"run {run}", scoring.totals(asked[-1].rows))
            for name, total in scoring.by_slice(asked[-1].rows).items():
                write_totals(out, f"  {name}", total)
            write_usage(api, out, asked[-1], len(questions))
            out.write("\n")
        runs = [one.rows for one in asked]
        write_totals(out, "WORST RUN", scoring.worst([scoring.totals(rows) for rows in runs]))
        made = report(args.question_set, settings, args.git_sha, runs, asked if chosen.nlq else ())
        out.write("Precision, each question at its worst run:\n")
        write_metrics(out, made["metrics"])
        if chosen.nlq:
            out.write("NLQ expectations, each question at its worst run (PRD 6 bars):\n")
            write_metrics(out, made["nlq_metrics"])
        if args.json_path is not None:
            args.json_path.write_text(json.dumps(made, indent=2) + "\n", encoding="utf-8")
        if base is not None and not check_against(base, made, out):
            return 1
    except (GoldenError, SettingsError, OSError, HTTPException, ValueError, KeyError) as error:
        # Short codes and names only: no message here carries the password.
        sys.stderr.write(f"error: {type(error).__name__}: {error}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
