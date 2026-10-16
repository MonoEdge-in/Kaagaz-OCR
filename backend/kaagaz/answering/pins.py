"""Most relevant picks as a ranking signal (Tushar, 16 Oct 2026; migration 0036).

A person may put one source of an answer on top ("Most relevant"). Later questions use it,
precision first:

* **Which picks** (``kaagaz.db.chat.pins_for``): only the asker's own, in this customer, the
  newest pick of each answer (the table keeps every pick), and none on a document in Trash.
  A pick in this conversation counts for every later question in it; one in the asker's other
  conversations only when the two questions share their words: at least half of the words of
  both, together, are in each (``lifts``, on the content words ``normalise`` gives).
* **What a pick does** (``pinned_first``): it moves a hit on the picked page (the document,
  a page inside the hit's pages) up the search order by at most ``LIFT_SAME`` places (this
  conversation) or ``LIFT_OTHER`` (another). Only a hit that passes the evidence gate by
  itself, on its score or its English keywords (the check cited sources and "Also in" use),
  moves; a weak one stays where it is. So a pick never adds a piece: it reorders the search's
  own hits after the gate has judged them in their own order, and may change which of them
  fill the model's window first. The answer's rules and words are as before.
* The order is steady: a hit's place is its old place less its lift, a lifted hit before an
  unlifted one on the same place, then the old place.

The service logs how many hits a pick moved on the timing line (``pinned=``), for tuning; it
is never in the answer.

PROVISIONAL: the lifts are starting values, to tune from what people pick.

Time: O(p * k) to decide the picks for p picks of k words, then O(e * d + e log e) for e hits
and d picks on one document.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from kaagaz.answering import glossary
from kaagaz.answering.evidence import Evidence
from kaagaz.answering.hinglish import normalise

LIFT_SAME = 3  # places a hit on a page picked in this conversation moves up
LIFT_OTHER = 1  # places for a page picked in another conversation on the same words


@dataclass(frozen=True, slots=True)
class Pin:
    """One answer's newest pick: its document and page, whether it was in this conversation,
    and the question that answer was for (as shown, its ``normalized_question``)."""

    document_id: str
    page_no: int
    same_conversation: bool
    question: str = ""


def lifts(pins: Sequence[Pin], keywords: Sequence[str]) -> dict[str, dict[int, int]]:
    """Document -> page -> places a hit there moves up (the larger, when two picks name it),
    for the picks that apply to a question with these content ``keywords``."""
    asked = set(keywords)
    found: dict[str, dict[int, int]] = {}
    for pin in pins:
        if pin.same_conversation:
            lift = LIFT_SAME
        elif _shares_words(asked, set(glossary.content_words(normalise(pin.question).keywords))):
            lift = LIFT_OTHER
        else:
            continue
        pages = found.setdefault(pin.document_id, {})
        pages[pin.page_no] = max(pages.get(pin.page_no, 0), lift)
    return found


def _shares_words(asked: set[str], before: set[str]) -> bool:
    """At least half of the two questions' words, together, are in both."""
    return bool(asked and before) and 2 * len(asked & before) >= len(asked | before)


def pinned_first(
    evidence: Sequence[Evidence],
    lifted: Mapping[str, Mapping[int, int]],
    passes: Callable[[Evidence], bool],
) -> tuple[list[Evidence], int]:
    """``evidence`` in the order the picks give, numbered again from 1, and how many hits
    moved up (the module docstring). ``passes``: the gate on one hit alone."""
    if not lifted:
        return list(evidence), 0
    places = [_lift(item, lifted, passes) for item in evidence]
    order = sorted(range(len(evidence)), key=lambda i: (i - places[i], -places[i], i))
    moved = sum(1 for new, old in enumerate(order) if new < old)
    return [replace(evidence[old], n=new) for new, old in enumerate(order, start=1)], moved


def _lift(item: Evidence, lifted: Mapping[str, Mapping[int, int]], passes: Callable[[Evidence], bool]) -> int:
    pages = lifted.get(item.hit.document_id)
    if item.neighbour or not pages:
        return 0
    lift = max((n for page, n in pages.items() if item.hit.page_from <= page <= item.hit.page_to), default=0)
    return lift if lift and passes(item) else 0
