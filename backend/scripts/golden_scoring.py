"""Score golden answers (PRD 3 G1 and G2): pure, so CI tests it without a stack.

``backend/tests/golden/questions.json`` holds the questions, each
``{id, question, follow_up_of?, tags, expect: {kind, value?, tolerance?,
not?, document?, pages}}``. ``tags`` name the slices a question belongs to; its kind
("not_found") and ``follow_up_of`` ("follow_up") add two more. ``problems``
checks the file against the G2 minimums, and ``score`` marks one answer (the
``Answer`` JSON that POST /api/chat returns) as:

* **right**: a number answer holds the expected value within ``tolerance``
  (absolute, in the value's own unit: rupees for money; 0 by default), read
  with verify's number parser after Devanagari digits are folded, so
  "Rs. 11,800.00", "₹11,800", "1,20,000" and Devanagari digits all parse; a text
  answer holds one of the expected texts as a whole word, any case ("8%" is
  not in "18%"). Either is wrong when it also holds a value of ``not``: the
  near answers a list of everything would hold;
* **wrong**: anything else for a question that has an answer;
* **correct not-found** when the status is "not_found", else **missed
  not-found**: "unchecked" says the figure exists, so it is a miss too.

``wrong_verified`` is set on a wrong or missed answer labelled verified: the
handover bar wants 0 of them (G3).

**recall@5** asks whether one of the first 5 hits is from the expected
document and spans an expected page (``page_from <= page <= page_to``).
Not-found questions and answers on the fixed route (no search) are left out,
so ``Totals`` keeps its own denominator.

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
from kaagaz.chunking.script import fold_digits

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
# A text matches only as a whole: no letter, digit or "/" (or "." before it) touching it.
_BEFORE, _AFTER = r"(?<![0-9A-Za-z\u0900-\u097F/.])", r"(?![0-9A-Za-z\u0900-\u097F/])"


class Outcome(StrEnum):
    RIGHT = "right"
    WRONG = "wrong"
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
        mine = _question_problems(item, seen, documents)
        if qid in seen:
            mine.append("the id is used twice")
        found += [f"{qid}: {problem}" for problem in mine]
        seen.add(qid)
        if not mine:
            for name in question_from(item).slices:
                counts[name] += 1
    if len(raw) != QUESTION_COUNT:
        found.append(f"{len(raw)} questions, not {QUESTION_COUNT}")
    found += [f"{n} {name}, at least {MINIMUMS[name]}" for name, n in counts.items() if n < MINIMUMS[name]]
    return found


def _question_problems(item: dict[str, Any], seen: set[str], documents: Collection[str]) -> list[str]:
    expect, tags = item.get("expect"), item.get("tags")
    if not isinstance(item.get("question"), str) or not item["question"].strip():
        return ["no question text"]
    if not isinstance(expect, dict) or expect.get("kind") not in KINDS:
        return [f"expect.kind must be one of {KINDS}"]
    if not isinstance(tags, list) or not set(tags) <= TAGS:
        return [f"tags must be a list from {sorted(TAGS)}"]
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


def score(question: Question, answer: Mapping[str, Any]) -> Score:
    """Mark the ``Answer`` JSON. Only status "answered" counts as an answer: "unchecked"
    gives no figure, so it is wrong for an answer, and claims one exists, so it is a miss
    for a not-found question."""
    answered = answer.get("status") == "answered"
    if question.kind == "not_found":
        found = answer.get("status") == "not_found"
        outcome = Outcome.CORRECT_NOT_FOUND if found else Outcome.MISSED_NOT_FOUND
    else:
        text = answer_text(answer)
        near = any(_holds_value(d, question.tolerance, text) for d in question.distractors)
        right = answered and not near and _holds(question, text)
        outcome = Outcome.RIGHT if right else Outcome.WRONG
    wrong = outcome in (Outcome.WRONG, Outcome.MISSED_NOT_FOUND)
    return Score(outcome, wrong_verified=wrong and answered and answer.get("label") == "verified")


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
        return any(abs(found - target) <= tolerance for found in numbers_in(fold_digits(text)))
    plain, wanted = " ".join(text.casefold().split()), " ".join(value.casefold().split())
    return re.search(_BEFORE + re.escape(wanted) + _AFTER, plain) is not None


def counts_for_recall(question: Question, route: str | None) -> bool:
    return question.kind != "not_found" and route != "fixed_query"


def recall_hit(question: Question, hits: Sequence[Hit]) -> bool:
    """Whether an expected page is inside one of the first ``RECALL_K`` hits of the
    expected document."""
    return any(
        hit.document == question.document and any(hit.page_from <= p <= hit.page_to for p in question.pages)
        for hit in hits[:RECALL_K]
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
