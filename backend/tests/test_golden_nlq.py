"""The NLQ eval set (PRD 6 package G): tests/golden/questions_nlq.json over the made-up
documents of tests/golden/nlq/, built by tests/golden/nlq/make_documents.py. CI checks the
files only: the live run needs the stack and the model (scripts/run_golden.py --set nlq,
NOT RUN here)."""

import copy
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from scripts import golden_scoring as scoring
from scripts.golden_scoring import (
    NLQ_SHA256,
    SPLITS,
    file_sha256,
    nlq_problems,
    phrase_in,
    question_from,
    sha256_manifest,
)
from tests.golden.nlq.make_documents import DOCUMENTS, build, manifest
from tests.test_golden_romanised import TUNED

GOLDEN = Path(__file__).resolve().parent / "golden"
QUESTIONS = GOLDEN / "questions_nlq.json"
DOCS = GOLDEN / "nlq"
GENERIC_CHOICES = {"All of these"}  # a choice no document needs to print


def _load() -> tuple[list[dict[str, Any]], dict[str, str]]:
    raw: list[dict[str, Any]] = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    return raw, sha256_manifest((DOCS / "documents.sha256").read_text(encoding="utf-8"))


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9/.]+", text.casefold()))


def test_the_file_meets_every_rule_and_is_frozen() -> None:
    raw, documents = _load()
    assert nlq_problems(raw, documents) == []
    # An edit means a new baseline: change NLQ_SHA256 with it, on purpose.
    assert file_sha256(QUESTIONS) == NLQ_SHA256


def test_the_committed_documents_are_the_generators_bytes() -> None:
    _, documents = _load()
    made = build()
    assert build() == made  # deterministic
    assert manifest(made) == (DOCS / "documents.sha256").read_text(encoding="utf-8")
    on_disk = {path.name: file_sha256(path) for path in DOCS.iterdir() if path.suffix in {".pdf", ".csv"}}
    assert on_disk == documents
    assert len(documents) == 30
    for name, data in made.items():
        assert len(data) < 2 * 1024 * 1024
        assert data.startswith(b"%PDF-") == name.endswith(".pdf")


def test_no_hidden_characters_and_every_text_is_nfc() -> None:
    texts = [QUESTIONS.read_text(encoding="utf-8")]
    texts += [line for document in DOCUMENTS.values() for page in document.pages for line in page]
    for text in texts:
        assert [hex(ord(ch)) for ch in text if unicodedata.category(ch) in {"Cf", "Co", "Cs"}] == []
        assert unicodedata.is_normalized("NFC", text)


def test_no_question_leaks_between_dev_and_test() -> None:
    raw, _ = _load()
    by_split: dict[str, set[str]] = {split: set() for split in SPLITS}
    for item in raw:
        by_split[item["split"]].add(" ".join(item["question"].casefold().split()))
    assert by_split["dev"] & by_split["test"] == set()
    slices = [set(question_from(item).slices) for item in raw]
    for tag in {tag for item in raw for tag in item["tags"]}:
        assert all(any({tag, split} <= found for found in slices) for split in SPLITS), tag


def test_every_expected_figure_is_printed_in_the_truth_table() -> None:
    raw, _ = _load()
    for item in raw:
        figure = item["expect"].get("figure")
        if figure is None:
            continue
        document = DOCUMENTS[figure["document"]]
        printed = {(f.field, f.as_printed, f.page) for f in document.figures}
        assert (figure["field"], figure["as_printed"], figure["page"]) in printed, item["id"]
        assert any(figure["as_printed"] in line for line in document.pages[figure["page"] - 1]), item["id"]


def test_every_clarify_choice_and_pick_comes_from_the_documents() -> None:
    raw, _ = _load()
    printed = [
        _words(" ".join([document.latin, *(line for page in document.pages for line in page)]))
        for document in DOCUMENTS.values()
    ]
    for item in raw:
        clarify = item["expect"].get("clarify")
        if clarify is None:
            continue
        for choice in clarify["choices"]:
            assert any(_words(choice) <= words for words in printed), (item["id"], choice)
        pick = clarify["pick"]
        fits = any(phrase_in(choice, pick) or phrase_in(pick, choice) for choice in clarify["choices"])
        assert pick in GENERIC_CHOICES or fits or any(_words(pick) <= words for words in printed), item["id"]


def test_no_question_or_name_uses_a_word_the_sound_key_was_tuned_on() -> None:
    raw, _ = _load()
    for item in raw:  # place names stay as they are spelled
        assert _words(item["question"]) & TUNED <= {"pune"}, item["id"]
    for document in DOCUMENTS.values():  # and a 7/12 prints its "Taluka:" label
        lines = [document.latin, *(line for page in document.pages for line in page)]
        assert _words(" ".join(lines)) & TUNED <= {"pune", "taluka"}


def test_a_broken_entry_is_refused() -> None:
    raw, documents = _load()
    broken = copy.deepcopy(raw)
    broken[0]["expect"]["figure"]["document"] = "missing.pdf"
    broken[1]["split"] = "test"  # n002 follows n001, a dev question
    broken[2]["expect"]["clarify"] = {"choices": ["Maval"]}
    broken[3]["expect"]["documents"]["not"] = [broken[3]["expect"]["documents"]["expected"][0]]
    broken[4]["expect"]["soft_not_found"] = {"closest": []}
    broken[5]["scope"] = {"document": "missing.pdf"}
    broken[6]["expect"]["no_total"] = {"allowed": "12,250 and 6,800"}
    found = nlq_problems(broken, documents)
    assert found[:7] == [
        "n001: expect.figure needs a manifest document, field, as_printed, a page and a label in"
        " (None, 'verified', 'needs_review')",
        "n002: a follow-up is in its question's split",
        "n003: expect.clarify needs choices (texts) and a pick, or is null",
        "n004: expect.documents lists a document as expected and not",
        "n005: a soft not-found is a not-found question",
        "n006: scope needs a document from the manifest, or all: true",  # Changed on purpose (#644)
        "n007: expect.no_total needs allowed: null or the permitted total's text, one number",
    ]


def test_too_few_items_in_a_slice_or_an_expectation_is_refused() -> None:
    raw, documents = _load()
    kept = [item for item in raw if "misspelling" not in item["tags"] and "figure" not in item["expect"]]
    found = nlq_problems(kept, documents)
    assert f"{len(kept)} questions, at least 150" in found
    assert "0 in slice misspelling, at least 15" in found
    assert "0 figure, at least 60" in found


def test_629_a_property_clarify_passes_only_with_labelled_choices() -> None:
    """#629: the clarify expectation also needs each property pick to name its kind, its number and
    a place or document: never a bare number, a date or several numbers joined."""
    asked = {"question": "Which property do you mean?"}
    good = [
        {"kind": "pick", "label": "Survey ६१ (61), Kusgaon, Maval · Land record"},
        {"kind": "other", "label": "x"},
    ]
    assert scoring.property_choices_labelled({**asked, "choices": good})
    date = "\u0967\u0969.\u0966\u096c.\u0966\u0966"  # 13.06.00 in Devanagari digits
    for bad in ("६१", date, "६५/३, ८१, ८५/२"):
        assert not scoring.property_choices_labelled({**asked, "choices": [{"kind": "pick", "label": bad}]})
    assert scoring.property_choices_labelled({"question": "Which Nikhil do you mean?", "choices": []})


def test_644_focus_related_and_scope_are_marked_on_a_reply() -> None:
    """Package K: the understood line names the focus's facts; each related document is listed in
    the first 10 with its reason; a document outside the scope or left out is never shown."""
    raw, _ = _load()
    items = {item["id"]: item for item in raw}
    related = question_from(items["n182"])  # "Find documents related to this land", after Kusgaon
    assert related.nlq["scope"] == {"all": True} and related.follow_up_of == "n181"
    names = {"k": "712-kusgaon-45-2.pdf", "u": "712-ulwe-9.pdf", "kh": "sale-deed-khalapur-gat-118.pdf"}
    names |= {"hj": "712-hinjewadi-12-mr.pdf", "km": "712-kamshet-77-mr.pdf"}
    why = {"u": "Same person Nikhil Varde", "kh": "Same person Ketaki Mhaskar; same person Nikhil Varde"}
    why |= {"hj": "Same person Nikhil Varde", "km": "Same person Ketaki Mhaskar"}
    reply: dict[str, Any] = {
        "assumptions": ["Searching all your documents for Survey 45/2, Kusgaon (from 712-kusgaon-45-2)."],
        "documents": [{"document_id": d, "why": w, "citations": []} for d, w in why.items()],
        "citations": [],
    }
    why_docs = reply["documents"]
    marks = scoring.nlq_score(related, reply, reply, names, "Vrushali").passed()
    assert marks["focus"] and marks["related"] and marks["scope"]
    wrong = {**reply, "assumptions": ["Searching all your documents for Survey 9, Ulwe (from 712-ulwe-9)."]}
    wrong["documents"] = [*reply["documents"][1:], {"document_id": "k", "why": "Same place Kusgaon"}]
    marks = scoring.nlq_score(related, wrong, wrong, names, "Vrushali").passed()
    assert not marks["focus"] and not marks["related"] and not marks["scope"]  # the focus listed itself
    # Every document listed, one without its reason.
    unsaid = {
        **reply,
        "documents": [{**d, "why": "Similar content"} if d["document_id"] == "u" else d for d in why_docs],
    }
    assert not scoring.nlq_score(related, unsaid, unsaid, names, "Vrushali").passed()["related"]
    metrics = scoring.nlq_metrics([scoring.nlq_score(related, reply, reply, names, "Vrushali")])
    assert metrics["related_recall"] == (4, 4) and metrics["focus"] == (1, 1) and metrics["scope"] == (1, 1)


def test_644_a_follow_up_is_marked_by_its_documents() -> None:
    raw, _ = _load()
    item = question_from(next(i for i in raw if i["id"] == "n206"))  # the Chowk share, after a switch
    names = {"c": "712-chowk-31-4.pdf"}
    right = {"documents": [{"document_id": "c", "why": "", "citations": [1]}], "citations": []}
    assert scoring.nlq_score(item, right, right, names, "Vrushali").passed()["follow_up"]
    assert not scoring.nlq_score(item, {}, {}, names, "Vrushali").passed()["follow_up"]
