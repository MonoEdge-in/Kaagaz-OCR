"""Score Ask's retrieval over a question file, for one customer (#721; ``kaagaz score-ask``).

The file is JSON, a list of questions, each with the documents (as named in the library) and
pages that answer it::

    [{"question": "Compare the rent in the two leases",
      "expect": [{"document": "lease-a.pdf", "pages": [2]}, {"document": "lease-b.pdf", "pages": [3]}]}]

Each question goes through the search Ask's search route runs (``AnswerService.evidence_hits``:
the hits it keeps as evidence, as the customer, under row level security), with no model call:
words and typos only. ``--embed`` adds the meaning list (one embedding call a question).
``--answers`` asks each question in full as Ask does (model calls: the rewrite, the translation,
embeddings and the draft) and checks every citation is on an expected page.

It prints, for all questions and for each kind Ask reads them as (``slots.question_kind``):
the page recall (each question's share of its expected pages among the kept evidence, averaged),
how many questions had any expected page kept, and MRR (1 / the rank of the first kept piece on
an expected page, 0 when none); then each question with an expected page missing. Questions and
document text stay in the operator's terminal, never a log.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TextIO

from kaagaz.answering.evidence import Hit
from kaagaz.answering.slots import question_kind


@dataclass(frozen=True, slots=True)
class Expected:
    document: str  # the display name
    pages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Item:
    question: str
    expected: tuple[Expected, ...]


@dataclass(frozen=True, slots=True)
class Score:
    found: int  # expected pages among the kept evidence
    expected: int
    rank: int | None  # of the first kept piece on an expected page


def load(text: str) -> list[Item]:
    """The questions of a file's ``text``; ValueError naming the first item that is not one."""
    raw = json.loads(text)
    if not isinstance(raw, list) or not raw:
        raise ValueError("the file must be a JSON list of at least one question")
    return [_item(at, entry) for at, entry in enumerate(raw, start=1)]


def _item(at: int, entry: Any) -> Item:
    problem = ValueError(f"question {at}: needs a question and expect [{{document, pages}}]")
    if not isinstance(entry, dict) or not _text(entry.get("question")):
        raise problem
    expect = entry.get("expect")
    if not isinstance(expect, list) or not expect:
        raise problem
    if not all(isinstance(e, dict) and _text(e.get("document")) and _pages(e.get("pages")) for e in expect):
        raise problem
    return Item(entry["question"], tuple(Expected(e["document"], tuple(e["pages"])) for e in expect))


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _pages(value: Any) -> bool:
    """A list of page numbers from 1 (``True`` is no number)."""
    return isinstance(value, list) and bool(value) and all(type(p) is int and p >= 1 for p in value)


def on_expected_page(item: Item, document: str, page_from: int, page_to: int) -> bool:
    """Whether a piece or citation of ``document`` covering pages ``page_from`` to ``page_to`` is on
    one of ``item``'s expected pages."""
    return any(
        e.document == document and any(page_from <= p <= page_to for p in e.pages) for e in item.expected
    )


def first_rank(item: Item, hits: Sequence[Hit]) -> int | None:
    """The rank (from 1) of the first hit on an expected page; None when none is."""
    for rank, hit in enumerate(hits, start=1):
        if on_expected_page(item, hit.document_name, hit.page_from, hit.page_to):
            return rank
    return None


def score(item: Item, hits: Sequence[Hit]) -> Score:
    """How many of ``item``'s expected pages the kept ``hits`` (best first) cover, and the rank of
    the first hit on one. O(h * p)."""
    wanted = {(e.document, p) for e in item.expected for p in e.pages}
    found = {
        (document, p)
        for document, p in wanted
        if any(h.document_name == document and h.page_from <= p <= h.page_to for h in hits)
    }
    return Score(len(found), len(wanted), first_rank(item, hits))


def run(
    items: Sequence[Item],
    evidence: Callable[[str], Sequence[Hit]],
    out: TextIO,
    answer: Callable[[str], Sequence[tuple[str, int, int]]] | None = None,
) -> None:
    """Score each item's ``evidence`` hits; with ``answer`` (a full answer's citations, each its
    document name and pages), its citations too. Prints the scores to ``out``."""
    scores: list[Score] = []
    right = total = answered = 0
    for item in items:
        scores.append(score(item, evidence(item.question)))
        citations = [] if answer is None else answer(item.question)
        right += sum(on_expected_page(item, *citation) for citation in citations)
        total += len(citations)
        answered += bool(citations)
    out.write(_line("all", scores) + "\n")
    kinds = [question_kind(item.question) for item in items]
    for kind in sorted(set(kinds)):
        out.write(_line(kind, [s for s, k in zip(scores, kinds, strict=True) if k == kind]) + "\n")
    if answer is not None:
        share = f"{right / total:.2f}" if total else "-"
        out.write(f"citations on an expected page: {right} of {total} ({share}), ")
        out.write(f"{answered} of {len(items)} answered\n")
    for item, marked in zip(items, scores, strict=True):
        if marked.found < marked.expected:
            missed = marked.expected - marked.found
            out.write(f"missed {missed} of {marked.expected} pages: {item.question}\n")


def _line(name: str, scores: Sequence[Score]) -> str:
    recall = sum(s.found / s.expected for s in scores) / len(scores)
    some = sum(s.found > 0 for s in scores)
    mrr = sum(1 / s.rank for s in scores if s.rank is not None) / len(scores)
    return f"{name}: page recall {recall:.2f}, any page found {some} of {len(scores)}, MRR {mrr:.2f}"
