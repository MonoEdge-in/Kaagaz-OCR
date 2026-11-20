"""How many search hits fill Ask's evidence slots (#723), and which (#722).

How many: ``question_kind`` reads the question's words as ``hinglish.normalise`` gives them
(no model call): a single fact (who, when, where), a list (all, every, list, how many), a
comparison (compare, difference, highest, lowest) or a summary (summary, explain); anything
else is unknown. ``evidence_slots`` scales ANSWER_MAX_EVIDENCE by it: a quarter fewer for a
fact, half as many again for a list, comparison or summary, the same for unknown, from 1 to
the search's MAX_LIMIT. PROVISIONAL: #724 tunes these with real questions (``score-ask``).

Which: the search ranks pieces; one page or one file often holds the best several, and then
fills every slot with near repeats while the page that answers waits just below. ``spread``
keeps at most PAGE_CAP pieces of one page and DOCUMENT_CAP of one document, and gives the freed
slots to the next ranked hits. A question about one document still gets every slot: when too
few other hits are left, the capped ones fill the rest, after them. The search is asked for
POOL_PER_SLOT hits a slot, so there are next ranked hits to take.

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

Kind = Literal["fact", "list", "compare", "summary", "unknown"]
# Normalised words ("kaun" is "who", "sabse jyada" "highest"); "how many" and "highest" or
# "lowest" are intent.py's own reading of a count and a ranking.
_LIST = frozenset({"all", "every", "each", "list"})
_COMPARE = frozenset({"compare", "compared", "comparison", "difference", "differences", "versus", "vs"})
_SUMMARY = frozenset({"summary", "summarise", "summarize", "overview", "explain", "describe"})
_FACT = frozenset({"who", "whom", "whose", "when", "where"})
_MANY = frozenset({"are", "were"})  # "who are the owners" asks for more than one

PAGE_CAP = 2  # a third piece of one page mostly repeats the first two
DOCUMENT_CAP = 3  # so the default 8 slots reach at least three documents
POOL_PER_SLOT = 3  # hits searched a slot: enough next ranked hits to take a capped one's place


def spread[H: Hit](hits: Sequence[H], slots: int) -> list[H]:
    """The best ``slots`` of ``hits`` (best first), at most PAGE_CAP of one page (its first) and
    DOCUMENT_CAP of one document, the next ranked hits taking the freed slots, in rank order; slots
    still free then take the capped hits, best first, after them (a repeat of a page goes below a
    page of its own, so the draft's prompt keeps it in its middle, ``draft.edges``)."""
    pages: Counter[tuple[str, int]] = Counter()
    documents: Counter[str] = Counter()
    kept: list[H] = []
    capped: list[H] = []
    for hit in hits:
        if len(kept) == slots:
            break
        page = (hit.document_id, hit.page_from)
        if pages[page] < PAGE_CAP and documents[hit.document_id] < DOCUMENT_CAP:
            pages[page] += 1
            documents[hit.document_id] += 1
            kept.append(hit)
        else:
            capped.append(hit)
    return kept + capped[: slots - len(kept)]


def question_kind(question: str) -> Kind:
    """What ``question`` asks for, from its words; a summary first, then a comparison, a list and
    a single fact (the module docstring). O(w)."""
    words = normalise(question).words
    said, aggregate = set(words), _aggregate(words)
    if said & _SUMMARY:
        return "summary"
    if said & _COMPARE or aggregate in ("highest", "lowest"):
        return "compare"
    if said & _LIST or aggregate == "count":
        return "list"
    if said & _FACT and not said & _MANY:
        return "fact"
    return "unknown"


def evidence_slots(kind: Kind, most: int) -> int:
    """The evidence slots for a question of ``kind``, from ``most`` (ANSWER_MAX_EVIDENCE)."""
    if kind == "fact":
        slots = most - most // 4  # one spot answers it: fewer near misses for the model to weigh
    elif kind == "unknown":
        slots = most
    else:
        slots = most + most // 2  # the answer is spread over pages and documents
    return max(1, min(slots, MAX_LIMIT))
