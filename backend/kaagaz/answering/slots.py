"""Which search hits fill Ask's evidence slots (#722).

The search ranks pieces; one page or one file often holds the best several, and then fills
every slot with near repeats while the page that answers waits just below. ``spread`` keeps at
most PAGE_CAP pieces of one page and DOCUMENT_CAP of one document, and gives the freed slots
to the next ranked hits. A question about one document still gets every slot: when too few
other hits are left, the capped ones fill the rest, best first. The search is asked for
POOL_PER_SLOT hits a slot, so there are next ranked hits to take.

A "which documents" question keeps its own rule (each document once, service
``_documents_once``). Time is O(h) over the h hits.
"""

from collections import Counter
from collections.abc import Sequence

from kaagaz.answering.evidence import Hit

PAGE_CAP = 2  # a third piece of one page mostly repeats the first two
DOCUMENT_CAP = 3  # so the default 8 slots reach at least three documents
POOL_PER_SLOT = 3  # hits searched a slot: enough next ranked hits to take a capped one's place


def spread[H: Hit](hits: Sequence[H], slots: int) -> list[H]:
    """The best ``slots`` of ``hits`` (best first), at most PAGE_CAP of one page (its first) and
    DOCUMENT_CAP of one document, the next ranked hits taking the freed slots; slots still free
    then take the capped hits, best first. Kept in rank order."""
    pages: Counter[tuple[str, int]] = Counter()
    documents: Counter[str] = Counter()
    kept: list[int] = []
    capped: list[int] = []
    for at, hit in enumerate(hits):
        if len(kept) == slots:
            break
        page = (hit.document_id, hit.page_from)
        if pages[page] < PAGE_CAP and documents[hit.document_id] < DOCUMENT_CAP:
            pages[page] += 1
            documents[hit.document_id] += 1
            kept.append(at)
        else:
            capped.append(at)
    kept += capped[: slots - len(kept)]
    return [hits[at] for at in sorted(kept)]
