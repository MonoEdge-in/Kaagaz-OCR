"""Score Ask's retrieval over a question file, for one customer (#721; ``kaagaz score-ask``).

The file is JSON, a list of questions, each with the documents (as named in the library) and
pages that answer it, and an optional kind of your own to group the scores by::

    [{"question": "What is the GST rate on cement?",
      "expect": [{"document": "tax-note.pdf", "pages": [2]}],
      "kind": "fact"}]

Each question goes through the search Ask's search route runs (``AnswerService.evidence_hits``:
the hits it keeps as evidence, as the customer, under row level security), with no model call:
words and typos only. ``--embed`` adds the meaning list (one embedding call a question).
``--answers`` asks each question in full as Ask does (model calls: the rewrite, embeddings and the
draft) and checks every citation is on an expected page.

It prints recall (an expected page among the kept evidence), MRR (1 / the rank of the first
such piece, 0 when none), each kind's, and each question it missed. Questions and document
text stay in the operator's terminal, never a log.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TextIO

from kaagaz.answering.evidence import Hit


@dataclass(frozen=True, slots=True)
class Expected:
    document: str  # the display name
    pages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Item:
    question: str
    expected: tuple[Expected, ...]
    kind: str | None = None


def load(text: str) -> list[Item]:
    """The questions of a file's ``text``; ValueError naming the first item that is not one."""
    raw = json.loads(text)
    if not isinstance(raw, list) or not raw:
        raise ValueError("the file must be a JSON list of at least one question")
    return [_item(at, entry) for at, entry in enumerate(raw, start=1)]


def _item(at: int, entry: Any) -> Item:
    problem = ValueError(f"question {at}: needs a question, expect [{{document, pages}}] and at most a kind")
    if not isinstance(entry, dict) or not _text(entry.get("question")):
        raise problem
    expect, kind = entry.get("expect"), entry.get("kind")
    if not isinstance(expect, list) or not expect or not (kind is None or isinstance(kind, str)):
        raise problem
    if not all(isinstance(e, dict) and _text(e.get("document")) and _pages(e.get("pages")) for e in expect):
        raise problem
    return Item(entry["question"], tuple(Expected(e["document"], tuple(e["pages"])) for e in expect), kind)


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


def run(
    items: Sequence[Item],
    evidence: Callable[[str], Sequence[Hit]],
    out: TextIO,
    answer: Callable[[str], Sequence[tuple[str, int, int]]] | None = None,
) -> None:
    """Score each item's ``evidence`` hits; with ``answer`` (a full answer's citations, each its
    document name and pages), its citations too. Prints the scores to ``out``."""
    ranks: list[int | None] = []
    right = total = answered = 0
    for item in items:
        ranks.append(first_rank(item, evidence(item.question)))
        citations = [] if answer is None else answer(item.question)
        right += sum(on_expected_page(item, *citation) for citation in citations)
        total += len(citations)
        answered += bool(citations)
    out.write(_line("all", ranks) + "\n")
    for kind in sorted({item.kind for item in items if item.kind is not None}):
        out.write(_line(kind, [r for item, r in zip(items, ranks, strict=True) if item.kind == kind]) + "\n")
    if answer is not None:
        share = f"{right / total:.2f}" if total else "-"
        out.write(f"citations on an expected page: {right} of {total} ({share}), ")
        out.write(f"{answered} of {len(items)} answered\n")
    for item, rank in zip(items, ranks, strict=True):
        if rank is None:
            out.write(f"missed: {item.question}\n")


def _line(name: str, ranks: Sequence[int | None]) -> str:
    found = [rank for rank in ranks if rank is not None]
    mrr = sum(1 / rank for rank in found) / len(ranks)
    return f"{name}: recall {len(found)} of {len(ranks)} ({len(found) / len(ranks):.2f}), MRR {mrr:.2f}"
