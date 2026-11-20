"""Ask's evidence slots (#722): spread over pages and documents, then wired into the service."""

import dataclasses

from kaagaz.answering import draft
from kaagaz.answering.slots import DOCUMENT_CAP, PAGE_CAP, POOL_PER_SLOT, spread
from tests.test_answering_context import (
    SEARCH_ONLY,
    A,
    FakeContext,
    Fused,
    Routed,
    answers,
    blocks,
    cites,
    page,
)
from tests.test_answering_golden import FakeHit

A_PDF, B_PDF, C_PDF = "a.pdf", "b.pdf", "c.pdf"


def pages(document: str, *numbers: int) -> list[FakeHit]:
    """One piece a page number given, in that order (a number twice: two pieces of that page)."""
    return [page(n, f"{document} piece {at}", document=document, seq=at) for at, n in enumerate(numbers)]


def test_the_caps_are_two_a_page_and_three_a_document() -> None:
    assert (PAGE_CAP, DOCUMENT_CAP, POOL_PER_SLOT) == (2, 3, 3)


def test_one_page_keeps_two_slots_and_the_next_hits_take_the_rest() -> None:
    same = pages(A_PDF, 4, 4, 4, 4)
    others = [*pages(B_PDF, 1), *pages(C_PDF, 7)]

    kept = spread([*same, *others], 4)

    assert kept == [same[0], same[1], others[0], others[1]]


def test_one_document_keeps_three_slots_and_the_next_hits_take_the_rest() -> None:
    mine = pages(A_PDF, 1, 2, 3, 4, 5)
    others = [*pages(B_PDF, 1, 2), *pages(C_PDF, 1)]

    kept = spread([*mine, *others], 6)

    assert kept == [*mine[:3], *others]


def test_capped_hits_fill_the_slots_no_other_hit_can() -> None:
    """A question about one document still gets every slot, best first."""
    mine = pages(A_PDF, 4, 4, 4, 5, 6)
    other = pages(B_PDF, 2)

    kept = spread([*mine, *other], 5)

    assert kept == [mine[0], mine[1], mine[2], mine[3], other[0]]  # rank order; mine[4] is the 6th


def test_fewer_hits_than_slots_keeps_them_all_in_order() -> None:
    hits = pages(A_PDF, 1, 1, 1)

    assert spread(hits, 8) == hits
    assert spread([], 8) == []


def test_the_service_spreads_the_hits_then_adds_topic_pieces_and_neighbours() -> None:
    """The search is asked for POOL_PER_SLOT hits a slot; the draft reads the spread hits, then the
    topic pieces, then the neighbours, each numbered after them (CX6 and Q4 unchanged)."""
    settings = dataclasses.replace(SEARCH_ONLY, answer_max_evidence=3)
    crowd = [page(9, f"Casual leave days, rule {at}.", seq=at) for at in range(5)]  # one page, best first
    theirs = page(2, "Casual leave: 10 a year.", document="staff-rules.pdf")  # ranked 6th
    topic = page(18, "Maternity leave: 26 weeks.")
    near = page(10, "Casual leave may be carried over.")
    retriever = Fused([*crowd, theirs])
    retriever.near = [near]
    context = FakeContext([*crowd, topic], topics=[("leave", ("maternity leave",), [crowd[0], topic])])
    model = Routed(cites(("Casual leave is 10 days a year.", theirs.text)))

    answers(retriever, context, model, settings).answer(A, "Casual leave days?")

    assert {call[3] for call in retriever.calls} == {POOL_PER_SLOT * 3}
    order = [crowd[0], crowd[1], theirs, topic, near]
    assert blocks(model.drafted[0]) == [hit.text for hit in draft.edges(order)]
