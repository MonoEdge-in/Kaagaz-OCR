"""Score golden answers (PRD 3 G1 and G2): pure, so CI tests it without a stack.

``backend/tests/golden/questions.json`` holds the questions, each
``{id, question, follow_up_of?, tags, expect: {kind, value?, tolerance?,
not?, document?, pages}}``. ``tags`` name the slices a question belongs to; its kind
("not_found") and ``follow_up_of`` ("follow_up") add two more. ``problems``
checks the file against the G2 minimums, and ``score`` marks one answer (the
``Answer`` JSON that POST /api/chat returns) as:

* **right**: a number answer holds the expected value within ``tolerance``
  (absolute, in the value's own unit: rupees for money; 0 by default), read
  with verify's number parser after every script's digits are folded, so
  "Rs. 11,800.00", "₹11,800", "1,20,000" and Devanagari or Bengali digits all parse;
  a text answer holds one of the expected texts as a whole word, any case, digits
  folded on both sides ("8%" is not in "18%"). Either is wrong when it also holds a value of ``not``: the
  near answers a list of everything would hold;
* **abstained**: a question that has an answer, answered "not_found" or "unchecked"
  (ask precision PR 1a): not right, and not wrong either;
* **wrong**: anything else for a question that has an answer;
* **correct not-found** when the status is "not_found", else **missed
  not-found**: "unchecked" says the figure exists, so it is a miss too.

``wrong_verified`` is set on a wrong or missed answer labelled verified: the
handover bar wants 0 of them (G3). ``multi_value`` marks a right answer that also
holds a money amount (currency-marked or comma-grouped) that is neither the expected
value nor a number of the question: a heuristic, so it only feeds
``answer_precision_strict``.

``metrics`` (PR 1a) gives the precision numbers as (count, of) pairs: answer_precision
(right / answered), answer_precision_strict (the same, a multi_value answer not
right), verified_precision (right and verified / verified), verified_coverage (right
and verified / answerable), coverage (answered / answerable), abstention_precision
(correct not-found / every reply that is not an answer), trap_accuracy (not-found items
whose every question was correct not-found / not-found items; a Romanised item's
spellings are one item) and citation_precision (citations on the expected document and
page / all citations). ``worst_score`` takes a question's worst run, in the order
wrong verified, wrong, abstained, right; ``compare`` is the paired gate between two runs.

**recall@5** asks whether one of the first 5 hits is from the expected
document and spans an expected page (``page_from <= page <= page_to``).
Not-found questions and answers on the fixed route (no search) are left out,
so ``Totals`` keeps its own denominator.

``questions_xlang.json`` (council 4 Oct 2026) has the same entries plus a
``split`` (dev or test, a slice too) and three tags: the document's language, the
question's style and its type; ``xlang_problems`` checks it. A file with no split
gets one from its id's sha256 (``split_of``: even is dev), always the same.

``questions_romanised.json`` (PR 2b-2) lists items with 1 to 3 ``spellings``;
``spelled_out`` asks each spelling as its own question "<id>#<n>", split by the item's
id so its spellings stay together; ``romanised_problems`` checks it. The file is frozen
by tests/test_golden_romanised.py, and ROMANISED_SHA256 holds the same hash.

``questions_nlq.json`` (PRD 6 package G) has the same entries, a split, the NLQ tags and
optional expectations (``nlq_problems`` checks them): ``documents`` {expected, not},
``clarify`` {choices, pick} or null (no question may be asked), ``figure`` {document, field,
as_printed, page, label}, ``no_total`` {allowed: null or the permitted total's text} and
``soft_not_found`` {closest}, and an item ``scope`` {document} ("Ask about this document") or
{all: true} (All documents, a scope switch). Package K (#644) adds ``focus`` {document, facts}: the
reply's understood line and rewrite name each fact of the document "this" is; ``related``
{expected: {document: reason}}: each listed in the first MAX_RELATED documents with its reason in
its why (recall at 10); and ``outside`` {documents}: none of them cited or listed (the ``scope``
metric, 0 violations). The ``follow_up`` metric is the ``documents`` mark of follow-up items.
``nlq_score`` marks them on a stored reply as pure functions, and a field a reply does not
have yet (packages A to C add them) counts as absent, never an error: a reply that asks back
(``clarify``) is the outcome **clarified**, counted like abstained and never wrong.
``nlq_metrics`` gives each expectation's (count, of); ``compare`` gates each one too.

Time is O(q) per file and O(s + h) per answer, in its characters and hits.
"""

import hashlib
import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from kaagaz.answering.verify import numbers_in
from kaagaz.chunking.script import ascii_digits

QUESTION_COUNT = 30
# G2's minimum counts per slice, plus the 3.1a plan's near misses and topic change.
MINIMUMS = {
    "follow_up": 5,
    "not_found": 5,
    "hinglish": 5,
    "name_in_latin": 3,  # a Devanagari name asked in English letters
    "cross_page": 3,
    "invoice_total": 3,
    "near_miss": 1,  # not found, though a near spelling exists ("Patel" for "Patil")
    "topic_change": 1,  # a follow-up that asks about another document
}
TAGS = frozenset(MINIMUMS) - {"follow_up", "not_found"}
KINDS = ("number", "text", "not_found")
RECALL_K = 5
# questions.json is frozen (plan 3.1a): any edit must change this line too, and means a new baseline.
QUESTIONS_SHA256 = "246d31c813a11279cc006e45ce9cae1048da043e12c7a7132ac224824a92d083"
# questions_xlang.json: English and Romanised questions over scanned non-English pages.
XLANG_SHA256 = "8e1e0916327bd70d0f5df8b87dce044fb0b26e8bed5161ed4a3b6b0abba71f24"
XLANG_COUNT, XLANG_NOT_FOUND = 60, 10
# questions_romanised.json: the same value as tests/test_golden_romanised.py's QUESTIONS_SHA256.
ROMANISED_SHA256 = "a935c1b79a54379d7d963f2e5836fcf929d715fe88efc6a3713a3840987a266d"
ROMANISED_TAGS = frozenset(
    {"land_record", "hindi_bill", "bank", "invoice", "agreement", "names", "inflected", "trap"}
)
XLANG_TAGS = (
    frozenset({"hi", "mr", "gu", "bn", "ta", "ur"}),  # the document's language
    frozenset({"english", "romanised"}),  # how the question is typed
    frozenset({"name", "number", "date", "topic"}),  # what it asks for
)
SPLITS = ("dev", "test")
# A text matches only as a whole: no letter, digit or "/" (or "." before it) touching it.
_BEFORE, _AFTER = r"(?<![0-9A-Za-z\u0900-\u097F/.])", r"(?![0-9A-Za-z\u0900-\u097F/])"
# A money amount for multi_value: currency-marked, or digits grouped by commas.
_MONEY = re.compile(r"(?:\u20B9|\brs\.?|\binr)\s*\d[\d,]*(?:\.\d+)?|\d{1,3}(?:,\d{2,3})+(?:\.\d+)?")
# compare's limits per set (the plan's paired gate).
MAX_NEWLY_ABSTAINED = 2
MAX_VERIFIED_COVERAGE_DROP = 2
# questions_nlq.json (PRD 6 G): frozen like the others; any edit means a new baseline.
NLQ_SHA256 = "5c6a9a7df4c0d27661499fc6dbe4d196c7c3e6769e18644dd6c13a196c573d48"  # #644: package K
NLQ_KEYS = ("documents", "clarify", "figure", "no_total", "soft_not_found", "focus", "related", "outside")
NLQ_TAGS = frozenset(
    {"example", "co_owner", "place", "between", "date", "misspelling", "missing_subject", "same_name"}
    | {"share", "profit", "hinglish", "trap", "opening", "scope"}
)
NLQ_SLICE_MINIMUM = 15  # items in every tag, split, follow_up and not_found slice (council)
NLQ_QUESTIONS = 150  # at least, so a 0.95 bar's Wilson interval is narrow enough to read
# Items per expectation, at least: "clarify" expects a question, "no_clarify" forbids one.
NLQ_MINIMUMS = {"documents": 80, "clarify": 30, "no_clarify": 40, "figure": 60, "no_total": 150}
NLQ_MINIMUMS |= {"soft_not_found": 25, "scope": 15, "focus": 15, "related": 10, "outside": 10}
MAX_RELATED = 10  # PRD 6 package K: related documents are found within the first 10 listed
MAX_CLARIFY_TURNS = 2  # PRD 6: at most 2 clarifying turns for one question
MAX_NEWLY_FAILED = 2  # compare: questions an expectation may newly fail (no_total: none)
LABELS = (None, "verified", "needs_review")


class Outcome(StrEnum):
    RIGHT = "right"
    WRONG = "wrong"
    ABSTAINED = "abstained"
    CORRECT_NOT_FOUND = "correct not-found"
    MISSED_NOT_FOUND = "missed not-found"
    CLARIFIED = "clarified"  # it asked back: counted like abstained, never wrong


@dataclass(frozen=True, slots=True)
class Question:
    """One question with its ``expect`` fields flattened."""

    id: str
    question: str
    follow_up_of: str | None
    slices: tuple[str, ...]
    kind: str
    value: Any  # a number, or one text or a list of texts (any of them is right)
    tolerance: Decimal
    distractors: tuple[Any, ...]  # expect.not: numbers or texts that make the answer wrong
    document: str | None
    pages: tuple[int, ...]
    nlq: Mapping[str, Any] = field(default_factory=dict)  # NLQ_KEYS present, and "scope"


@dataclass(frozen=True, slots=True)
class Hit:
    document: str
    page_from: int
    page_to: int


@dataclass(frozen=True, slots=True)
class Score:
    outcome: Outcome
    wrong_verified: bool
    answered: bool = False  # status "answered"
    verified: bool = False  # answered and labelled verified
    multi_value: bool = False
    cited: int = 0  # the answer's citations
    cited_right: int = 0  # of them, on the expected document and an expected page


@dataclass(frozen=True, slots=True)
class Totals:
    questions: int
    outcomes: Mapping[Outcome, int]
    wrong_verified: int
    recall_hits: int
    recall_of: int  # recall@5's denominator


# --- the questions file -----------------------------------------------------------


def sha256_manifest(text: str) -> dict[str, str]:
    """``sha256sum`` lines ("<hash>  <name>") as {name: hash}."""
    rows = (line.split(maxsplit=1) for line in text.splitlines() if line.strip())
    return {name.lstrip("*"): digest for digest, name in rows}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def problems(raw: Any, documents: Collection[str]) -> list[str]:
    """What is wrong with the parsed questions file, in plain words; [] when nothing.
    ``documents`` are the names in the sha256 manifest."""
    if not isinstance(raw, list):
        return ["the file must be a list of questions"]
    found: list[str] = []
    seen: set[str] = set()
    counts = dict.fromkeys(MINIMUMS, 0)
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            found.append(f"not a question with an id: {item!r}")
            continue
        qid = item["id"]
        mine = _question_problems(item, seen, documents, TAGS)
        if qid in seen:
            mine.append("the id is used twice")
        found += [f"{qid}: {problem}" for problem in mine]
        seen.add(qid)
        if not mine:
            for name in question_from(item).slices:
                if name in counts:  # not the split
                    counts[name] += 1
    if len(raw) != QUESTION_COUNT:
        found.append(f"{len(raw)} questions, not {QUESTION_COUNT}")
    found += [f"{n} {name}, at least {MINIMUMS[name]}" for name, n in counts.items() if n < MINIMUMS[name]]
    return found


def xlang_problems(raw: Any, documents: Collection[str]) -> list[str]:
    """``problems`` for questions_xlang.json: its counts, one tag from each group, a
    split, the document of every question (not-found ones too), and typed letters only."""
    if not isinstance(raw, list):
        return ["the file must be a list of questions"]
    found: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            found.append(f"not a question with an id: {item!r}")
            continue
        qid = item["id"]
        mine = _question_problems(item, seen, documents, frozenset().union(*XLANG_TAGS))
        if qid in seen:
            mine.append("the id is used twice")
        if not mine and any(len(group.intersection(item["tags"])) != 1 for group in XLANG_TAGS):
            mine.append("tags must name one language, one style and one type")
        if item.get("split") not in SPLITS:
            mine.append(f"split must be one of {SPLITS}")
        if not mine and item["expect"].get("document") is None:
            mine.append("every question names its document")
        if not mine and not item["question"].isascii():
            mine.append("the question must be typed in English letters")
        found += [f"{qid}: {problem}" for problem in mine]
        seen.add(qid)
    asked = [item for item in raw if isinstance(item, dict) and isinstance(item.get("expect"), dict)]
    missing = sum(item["expect"].get("kind") == "not_found" for item in asked)
    if (len(raw), missing) != (XLANG_COUNT, XLANG_NOT_FOUND):
        found.append(f"{len(raw)} questions and {missing} not-found, not {XLANG_COUNT} and {XLANG_NOT_FOUND}")
    return found


def spelled_out(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """questions_romanised.json's items as questions: each spelling "<id>#<n>" (from 1),
    with the item's tags and expect, and the item's split."""
    return [
        {
            "id": f"{item['id']}#{n}",
            "question": spelling,
            "tags": item["tags"],
            "expect": item["expect"],
            "split": split_of(item["id"]),
        }
        for item in raw
        for n, spelling in enumerate(item["spellings"], start=1)
    ]


def romanised_problems(raw: Any, documents: Collection[str]) -> list[str]:
    """``problems`` for questions_romanised.json, each spelling checked as a question.
    Its counts and spelling rules are tests/test_golden_romanised.py's."""
    if not isinstance(raw, list) or not all(
        isinstance(item, dict) and isinstance(item.get("spellings"), list) and "id" in item for item in raw
    ):
        return ["the file must be a list of items with spellings"]
    found: list[str] = []
    seen: set[str] = set()
    for item in spelled_out(raw):
        found += [f"{item['id']}: {p}" for p in _question_problems(item, seen, documents, ROMANISED_TAGS)]
        seen.add(item["id"])
    return found


def split_of(key: str) -> str:
    """The split of a question (or a Romanised item) whose file has none: "dev" when its
    id's sha256 is even, else "test". The same for every run."""
    return "dev" if int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % 2 == 0 else "test"


def _question_problems(
    item: dict[str, Any], seen: set[str], documents: Collection[str], allowed: frozenset[str]
) -> list[str]:
    expect, tags = item.get("expect"), item.get("tags")
    if not isinstance(item.get("question"), str) or not item["question"].strip():
        return ["no question text"]
    if not isinstance(expect, dict) or expect.get("kind") not in KINDS:
        return [f"expect.kind must be one of {KINDS}"]
    if not isinstance(tags, list) or not set(tags) <= allowed:
        return [f"tags must be a list from {sorted(allowed)}"]
    pages = expect.get("pages")
    if not isinstance(pages, list) or not all(isinstance(p, int) and p >= 1 for p in pages):
        return ["expect.pages must be a list of page numbers from 1"]
    wrong: list[str] = []
    parent = item.get("follow_up_of")
    if parent is not None and parent not in seen:
        wrong.append(f"follow_up_of {parent!r} is not an earlier question")
    document = expect.get("document")
    if document is not None and document not in documents:
        wrong.append(f"document {document!r} is not in the sha256 manifest")
    kind, value = expect["kind"], expect.get("value")
    if kind != "not_found" and (document is None or not pages):
        wrong.append("an answer needs its document and pages")
    if kind == "number" and (isinstance(value, bool) or not isinstance(value, int | float)):
        wrong.append("a number answer needs a number value")
    texts = value if isinstance(value, list) else [value]
    if kind == "text" and not all(isinstance(t, str) and t.strip() for t in texts):
        wrong.append("a text answer needs a text value, or a list of them")
    if kind == "not_found" and value is not None:
        wrong.append("a not-found question has no value")
    distractors = expect.get("not", [])
    if not isinstance(distractors, list) or not all(_is_value(d) for d in distractors):
        wrong.append("expect.not must be a list of numbers and texts")
    elif kind == "not_found" and distractors:
        wrong.append("a not-found question has no expect.not")
    if "cross_page" in tags and len(set(pages)) < 2:
        wrong.append("a cross-page answer names at least 2 pages")
    if "near_miss" in tags and kind != "not_found":
        wrong.append("a near miss is a not-found question")
    if "topic_change" in tags and parent is None:
        wrong.append("a topic change is a follow-up")
    return wrong


def question_from(item: Mapping[str, Any]) -> Question:
    """One question of a file ``problems`` passed."""
    expect = item["expect"]
    slices = [*item["tags"], *(["not_found"] if expect["kind"] == "not_found" else [])]
    slices += ["follow_up"] if item.get("follow_up_of") is not None else []
    slices.append(item["split"] if "split" in item else split_of(item["id"]))
    tolerance = Decimal(str(expect.get("tolerance", 0)))
    return Question(
        item["id"],
        item["question"],
        item.get("follow_up_of"),
        tuple(slices),
        expect["kind"],
        expect.get("value"),
        tolerance,
        tuple(expect.get("not", [])),
        expect.get("document"),
        tuple(expect["pages"]),
        {key: value for key, value in {**expect, **item}.items() if key in (*NLQ_KEYS, "scope")},
    )


# --- one answer -----------------------------------------------------------------------


def answer_text(answer: Mapping[str, Any]) -> str:
    return " ".join(str(s.get("text", "")) for s in answer.get("sentences") or [] if isinstance(s, dict))


def score(question: Question, answer: Mapping[str, Any], cited: Sequence[Hit] = ()) -> Score:
    """Mark the ``Answer`` JSON, with its citations as ``cited`` hits. Only status
    "answered" counts as an answer: "unchecked" gives no figure, so it is abstained for
    an answer, and claims one exists, so it is a miss for a not-found question."""
    answered = answer.get("status") == "answered" and clarify_of(answer) is None
    text = answer_text(answer)
    if clarify_of(answer) is not None:
        outcome = Outcome.CLARIFIED
    elif question.kind == "not_found":
        found = answer.get("status") == "not_found"
        outcome = Outcome.CORRECT_NOT_FOUND if found else Outcome.MISSED_NOT_FOUND
    elif not answered:
        outcome = Outcome.ABSTAINED
    else:
        near = any(_holds_value(d, question.tolerance, text) for d in question.distractors)
        outcome = Outcome.RIGHT if not near and _holds(question, text) else Outcome.WRONG
    wrong = outcome in (Outcome.WRONG, Outcome.MISSED_NOT_FOUND)
    verified = answered and answer.get("label") == "verified"
    on_page = sum(_on_expected_page(question, hit) for hit in cited)
    multi = outcome == Outcome.RIGHT and _other_money(question, text)
    return Score(outcome, wrong and verified, answered, verified, multi, len(cited), on_page)


def _other_money(question: Question, text: str) -> bool:
    """Whether ``text`` holds a money amount that is neither an expected value (within
    the tolerance) nor a number of the question."""
    values = question.value if isinstance(question.value, list) else [question.value]
    expected = {n for value in values for n in numbers_in(ascii_digits(str(value)))}
    asked = numbers_in(ascii_digits(question.question))
    plain = ascii_digits(text).casefold()
    return any(
        found not in asked and all(abs(found - want) > question.tolerance for want in expected)
        for match in _MONEY.finditer(plain)
        for found in numbers_in(match[0])
    )


def _is_value(value: Any) -> bool:
    return isinstance(value, int | float | str) and not isinstance(value, bool) and str(value).strip() != ""


def _holds(question: Question, text: str) -> bool:
    values = question.value if isinstance(question.value, list) else [question.value]
    return any(_holds_value(value, question.tolerance, text) for value in values)


def _holds_value(value: Any, tolerance: Decimal, text: str) -> bool:
    """A number anywhere in ``text`` within ``tolerance``, or a text as a whole word.
    ponytail: the number is not bound to its label, so an answer listing many numbers
    passes unless ``expect.not`` lists them; binding waits for the atom check (Part B)."""
    if not isinstance(value, str):
        target = Decimal(str(value))
        return any(abs(found - target) <= tolerance for found in numbers_in(ascii_digits(text)))
    plain = " ".join(ascii_digits(text).casefold().split())
    wanted = " ".join(ascii_digits(value).casefold().split())
    return re.search(_BEFORE + re.escape(wanted) + _AFTER, plain) is not None


def counts_for_recall(question: Question, route: str | None) -> bool:
    return question.kind != "not_found" and route != "fixed_query"


def recall_hit(question: Question, hits: Sequence[Hit]) -> bool:
    """Whether an expected page is inside one of the first ``RECALL_K`` hits of the
    expected document."""
    return any(_on_expected_page(question, hit) for hit in hits[:RECALL_K])


def _on_expected_page(question: Question, hit: Hit) -> bool:
    return hit.document == question.document and any(
        hit.page_from <= p <= hit.page_to for p in question.pages
    )


# --- totals --------------------------------------------------------------------------------


def totals(rows: Sequence[tuple[Question, Score, bool | None]]) -> Totals:
    """``rows`` are (question, score, recall hit or None when left out)."""
    outcomes = dict.fromkeys(Outcome, 0)
    for _, marked, _ in rows:
        outcomes[marked.outcome] += 1
    recall = [hit for _, _, hit in rows if hit is not None]
    return Totals(
        questions=len(rows),
        outcomes=outcomes,
        wrong_verified=sum(marked.wrong_verified for _, marked, _ in rows),
        recall_hits=sum(recall),
        recall_of=len(recall),
    )


def by_slice(rows: Sequence[tuple[Question, Score, bool | None]]) -> dict[str, Totals]:
    names = sorted({name for question, _, _ in rows for name in question.slices})
    return {name: totals([row for row in rows if name in row[0].slices]) for name in names}


def worst(runs: Sequence[Totals]) -> Totals:
    """Each number at its worst over the runs: the fewest right, correct not-found and
    recall hits (the run with the lowest recall rate), the most of the rest."""
    lowest = min(runs, key=lambda t: t.recall_hits / t.recall_of if t.recall_of else 1.0)
    fewest = (Outcome.RIGHT, Outcome.CORRECT_NOT_FOUND)
    return Totals(
        questions=runs[0].questions,
        outcomes={o: (min if o in fewest else max)(run.outcomes[o] for run in runs) for o in Outcome},
        wrong_verified=max(run.wrong_verified for run in runs),
        recall_hits=lowest.recall_hits,
        recall_of=lowest.recall_of,
    )


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The Wilson score interval (95% by default) for ``successes`` of ``n``; (0, 1) for n = 0."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


# --- precision (ask precision PR 1a) ---------------------------------------------------------

_RANK = {"right": 0, "abstained": 1, "wrong": 2, "wrong_verified": 3}


def category(marked: Score) -> str:
    """The outcome ``compare`` reads: wrong_verified, wrong, abstained or right (a correct
    not-found is right, a missed one wrong)."""
    if marked.wrong_verified:
        return "wrong_verified"
    if marked.outcome in (Outcome.WRONG, Outcome.MISSED_NOT_FOUND):
        return "wrong"
    return "abstained" if marked.outcome in (Outcome.ABSTAINED, Outcome.CLARIFIED) else "right"


def worst_score(runs: Sequence[Score]) -> Score:
    """A question's worst run: wrong verified, wrong, abstained, right; of two right
    answers, the one not labelled verified."""
    return max(runs, key=lambda m: (_RANK[category(m)], m.outcome == Outcome.RIGHT and not m.verified))


def metrics(scored: Sequence[tuple[Question, Score]]) -> dict[str, tuple[int, int]]:
    """The precision numbers (the module docstring) of each question's score, as (count, of)."""
    answerable = [m for q, m in scored if q.kind != "not_found"]
    answered = [m for _, m in scored if m.answered]
    right = [m for m in answered if m.outcome == Outcome.RIGHT]
    replies = [m for _, m in scored if not m.answered]  # not found or unchecked
    traps: dict[str, bool] = {}
    for q, m in scored:
        if q.kind == "not_found":
            item = q.id.split("#")[0]  # a Romanised item's spellings are one item
            traps[item] = traps.get(item, True) and m.outcome == Outcome.CORRECT_NOT_FOUND
    right_verified = sum(m.verified for m in right)
    return {
        "answer_precision": (len(right), len(answered)),
        "answer_precision_strict": (sum(not m.multi_value for m in right), len(answered)),
        "verified_precision": (right_verified, sum(m.verified for m in answered)),
        "verified_coverage": (right_verified, len(answerable)),
        "coverage": (sum(m.answered for m in answerable), len(answerable)),
        "abstention_precision": (sum(m.outcome == Outcome.CORRECT_NOT_FOUND for m in replies), len(replies)),
        "trap_accuracy": (sum(traps.values()), len(traps)),
        "citation_precision": (sum(m.cited_right for _, m in scored), sum(m.cited for _, m in scored)),
    }


def mcnemar(base_only: int, now_only: int) -> float:
    """The exact two-sided McNemar p for the discordant pairs: right only before, right
    only now. 1.0 with none."""
    n = base_only + now_only
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(base_only, now_only) + 1))
    return min(1.0, 2 * tail / (1 << n))


def compare(
    base: Mapping[str, Mapping[str, Any]], now: Mapping[str, Mapping[str, Any]]
) -> tuple[list[str], list[str], float]:
    """The paired gate between two runs of one set, on the question ids both have; each
    question is {"outcome": a ``category``, "verified": bool}. Gives each changed question
    ("id: old -> new"), the failures, and McNemar's p (reported, never a failure). It fails
    a question that moved into wrong or wrong_verified, more than MAX_NEWLY_ABSTAINED right
    to abstained, and verified_coverage down by more than MAX_VERIFIED_COVERAGE_DROP questions.
    A question's "expect" ({expectation: passed}, the NLQ set) gates each expectation too:
    more than MAX_NEWLY_FAILED questions newly failing it fails, any one for no_total and
    clarify_turns."""
    changed: list[str] = []
    failures: list[str] = []
    abstained = base_only = now_only = 0
    covered = {"base": 0, "now": 0}
    newly: dict[str, int] = {}
    for qid in sorted(set(base) & set(now)):
        old, new = base[qid]["outcome"], now[qid]["outcome"]
        for name, run in (("base", base), ("now", now)):
            covered[name] += run[qid]["outcome"] == "right" and bool(run[qid]["verified"])
        was, now_passed = base[qid].get("expect") or {}, now[qid].get("expect") or {}
        for name in sorted(set(was) & set(now_passed)):
            if was[name] and not now_passed[name]:
                newly[name] = newly.get(name, 0) + 1
                changed.append(f"{qid}: {name} passed -> failed")
        base_only += old == "right" and new != "right"
        now_only += old != "right" and new == "right"
        if old == new:
            continue
        changed.append(f"{qid}: {old} -> {new}")
        if _RANK[new] >= _RANK["wrong"] and _RANK[new] > _RANK[old]:
            failures.append(f"{qid} moved into {new}")
        abstained += old == "right" and new == "abstained"
    if abstained > MAX_NEWLY_ABSTAINED:
        failures.append(f"{abstained} right answers abstained, at most {MAX_NEWLY_ABSTAINED}")
    if covered["base"] - covered["now"] > MAX_VERIFIED_COVERAGE_DROP:
        failures.append(
            f"verified_coverage fell from {covered['base']} to {covered['now']} questions,"
            f" at most {MAX_VERIFIED_COVERAGE_DROP} fewer"
        )
    for name, count in sorted(newly.items()):
        limit = 0 if name in ("no_total", "clarify_turns") else MAX_NEWLY_FAILED
        if count > limit:
            failures.append(f"{count} questions newly fail {name}, at most {limit}")
    return changed, failures, mcnemar(base_only, now_only)


# --- NLQ: the questions file (PRD 6 package G) -------------------------------------------------

# A page reference ("page 2", "pages 2 to 3"): its numbers are never a figure.
_PAGES = re.compile(r"\b(?:pages?|pp?\.)\s*\d+(?:\s*(?:to|and|-|,)\s*\d+)*")
# A number not inside a longer one, with "/" runs (dates, surveys, fractions) kept whole.
_NUMERAL = re.compile(r"(?<!\d)(?<!\d[.,/])(\d[\d,]*(?:\.\d+)?(?:/\d+)*)\s*(%|per\s?cent\b)?")


def nlq_problems(raw: Any, documents: Collection[str]) -> list[str]:
    """``problems`` for questions_nlq.json: a split, the NLQ tags, each expectation's shape,
    a follow-up in its question's split (no leakage), and the minimums: NLQ_QUESTIONS items,
    NLQ_SLICE_MINIMUM in every slice and NLQ_MINIMUMS per expectation."""
    if not isinstance(raw, list):
        return ["the file must be a list of questions"]
    found: list[str] = []
    split_by_id: dict[str, Any] = {}
    slices = dict.fromkeys([*sorted(NLQ_TAGS), *SPLITS, "follow_up", "not_found"], 0)
    counts = dict.fromkeys(NLQ_MINIMUMS, 0)
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            found.append(f"not a question with an id: {item!r}")
            continue
        qid = item["id"]
        mine = _question_problems(item, set(split_by_id), documents, NLQ_TAGS)
        if qid in split_by_id:
            mine.append("the id is used twice")
        if item.get("split") not in SPLITS:
            mine.append(f"split must be one of {SPLITS}")
        elif split_by_id.get(str(item.get("follow_up_of")), item["split"]) != item["split"]:
            mine.append("a follow-up is in its question's split")
        mine = mine or _nlq_expect_problems(item, documents)
        found += [f"{qid}: {problem}" for problem in mine]
        split_by_id[qid] = item.get("split")
        if not mine:
            question = question_from(item)
            for name in question.slices:
                slices[name] += 1
            nlq = question.nlq
            counts["clarify"] += nlq.get("clarify") is not None
            counts["no_clarify"] += "clarify" in nlq and nlq["clarify"] is None
            for name in (
                "documents",
                "figure",
                "no_total",
                "soft_not_found",
                "scope",
                "focus",
                "related",
                "outside",
            ):
                counts[name] += name in nlq
    if len(raw) < NLQ_QUESTIONS:
        found.append(f"{len(raw)} questions, at least {NLQ_QUESTIONS}")
    found += [
        f"{n} in slice {name}, at least {NLQ_SLICE_MINIMUM}"
        for name, n in slices.items()
        if n < NLQ_SLICE_MINIMUM
    ]
    found += [
        f"{n} {name}, at least {NLQ_MINIMUMS[name]}" for name, n in counts.items() if n < NLQ_MINIMUMS[name]
    ]
    return found


def _nlq_expect_problems(item: dict[str, Any], documents: Collection[str]) -> list[str]:
    expect, scope, wrong = item["expect"], item.get("scope"), []

    def names(value: Any, least: int = 0) -> bool:
        return isinstance(value, list) and len(value) >= least and all(name in documents for name in value)

    def texts(value: Any) -> bool:
        return (
            isinstance(value, list) and bool(value) and all(isinstance(t, str) and t.strip() for t in value)
        )

    docs, choose, figure = expect.get("documents", {}), expect.get("clarify"), expect.get("figure", {})
    allowed, soft = expect.get("no_total", {}), expect.get("soft_not_found", {})
    if "documents" in expect and not (
        isinstance(docs, dict) and names(docs.get("expected"), 1) and names(docs.get("not", []))
    ):
        wrong.append("expect.documents needs expected (at least 1) and not, names from the manifest")
    elif "documents" in expect and set(docs["expected"]) & set(docs.get("not", [])):
        wrong.append("expect.documents lists a document as expected and not")
    if choose is not None and not (
        isinstance(choose, dict) and texts(choose.get("choices")) and texts([choose.get("pick")])
    ):
        wrong.append("expect.clarify needs choices (texts) and a pick, or is null")
    if "figure" in expect and not (
        isinstance(figure, dict)
        and figure.get("document") in documents
        and texts([figure.get("field"), figure.get("as_printed")])
        and isinstance(figure.get("page"), int)
        and figure["page"] >= 1
        and figure.get("label") in LABELS
    ):
        wrong.append(
            f"expect.figure needs a manifest document, field, as_printed, a page and a label in {LABELS}"
        )
    if "no_total" in expect and not (
        isinstance(allowed, dict)
        and "allowed" in allowed
        and (allowed["allowed"] is None or len(numerals(str(allowed["allowed"]))) == 1)
    ):
        wrong.append("expect.no_total needs allowed: null or the permitted total's text, one number")
    if "soft_not_found" in expect and not (isinstance(soft, dict) and names(soft.get("closest"))):
        wrong.append("expect.soft_not_found needs closest, names from the manifest")
    elif "soft_not_found" in expect and expect["kind"] != "not_found":
        wrong.append("a soft not-found is a not-found question")
    if (
        scope is not None
        and scope != {"all": True}
        and (not isinstance(scope, dict) or scope.get("document") not in documents)
    ):
        wrong.append("scope needs a document from the manifest, or all: true")
    focus, related, outside = expect.get("focus", {}), expect.get("related", {}), expect.get("outside", {})
    if "focus" in expect and not (
        isinstance(focus, dict) and focus.get("document") in documents and texts(focus.get("facts"))
    ):
        wrong.append("expect.focus needs a manifest document and facts (texts)")
    expected = related.get("expected") if isinstance(related, dict) else None
    if "related" in expect and not (
        isinstance(expected, dict) and names(list(expected), 1) and texts(list(expected.values()))
    ):
        wrong.append("expect.related needs expected: {manifest document: its reason}")
    if "outside" in expect and not (isinstance(outside, dict) and names(outside.get("documents"), 1)):
        wrong.append("expect.outside needs documents, names from the manifest")
    return wrong


def numerals(text: str) -> list[tuple[str, bool]]:
    """Every number in ``text`` outside a page reference, as (its value, a percent?): any
    script's digits folded, grouping and trailing zeros dropped ("Rs. 18,400.00" -> "18400");
    a "/" run keeps its parts ("12/09/2026" -> "12/9/2026", "45/2")."""
    plain = _PAGES.sub(" ", ascii_digits(text).casefold())
    found = []
    for match in _NUMERAL.finditer(plain):
        parts = match[1].rstrip(",").split("/")
        value = "/".join(format(Decimal(part.replace(",", "") or "0").normalize(), "f") for part in parts)
        found.append((value, match[2] is not None))
    return found


def phrase_in(wanted: str, text: str) -> bool:
    """``wanted`` in ``text`` as a whole phrase, any case and spacing ("Pune" is not in "Puneri")."""
    return _holds_value(wanted, Decimal(0), text)


# --- NLQ: one reply (PRD 6 package G) ----------------------------------------------------------

# A share or rate in words, which no printed figure backs unless printed so.
_FRACTION = re.compile(
    r"\b(?:half|halves|(?:one|two|three|four)[- ](?:thirds?|quarters?|fifths?|tenths?)"
    r"|(?:ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ][a-z]+)?[- ]per ?cent)\b"
)
_BARE = re.compile(
    r"(?:no|not found(?: in the documents searched)?|i couldn't find this in your documents)\.?"
)


@dataclass(frozen=True, slots=True)
class NlqScore:
    """One reply's NLQ expectations; None (or 0) where the question has none."""

    documents: tuple[int, int, int] | None = None  # expected and listed, expected, listed
    unwanted: int = 0  # documents of expect.documents.not listed
    clarify_expected: bool | None = None
    asked: bool = False  # the first reply asked back
    choices_right: bool = False  # with every expected choice
    figure: bool | None = None
    violations: tuple[str, ...] | None = None  # no_total's
    soft: bool | None = None
    clarify_turns: int = 0  # replies that asked back, counted by the runner
    focus: bool | None = None  # package K: the understood line names the focus's facts
    related: tuple[int, int] | None = None  # found with their reason in the first 10, expected
    outside: int | None = None  # documents cited or listed that must not be (the scope metric)
    follow_up: bool | None = None  # a follow-up's documents mark

    def passed(self) -> dict[str, bool]:
        """Each expectation the question has: passed or not (the paired gate reads these)."""
        marks = {"clarify_turns": self.clarify_turns <= MAX_CLARIFY_TURNS}
        if self.documents is not None:
            marks["documents"] = self.documents[0] == self.documents[1] and not self.unwanted
        if self.clarify_expected is not None:
            marks["clarify"] = self.asked == self.clarify_expected and (self.choices_right or not self.asked)
        if self.figure is not None:
            marks["figure"] = self.figure
        if self.violations is not None:
            marks["no_total"] = not self.violations
        if self.soft is not None:
            marks["soft_not_found"] = self.soft
        if self.focus is not None:
            marks["focus"] = self.focus
        if self.related is not None:
            marks["related"] = self.related[0] == self.related[1]
        if self.outside is not None:
            marks["scope"] = not self.outside
        if self.follow_up is not None:
            marks["follow_up"] = self.follow_up
        return marks


def _dicts(value: Any) -> list[Mapping[str, Any]]:
    return [each for each in value if isinstance(each, Mapping)] if isinstance(value, list) else []


def clarify_of(answer: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The reply's clarifying question (PRD 6: ``clarify``, or kind "clarify"); None when it
    does not ask back. Today's replies never do."""
    clarify = answer.get("clarify")
    if isinstance(clarify, Mapping):
        return clarify
    return {} if answer.get("kind") == "clarify" else None


# #629 (Tushar, 15 Nov 2026): a property choice names its kind, its number and a place or document.
PROPERTY_CHOICE = re.compile(r"(Gat|Survey|Khata|Plot|CTS) \S*\d\S*( \(\S+\))?, .+")


def property_choices_labelled(clarify: Mapping[str, Any]) -> bool:
    """True unless ``clarify`` asks which property and one of its picks is not ``PROPERTY_CHOICE``
    (a bare number, a date, several numbers joined, no place): the clarify expectation's too."""
    if clarify.get("question") != "Which property do you mean?":
        return True
    picks = [c.get("label") for c in _dicts(clarify.get("choices")) if c.get("kind") == "pick"]
    return all(isinstance(label, str) and PROPERTY_CHOICE.fullmatch(label) for label in picks)


def choice_labels(clarify: Mapping[str, Any]) -> list[str]:
    """Its choices' labels: a choice is a text, or {"id", "label"} (council: choices by id)."""
    choices = clarify.get("choices")
    items = choices if isinstance(choices, list) else []
    labels = [choice.get("label") if isinstance(choice, Mapping) else choice for choice in items]
    return [label for label in labels if isinstance(label, str)]


def listed_documents(answer: Mapping[str, Any], names: Mapping[str, str]) -> list[str]:
    """The documents a reply lists, as file names (``names``: document id -> name; "?" for
    another): PRD 6's ``documents``, else today's cited and closest documents."""
    if isinstance(answer.get("documents"), list):
        # #644: a reply's documents carry document_id (service.Answer); "id" kept for older replies.
        ids = [doc.get("document_id") or doc.get("id") for doc in _dicts(answer["documents"])]
    else:
        ids = [c.get("document_id") for c in _dicts(answer.get("citations"))]
        ids += [doc.get("id") for doc in _dicts(answer.get("closest_documents"))]
    return list(dict.fromkeys(names.get(str(i), "?") for i in ids))


def said(answer: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every text the reply writes, as (field, text): the opening, sentences, each document's
    why, assumptions, the clarifying question and the total. Printed figures and the
    clarify choices (names from the documents) are not in it."""
    total, clarify = answer.get("total"), clarify_of(answer) or {}
    found = [("opening", answer.get("opening")), ("clarify", clarify.get("question"))]
    found += [("sentences", s.get("text")) for s in _dicts(answer.get("sentences"))]
    found += [("why", doc.get("why")) for doc in _dicts(answer.get("documents"))]
    assumptions = answer.get("assumptions")
    found += [("assumptions", a) for a in assumptions] if isinstance(assumptions, list) else []
    found += [("total", total.get("as_text"))] if isinstance(total, Mapping) else []
    return [(where, text) for where, text in found if isinstance(text, str) and text.strip()]


def _printed(answer: Mapping[str, Any], names: Mapping[str, str]) -> list[str]:
    """What the reply shows as printed: figures with their page, cited snippets, and the
    documents' names (their display names)."""
    found = [str(c.get("snippet", "")) for c in _dicts(answer.get("citations"))]
    found += [str(c.get("document_name", "")) for c in _dicts(answer.get("citations"))]
    found += [str(doc.get("display_name", "")) for doc in _dicts(answer.get("closest_documents"))]
    found += listed_documents(answer, names)
    for doc in _dicts(answer.get("documents")):
        shown = _dicts(doc.get("figures"))
        found += [str(f.get("as_printed", "")) for f in shown if isinstance(f.get("page"), int)]
    return found


def _counts(answer: Mapping[str, Any], names: Mapping[str, str]) -> set[str]:
    """Counts a reply may state: the documents it lists, those with and without each figure,
    and its choices."""
    docs = _dicts(answer.get("documents"))
    counts = {len(listed_documents(answer, names)), len(choice_labels(clarify_of(answer) or {}))}
    for name in {f.get("field") for doc in docs for f in _dicts(doc.get("figures"))}:
        holding = sum(any(f.get("field") == name for f in _dicts(doc.get("figures"))) for doc in docs)
        counts |= {holding, len(docs) - holding}
    return {str(count) for count in counts}


def _total_problem(total: Mapping[str, Any], allowed: Any) -> str | None:
    """None when the reply's total is permitted: the expected total, the code's sum of its
    working, every figure in it printed with its page and Verified."""
    if allowed is None:
        return "a total where none is permitted"
    working = _dicts(total.get("working"))
    values = [numerals(str(f.get("as_printed", ""))) for f in working]
    want = numerals(str(allowed))[0][0]
    if not working or any(
        len(v) != 1 or "/" in v[0][0] or not isinstance(f.get("page"), int) or f.get("label") != "verified"
        for v, f in zip(values, working, strict=True)
    ):
        return "a total whose working is not printed, Verified figures"
    shown = [value for value, _ in numerals(str(total.get("as_text", "")))]
    if sum(Decimal(v[0][0]) for v in values) != Decimal(want) or shown != [want]:
        return f"a total that is not {allowed}, the sum of its working"
    return None


def no_total_violations(question: Question, answer: Mapping[str, Any], names: Mapping[str, str]) -> list[str]:
    """The numbers rule (PRD 6): every number the reply writes is printed (a figure with its
    page, a cited snippet, a document's name), the permitted total, a count of what it lists,
    a page number, or in the question; a day, month or year of a printed date counts as
    printed (an assumed year comes from one), never as a percent. Each other number, and
    each share in words, is a violation: "<value> in <field>".
    ponytail: a number past a snippet's 300-character cut reads as unprinted for today's
    replies; PRD 6 replies carry their figures."""
    printed = _printed(answer, names)
    wholes = {value for text in printed for value, _ in numerals(text)}
    parts = {part for value in wholes for part in value.split("/")}
    asked = {part for value, _ in numerals(question.question) for part in (value, *value.split("/"))}
    counts, wrong = _counts(answer, names), []
    total = answer.get("total")
    if isinstance(total, Mapping):
        problem = _total_problem(total, (question.nlq.get("no_total") or {}).get("allowed"))
        wrong += [] if problem is None else [problem]
        wholes |= set() if problem else {value for value, _ in numerals(str(total.get("as_text", "")))}
    seen = " ".join(printed).casefold()
    for where, text in said(answer):
        for value, percent in numerals(text):
            plain = not percent and "/" not in value and (value in parts or value in counts)
            if not (value in wholes or value in asked or plain):
                wrong.append(f"{value}{'%' if percent else ''} in {where}")
        wrong += [f"{m[0]} in {where}" for m in _FRACTION.finditer(text.casefold()) if m[0] not in seen]
    return wrong


def figure_right(figure: Mapping[str, Any], answer: Mapping[str, Any], names: Mapping[str, str]) -> bool:
    """The printed figure shown exactly (the same numbers), on its document and page, with
    its label when one is expected: as a PRD 6 document's figure, or in today's reply as a
    sentence citing that document's page."""
    want, page, label = numerals(str(figure["as_printed"])), figure["page"], figure.get("label")
    for doc in _dicts(answer.get("documents")):
        if names.get(str(doc.get("id"))) == figure["document"] and any(
            numerals(str(f.get("as_printed", ""))) == want
            and f.get("page") == page
            and label in (None, f.get("label"))
            for f in _dicts(doc.get("figures"))
        ):
            return True
    cited = {c.get("n"): c for c in _dicts(answer.get("citations"))}
    for sentence in _dicts(answer.get("sentences")):
        held = numerals(str(sentence.get("text", "")))
        refs = sentence.get("citations")
        for c in [cited[n] for n in refs if n in cited] if isinstance(refs, list) and want else []:
            low = c.get("page_from") or c.get("page_no")
            high = c.get("page_to") or low
            on_page = isinstance(low, int) and isinstance(high, int) and low <= page <= high
            if (
                all(w in held for w in want)
                and on_page
                and names.get(str(c.get("document_id"))) == figure["document"]
            ):
                return label in (None, c.get("label"))
    return False


def soft_right(
    soft: Mapping[str, Any], answer: Mapping[str, Any], names: Mapping[str, str], asker: str
) -> bool:
    """PRD 6's soft rejection: status not_found, written text that names the asker and is
    never a bare refusal, and every expected closest document among its documents."""
    texts = [text for _, text in said(answer)]
    if answer.get("status") != "not_found" or clarify_of(answer) is not None or not texts:
        return False
    if not phrase_in(asker, " ".join(texts)) or any(_BARE.fullmatch(t.strip().casefold()) for t in texts):
        return False
    shown = _dicts(answer.get("closest_documents")) + _dicts(answer.get("documents"))
    return set(soft["closest"]) <= {names.get(str(doc.get("id"))) for doc in shown}


def _shown_ids(answer: Mapping[str, Any]) -> list[str]:
    """The documents a reply cites or lists (PRD 6's ``documents`` by ``document_id``), in order."""
    ids = [doc.get("document_id") or doc.get("id") for doc in _dicts(answer.get("documents"))]
    ids += [c.get("document_id") for c in _dicts(answer.get("citations"))]
    return [str(i) for i in dict.fromkeys(ids) if i]


def focus_right(focus: Mapping[str, Any], answer: Mapping[str, Any]) -> bool:
    """Package K: every fact of the focus in the reply's understood line or its rewrite."""
    lines = answer.get("assumptions")
    said = [*(lines if isinstance(lines, list) else []), answer.get("rewritten_question") or ""]
    told = " ".join(text for text in said if isinstance(text, str))
    return all(phrase_in(fact, told) for fact in focus["facts"])


def related_found(related: Mapping[str, Any], answer: Mapping[str, Any], names: Mapping[str, str]) -> int:
    """Package K: the expected related documents among the first MAX_RELATED listed, each with its
    reason in its why."""
    why = {
        names.get(str(doc.get("document_id") or doc.get("id")), "?"): str(doc.get("why", ""))
        for doc in _dicts(answer.get("documents"))[:MAX_RELATED]
    }
    return sum(name in why and phrase_in(reason, why[name]) for name, reason in related["expected"].items())


def nlq_score(
    question: Question,
    first: Mapping[str, Any],
    final: Mapping[str, Any],
    names: Mapping[str, str],
    asker: str,
    clarify_turns: int = 0,
) -> NlqScore:
    """Mark the NLQ expectations: clarify on the ``first`` reply, the rest on the ``final``
    one (after the runner answered any question back). ``asker`` is the first name."""
    nlq, asked = question.nlq, clarify_of(first)
    want = nlq.get("clarify")
    found = None
    if "documents" in nlq:
        listed, expected = listed_documents(final, names), nlq["documents"]["expected"]
        found = (sum(name in listed for name in expected), len(expected), len(listed))
    labels = choice_labels(asked or {})
    return NlqScore(
        documents=found,
        unwanted=sum(
            name in listed_documents(final, names) for name in (nlq.get("documents") or {}).get("not", [])
        ),
        clarify_expected=None if "clarify" not in nlq else want is not None,
        asked=asked is not None,
        choices_right=want is not None
        and all(any(phrase_in(c, got) for got in labels) for c in want["choices"])
        and property_choices_labelled(asked or {}),  # #629
        figure=None if "figure" not in nlq else figure_right(nlq["figure"], final, names),
        violations=None if "no_total" not in nlq else tuple(no_total_violations(question, final, names)),
        soft=None if "soft_not_found" not in nlq else soft_right(nlq["soft_not_found"], final, names, asker),
        clarify_turns=clarify_turns,
        focus=None if "focus" not in nlq else focus_right(nlq["focus"], final),
        related=None
        if "related" not in nlq
        else (related_found(nlq["related"], final, names), len(nlq["related"]["expected"])),
        outside=None
        if "outside" not in nlq
        else sum(names.get(i) in nlq["outside"]["documents"] for i in _shown_ids(final)),
        follow_up=None
        if question.follow_up_of is None or found is None
        else found[0] == found[1]
        and not sum(name in listed_documents(final, names) for name in nlq["documents"].get("not", [])),
    )


def worst_nlq(runs: Sequence[NlqScore]) -> NlqScore:
    """A question's NLQ marks at their worst over the runs: the lowest document recall (the
    most listed on a tie), any run failing a mark fails it."""
    docs = [run.documents for run in runs if run.documents is not None]
    expected = runs[0].clarify_expected
    return NlqScore(
        documents=min(docs, key=lambda d: (d[0] / d[1], -d[2])) if docs else None,
        unwanted=max(run.unwanted for run in runs),
        clarify_expected=expected,
        asked=all(run.asked for run in runs) if expected else any(run.asked for run in runs),
        choices_right=all(run.choices_right for run in runs),
        figure=None if runs[0].figure is None else all(run.figure for run in runs),
        violations=None
        if runs[0].violations is None
        else tuple(dict.fromkeys(v for run in runs for v in run.violations or ())),
        soft=None if runs[0].soft is None else all(run.soft for run in runs),
        clarify_turns=max(run.clarify_turns for run in runs),
        focus=None if runs[0].focus is None else all(run.focus for run in runs),
        related=None
        if runs[0].related is None
        else min((run.related for run in runs if run.related is not None), key=lambda r: r[0]),
        outside=None if runs[0].outside is None else max(run.outside or 0 for run in runs),
        follow_up=None if runs[0].follow_up is None else all(run.follow_up for run in runs),
    )


def nlq_metrics(scored: Sequence[NlqScore]) -> dict[str, tuple[int, int]]:
    """Each expectation as (count, of): documents recall and precision over the documents
    expected and listed (micro), and per item; clarify asked with the right choices when
    expected, and asked when not (the bar wants it low); figure, no_total (items with no
    violation), soft_not_found and clarify_turns per item."""
    docs = [s.documents for s in scored if s.documents is not None]
    related = [s.related for s in scored if s.related is not None]

    def rate(name: str) -> tuple[int, int]:
        marks = [s.passed()[name] for s in scored if name in s.passed()]
        return (sum(marks), len(marks))

    expected = [s for s in scored if s.clarify_expected]
    unexpected = [s for s in scored if s.clarify_expected is False]
    return {
        "documents_recall": (sum(d[0] for d in docs), sum(d[1] for d in docs)),
        "documents_precision": (sum(d[0] for d in docs), sum(d[2] for d in docs)),
        "documents": rate("documents"),
        "clarify_asked": (sum(s.asked and s.choices_right for s in expected), len(expected)),
        "clarify_unneeded": (sum(s.asked for s in unexpected), len(unexpected)),
        "figure": rate("figure"),
        "no_total": rate("no_total"),
        "soft_not_found": rate("soft_not_found"),
        "clarify_turns": rate("clarify_turns"),
        "follow_up": rate("follow_up"),
        "focus": rate("focus"),
        "related_recall": (sum(r[0] for r in related), sum(r[1] for r in related)),
        "related": rate("related"),
        "scope": rate("scope"),
    }


def opening_problems(openings: Sequence[str | None], asker: str) -> list[str]:
    """One chat's openings, reply by reply (None: it has none). PRD 6: the first names the
    asker; none is repeated, over 12 words or holds an emoji."""
    found = (
        [] if openings and openings[0] and phrase_in(asker, openings[0]) else ["no name on the first reply"]
    )
    seen: set[str] = set()
    for n, text in enumerate(openings, start=1):
        key = " ".join((text or "").casefold().split())
        found += [f"reply {n} repeats an opening"] if key and key in seen else []
        found += [f"reply {n}'s opening is over 12 words"] if len(key.split()) > 12 else []
        found += [f"reply {n}'s opening has an emoji"] if any(ord(ch) > 0xFFFF for ch in key) else []
        seen.add(key)
    return found
