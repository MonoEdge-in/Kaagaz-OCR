"""Sources by file (#718) and the files searched (#719): the pages each listed document shows and
the closest documents of the search's fused list (kaagaz/answering/sources.py), and how the
service fills them in. The folder paths' SQL is proved in test_isolation_ask_sources.py."""

import dataclasses
from typing import Any

from kaagaz.answering import sources
from kaagaz.answering.evidence import Evidence, number_evidence
from kaagaz.answering.service import Answer, Citation
from kaagaz.api import views
from tests.test_answering_golden import (
    INVOICE,
    TERMS,
    A,
    FakeHit,
    FakeModel,
    assert_well_formed,
    evidence_blocks,
    piece,
    service,
)


def _on(page: int, name: str = "deed.pdf", text: str = "Survey 44 area 0.81") -> FakeHit:
    return piece(name, text, regions=[{"kind": "box", "page": page, "box": [0.1, 0.2, 0.9, 0.3]}])


def _cite(n: int, hit: FakeHit, *, rank: int | None = None) -> Citation:
    region = hit.regions[0]
    return Citation(
        n=n,
        kind="piece",
        ref_id=hit.piece_id,
        document_id=hit.document_id,
        document_name=hit.document_name,
        detected_type="pdf",
        page_no=int(region["page"]),
        regions=[dict(region)],
        snippet=f"cited {n}",
        label="verified",
        page_from=int(region["page"]),
        page_to=int(region["page"]),
        label_page_no=None,
        label_regions=[],
        rank=rank,
    )


def _searched(*hits: FakeHit) -> list[Evidence]:
    return number_evidence(hits)


def _pages(found: list[dict[str, Any]]) -> list[tuple[int, list[int]]]:
    return [(page["page_no"], page["citations"]) for page in found]


def test_cited_pages_come_first_by_rank_then_the_pages_that_also_matched_in_search_order() -> None:
    p1, p2, p3, p4 = _on(1), _on(2), _on(3), _on(4)
    citations = [_cite(1, p3, rank=4), _cite(2, p1, rank=2)]
    found = sources.file_pages(p1.document_id, citations, _searched(p4, p1, p2, p3))
    assert _pages(found) == [(1, [2]), (3, [1]), (4, []), (2, [])]
    # What opens the exact spot: the page's own regions, and the cited or matched text.
    assert found[0]["regions"] == p1.regions and found[0]["snippet"] == "cited 2"
    assert found[2]["regions"] == p4.regions and found[2]["snippet"] == "Survey 44 area 0.81"


def test_a_page_shows_once_with_every_number_citing_it() -> None:
    first, again = _on(2), _on(2, text="Survey 44 owner Shinde")
    citations = [_cite(1, first, rank=1), _cite(2, again, rank=3)]
    found = sources.file_pages(first.document_id, citations, _searched(first, again, _on(2)))
    assert _pages(found) == [(2, [1, 2])]


def test_a_file_shows_at_most_five_pages_cited_ones_kept_first() -> None:
    hits = [_on(page) for page in range(1, 9)]
    citations = [_cite(1, hits[7], rank=8)]
    found = sources.file_pages(hits[0].document_id, citations, _searched(*hits))
    assert _pages(found) == [(8, [1]), (1, []), (2, []), (3, []), (4, [])]


def test_another_documents_hits_and_citations_are_never_its_pages() -> None:
    mine, theirs = _on(1), _on(5, name="other.pdf")
    found = sources.file_pages(mine.document_id, [_cite(1, theirs)], _searched(theirs, mine))
    assert _pages(found) == [(1, [])]


def test_a_whole_document_answer_without_ranks_lists_cited_pages_by_number() -> None:
    p1, p2 = _on(1), _on(2)
    found = sources.file_pages(p1.document_id, [_cite(1, p2), _cite(2, p1)], [])
    assert _pages(found) == [(2, [1]), (1, [2])]


def test_the_searched_files_are_the_ten_closest_documents_best_first_each_marked_used() -> None:
    hits = [_on(1, name=f"file-{n}.pdf") for n in range(12)]
    ordered = [hits[3], hits[0], hits[3], *hits]  # file-3 twice before the rest
    files = sources.searched_files([_cite(1, hits[0], rank=2)], _searched(*ordered))
    names = [f"file-{n}.pdf" for n in (3, 0, 1, 2, 4, 5, 6, 7, 8, 9)]
    assert [f["name"] for f in files] == names
    assert [f["used"] for f in files] == [name == "file-0.pdf" for name in names]
    assert files[1]["pages"] == [
        {"page_no": 1, "regions": hits[0].regions, "snippet": "cited 1", "citations": [1]}
    ]
    assert set(files[0]) == {"document_id", "name", "used", "pages"}


def test_with_paths_puts_each_documents_path_and_none_reads_as_shared() -> None:
    rows = [{"document_id": "a"}, {"document_id": "b"}, {"document_id": "c"}]
    paths: dict[str, list[str] | None] = {"a": ["Land & Legal", "Sale deeds"], "b": []}
    assert sources.with_paths(rows, paths) == [
        {"document_id": "a", "path": ["Land & Legal", "Sale deeds"]},
        {"document_id": "b", "path": []},
        {"document_id": "c", "path": None},  # a folder the asker can't see: "Shared with you"
    ]


# --- the service --------------------------------------------------------------


def test_an_answer_lists_its_files_pages_and_the_files_searched_from_a_wider_part_of_the_same_list() -> None:
    near = [
        _on(page, name="bill-1042.pdf", text=f"Invoice INV-1042 grand total page {page}") for page in (2, 3)
    ]
    pieces = [INVOICE, *near, TERMS] + [
        _on(1, name=f"other-{n}.pdf", text=f"grand total of INV-1042 copy {n}") for n in range(12)
    ]
    model = FakeModel()
    answer_service, retriever, _ = service(pieces=pieces, model=model)
    answer = answer_service.answer(A, "What is the grand total of INV-1042?")
    assert_well_formed(answer)
    data = answer.to_json()
    # The search fetched SEARCHED_PIECES of its fused list; the model still read answer_max_evidence.
    assert {call[3] for call in retriever.calls} == {sources.SEARCHED_PIECES}
    assert max(len(evidence_blocks(user)) for _, user in model.prompts) == 5
    (listed,) = data["documents"]
    assert listed["path"] is None  # no path port in these tests: never a folder name
    assert listed["pages"][0]["citations"] == [1]
    assert [page["citations"] for page in listed["pages"][1:]] == [[], []]  # "Also matches"
    searched = data["searched_files"]
    assert len(searched) == sources.SEARCHED_FILES
    assert searched[0] | {"pages": None} == {
        "document_id": INVOICE.document_id,
        "name": "bill-1042.pdf",
        "used": True,
        "pages": None,
        "path": None,
    }
    assert not any(f["used"] for f in searched[1:])


def test_a_saved_answer_shows_only_the_searched_files_still_live_and_never_is_replaced_for_one() -> None:
    files = [{"document_id": d, "name": f"{d}.pdf", "used": False, "pages": [], "path": []} for d in "abcde"]
    saved = {**Answer("not_found", None, [], [], [], "search", "q", False).to_json(), "searched_files": files}
    assert views.shown_document_ids(saved) == list("abcde")
    states = {"a": "live", "b": "older_version", "c": "in_trash", "d": "locked"}  # e: hidden now
    shown = views.answer_with_source_states(saved, states)
    assert shown["status"] == "not_found"
    assert [f["document_id"] for f in shown["searched_files"]] == ["a", "b"]
    assert "searched_files" not in views.answer_with_source_states(saved, {"c": "in_trash"})


def test_a_not_found_answer_still_lists_the_files_it_searched() -> None:
    weak = _on(2, name="notes.pdf", text="total of the month")  # one keyword of three: the gate fails
    answer_service, _, _ = service(pieces=[weak], model=FakeModel())
    data = answer_service.answer(A, "What is the grand total of INV-1042?").to_json()
    assert data["status"] == "not_found"
    (searched,) = data["searched_files"]
    assert (searched["name"], searched["used"]) == ("notes.pdf", False)
    assert [page["page_no"] for page in searched["pages"]] == [2]


def test_an_answer_without_searched_files_keeps_its_shape() -> None:
    plain = Answer("not_found", None, [], [], [], "search", "q", False)
    assert "searched_files" not in plain.to_json()
    assert dataclasses.replace(plain, searched_files=[]).to_json() == plain.to_json()
