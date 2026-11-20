"""How many search hits fill Ask's evidence slots (#723), and which (#722).

How many: ``question_kind`` reads the question's words as ``hinglish.normalise`` gives them
(no model call): a list (all, every, list, how many), a comparison (compare, difference,
highest, lowest) or a summary (summary, explain); anything else is unknown, a single fact
included. ``evidence_slots`` gives a list, comparison or summary a quarter more than
ANSWER_MAX_EVIDENCE (8 -> 10; more slots and their neighbours would push long Devanagari
Precision answers past ANSWER_PARTS_TOKENS into extra model calls), and any other question the
setting itself, from 1 to the search's MAX_LIMIT. A question asking for "the top of page 3"
reads as a comparison (intent.py's "top"): it costs two pieces more, never a wrong answer.
PROVISIONAL: #724 tunes these with real questions.

Which: the search ranks pieces; one page often holds the best several, and then fills every
slot with near repeats while the page that answers waits just below. ``spread`` keeps at most
PAGE_CAP pieces of one page and gives the freed slots to the next ranked hits; slots still free
then take the capped pieces, after them. A file's other pages are never capped: "List the
conditions in the Sharma lease" keeps every lease page that ranks (a cap on a file comes back only
if real questions show a gain, #724). The search is asked for POOL_PER_SLOT hits a slot, so there
are next ranked hits to take.

A "which documents" question keeps its own rule and count (each document once, service
``_documents_once``). Time is O(h) over the h hits, O(w) over the question's w words.
"""

from collections import Counter
from collections.abc import Sequence
from typing import Literal

from kaagaz.answering.evidence import Hit
from kaagaz.answering.hinglish import normalise
from kaagaz.answering.intent import _aggregate
from kaagaz.search.hybrid import MAX_LIMIT

Kind = Literal["list", "compare", "summary", "unknown"]
# Normalised words ("sabse jyada" is "highest"); "how many" and "highest" or "lowest" are
# intent.py's own reading of a count and a ranking.
_LIST = frozenset({"all", "every", "each", "list"})
_COMPARE = frozenset({"compare", "compared", "comparison", "difference", "differences", "versus", "vs"})
_SUMMARY = frozenset({"summary", "summarise", "summarize", "overview", "explain", "describe"})

PAGE_CAP = 2  # a third piece of one page mostly repeats the first two
POOL_PER_SLOT = 3  # hits searched a slot: enough next ranked hits to take a capped one's place


def spread[H: Hit](hits: Sequence[H], slots: int) -> list[H]:
    """The best ``slots`` of ``hits`` (best first), at most PAGE_CAP of one page (its first), the
    next ranked hits taking the freed slots, in rank order; slots still free then take the capped
    hits, best first, after them (a repeat of a page goes below a page of its own, so the draft's
    prompt keeps it in its middle, ``draft.edges``)."""
    pages: Counter[tuple[str, int]] = Counter()
    kept: list[H] = []
    capped: list[H] = []
    for hit in hits:
        if len(kept) == slots:
            break
        page = (hit.document_id, hit.page_from)
        if pages[page] < PAGE_CAP:
            pages[page] += 1
            kept.append(hit)
        else:
            capped.append(hit)
    return kept + capped[: slots - len(kept)]


def question_kind(question: str) -> Kind:
    """What ``question`` asks for, from its words: a summary first, then a comparison, then a list
    (the module docstring). O(w)."""
    words = normalise(question).words
    said, aggregate = set(words), _aggregate(words)
    if said & _SUMMARY:
        return "summary"
    if said & _COMPARE or aggregate in ("highest", "lowest"):
        return "compare"
    if said & _LIST or aggregate == "count":
        return "list"
    return "unknown"


def evidence_slots(kind: Kind, most: int) -> int:
    """The evidence slots for a question of ``kind``, from ``most`` (ANSWER_MAX_EVIDENCE)."""
    slots = most if kind == "unknown" else most + most // 4  # its answer spans pages and files
    return max(1, min(slots, MAX_LIMIT))
