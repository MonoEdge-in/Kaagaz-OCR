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

Time is O(q) per file and O(s + h) per answer, in its characters and hits.
"""

import hashlib
import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
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


class Outcome(StrEnum):
    RIGHT = "right"
    WRONG = "wrong"
    ABSTAINED = "abstained"
    CORRECT_NOT_FOUND = "correct not-found"
    MISSED_NOT_FOUND = "missed not-found"


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
    )


# --- one answer -----------------------------------------------------------------------


def answer_text(answer: Mapping[str, Any]) -> str:
    return " ".join(str(s.get("text", "")) for s in answer.get("sentences") or [] if isinstance(s, dict))


def score(question: Question, answer: Mapping[str, Any], cited: Sequence[Hit] = ()) -> Score:
    """Mark the ``Answer`` JSON, with its citations as ``cited`` hits. Only status
    "answered" counts as an answer: "unchecked" gives no figure, so it is abstained for
    an answer, and claims one exists, so it is a miss for a not-found question."""
    answered = answer.get("status") == "answered"
    text = answer_text(answer)
    if question.kind == "not_found":
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
    return "abstained" if marked.outcome == Outcome.ABSTAINED else "right"


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
    to abstained, and verified_coverage down by more than MAX_VERIFIED_COVERAGE_DROP questions."""
    changed: list[str] = []
    failures: list[str] = []
    abstained = base_only = now_only = 0
    covered = {"base": 0, "now": 0}
    for qid in sorted(set(base) & set(now)):
        old, new = base[qid]["outcome"], now[qid]["outcome"]
        for name, run in (("base", base), ("now", now)):
            covered[name] += run[qid]["outcome"] == "right" and bool(run[qid]["verified"])
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
    return changed, failures, mcnemar(base_only, now_only)
